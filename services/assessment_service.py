from __future__ import annotations


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

    for index, question in enumerate(questions, start=1):
        answer = (question.get("correct_answer") or "").strip().upper()
        points = question.get("points", 1)
        learning_objective = (question.get("learning_objective") or "").strip()
        if answer not in {"A", "B", "C", "D"}:
            errors.append(f"Question {index} has an invalid answer key.")
        if points <= 0:
            errors.append(f"Question {index} must have a positive point value.")
        if not learning_objective:
            errors.append(f"Question {index} must include a learning objective.")

    return not errors, errors
