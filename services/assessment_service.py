from __future__ import annotations

from decimal import Decimal, InvalidOperation

from extensions import db
from models import Assessment, Question

ASSESSMENT_QUESTION_LIMIT = 32


def validate_assessment_payload(payload: dict) -> tuple[bool, list[str]]:
    errors: list[str] = []
    title = (payload.get("title") or "").strip()
    subject = (payload.get("subject") or "").strip()
    class_section = (payload.get("class_section") or "").strip()
    questions = payload.get("questions") or []
    if not title:
        errors.append("Assessment title is required.")
    if not subject:
        errors.append("Subject is required.")
    if not class_section:
        errors.append("Class or section is required.")
    if not questions:
        errors.append("At least one question is required.")
    if len(title) > 200 or len(subject) > 100 or len(class_section) > 100:
        errors.append("Assessment title, subject, or class section exceeds its allowed length.")
    if len(questions) > ASSESSMENT_QUESTION_LIMIT:
        errors.append(f"An assessment can contain at most {ASSESSMENT_QUESTION_LIMIT} questions on one A4 sheet.")

    for index, question in enumerate(questions, start=1):
        answer = (question.get("correct_answer") or "").strip().upper()
        try:
            points = Decimal(str(question.get("points", 1)))
        except (InvalidOperation, TypeError):
            points = Decimal("0")
        objective = (question.get("learning_objective") or "").strip()
        prompt = (question.get("prompt") or "").strip()
        options = [(question.get(f"option_{option.lower()}") or "").strip() for option in "ABCD"]
        if answer not in {"A", "B", "C", "D"}:
            errors.append(f"Question {index} has an invalid answer key.")
        if not points.is_finite() or points <= 0 or points > 999999 or points.as_tuple().exponent < -2:
            errors.append(f"Question {index} must have a positive point value with at most two decimals.")
        if not objective:
            errors.append(f"Question {index} must include a learning objective.")
        if len(objective) > 255:
            errors.append(f"Question {index} learning objective must be 255 characters or fewer.")
        if not prompt:
            errors.append(f"Question {index} must include a prompt.")
        if len(prompt) > 2000 or any(len(option) > 500 for option in options):
            errors.append(f"Question {index} prompt or choice exceeds its allowed length.")
        if any(not option for option in options):
            errors.append(f"Question {index} must include choices A, B, C, and D.")
    return not errors, errors


def create_assessment(payload: dict) -> Assessment:
    valid, errors = validate_assessment_payload(payload)
    if not valid:
        raise ValueError(" ".join(errors))
    assessment = Assessment(
        title=payload["title"].strip(),
        subject=payload["subject"].strip(),
        class_section=payload["class_section"].strip(),
        total_questions=len(payload["questions"]),
    )
    assessment.questions = [
        Question(question_number=index, correct_answer=row["correct_answer"].strip().upper(),
                 points=Decimal(str(row.get("points", 1))), learning_objective=row["learning_objective"].strip(),
                 prompt=row["prompt"].strip(), option_a=row["option_a"].strip(), option_b=row["option_b"].strip(),
                 option_c=row["option_c"].strip(), option_d=row["option_d"].strip())
        for index, row in enumerate(payload["questions"], start=1)
    ]
    db.session.add(assessment)
    db.session.commit()
    return assessment


def update_assessment(assessment: Assessment, payload: dict) -> Assessment:
    valid, errors = validate_assessment_payload(payload)
    if not valid:
        raise ValueError(" ".join(errors))
    if assessment.submissions:
        raise ValueError("This assessment already has submissions; its answer key is locked to preserve grades.")
    assessment.title = payload["title"].strip()
    assessment.subject = payload["subject"].strip()
    assessment.class_section = payload["class_section"].strip()
    assessment.questions.clear()
    assessment.questions.extend(
        Question(question_number=index, correct_answer=row["correct_answer"].strip().upper(),
                 points=Decimal(str(row.get("points", 1))), learning_objective=row["learning_objective"].strip(),
                 prompt=row["prompt"].strip(), option_a=row["option_a"].strip(), option_b=row["option_b"].strip(),
                 option_c=row["option_c"].strip(), option_d=row["option_d"].strip())
        for index, row in enumerate(payload["questions"], start=1)
    )
    assessment.total_questions = len(payload["questions"])
    db.session.commit()
    return assessment


def parse_assessment_form(form) -> dict:
    questions = []
    fields = [form.getlist(name) for name in (
        "prompt", "option_a", "option_b", "option_c", "option_d", "correct_answer", "points", "learning_objective"
    )]
    if len({len(values) for values in fields}) != 1:
        return {"title": form.get("title", ""), "subject": form.get("subject", ""),
                "class_section": form.get("class_section", ""), "questions": [{}]}
    for values in zip(*fields):
        prompt, option_a, option_b, option_c, option_d, answer, points, objective = values
        if any(value.strip() for value in values):
            questions.append({"prompt": prompt, "option_a": option_a, "option_b": option_b, "option_c": option_c,
                              "option_d": option_d, "correct_answer": answer, "points": points,
                              "learning_objective": objective})
    return {"title": form.get("title", ""), "subject": form.get("subject", ""),
            "class_section": form.get("class_section", ""), "questions": questions}
