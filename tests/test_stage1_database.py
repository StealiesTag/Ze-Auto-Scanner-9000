import os
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
import pymupdf
import pytest
import qrcode
from sqlalchemy.exc import IntegrityError
from werkzeug.datastructures import FileStorage

from app import db
from models import AnswerCorrection, Assessment, Question, Response, Student, Submission
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
from services.submission_service import make_qr_payload
from services.paparazzi_v3_scanner import get_answer_bubble_centers_mm, get_student_id_bubble_centers_mm, load_v3_profile
from services.paparazzi_v3_submission_service import confirm_v3_scan_draft, create_v3_scan_draft_from_image
from services.analytics_service import build_analytics


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


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL test database is not configured")
def test_v3_pdf_image_scan_stages_reviews_and_grades(app, tmp_path):
    question_count = 20
    answers = ["ABCDE"[(number - 1) % 5] for number in range(1, question_count + 1)]
    student = Student(student_number="1234567", name="V3 Scan Learner", class_section="V3A")
    assessment = Assessment(title="V3 Integration Quiz", subject="Science", class_section="V3A", total_questions=question_count)
    assessment.questions = [
        Question(
            question_number=number,
            prompt=f"Question {number}",
            option_a="A", option_b="B", option_c="C", option_d="D", option_e="E",
            correct_answer=answer, points=1, learning_objective="V3 integration",
        )
        for number, answer in enumerate(answers, start=1)
    ]
    db.session.add_all([student, assessment])
    db.session.commit()

    asset_dir = Path(__file__).resolve().parents[1] / "static" / "answer_sheets" / "v3" / "Paparazzi_Answer_Sheets_V3_Package"
    page = pymupdf.open(asset_dir / "Answer_Sheet_20Q_V3.pdf")[0]
    rendered = page.get_pixmap(matrix=pymupdf.Matrix(240 / 72, 240 / 72), alpha=False)
    image = cv2.cvtColor(np.frombuffer(rendered.samples, dtype=np.uint8).reshape(rendered.height, rendered.width, rendered.n), cv2.COLOR_RGB2BGR)
    profile = load_v3_profile()
    pixels_per_mm = 240 / 25.4
    id_centers = get_student_id_bubble_centers_mm(profile)
    for column, digit in enumerate(student.student_number):
        x_mm, y_mm = id_centers[column][int(digit)]
        cv2.circle(image, (round(x_mm * pixels_per_mm), round(y_mm * pixels_per_mm)), round(1.12 * pixels_per_mm * 0.72), (15, 15, 15), -1)
    answer_centers = get_answer_bubble_centers_mm(question_count, profile)
    for question_number, answer in enumerate(answers, start=1):
        x_mm, y_mm = answer_centers[question_number]["ABCDE".index(answer)]
        cv2.circle(image, (round(x_mm * pixels_per_mm), round(y_mm * pixels_per_mm)), round(1.48 * pixels_per_mm * 0.70), (15, 15, 15), -1)

    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    draft = create_v3_scan_draft_from_image(image, app.config, assessment, question_count)
    assert draft.detected_student_number == student.student_number
    assert draft.student_id == student.id
    assert all(item["status"] == "detected" for item in draft.recognized_answers)

    submission = confirm_v3_scan_draft(draft, student, [item["answer"] for item in draft.recognized_answers])
    assert submission.score == question_count
    assert submission.percentage_score == 100
    assert [item.answer for item in submission.responses] == answers


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL test database is not configured")
def test_manual_correction_persists_history_and_recalculates_grade(app):
    student = Student(student_number="V3-CORR-1", name="Correction Learner", class_section="V3B")
    assessment = Assessment(title="Correction Quiz", subject="Science", class_section="V3B", total_questions=1)
    assessment.questions = [Question(
        question_number=1, prompt="Choose E", option_a="A", option_b="B", option_c="C", option_d="D",
        option_e="E", correct_answer="E", points=2, learning_objective="Correction history",
    )]
    db.session.add_all([student, assessment])
    db.session.flush()
    submission = Submission(
        student_id=student.id, assessment_id=assessment.id, score=0, total_points=2,
        percentage_score=0, processing_status="processed", review_status="not_required",
        processing_notes={},
    )
    submission.responses.append(Response(
        question_id=assessment.questions[0].id, detected_answer="A", answer="A",
        detection_status="detected", diagnostic_values={}, correctness=False, confidence=0.9,
        manual_correction_flag=False,
    ))
    db.session.add(submission)
    db.session.commit()
    response = submission.responses[0]

    client = app.test_client()
    result = client.post(
        f"/scanning/submissions/{submission.id}/responses/{response.id}/correct",
        data={"answer": "E", "note": "Teacher verified the faint mark."},
    )

    assert result.status_code == 302
    db.session.refresh(submission)
    db.session.refresh(response)
    correction = db.session.query(AnswerCorrection).filter_by(response_id=response.id).one()
    assert response.detected_answer == "A"
    assert response.answer == "E"
    assert response.manual_correction_flag is True
    assert response.correctness is True
    assert submission.score == 2
    assert submission.percentage_score == 100
    assert submission.review_status == "complete"
    assert correction.previous_answer == "A"
    assert correction.corrected_answer == "E"
    assert correction.note == "Teacher verified the faint mark."
    review = client.get(f"/scanning/submissions/{submission.id}")
    assert review.status_code == 200
    assert b"Correction history (1)" in review.data


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL test database is not configured")
def test_analytics_reports_finalized_results_and_excludes_pending_submissions(app):
    students = [
        Student(student_number=f"AN-{index}", name=f"Analytics Student {index}", class_section="AN")
        for index in range(1, 5)
    ]
    assessment = Assessment(title="Analytics V3 Quiz", subject="Science", class_section="AN", total_questions=1)
    assessment.questions = [Question(
        question_number=1, prompt="Choose", option_a="A", option_b="B", option_c="C", option_d="D",
        option_e="E", correct_answer="E", points=2, learning_objective="Analytics",
    )]
    db.session.add_all([*students, assessment])
    db.session.flush()
    answers = [
        ("E", "not_required", True, 100),
        ("A", "complete", False, 0),
        (None, "not_required", False, 0),
        ("B", "required", False, 0),
    ]
    for student, (answer, review_status, correctness, percentage) in zip(students, answers):
        submission = Submission(
            student_id=student.id, assessment_id=assessment.id, score=2 if percentage else 0,
            total_points=2, percentage_score=percentage, processing_status="processed",
            review_status=review_status, processing_notes={},
        )
        submission.responses.append(Response(
            question_id=assessment.questions[0].id, detected_answer=answer, answer=answer,
            detection_status="blank" if answer is None else "detected", diagnostic_values={},
            correctness=correctness, confidence=0.9, manual_correction_flag=False,
        ))
        db.session.add(submission)
    db.session.commit()

    analytics = build_analytics(assessment_id=assessment.id)
    question = analytics["questions"][0]
    assert analytics["submissions"] == 4
    assert analytics["students_assessed"] == 3
    assert analytics["scored_submissions"] == 3
    assert analytics["average_score"] == pytest.approx(100 / 3, rel=0.01)
    assert analytics["highest_score"] == 100
    assert analytics["lowest_score"] == 0
    assert analytics["awaiting_review"] == 1
    assert question["valid_responses"] == 2
    assert question["correct"] == 1
    assert question["incorrect"] == 1
    assert question["blank"] == 1
    assert question["answer_distribution"] == {"A": 1, "B": 0, "C": 0, "D": 0, "E": 1}
