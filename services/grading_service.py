from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


def grade_detections(questions, detections: list[dict]) -> dict:
    """Blank answers earn zero; ambiguous/multiple marks earn zero pending review."""
    detection_by_number = {item["question_number"]: item for item in detections}
    detail = []
    score = Decimal("0")
    total = Decimal("0")
    needs_review = False

    for question in questions:
        number = int(question.question_number)
        points = Decimal(str(question.points))
        detection = detection_by_number.get(number, {"answer": None, "status": "ambiguous", "confidence": 0, "values": {}})
        answer = detection["answer"] if detection["status"] == "detected" else None
        correctness = answer == question.correct_answer if answer is not None else (False if detection["status"] == "blank" else None)
        if correctness is True:
            score += points
        if correctness is None:
            needs_review = True
        total += points
        detail.append({
            "question": question,
            "answer": answer,
            "detected_answer": answer,
            "status": detection["status"],
            "confidence": float(detection["confidence"]),
            "values": detection["values"],
            "correctness": correctness,
        })

    percentage = (score / total * Decimal("100")) if total else Decimal("0")
    return {
        "correct": sum(item["correctness"] is True for item in detail),
        "incorrect": sum(item["status"] == "detected" and item["correctness"] is False for item in detail),
        "unanswered": sum(item["status"] == "blank" for item in detail),
        "unresolved": sum(item["status"] in {"multiple_marks", "ambiguous"} for item in detail),
        "score": score.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "total_points": total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "percentage": percentage.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "needs_review": needs_review,
        "responses": detail,
    }


def apply_manual_correction(submission, response, answer: str | None) -> str | None:
    if answer not in {None, "A", "B", "C", "D"}:
        raise ValueError("A correction must be A, B, C, D, or blank.")
    previous_answer = response.answer
    response.answer = answer
    response.manual_correction_flag = True
    response.correctness = answer == response.question.correct_answer if answer else False
    recalculate_submission(submission)
    return previous_answer


def recalculate_submission(submission) -> None:
    score = Decimal("0")
    total = Decimal("0")
    unresolved = False
    for response in submission.responses:
        points = Decimal(str(response.question.points))
        total += points
        if response.answer is None and response.detection_status not in {"blank"} and not response.manual_correction_flag:
            response.correctness = None
            unresolved = True
        else:
            response.correctness = response.answer == response.question.correct_answer
            if response.correctness:
                score += points
    submission.score = score
    submission.total_points = total
    submission.percentage_score = (score * Decimal("100") / total).quantize(Decimal("0.01")) if total else Decimal("0")
    if unresolved:
        submission.review_status = "required"
    elif any(response.manual_correction_flag for response in submission.responses):
        submission.review_status = "complete"
    else:
        submission.review_status = "not_required"
