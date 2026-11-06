from pathlib import Path
from io import BytesIO
from datetime import datetime, timedelta, timezone

import cv2
import numpy as np
import pymupdf
import pytest
from sqlalchemy import select, text

from extensions import db
from models import Assessment, CameraIngestionState, CaptureJob, Question, ScannerControl, Student, Submission
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
from services.camera_image_validation import validate_camera_image
from services.camera_ingestion import IngestionResult
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
    result = detect_v3_registration_markers(blank_v3_frame, load_v3_profile())
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
    result = detect_v3_registration_markers(blank_v3_frame, profile)
    assert result["valid"] is False


def test_marker_detector_survives_lighting_and_unrelated_square_contours(blank_v3_frame):
    lighter = cv2.convertScaleAbs(blank_v3_frame, alpha=0.72, beta=70)
    profile = load_v3_profile()
    result = detect_v3_registration_markers(lighter, profile)
    assert result["valid"] is True
    original = detect_v3_registration_markers(blank_v3_frame, profile)
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
    marked = detect_v3_registration_markers(blank_v3_frame, profile)
    content_change = sum(a != b for a, b in zip(original["page_signature"], marked["page_signature"])) / len(original["page_signature"])
    assert content_change > 0.0015
    cv2.rectangle(blank_v3_frame, (700, 300), (780, 380), (0, 0, 0), -1)
    cv2.rectangle(blank_v3_frame, (1000, 500), (1080, 580), (0, 0, 0), -1)
    with_false_squares = detect_v3_registration_markers(blank_v3_frame, profile)
    assert with_false_squares["valid"] is True


def test_marker_detector_rejects_partial_or_wrong_geometry(blank_v3_frame):
    cropped = blank_v3_frame[:-350, :]
    assert detect_v3_registration_markers(cropped, load_v3_profile())["valid"] is False
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
    assert processed.status == "COMPLETED"
    submission = processed.submission
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


def test_start_pause_and_resume_controls_are_persisted(app, monkeypatch):
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
    monkeypatch.setattr("routes.automatic_scanning_routes.ESP32Camera.read_frame", lambda _camera: np.zeros((480, 640, 3), dtype=np.uint8))
    monkeypatch.setattr("routes.automatic_scanning_routes.ESP32Camera.close_stream", lambda _camera: None)
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


def test_scan_session_waits_for_queue_drain_before_completed(app):
    from routes.automatic_scanning_routes import _session_progress

    with app.app_context():
        now = datetime.now(timezone.utc)
        assessment = Assessment(title="Session lifecycle", subject="Science", class_section="SESSION", total_questions=1)
        assessment.questions = [Question(question_number=1, prompt="Q", option_a="A", option_b="B", option_c="C", option_d="D", option_e="E", correct_answer="A", points=1, learning_objective="Basics")]
        student = Student(student_number="7654321", name="Session Student", class_section="SESSION")
        submission = Submission(student=student, assessment=assessment, score=1, total_points=1,
                                percentage_score=100, processing_status="processed", review_status="complete")
        db.session.add_all([assessment, student, submission])
        db.session.flush()
        job = CaptureJob(capture_id="session-progress-1", assessment_id=assessment.id,
                         image_filename="session.jpg", status="QUEUED")
        control = ScannerControl(id=1, active=False, paused=False, assessment_id=assessment.id,
                                 session_started_at=now - timedelta(minutes=1), session_ended_at=now,
                                 session_camera_baseline={"camera_id": "session-camera", "rejected": 0, "duplicates": 0})
        db.session.add_all([job, control])
        db.session.commit()
        waiting = _session_progress(control)
        assert waiting["state"] == "PROCESSING_REMAINING_QUEUE"
        assert waiting["valid_sheets_captured"] == 1
        assert waiting["images_awaiting_processing"] == 1

        job.status = "COMPLETED"
        job.submission_id = submission.id
        db.session.commit()
        complete = _session_progress(control)
        assert complete["state"] == "COMPLETED"
        assert complete["successfully_graded"] == 1
        assert complete["images_awaiting_processing"] == 0


def _api_assessment():
    assessment = Assessment(title="API ingestion assessment", subject="Science", class_section="API", total_questions=20)
    assessment.questions = [
        Question(question_number=number, prompt=f"Q{number}", option_a="A", option_b="B", option_c="C", option_d="D", option_e="E", correct_answer="A", points=1, learning_objective="API")
        for number in range(1, 21)
    ]
    db.session.add(assessment)
    db.session.flush()
    db.session.add(ScannerControl(id=1, assessment_id=assessment.id))
    db.session.commit()
    return assessment


def _api_validation(signature, *, valid=True, bottom=True):
    return {
        "valid": valid, "reason": None if valid else "registration_markers_not_found",
        "detected_markers": 4 if valid else 0,
        "marker_coordinates": ({"top_left": [100.0, 100.0], "top_right": [500.0, 100.0], "bottom_right": [500.0, 700.0], "bottom_left": [100.0, 700.0]} if valid else {}),
        "geometry_valid": valid, "confidence": 0.98 if valid else 0.0,
        "bottom_markers_visible": valid and bottom, "sheet_sufficiently_visible": valid,
        "page_signature": signature if valid else None, "extension": ".png",
    }


def test_camera_ingest_rejects_invalid_image_and_does_not_create_job(app, client, monkeypatch, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    _api_assessment()
    image = _blank_png()
    monkeypatch.setattr("services.camera_ingestion.validate_camera_image", lambda *_: _api_validation(None, valid=False))
    response = client.post("/api/camera/ingest", data=image, content_type="image/png", headers={"X-Camera-ID": "test-camera"})
    assert response.status_code == 200
    assert response.get_json()["status"] == "rejected"
    assert db.session.query(CaptureJob).count() == 0
    state = db.session.get(CameraIngestionState, "test-camera")
    assert state.received_count == 1 and state.rejected_count == 1


def test_camera_ingest_persists_replacement_state_and_queues_one_per_sheet(app, client, monkeypatch, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    app.config["CAMERA_STABLE_FRAME_COUNT"] = 2
    app.config["CAMERA_BOTTOM_MISSING_FRAME_COUNT"] = 2
    _api_assessment()
    image = _blank_png()
    next_image_array = np.full((600, 600, 3), 245, dtype=np.uint8)
    next_ok, next_encoded = cv2.imencode(".png", next_image_array)
    assert next_ok
    next_image = next_encoded.tobytes()
    first_signature, next_signature = [0] * 1440, [1] * 1440

    def validate(payload, *_):
        return _api_validation(first_signature if payload == image else next_signature)

    monkeypatch.setattr("services.camera_ingestion.validate_camera_image", validate)

    def ingest(capture_id, payload=image):
        return client.post(
            "/api/camera/ingest", data=payload, content_type="image/png",
            headers={"X-Camera-ID": "test-camera", "X-Capture-ID": capture_id},
        )

    assert ingest("frame-1").get_json()["status"] == "deferred"
    assert ingest("frame-2").get_json()["status"] == "deferred"
    first = ingest("frame-3")
    assert first.status_code == 202 and first.get_json()["status"] == "queued"
    assert ingest("frame-3").get_json()["status"] == "duplicate"
    assert ingest("same-sheet").get_json()["status"] == "duplicate"

    monkeypatch.setattr("services.camera_ingestion.validate_camera_image", lambda *_: _api_validation(None, valid=False))
    assert ingest("obscured-1").get_json()["status"] == "rejected"
    assert ingest("obscured-2").get_json()["status"] == "rejected"
    monkeypatch.setattr("services.camera_ingestion.validate_camera_image", validate)
    assert ingest("new-sheet-1", next_image).get_json()["status"] == "deferred"
    assert ingest("new-sheet-2", next_image).get_json()["status"] == "deferred"
    second = ingest("new-sheet-3", next_image)
    assert second.status_code == 202 and second.get_json()["status"] == "queued"

    jobs = db.session.scalars(select(CaptureJob).order_by(CaptureJob.id)).all()
    assert [job.capture_id for job in jobs] == ["frame-3", "new-sheet-3"]
    assert all(job.camera_id == "test-camera" and job.source == "api" for job in jobs)
    assert jobs[0].detection_metadata["detected_markers"] == 4
    status = client.get("/api/camera/status").get_json()
    assert status["images_received"] == 10
    assert status["images_accepted"] == 2
    assert status["duplicates_ignored"] >= 2
    detail = client.get("/api/camera/captures/frame-3").get_json()
    assert detail["camera_id"] == "test-camera"
    assert client.get(detail["image_url"]).status_code == 200


def test_camera_ingest_validates_real_v3_template_before_queueing(app, client, blank_v3_frame, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    app.config["CAMERA_STABLE_FRAME_COUNT"] = 2
    _api_assessment()
    ok, encoded = cv2.imencode(".png", blank_v3_frame)
    assert ok
    image = encoded.tobytes()
    statuses = []
    for index in range(3):
        response = client.post(
            "/api/camera/ingest", data=image, content_type="image/png",
            headers={"X-Camera-ID": "real-v3-camera", "X-Capture-ID": f"real-v3-{index}"},
        )
        assert response.status_code in {200, 202}
        statuses.append(response.get_json()["status"])
    assert statuses == ["deferred", "deferred", "queued"]
    job = db.session.scalar(select(CaptureJob).where(CaptureJob.camera_id == "real-v3-camera"))
    assert job is not None
    assert job.detection_metadata["detected_markers"] == 4
    assert job.detection_metadata["geometry_valid"] is True


def test_camera_ingest_supports_multipart_and_missing_optional_metadata(app, client, monkeypatch, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    app.config["CAMERA_STABLE_FRAME_COUNT"] = 2
    _api_assessment()
    image = _blank_png()
    monkeypatch.setattr("services.camera_ingestion.validate_camera_image", lambda *_: _api_validation([0] * 1440))
    responses = [
        client.post("/api/camera/ingest", data={"image": (BytesIO(image), "page.png")}, content_type="multipart/form-data")
        for _ in range(3)
    ]
    assert [response.get_json()["status"] for response in responses] == ["deferred", "deferred", "queued"]
    job = db.session.scalar(select(CaptureJob).where(CaptureJob.camera_id == "default-camera"))
    assert job is not None and len(job.capture_id) == 32


def test_image_validation_accepts_jpeg_of_a_real_v3_template(app, blank_v3_frame):
    ok, encoded = cv2.imencode(".jpg", blank_v3_frame)
    assert ok
    result = validate_camera_image(encoded.tobytes(), "image/jpeg", app.config)
    assert result["valid"] is True
    assert result["extension"] == ".jpg"
    assert result["detected_markers"] == 4
    assert result["geometry_valid"] is True


class _FakeCameraHeaders(dict):
    def get_content_type(self):
        return self["Content-Type"]


class _FakeCameraResponse:
    def __init__(self, payload, content_type="image/jpeg", status=200):
        self.payload = payload
        self.headers = _FakeCameraHeaders({"Content-Type": content_type})
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size=-1):
        return self.payload


def _jpeg(image):
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return encoded.tobytes()


def _configured_poller(app, opener):
    from services.camera_poller import CameraPoller

    app.config["PAPARAZZI_CAMERA_URL"] = "http://192.168.1.73/capture"
    app.config["PAPARAZZI_CAMERA_ID"] = "poller-test-camera"
    return CameraPoller(app, opener=opener)


def test_camera_poller_fetches_jpeg_and_calls_shared_ingestion_service(app, monkeypatch, blank_v3_frame):
    from services.camera_poller import CameraPoller

    payload = _jpeg(blank_v3_frame)
    requests = []

    def open_camera(req, timeout):
        requests.append((req.full_url, req.get_header("Cache-control"), timeout))
        return _FakeCameraResponse(payload)

    app.config["PAPARAZZI_CAMERA_URL"] = "http://192.168.1.73/capture"
    app.config["PAPARAZZI_CAMERA_ID"] = "poller-fetch-test"
    app.config["PAPARAZZI_CAMERA_TIMEOUT"] = 2.5
    received = []
    monkeypatch.setattr(
        "services.camera_poller.ingest_camera_image_bytes",
        lambda *args, **kwargs: received.append((args, kwargs)) or IngestionResult({"status": "deferred"}),
    )
    result = CameraPoller(app, opener=open_camera).poll_once()
    assert result["status"] == "deferred"
    assert requests == [("http://192.168.1.73/capture", "no-cache, no-store", 2.5)]
    assert received[0][0][:3] == (payload, "image/jpeg", "poller-fetch-test")
    state = db.session.get(CameraIngestionState, "poller-fetch-test")
    assert state.connection_status == "connected"
    assert state.frames_fetched == 1
    assert state.last_fetch_at is not None


@pytest.mark.parametrize("failure", [
    lambda: _FakeCameraResponse(b"offline", "text/plain", 503),
    lambda: TimeoutError("timeout"),
    lambda: _FakeCameraResponse(b"not-jpeg", "image/jpeg"),
])
def test_camera_poller_records_http_timeout_and_invalid_frame_failures(app, failure):
    from services.camera_poller import CameraPoller

    def open_camera(*_args, **_kwargs):
        result = failure()
        if isinstance(result, Exception):
            raise result
        return result

    poller = _configured_poller(app, open_camera)
    result = poller.poll_once()
    assert result["status"] == "error"
    state = db.session.get(CameraIngestionState, "poller-test-camera")
    assert state.last_error
    assert db.session.query(CaptureJob).count() == 0
    if state.frames_fetched:
        assert state.frames_fetched == 1
        assert state.rejected_count == 1
    else:
        assert state.connection_status == "disconnected"
        assert state.frames_fetched == 0


def test_camera_poller_reconnects_after_camera_timeout(app, monkeypatch, blank_v3_frame):
    from services.camera_poller import CameraPoller

    calls = 0

    def open_camera(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("camera asleep")
        return _FakeCameraResponse(_jpeg(blank_v3_frame))

    app.config["PAPARAZZI_CAMERA_URL"] = "http://192.168.1.73/capture"
    app.config["PAPARAZZI_CAMERA_ID"] = "recovery-test-camera"
    monkeypatch.setattr("services.camera_poller.ingest_camera_image_bytes", lambda *_args, **_kwargs: IngestionResult({"status": "deferred"}))
    poller = CameraPoller(app, opener=open_camera)
    assert poller.poll_once()["status"] == "error"
    assert poller.poll_once()["status"] == "deferred"
    state = db.session.get(CameraIngestionState, "recovery-test-camera")
    assert state.connection_status == "connected"
    assert state.frames_fetched == 1
    assert state.last_error is None


def test_camera_poller_respects_poll_interval_and_shuts_down_gracefully(app, monkeypatch):
    from services.camera_poller import CameraPoller

    app.config["PAPARAZZI_CAMERA_URL"] = "http://192.168.1.73/capture"
    app.config["PAPARAZZI_CAMERA_ID"] = "interval-test-camera"
    app.config["PAPARAZZI_CAMERA_POLL_INTERVAL"] = 0.75

    class StopAfterOneWait:
        stopped = False
        waits = []

        def is_set(self):
            return self.stopped

        def wait(self, timeout):
            self.waits.append(timeout)
            self.stopped = True
            return True

    event = StopAfterOneWait()
    poller = CameraPoller(app, opener=lambda *_a, **_kw: None, stop_event=event)
    monkeypatch.setattr(poller, "poll_once", lambda: {"status": "deferred"})
    poller.run()
    assert event.waits == [pytest.approx(0.75, abs=0.01)]
    assert db.session.get(CameraIngestionState, "interval-test-camera").poller_running is False


def test_second_poller_cannot_acquire_the_same_camera_lock(app):
    from services.camera_poller import CameraPoller

    app.config["PAPARAZZI_CAMERA_URL"] = "http://192.168.1.73/capture"
    app.config["PAPARAZZI_CAMERA_ID"] = "locked-camera"
    lock_key = "paparazzi-camera-poller:locked-camera"
    lock_owner = db.engines[None].engine.connect()
    acquired = lock_owner.scalar(text("SELECT pg_try_advisory_lock(hashtextextended(:lock_key, 0))"), {"lock_key": lock_key})
    assert acquired is True
    try:
        with pytest.raises(RuntimeError, match="already holds the lock"):
            CameraPoller(app, opener=lambda *_args, **_kwargs: None).run()
    finally:
        lock_owner.execute(text("SELECT pg_advisory_unlock(hashtextextended(:lock_key, 0))"), {"lock_key": lock_key})
        lock_owner.close()


def test_camera_poller_processes_duplicate_and_replacement_frames_once(app, blank_v3_frame, tmp_path):
    from services.camera_poller import CameraPoller

    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    app.config["CAMERA_STABLE_FRAME_COUNT"] = 2
    app.config["CAMERA_BOTTOM_MISSING_FRAME_COUNT"] = 2
    app.config["PAPARAZZI_CAMERA_URL"] = "http://192.168.1.73/capture"
    app.config["PAPARAZZI_CAMERA_ID"] = "replacement-poller-camera"
    assessment = _api_assessment()

    next_page = blank_v3_frame.copy()
    profile = load_v3_profile()
    answer_centers = get_answer_bubble_centers_mm(20, profile)
    pixels_per_mm = 240 / 25.4
    for question in range(1, 21):
        choice = "ABCDE"[(question - 1) % 5]
        x_mm, y_mm = answer_centers[question]["ABCDE".index(choice)]
        cv2.circle(next_page, (round(x_mm * pixels_per_mm), round(y_mm * pixels_per_mm)), 10, (15, 15, 15), -1)

    obscured = blank_v3_frame.copy()
    centers = _profile_mark_centers(profile)
    half = round(profile["registration_marks"]["size_mm"] * pixels_per_mm / 2) + 6
    for name in ("bottomleft", "bottomright"):
        x_mm, y_mm = centers[name]
        x, y = round(x_mm * pixels_per_mm), round(y_mm * pixels_per_mm)
        cv2.rectangle(obscured, (x - half, y - half), (x + half, y + half), (255, 255, 255), -1)

    fetched = [_jpeg(blank_v3_frame)] * 4 + [_jpeg(obscured)] * 2 + [_jpeg(next_page)] * 3
    index = 0

    def open_camera(*_args, **_kwargs):
        nonlocal index
        response = _FakeCameraResponse(fetched[index])
        index += 1
        return response

    poller = CameraPoller(app, opener=open_camera)
    statuses = [poller.poll_once()["status"] for _ in fetched]
    assert statuses == ["deferred", "deferred", "queued", "duplicate", "rejected", "rejected", "deferred", "deferred", "queued"]
    jobs = db.session.scalars(select(CaptureJob).order_by(CaptureJob.id)).all()
    assert len(jobs) == 2
    assert {job.assessment_id for job in jobs} == {assessment.id}
    assert {job.camera_id for job in jobs} == {"replacement-poller-camera"}
    assert db.session.get(CameraIngestionState, "replacement-poller-camera").frames_fetched == 9


def test_camera_poller_rejects_public_hosts_and_start_stop_controls_require_token(app, client):
    from services.camera_poller import _NoRedirectHandler, validate_camera_url

    for unsafe in ("https://example.com/capture", "http://127.0.0.1.evil.example/capture", "file:///etc/passwd"):
        with pytest.raises(ValueError):
            validate_camera_url(unsafe)
    assert _NoRedirectHandler().redirect_request(None, None, 302, "Found", {}, "http://192.168.1.74/elsewhere") is None
    app.config["PAPARAZZI_CAMERA_URL"] = "http://192.168.1.73/capture"
    app.config["CAMERA_CONTROL_TOKEN"] = "unit-test-secret"
    assessment = _api_assessment()
    invalid_assessment = Assessment(title="Incomplete camera assessment", subject="Test", class_section="API", total_questions=1)
    db.session.add(invalid_assessment)
    db.session.commit()
    scanner_control = db.session.get(ScannerControl, 1)
    scanner_control.active = True
    db.session.commit()
    denied = client.post("/scanning/automatic/poller/start", data={"camera_control_token": "wrong"})
    assert denied.status_code == 403
    incomplete = client.post("/scanning/automatic/poller/start", data={"camera_control_token": "unit-test-secret", "assessment_id": invalid_assessment.id})
    assert incomplete.status_code == 302
    conflict = client.post("/scanning/automatic/poller/start", data={"camera_control_token": "unit-test-secret", "assessment_id": assessment.id})
    assert conflict.status_code == 302
    assert db.session.get(CameraIngestionState, "paparazzi-esp32-01") is None
    scanner_control.active = False
    db.session.commit()
    started = client.post("/scanning/automatic/poller/start", data={"camera_control_token": "unit-test-secret", "assessment_id": assessment.id, "camera_url": "http://203.0.113.8/capture"})
    assert started.status_code == 302
    assert app.config["PAPARAZZI_CAMERA_URL"] == "http://192.168.1.73/capture"
    state = db.session.get(CameraIngestionState, "paparazzi-esp32-01")
    assert state.polling_enabled is True
    app.config["ESP32_STREAM_URL"] = "http://192.168.1.73:81/stream"
    app.config["ESP32_CAPTURE_URL"] = "http://192.168.1.73/capture"
    client.post("/scanning/automatic/start", data={"assessment_id": assessment.id})
    assert db.session.get(ScannerControl, 1).active is False
    assert client.post("/scanning/automatic/manual-capture").status_code == 302
    stopped = client.post("/scanning/automatic/poller/stop", data={"camera_control_token": "unit-test-secret"})
    assert stopped.status_code == 302
    db.session.refresh(state)
    assert state.polling_enabled is False
    page = client.get("/scanning/automatic")
    assert page.status_code == 200
    assert b"http://192.168.1.73/capture" in page.data
    assert b"Start polling" in page.data
    assert b"unit-test-secret" not in page.data


def test_camera_ingest_rejects_corrupt_and_unsupported_uploads(client):
    unsupported = client.post("/api/camera/ingest", data=b"not an image", content_type="text/plain")
    assert unsupported.get_json()["status"] == "rejected"
    corrupt = client.post("/api/camera/ingest", data=b"\xff\xd8\xffbroken", content_type="image/jpeg")
    assert corrupt.get_json()["reason"] == "corrupted_image"
    empty = client.post("/api/camera/ingest", data=b"", content_type="image/png")
    assert empty.status_code == 400
