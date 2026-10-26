from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
import qrcode
from werkzeug.datastructures import FileStorage

from services.assessment_service import validate_assessment_payload
from services.analytics_service import calculate_average, calculate_median, question_accuracy, question_analytics_statement
from services.grading_service import apply_manual_correction, grade_detections
from services.image_processor import ImageProcessingError, align_sheet, decode_qr, validate_uploaded_image
from services.omr_service import classify_bubbles, detect_answers
from services.recommendation_service import recommend_learning_objectives, validate_resource_payload
from services.sheet_generator import generate_answer_sheet_pdf, make_qr_payload
from services.sheet_layout import (
    BUBBLE_X_MM,
    CANONICAL_HEIGHT,
    CANONICAL_WIDTH,
    MARKER_ORIGINS_MM,
    MARKER_SIZE_MM,
    PIXELS_PER_MM_X,
    PIXELS_PER_MM_Y,
    QR_ORIGIN_MM,
    QR_SIZE_MM,
    bubble_center_mm,
    mm_to_pixel,
)
from services.student_service import validate_student_payload
from services.submission_service import parse_qr_identity


def synthetic_sheet():
    page = np.full((CANONICAL_HEIGHT, CANONICAL_WIDTH, 3), 255, dtype=np.uint8)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    for marker_id, (x_mm, y_mm) in MARKER_ORIGINS_MM.items():
        x = round(x_mm * PIXELS_PER_MM_X)
        y = round(y_mm * PIXELS_PER_MM_Y)
        size = round(MARKER_SIZE_MM * PIXELS_PER_MM_X)
        marker = cv2.aruco.generateImageMarker(dictionary, marker_id, size)
        page[y:y + size, x:x + size] = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)
    for question_number in range(1, 3):
        for option in BUBBLE_X_MM:
            x, y = mm_to_pixel(bubble_center_mm(question_number, option))
            cv2.circle(page, (x, y), 10, (0, 0, 0), 1)
            qr = qrcode.make(make_qr_payload(17, 29)).convert("RGB")
            qr_size = round(QR_SIZE_MM * PIXELS_PER_MM_X)
            qr = cv2.cvtColor(np.asarray(qr.resize((qr_size, qr_size))), cv2.COLOR_RGB2BGR)
            qr_x = round(QR_ORIGIN_MM[0] * PIXELS_PER_MM_X)
            qr_y = round(QR_ORIGIN_MM[1] * PIXELS_PER_MM_Y)
            page[qr_y:qr_y + qr_size, qr_x:qr_x + qr_size] = qr
    return page


def test_student_and_assessment_validation():
    assert validate_student_payload({"student_number": "S1", "name": "A Student", "class_section": "7A"}) == (True, [])
    assert not validate_student_payload({"student_number": "", "name": "", "class_section": ""})[0]
    question = {
        "prompt": "What is 2 + 2?", "option_a": "3", "option_b": "4", "option_c": "5", "option_d": "6",
        "correct_answer": "B", "points": "2.50", "learning_objective": "Addition",
    }
    payload = {"title": "Arithmetic", "subject": "Math", "class_section": "7A", "questions": [question]}
    assert validate_assessment_payload(payload) == (True, [])
    payload["questions"][0]["correct_answer"] = "Z"
    assert not validate_assessment_payload(payload)[0]


def test_omr_detects_blank_multiple_ambiguous_and_marked_answers():
    assert classify_bubbles({"A": 0.01, "B": 0.02, "C": 0.00, "D": 0.01})["status"] == "blank"
    assert classify_bubbles({"A": 0.6, "B": 0.7, "C": 0.0, "D": 0.0})["status"] == "multiple_marks"
    assert classify_bubbles({"A": 0.20, "B": 0.15, "C": 0.0, "D": 0.0})["status"] == "ambiguous"
    assert classify_bubbles({"A": 0.0, "B": 0.75, "C": 0.0, "D": 0.0})["answer"] == "B"


def test_synthetic_image_omr_handles_blank_mark_and_multiple_marks():
    page = synthetic_sheet()
    aligned = align_sheet(page).aligned_image
    questions = [1, 2]
    assert [answer["status"] for answer in detect_answers(aligned, questions)] == ["blank", "blank"]
    for option in ("B", "C"):
        x, y = mm_to_pixel(bubble_center_mm(1, option))
        cv2.circle(aligned, (x, y), 7, (35, 35, 35), -1)
    marked, = detect_answers(aligned, [1])
    assert marked["status"] == "multiple_marks"
    x, y = mm_to_pixel(bubble_center_mm(2, "D"))
    cv2.circle(aligned, (x, y), 7, (35, 35, 35), -1)
    detected = detect_answers(aligned, [2])[0]
    assert detected["status"] == "detected"
    assert detected["answer"] == "D"


def test_omr_remains_detectable_under_moderate_brightness_change():
    page = synthetic_sheet()
    x, y = mm_to_pixel(bubble_center_mm(1, "B"))
    cv2.circle(page, (x, y), 9, (0, 0, 0), -1)
    brighter = cv2.convertScaleAbs(page, alpha=0.9, beta=35)
    result = align_sheet(brighter)
    reading = detect_answers(result.aligned_image, [1])[0]
    assert reading["status"] == "detected"
    assert reading["answer"] == "B"


def test_aruco_alignment_handles_perspective_and_rotation():
    page = synthetic_sheet()
    aligned = align_sheet(page)
    assert aligned.detected_marker_ids == [0, 1, 2, 3]
    rotated = cv2.rotate(page, cv2.ROTATE_180)
    rotated_aligned = align_sheet(rotated)
    assert rotated_aligned.aligned_image.shape[:2] == (CANONICAL_HEIGHT, CANONICAL_WIDTH)
    assert decode_qr(rotated_aligned.aligned_image) == make_qr_payload(17, 29)

    target = np.float32([[70, 40], [1280, 90], [1240, 1800], [25, 1760]])
    source = np.float32([[0, 0], [CANONICAL_WIDTH - 1, 0], [CANONICAL_WIDTH - 1, CANONICAL_HEIGHT - 1], [0, CANONICAL_HEIGHT - 1]])
    transform = cv2.getPerspectiveTransform(source, target)
    photographed = cv2.warpPerspective(page, transform, (1350, 1850), borderValue=(255, 255, 255))
    perspective_aligned = align_sheet(photographed)
    assert perspective_aligned.detected_marker_ids == [0, 1, 2, 3]


def test_alignment_rejects_missing_fiducial_marker():
    page = synthetic_sheet()
    page[45:160, 45:160] = 255
    with pytest.raises(ImageProcessingError, match="missing corner marker"):
        align_sheet(page)


def test_qr_decode_and_identity_payload_round_trip():
    payload = make_qr_payload(17, 29)
    qr_image = qrcode.make(payload).convert("RGB")
    image = cv2.cvtColor(np.asarray(qr_image), cv2.COLOR_RGB2BGR)
    assert decode_qr(image) == payload
    assert parse_qr_identity(payload) == (17, 29)
    full_page = align_sheet(synthetic_sheet()).aligned_image
    assert parse_qr_identity(decode_qr(full_page)) == (17, 29)
    with pytest.raises(ValueError, match="only student and assessment"):
        parse_qr_identity('{"s":17,"a":29,"name":"not allowed"}')


def test_pdf_sheet_generation_writes_downloadable_document():
    student = SimpleNamespace(id=17, name="Fictional Student", student_number="TEST-17")
    question = SimpleNamespace(question_number=1, correct_answer="A", prompt="Question?", option_a="one",
                               option_b="two", option_c="three", option_d="four")
    assessment = SimpleNamespace(id=29, title="Synthetic Assessment", questions=[question])
    output = BytesIO()
    generate_answer_sheet_pdf(student, assessment, output)
    assert output.getvalue().startswith(b"%PDF-")
    assert len(output.getvalue()) > 1000


def test_invalid_upload_type_and_size_are_rejected():
    invalid_type = FileStorage(stream=BytesIO(b"not an image"), filename="sheet.svg")
    with pytest.raises(ImageProcessingError, match="JPEG and PNG"):
        validate_uploaded_image(invalid_type)
    image = synthetic_sheet()
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    too_large = FileStorage(stream=BytesIO(encoded.tobytes()), filename="sheet.png")
    with pytest.raises(ImageProcessingError, match="size limit"):
        validate_uploaded_image(too_large, max_bytes=100)


def test_weighted_grading_keeps_ambiguous_answers_unresolved():
    questions = [
        SimpleNamespace(question_number=1, correct_answer="A", points=Decimal("2.50")),
        SimpleNamespace(question_number=2, correct_answer="B", points=Decimal("1.00")),
        SimpleNamespace(question_number=3, correct_answer="C", points=Decimal("1.00")),
    ]
    detections = [
        {"question_number": 1, "answer": "A", "status": "detected", "confidence": 0.9, "values": {}},
        {"question_number": 2, "answer": None, "status": "blank", "confidence": 0.9, "values": {}},
        {"question_number": 3, "answer": None, "status": "ambiguous", "confidence": 0.1, "values": {}},
    ]
    result = grade_detections(questions, detections)
    assert result["score"] == Decimal("2.50")
    assert result["total_points"] == Decimal("4.50")
    assert result["percentage"] == Decimal("55.56")
    assert result["unresolved"] == 1
    assert result["unanswered"] == 1
    assert result["needs_review"] is True


def test_manual_correction_preserves_detection_and_recalculates_score():
    question = SimpleNamespace(question_number=1, correct_answer="B", points=Decimal("2.00"))
    response = SimpleNamespace(answer=None, detected_answer=None, detection_status="ambiguous",
                               manual_correction_flag=False, correctness=None, question=question)
    submission = SimpleNamespace(responses=[response])
    previous = apply_manual_correction(submission, response, "B")
    assert previous is None
    assert response.answer == "B"
    assert response.detected_answer is None
    assert response.manual_correction_flag is True
    assert response.correctness is True
    assert submission.score == Decimal("2.00")
    assert submission.percentage_score == Decimal("100.00")
    assert submission.review_status == "complete"


def test_factory_registers_routes_and_rejects_non_postgresql(monkeypatch):
    from app import create_app
    from config import config

    monkeypatch.setattr(config["default"], "SQLALCHEMY_DATABASE_URI", "postgresql+psycopg://local:local@127.0.0.1/not_connected")
    monkeypatch.setattr(config["default"], "SECRET_KEY", "factory-test-key")
    app = create_app()
    client = app.test_client()
    assert client.get("/health").get_json() == {"status": "ok"}
    student_form = client.get("/students/new")
    assessment_form = client.get("/assessments/new")
    assert student_form.status_code == 200 and b'name="csrf_token"' in student_form.data
    assert assessment_form.status_code == 200 and b'name="csrf_token"' in assessment_form.data
    assert "/students/new" in {rule.rule for rule in app.url_map.iter_rules()}
    monkeypatch.setattr(config["default"], "SQLALCHEMY_DATABASE_URI", "sqlite:///not-allowed.db")
    with pytest.raises(RuntimeError, match="Only PostgreSQL"):
        create_app()


def test_recommendations_use_configurable_threshold_and_resources():
    resource = SimpleNamespace(learning_objective="Fractions", title="Class notes")
    stats = {"Fractions": {"valid_responses": 10, "incorrect": 4}, "Geometry": {"valid_responses": 10, "incorrect": 3}}
    result = recommend_learning_objectives(stats, [resource], threshold=0.4)
    assert len(result) == 1
    assert result[0]["objective"] == "Fractions"
    assert result[0]["resources"] == [resource]
    assert result[0]["valid_responses"] == 10


def test_analytics_denominators_exclude_unknowns_and_empty_groups():
    assert question_accuracy(correct=3, valid_responses=5) == 0.6
    assert question_accuracy(correct=0, valid_responses=0) is None
    assert calculate_average([50, 75, 100]) == 75
    assert calculate_median([20, 40, 60, 80]) == 50
    sql = str(question_analytics_statement().compile(dialect=__import__("sqlalchemy").dialects.postgresql.dialect()))
    assert "answers" not in sql
    assert "IS NOT NULL" in sql
    assert "CASE" in sql


def test_learning_resource_validation_requires_teacher_supplied_http_url():
    valid = {"title": "Notes", "description": "Class notes", "url": "https://school.example/notes",
             "learning_objective": "Fractions", "resource_type": "reading"}
    assert validate_resource_payload(valid) == (True, [])
    valid["url"] = "javascript:alert(1)"
    assert not validate_resource_payload(valid)[0]