from pathlib import Path

import cv2
import numpy as np
import pymupdf
import pytest
from sqlalchemy import select

from extensions import db
from models import Assessment, CaptureJob, Question, Student
from services.capture_queue import (
    QueueFullError,
    claim_next_job,
    get_queue_counts,
    process_job,
    queue_image_bytes,
    recover_interrupted_jobs,
    retry_failed_job,
)
from services.capture_state_machine import CaptureState, CaptureStateMachine
from services.marker_detection import detect_v3_registration_markers
from services.esp32_camera import CameraError, ESP32Camera
from services.paparazzi_v3_submission_service import confirm_v3_scan_draft
from services.paparazzi_v3_scanner import (
    _profile_mark_centers,
    get_answer_bubble_centers_mm,
    get_student_id_bubble_centers_mm,
    load_v3_profile,
)


ASSET_DIR = Path(__file__).resolve().parents[1] / "static" / "answer_sheets" / "v3" / "Paparazzi_Answer_Sheets_V3_Package"


@pytest.fixture
def blank_v3_frame():
    page = pymupdf.open(ASSET_DIR / "Answer_Sheet_20Q_V3.pdf")[0]
    pix = page.get_pixmap(matrix=pymupdf.Matrix(240 / 72, 240 / 72), alpha=False)
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _detection(signature=None, *, valid=True, bottom=True, xshift=0.0):
    return {
        "valid": valid,
        "markers_visible": valid,
        "geometry_valid": valid,
        "sheet_sufficiently_visible": valid,
        "bottom_markers_visible": bottom,
        "marker_coordinates": {
            "top_left": [10 + xshift, 10], "top_right": [90 + xshift, 10],
            "bottom_right": [90 + xshift, 120], "bottom_left": [10 + xshift, 120],
        } if valid else {},
        "page_signature": signature or [0] * 1440,
    }


def _stabilize(machine, signature, *, shift=0.0):
    requested = False
    for _ in range(8):
        requested = machine.observe(_detection(signature, xshift=shift))
        if requested:
            break
    return requested


def _capture_first_sheet(machine, signature):
    assert not machine.observe(_detection(signature))
    assert machine.state == CaptureState.SHEET_DETECTED
    assert _stabilize(machine, signature)
    machine.capture_and_queue_succeeded("a" * 32, signature)


def test_marker_detector_uses_v3_profile_and_recognizes_full_sheet(blank_v3_frame):
    result = detect_v3_registration_markers(blank_v3_frame)
    assert result["valid"] is True
    assert result["markers_visible"] is True
    assert result["geometry_valid"] is True
    assert result["sheet_sufficiently_visible"] is True
    assert set(result["marker_coordinates"]) == {"top_left", "top_right", "bottom_right", "bottom_left"}
    assert len(result["page_signature"]) == 96 * 136


def test_marker_detector_rejects_missing_registration_mark(blank_v3_frame):
    profile = load_v3_profile()
    markers = _profile_mark_centers(profile)
    x, y = markers["bottomright"]
    px = blank_v3_frame.shape[1] / profile["paper"]["width_mm"]
    py = blank_v3_frame.shape[0] / profile["paper"]["height_mm"]
    half = round(profile["registration_marks"]["size_mm"] * (px + py) / 4)
    cx, cy = round(x * px), round(y * py)
    cv2.rectangle(blank_v3_frame, (cx - half - 3, cy - half - 3), (cx + half + 3, cy + half + 3), (255, 255, 255), -1)
    result = detect_v3_registration_markers(blank_v3_frame)
    assert result["valid"] is False


def test_marker_detector_survives_lighting_and_unrelated_square_contours(blank_v3_frame):
    lighter = cv2.convertScaleAbs(blank_v3_frame, alpha=0.72, beta=70)
    result = detect_v3_registration_markers(lighter)
    assert result["valid"] is True
    original = detect_v3_registration_markers(blank_v3_frame)
    lighting_change = sum(a != b for a, b in zip(original["page_signature"], result["page_signature"])) / len(original["page_signature"])
    assert lighting_change < 0.0015
    profile = load_v3_profile()
    pixels_per_mm = 240 / 25.4
    id_centers = get_student_id_bubble_centers_mm(profile)
    for column, digit in enumerate("1234567"):
        x_mm, y_mm = id_centers[column][int(digit)]
        cv2.circle(blank_v3_frame, (round(x_mm * pixels_per_mm), round(y_mm * pixels_per_mm)), 8, (15, 15, 15), -1)
    answer_centers = get_answer_bubble_centers_mm(20, profile)
    for question in range(1, 21):
        choice = "ABCDE"[(question - 1) % 5]
        x_mm, y_mm = answer_centers[question]["ABCDE".index(choice)]
        cv2.circle(blank_v3_frame, (round(x_mm * pixels_per_mm), round(y_mm * pixels_per_mm)), 10, (15, 15, 15), -1)
    marked = detect_v3_registration_markers(blank_v3_frame)
    content_change = sum(a != b for a, b in zip(original["page_signature"], marked["page_signature"])) / len(original["page_signature"])
    assert content_change > 0.0015
    cv2.rectangle(blank_v3_frame, (700, 300), (780, 380), (0, 0, 0), -1)
    cv2.rectangle(blank_v3_frame, (1000, 500), (1080, 580), (0, 0, 0), -1)
    with_false_squares = detect_v3_registration_markers(blank_v3_frame)
    assert with_false_squares["valid"] is True


def test_marker_detector_rejects_partial_or_wrong_geometry(blank_v3_frame):
    cropped = blank_v3_frame[:-350, :]
    assert detect_v3_registration_markers(cropped)["valid"] is False
    wrong = np.full((900, 900, 3), 255, dtype=np.uint8)
    for x, y in ((100, 100), (780, 100), (780, 300), (100, 300)):
        cv2.rectangle(wrong, (x, y), (x + 40, y + 40), (0, 0, 0), -1)
    result = detect_v3_registration_markers(wrong)
    assert result["valid"] is False
    assert result["status"] == "invalid_marker_geometry"


def test_first_sheet_captures_once_and_locked_visible_markers_never_recapture():
    machine = CaptureStateMachine(stable_frames=3)
    signature = [0] * 1440
    _capture_first_sheet(machine, signature)
    for _ in range(30):
        assert machine.observe(_detection(signature)) is False
    assert machine.completed_capture_count == 1
    assert machine.state == CaptureState.LOCKED


def test_one_missing_bottom_marker_frame_and_lighting_change_do_not_advance():
    machine = CaptureStateMachine(stable_frames=3, disappearance_frames=3)
    signature = [0] * 1440
    _capture_first_sheet(machine, signature)
    assert not machine.observe(_detection(signature, bottom=False))
    assert machine.state == CaptureState.WAITING_FOR_BOTTOM_MARKER_DISAPPEARANCE
    assert not machine.observe(_detection(signature, bottom=True))
    assert machine.state == CaptureState.LOCKED
    for _ in range(4):
        assert not machine.observe(_detection(signature, bottom=True))
    assert machine.completed_capture_count == 1
    assert machine.state == CaptureState.LOCKED


def test_persistent_bottom_disappearance_requires_new_page_content_and_stability():
    machine = CaptureStateMachine(stable_frames=3, disappearance_frames=3)
    previous = [0] * 1440
    replacement = [1] * 1440
    _capture_first_sheet(machine, previous)
    for _ in range(3):
        assert not machine.observe(_detection(previous, bottom=False))
    assert machine.state == CaptureState.WAITING_FOR_NEW_SHEET
    # Restored old sheet after a temporary obstruction is not a replacement.
    assert not machine.observe(_detection(previous, bottom=True))
    assert machine.state == CaptureState.NEW_SHEET_DETECTED
    for _ in range(3):
        assert not machine.observe(_detection(previous))
    assert machine.state == CaptureState.WAITING_FOR_NEW_SHEET
    assert not machine.observe(_detection(replacement))
    assert machine.state == CaptureState.NEW_SHEET_DETECTED
    assert _stabilize(machine, replacement)
    machine.capture_and_queue_succeeded("b" * 32, replacement)
    assert machine.completed_capture_count == 2
    assert machine.state == CaptureState.LOCKED
    for _ in range(10):
        assert not machine.observe(_detection(replacement))
    assert machine.completed_capture_count == 2


def test_unstable_sheet_is_not_captured_until_frames_settle():
    machine = CaptureStateMachine(stable_frames=4, movement_tolerance_px=1.0)
    assert not machine.observe(_detection())
    assert not machine.observe(_detection(xshift=0))
    assert not machine.observe(_detection(xshift=8))
    assert not machine.observe(_detection(xshift=0))
    assert not machine.observe(_detection(xshift=8))
    assert machine.state == CaptureState.STABILIZING
    assert _stabilize(machine, [0] * 1440)


def test_failed_capture_does_not_advance_and_resume_revalidates():
    machine = CaptureStateMachine(stable_frames=3)
    _capture_first_sheet(machine, [0] * 1440)
    for _ in range(4):
        machine.observe(_detection([1] * 1440, bottom=False))
    machine.observe(_detection([1] * 1440))
    assert _stabilize(machine, [1] * 1440)
    machine.capture_failed("still endpoint timed out")
    assert machine.state == CaptureState.ERROR
    assert machine.completed_capture_count == 1
    machine.pause()
    machine.resume()
    assert machine.state == CaptureState.STABILIZING
    assert _stabilize(machine, [1] * 1440)
    machine.capture_and_queue_succeeded("c" * 32, [1] * 1440)
    assert machine.completed_capture_count == 2


def test_pause_resume_and_locked_restart_preserve_one_sheet_lock():
    machine = CaptureStateMachine(stable_frames=3)
    machine.seed_locked("d" * 32, [0] * 1440)
    machine.pause()
    assert machine.state == CaptureState.PAUSED
    assert not machine.observe(_detection([1] * 1440, bottom=False))
    machine.resume()
    assert machine.state == CaptureState.LOCKED
    for _ in range(5):
        assert not machine.observe(_detection([0] * 1440))
    assert machine.completed_capture_count == 1


@pytest.fixture
def capture_assessment(app):
    assessment = Assessment(title="Camera queue test", subject="Science", class_section="AUTO", total_questions=20)
    db.session.add(assessment)
    db.session.commit()
    return assessment


def _blank_png():
    image = np.full((600, 600, 3), 255, dtype=np.uint8)
    ok, payload = cv2.imencode(".png", image)
    assert ok
    return payload.tobytes()


def test_queue_preserves_order_and_producer_can_enqueue_while_first_job_is_processing(app, capture_assessment, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    image = _blank_png()
    first = queue_image_bytes(image, ".png", capture_assessment.id, app.config, capture_id="1" * 32)
    claimed = claim_next_job()
    assert claimed.id == first.id and claimed.status == "PROCESSING"
    second = queue_image_bytes(image, ".png", capture_assessment.id, app.config, capture_id="2" * 32)
    next_claimed = claim_next_job()
    assert second.id == next_claimed.id
    assert get_queue_counts()["PROCESSING"] == 2


def test_queue_capacity_is_enforced_without_losing_an_image(app, capture_assessment, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    app.config["CAPTURE_QUEUE_CAPACITY"] = 1
    queue_image_bytes(_blank_png(), ".png", capture_assessment.id, app.config, capture_id="3" * 32)
    with pytest.raises(QueueFullError):
        queue_image_bytes(_blank_png(), ".png", capture_assessment.id, app.config, capture_id="4" * 32)
    assert len(list(tmp_path.glob("capture-*"))) == 1


def test_synthetic_replacement_sequence_queues_exactly_two_capture_jobs(app, capture_assessment, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    machine = CaptureStateMachine(stable_frames=3, disappearance_frames=3)
    previous, next_page = [0] * 1440, [1] * 1440
    physical_capture_count = 0

    def capture_current(signature):
        nonlocal physical_capture_count
        physical_capture_count += 1
        capture_id = f"{physical_capture_count:x}" * 32
        queued = queue_image_bytes(_blank_png(), ".png", capture_assessment.id, app.config, capture_id=capture_id)
        machine.capture_and_queue_succeeded(queued.capture_id, signature)

    assert not machine.observe(_detection(previous))
    assert _stabilize(machine, previous)
    capture_current(previous)
    for _ in range(12):
        assert not machine.observe(_detection(previous))
    for _ in range(3):
        assert not machine.observe(_detection(previous, bottom=False))
    assert machine.state == CaptureState.WAITING_FOR_NEW_SHEET
    assert not machine.observe(_detection(next_page))
    assert _stabilize(machine, next_page)
    capture_current(next_page)
    for _ in range(12):
        assert not machine.observe(_detection(next_page))

    jobs = db.session.scalars(select(CaptureJob).order_by(CaptureJob.captured_at, CaptureJob.id)).all()
    assert physical_capture_count == 2
    assert len(jobs) == 2
    assert [job.capture_id for job in jobs] == ["1" * 32, "2" * 32]


def test_worker_failure_retry_and_interrupted_recovery_are_idempotent(app, capture_assessment, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    job = queue_image_bytes(_blank_png(), ".png", capture_assessment.id, app.config, capture_id="5" * 32)
    claimed = claim_next_job()
    failed = process_job(claimed.id, app.config)
    assert failed.status == "FAILED"
    retried = retry_failed_job(failed.id)
    assert retried.capture_id == job.capture_id
    assert db.session.query(CaptureJob).filter_by(capture_id=job.capture_id).count() == 1
    claimed_again = claim_next_job()
    assert claimed_again.status == "PROCESSING"
    assert recover_interrupted_jobs() == 1
    db.session.refresh(job)
    assert job.status == "QUEUED"


def test_capture_job_runs_the_existing_v3_draft_and_grading_pipeline(app, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    answers = ["ABCDE"[(number - 1) % 5] for number in range(1, 21)]
    assessment = Assessment(title="Queued V3 scan", subject="Science", class_section="AUTO", total_questions=20)
    assessment.questions = [
        Question(question_number=number, prompt=f"Question {number}", option_a="A", option_b="B", option_c="C", option_d="D", option_e="E", correct_answer=answer, points=1, learning_objective="Queue integration")
        for number, answer in enumerate(answers, 1)
    ]
    student = Student(student_number="1234567", name="Queue Integration Learner", class_section="AUTO")
    db.session.add_all([assessment, student])
    db.session.commit()

    pix = pymupdf.open(ASSET_DIR / "Answer_Sheet_20Q_V3.pdf")[0].get_pixmap(matrix=pymupdf.Matrix(240 / 72, 240 / 72), alpha=False)
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    page = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    profile = load_v3_profile()
    pixels_per_mm = 240 / 25.4
    id_centers = get_student_id_bubble_centers_mm(profile)
    for column, digit in enumerate(student.student_number):
        x_mm, y_mm = id_centers[column][int(digit)]
        cv2.circle(page, (round(x_mm * pixels_per_mm), round(y_mm * pixels_per_mm)), round(1.12 * pixels_per_mm * 0.72), (15, 15, 15), -1)
    answer_centers = get_answer_bubble_centers_mm(20, profile)
    for question, answer in enumerate(answers, 1):
        x_mm, y_mm = answer_centers[question]["ABCDE".index(answer)]
        cv2.circle(page, (round(x_mm * pixels_per_mm), round(y_mm * pixels_per_mm)), round(1.48 * pixels_per_mm * 0.70), (15, 15, 15), -1)
    success, encoded = cv2.imencode(".png", page)
    assert success
    job = queue_image_bytes(encoded.tobytes(), ".png", assessment.id, app.config, capture_id="6" * 32)
    claimed = claim_next_job()
    processed = process_job(claimed.id, app.config)
    assert processed.status == "NEEDS_REVIEW"
    draft = processed.draft
    assert draft.detected_student_number == "1234567"
    assert [item["answer"] for item in draft.recognized_answers] == answers
    submission = confirm_v3_scan_draft(draft, student, answers)
    db.session.refresh(job)
    assert submission.score == 20
    assert submission.percentage_score == 100
    assert job.status == "COMPLETED"
    assert job.submission_id == submission.id


def test_esp32_capture_rejects_non_image_and_accepts_jpeg_bytes(monkeypatch):
    class Headers(dict):
        def get_content_type(self):
            return self["Content-Type"]

    class FakeResponse:
        status = 200

        def __init__(self, payload, content_type):
            self.payload = payload
            self.headers = Headers({"Content-Type": content_type})

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size=-1):
            return self.payload

    image = np.full((600, 800, 3), 255, dtype=np.uint8)
    ok, jpeg = cv2.imencode(".jpg", image)
    assert ok
    camera = ESP32Camera("http://camera/stream", "http://camera/capture")
    monkeypatch.setattr("services.esp32_camera.urlopen", lambda *_args, **_kwargs: FakeResponse(jpeg.tobytes(), "image/jpeg"))
    received, extension = camera.capture_still()
    assert received == jpeg.tobytes()
    assert extension == ".jpg"
    monkeypatch.setattr("services.esp32_camera.urlopen", lambda *_args, **_kwargs: FakeResponse(b"bad", "image/jpeg"))
    with pytest.raises(CameraError, match="JPEG or PNG image"):
        camera.capture_still()


def test_camera_dashboard_and_manual_upload_routes_remain_available(client):
    assert client.get("/scanning/upload").status_code == 200
    page = client.get("/scanning/automatic")
    assert page.status_code == 200
    assert b"ESP32 CAMERA" in page.data
    assert b"python -m services.scan_worker" in page.data


def test_start_pause_and_resume_controls_are_persisted(app):
    app.config["ESP32_STREAM_URL"] = "http://camera.local:81/stream"
    app.config["ESP32_CAPTURE_URL"] = "http://camera.local/capture"
    assessment = Assessment(title="Auto scan controls", subject="Science", class_section="AUTO", total_questions=20)
    assessment.questions = [
        Question(question_number=number, prompt=f"Q{number}", option_a="A", option_b="B", option_c="C", option_d="D", option_e="E", correct_answer="A", points=1, learning_objective="Basics")
        for number in range(1, 21)
    ]
    db.session.add(assessment)
    db.session.commit()
    client = app.test_client()
    started = client.post("/scanning/automatic/start", data={"assessment_id": assessment.id})
    assert started.status_code == 302
    status = client.get("/scanning/automatic/status").get_json()
    assert status["active"] is True and status["paused"] is False
    assert status["assessment_id"] == assessment.id
    assert client.post("/scanning/automatic/pause").status_code == 302
    assert client.get("/scanning/automatic/status").get_json()["paused"] is True
    assert client.post("/scanning/automatic/resume").status_code == 302
    assert client.get("/scanning/automatic/status").get_json()["paused"] is False
    assert client.post("/scanning/automatic/stop").status_code == 302
    assert client.get("/scanning/automatic/status").get_json()["active"] is False
