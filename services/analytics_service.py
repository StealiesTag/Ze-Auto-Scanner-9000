from __future__ import annotations

from sqlalchemy import case, func, select

from extensions import db
from models import Assessment, LearningResource, Question, Response, Student, Submission
from services.recommendation_service import recommend_learning_objectives


def calculate_average(values):
    if not values:
        return 0.0
    return sum(values) / len(values)


def calculate_median(values):
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 0:
        return (ordered[mid - 1] + ordered[mid]) / 2
    return ordered[mid]


def question_accuracy(correct: int, valid_responses: int) -> float | None:
    return correct / valid_responses if valid_responses else None


def question_analytics_statement(assessment_id: int | None = None):
    valid_response = Response.correctness.is_not(None) & Response.answer.is_not(None)
    filters = [Assessment.id == assessment_id] if assessment_id is not None else []
    return (
        select(Question.id, Question.question_number, Assessment.title,
               func.count(case((valid_response, Response.id))),
               func.sum(case((valid_response & Response.correctness.is_(True), 1), else_=0)))
        .join(Assessment, Question.assessment_id == Assessment.id)
        .outerjoin(Response, Response.question_id == Question.id)
        .where(*filters)
        .group_by(Question.id, Assessment.title)
        .order_by(Assessment.title, Question.question_number)
    )


def build_analytics(assessment_id: int | None = None, recommendation_threshold: float = 0.4) -> dict:
    percentage = (Submission.score * 100.0) / func.nullif(Submission.total_points, 0)
    finalized_filter = [
        Submission.processing_status == "processed",
        Submission.review_status != "required",
        Submission.total_points > 0,
        ~select(Response.id).where(
            Response.submission_id == Submission.id,
            Response.correctness.is_(None),
        ).exists(),
    ]
    if assessment_id is not None:
        finalized_filter.append(Submission.assessment_id == assessment_id)

    score_stats = db.session.execute(
        select(func.count(Submission.id), func.avg(percentage),
               func.percentile_cont(0.5).within_group(percentage))
        .where(*finalized_filter)
    ).one()
    distribution_case = case(
        (percentage < 50, "0-49"),
        (percentage < 60, "50-59"),
        (percentage < 70, "60-69"),
        (percentage < 80, "70-79"),
        (percentage < 90, "80-89"),
        else_="90-100",
    )
    distribution_rows = db.session.execute(
        select(distribution_case, func.count(Submission.id))
        .where(*finalized_filter)
        .group_by(distribution_case)
    ).all()

    question_rows = db.session.execute(question_analytics_statement(assessment_id)).all()
    question_stats = []
    for question_id, number, title, count, correct in question_rows:
        correct_count = int(correct or 0)
        valid_count = int(count or 0)
        question_stats.append({
            "question_id": question_id,
            "question_number": number,
            "assessment_title": title,
            "valid_responses": valid_count,
            "correct": correct_count,
            "accuracy": question_accuracy(correct_count, valid_count),
        })

    objective_filters = [Response.correctness.is_not(None), Response.answer.is_not(None), Submission.processing_status == "processed"]
    if assessment_id is not None:
        objective_filters.append(Submission.assessment_id == assessment_id)
    objective_rows = db.session.execute(
        select(Question.learning_objective, func.count(Response.id),
               func.sum(case((Response.correctness.is_(False), 1), else_=0)))
        .join(Response, Response.question_id == Question.id)
        .join(Submission, Submission.id == Response.submission_id)
        .where(*objective_filters)
        .group_by(Question.learning_objective)
    ).all()
    objective_stats = {
        objective: {"valid_responses": int(count), "incorrect": int(incorrect or 0)}
        for objective, count, incorrect in objective_rows
    }
    resources = db.session.scalars(select(LearningResource).order_by(LearningResource.title)).all()
    recommendations = recommend_learning_objectives(objective_stats, resources, recommendation_threshold)
    pending_filter = [Submission.review_status == "required"]
    if assessment_id is not None:
        pending_filter.append(Submission.assessment_id == assessment_id)
    unresolved_count = db.session.scalar(select(func.count(Submission.id)).where(*pending_filter)) or 0
    submissions_filter = [Submission.processing_status == "processed"]
    if assessment_id is not None:
        submissions_filter.append(Submission.assessment_id == assessment_id)
    submissions_count = db.session.scalar(select(func.count(Submission.id)).where(*submissions_filter)) or 0
    student_count = db.session.scalar(select(func.count(Student.id))) or 0
    return {
        "students": student_count,
        "submissions": submissions_count,
        "scored_submissions": int(score_stats[0] or 0),
        "average_score": float(score_stats[1]) if score_stats[1] is not None else None,
        "median_score": float(score_stats[2]) if score_stats[2] is not None else None,
        "score_distribution": dict(distribution_rows),
        "questions": question_stats,
        "most_missed": sorted(
            [row for row in question_stats if row["valid_responses"]],
            key=lambda row: (row["accuracy"], -row["valid_responses"]),
        ),
        "objectives": objective_stats,
        "recommendations": recommendations,
        "recommendation_threshold": recommendation_threshold,
        "awaiting_review": int(unresolved_count),
    }


__all__ = ["build_analytics", "calculate_average", "calculate_median", "question_accuracy", "question_analytics_statement"]



