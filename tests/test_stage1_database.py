import os
from io import BytesIO

import cv2
import numpy as np
import pytest
import qrcode
from sqlalchemy.exc import IntegrityError
from werkzeug.datastructures import FileStorage

from app import db
from models import Assessment, Question, Response, Student, Submission
from services.sheet_layout import (
    CANONICAL_HEIGHT,
    CANONICAL_WIDTH,
    MARKER_ORIGINS_MM,
    MARKER_SIZE_MM,
    PIXELS_PER_MM_X,
    PIXELS_PER_MM_Y,
    QR_ORIGIN_MM,
    QR_SIZE_MM,
    bubble_center_mm,
)
from services.submission_service import DuplicateSubmissionError, process_answer_sheet
from services.sheet_generator import make_qr_payload


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL test database is not configured")
def test_student_number_is_unique(app):
    student_one = Student(student_number="S-1001", name="Alice Smith", class_section="7A")
    student_two = Student(student_number="S-1001", name="Alice Copy", class_section="7A")

    db.session.add(student_one)
    db.session.commit()

    db.session.add(student_two)
    with pytest.raises(IntegrityError):
        db.session.commit()

    db.session.rollback()


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL test database is not configured")
def test_assessment_question_number_is_unique(app):
    assessment = Assessment(title="Math Quiz", subject="Mathematics", class_section="7A", total_questions=2)
    db.session.add(assessment)
    db.session.commit()

    question_one = Question(assessment_id=assessment.id, question_number=1, correct_answer="B", points=1.0, learning_objective="Fractions")
    question_two = Question(assessment_id=assessment.id, question_number=1, correct_answer="C", points=1.0, learning_objective="Fractions")

    db.session.add_all([question_one, question_two])
    with pytest.raises(IntegrityError):
        db.session.commit()

    db.session.rollback()


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL test database is not configured")
def test_submission_and_response_uniqueness_constraints(app):
    student = Student(student_number="S-2001", name="Fictional Learner", class_section="8B")
    assessment = Assessment(title="Science Check", subject="Science", class_section="8B", total_questions=1)
    assessment.questions.append(Question(question_number=1, prompt="Choose", option_a="A", option_b="B",
                                         option_c="C", option_d="D", correct_answer="A", points=1,
                                         learning_objective="Matter"))
    db.session.add_all([student, assessment])
    db.session.commit()
    submission = Submission(student_id=student.id, assessment_id=assessment.id)
    db.session.add(submission)
    db.session.commit()
    db.session.add(Submission(student_id=student.id, assessment_id=assessment.id))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
    response = Response(submission_id=submission.id, question_id=assessment.questions[0].id,
                        answer="A", detected_answer="A", detection_status="detected", correctness=True)
    db.session.add(response)
    db.session.commit()
    db.session.add(Response(submission_id=submission.id, question_id=assessment.questions[0].id,
                            answer="B", detected_answer="B", detection_status="detected", correctness=False))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL test database is not configured")
def test_synthetic_scan_persists_grade_and_rejects_duplicate(app, tmp_path):
    student = Student(student_number="S-3001", name="Fictional Scan Learner", class_section="9C")
    assessment = Assessment(title="Synthetic Scan Quiz", subject="Science", class_section="9C", total_questions=1)
    assessment.questions.append(Question(
        question_number=1, prompt="Choose B", option_a="A", option_b="B", option_c="C", option_d="D",
        correct_answer="B", points=2, learning_objective="Matter",
    ))
    db.session.add_all([student, assessment])
    db.session.commit()

    page = np.full((CANONICAL_HEIGHT, CANONICAL_WIDTH, 3), 255, dtype=np.uint8)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    for marker_id, (x_mm, y_mm) in MARKER_ORIGINS_MM.items():
        x, y = round(x_mm * PIXELS_PER_MM_X), round(y_mm * PIXELS_PER_MM_Y)
        size = round(MARKER_SIZE_MM * PIXELS_PER_MM_X)
        marker = cv2.aruco.generateImageMarker(dictionary, marker_id, size)
        page[y:y + size, x:x + size] = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)
    qr = qrcode.make(make_qr_payload(student.id, assessment.id)).convert("RGB")
    qr_size = round(QR_SIZE_MM * PIXELS_PER_MM_X)
    qr = cv2.cvtColor(np.asarray(qr.resize((qr_size, qr_size))), cv2.COLOR_RGB2BGR)
    qr_x, qr_y = round(QR_ORIGIN_MM[0] * PIXELS_PER_MM_X), round(QR_ORIGIN_MM[1] * PIXELS_PER_MM_Y)
    page[qr_y:qr_y + qr_size, qr_x:qr_x + qr_size] = qr
    x_mm, y_mm = bubble_center_mm(1, "B")
    cv2.circle(page, (round(x_mm * PIXELS_PER_MM_X), round(y_mm * PIXELS_PER_MM_Y)), 10, (0, 0, 0), -1)
    success, encoded = cv2.imencode(".png", page)
    assert success
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    upload = FileStorage(stream=BytesIO(encoded.tobytes()), filename="student-scan.png")

    submission = process_answer_sheet(upload, app.config)
    assert submission.score == 2
    assert submission.percentage_score == 100
    assert submission.responses[0].answer == "B"
    assert submission.responses[0].correctness is True
    with pytest.raises(DuplicateSubmissionError):
        process_answer_sheet(FileStorage(stream=BytesIO(encoded.tobytes()), filename="repeat.png"), app.config)
