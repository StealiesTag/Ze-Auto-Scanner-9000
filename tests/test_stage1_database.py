import os

import pytest
from sqlalchemy.exc import IntegrityError

from app import db
from models import Assessment, Question, Student


@pytest.mark.skipif(not (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")), reason="PostgreSQL database is not configured")
def test_student_number_is_unique(app):
    student_one = Student(student_number="S-1001", name="Alice Smith", class_section="7A")
    student_two = Student(student_number="S-1001", name="Alice Copy", class_section="7A")

    db.session.add(student_one)
    db.session.commit()

    db.session.add(student_two)
    with pytest.raises(IntegrityError):
        db.session.commit()

    db.session.rollback()


@pytest.mark.skipif(not (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")), reason="PostgreSQL database is not configured")
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


@pytest.mark.skipif(not (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")), reason="PostgreSQL database is not configured")
def test_app_health_route(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"
