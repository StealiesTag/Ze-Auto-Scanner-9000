import json

import pytest

from extensions import db
from models import Assessment, Question, Response, Student, Submission, TeachingInsight
from services import teaching_insights_service as ai


@pytest.fixture
def analytics_fixture(app):
    assessment = Assessment(title="Fractions checkpoint", subject="Math", class_section="7B", total_questions=2)
    assessment.questions = [
        Question(question_number=1, prompt="Compare fractions", option_a="A", option_b="B", option_c="C", option_d="D", option_e="E", correct_answer="B", points=1, learning_objective="Compare fractions"),
        Question(question_number=2, prompt="Equivalent fractions", option_a="A", option_b="B", option_c="C", option_d="D", option_e="E", correct_answer="C", points=1, learning_objective="Equivalent fractions"),
    ]
    first = Student(student_number="PRIV-1", name="Private Learner One", class_section="7B")
    second = Student(student_number="PRIV-2", name="Private Learner Two", class_section="7B")
    pending_student = Student(student_number="PRIV-3", name="Private Pending Learner", class_section="7B")
    db.session.add_all([assessment, first, second, pending_student])
    db.session.flush()
    for student, answers, review in (
        (first, ["A", None], "not_required"),
        (second, ["B", "C"], "not_required"),
        (pending_student, ["A", "A"], "required"),
    ):
        submission = Submission(student_id=student.id, assessment_id=assessment.id, score=1, total_points=2,
                                percentage_score=50, processing_status="processed", review_status=review)
        db.session.add(submission)
        db.session.flush()
        for question, answer in zip(assessment.questions, answers):
            db.session.add(Response(submission_id=submission.id, question_id=question.id, answer=answer,
                                    detected_answer=answer, correctness=(answer == question.correct_answer) if answer else False,
                                    detection_status="detected" if answer else "blank", diagnostic_values={}, confidence=0.9))
    db.session.commit()
    return assessment


def test_payload_uses_only_finalized_submissions_and_contains_no_student_identifiers(analytics_fixture):
    payload = ai.build_teaching_payload(analytics_fixture.id)
    assert payload["assessment"]["finalized_submission_count"] == 2
    assert payload["class_performance"]["students_assessed"] == 2
    serialized = json.dumps(payload)
    for private_value in ("Private Learner", "PRIV-1", "PRIV-2", "PRIV-3", "student_id", "student_name"):
        assert private_value not in serialized


def test_payload_contains_aggregated_answer_distribution_and_blank_count(analytics_fixture):
    payload = ai.build_teaching_payload(analytics_fixture.id)
    first_question = payload["questions"][0]
    assert first_question["correct_count"] == 1
    assert first_question["incorrect_count"] == 1
    assert first_question["blank_count"] == 0
    assert first_question["answer_choice_distribution"] == {"A": 1, "B": 1, "C": 0, "D": 0, "E": 0}
    second_question = payload["questions"][1]
    assert second_question["blank_count"] == 1
    assert second_question["answer_choice_distribution"]["C"] == 1


def test_gap_detector_prioritizes_low_accuracy_blank_rates_and_objectives():
    payload = {"questions": [
        {"question_number": 1, "correct_count": 1, "valid_answer_count": 5, "accuracy_percent": 20.0, "blank_count": 0, "blank_percent": 0.0, "answer_choice_distribution": {"A": 3, "B": 1, "C": 1, "D": 0, "E": 0}, "correct_answer": "B", "learning_objective": "Fractions"},
        {"question_number": 2, "correct_count": 4, "valid_answer_count": 5, "accuracy_percent": 80.0, "blank_count": 2, "blank_percent": 28.6, "answer_choice_distribution": {"A": 0, "B": 0, "C": 4, "D": 1, "E": 0}, "correct_answer": "C", "learning_objective": "Fractions"},
    ]}
    gaps = ai.detect_learning_gaps(payload)
    assert any(gap["type"] == "question_accuracy" and gap["question_numbers"] == [1] for gap in gaps["gaps"])
    assert any(gap["type"] == "high_blank_rate" and gap["question_numbers"] == [2] for gap in gaps["gaps"])
    assert any(gap["type"] == "objective_accuracy" and gap["learning_objective"] == "Fractions" for gap in gaps["gaps"])
    assert gaps["strengths"][0]["question_number"] == 2


def test_unavailable_ai_does_not_create_fabricated_output():
    with pytest.raises(ai.TeachingAIUnavailable, match="AI_API_KEY"):
        ai.generate_teaching_output("insights", {"questions": []}, {"gaps": []}, {})


def test_provider_errors_are_reported_without_returning_fake_content(monkeypatch):
    def fail(*_args, **_kwargs):
        raise ai.TeachingAIError("provider unavailable")
    monkeypatch.setattr(ai, "_request_llm", fail)
    with pytest.raises(ai.TeachingAIError, match="provider unavailable"):
        ai.generate_teaching_output("insights", {"questions": []}, {"gaps": []}, {"AI_API_KEY": "mock", "AI_MODEL": "mock"})


def test_lesson_output_requires_all_sections_and_allowed_duration():
    lesson = {"lesson_title": "Fractions", "target_learning_objectives": ["Compare"], "identified_learning_gaps": ["Q1"],
              "duration_minutes": 30, "required_materials": ["Fraction strips"], "introduction_or_review_activity": "Review",
              "main_teaching_discussion": "Discuss", "guided_practice": "Practice", "independent_or_collaborative_activity": "Pairs",
              "assessment_or_evaluation": "Exit ticket", "expected_learning_outcomes": ["Compare fractions"]}
    assert ai.validate_generated_output("lesson", lesson) == lesson
    lesson["duration_minutes"] = 20
    with pytest.raises(ai.TeachingAIError, match="unsupported duration"):
        ai.validate_generated_output("lesson", lesson)


def test_remediation_requires_answer_key_and_explanation():
    activity = {"learning_objective": "Fractions", "prompt": "Which is larger, 1/2 or 1/3?", "answer": "1/2", "explanation": "One half is greater."}
    assert ai.validate_generated_output("remediation", {"activities": [activity]}) == {"activities": [activity]}
    with pytest.raises(ai.TeachingAIError, match="answer keys"):
        ai.validate_generated_output("remediation", {"activities": [{"prompt": "Question"}]})


def test_fingerprint_changes_when_finalized_analytics_change():
    original = {"assessment": {"name": "Quiz"}, "questions": [{"accuracy_percent": 50}]}
    changed = {"assessment": {"name": "Quiz"}, "questions": [{"accuracy_percent": 75}]}
    assert ai.analytics_fingerprint(original) != ai.analytics_fingerprint(changed)
    assert ai.output_fingerprint(ai.analytics_fingerprint(original), "lesson", 30) != ai.output_fingerprint(ai.analytics_fingerprint(changed), "lesson", 30)


def test_teacher_can_edit_and_save_generated_output(analytics_fixture, app, monkeypatch):
    output = {"overall_summary": "Half of nonblank answers were correct.", "demonstrated_understanding": [],
              "areas_needing_instruction": ["Compare fractions"], "notable_patterns": [], "learning_gaps": [], "teaching_recommendations": ["Use fraction strips"]}
    monkeypatch.setattr(ai, "_request_llm", lambda *_args, **_kwargs: output)
    client = app.test_client()
    response = client.post("/analytics/ai/generate", data={"assessment_id": analytics_fixture.id, "kind": "insights"})
    assert response.status_code == 302
    saved = db.session.query(TeachingInsight).filter_by(assessment_id=analytics_fixture.id, kind="insights").one()
    edited = dict(output, overall_summary="Teacher edited summary.")
    response = client.post(f"/analytics/ai/{saved.id}/save", data={"output_json": json.dumps(edited)})
    assert response.status_code == 302
    db.session.refresh(saved)
    assert saved.teacher_saved is True
    assert saved.output["overall_summary"] == "Teacher edited summary."
    page = client.get(f"/analytics?assessment_id={analytics_fixture.id}")
    assert page.status_code == 200
    assert b"AI Teaching Insights" in page.data
    assert b"Teacher edited summary." in page.data


def test_ai_suggestions_are_cached_by_kind_duration_and_selected_gaps(analytics_fixture, app, monkeypatch):
    calls = []
    lesson = {"lesson_title": "Fractions", "target_learning_objectives": ["Compare fractions"], "identified_learning_gaps": ["Q1"],
              "duration_minutes": 30, "required_materials": [], "introduction_or_review_activity": "Review",
              "main_teaching_discussion": "Discuss", "guided_practice": "Practice", "independent_or_collaborative_activity": "Pairs",
              "assessment_or_evaluation": "Exit ticket", "expected_learning_outcomes": ["Compare fractions"]}
    monkeypatch.setattr(ai, "_request_llm", lambda *_args, **_kwargs: calls.append(1) or lesson)
    client = app.test_client()
    form = {"assessment_id": analytics_fixture.id, "kind": "lesson", "duration_minutes": "30"}
    assert client.post("/analytics/ai/generate", data=form).status_code == 302
    assert client.post("/analytics/ai/generate", data=form).status_code == 302
    assert len(calls) == 1
    form["duration_minutes"] = "45"
    other_lesson = dict(lesson, duration_minutes=45)
    monkeypatch.setattr(ai, "_request_llm", lambda *_args, **_kwargs: calls.append(1) or other_lesson)
    assert client.post("/analytics/ai/generate", data=form).status_code == 302
    assert len(calls) == 2
