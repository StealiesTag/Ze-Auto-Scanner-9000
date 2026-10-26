from __future__ import annotations

from urllib.parse import urlparse

from extensions import db
from models import LearningResource


def recommend_learning_objectives(objective_stats, resources, threshold: float = 0.4) -> list[dict]:
    by_objective: dict[str, list[LearningResource]] = {}
    for resource in resources:
        by_objective.setdefault(resource.learning_objective, []).append(resource)
    recommendations = []
    for objective, stats in objective_stats.items():
        valid_count = int(stats["valid_responses"])
        incorrect = int(stats["incorrect"])
        if valid_count == 0:
            continue
        error_rate = incorrect / valid_count
        if error_rate >= threshold:
            recommendations.append({
                "objective": objective,
                "valid_responses": valid_count,
                "incorrect": incorrect,
                "error_rate": error_rate,
                "resources": by_objective.get(objective, []),
            })
    return recommendations


def validate_resource_payload(payload: dict) -> tuple[bool, list[str]]:
    errors = []
    title = (payload.get("title") or "").strip()
    description = (payload.get("description") or "").strip()
    url = (payload.get("url") or "").strip()
    objective = (payload.get("learning_objective") or "").strip()
    resource_type = (payload.get("resource_type") or "").strip()
    parsed_url = urlparse(url)
    if not title or len(title) > 200:
        errors.append("Resource title is required and must be at most 200 characters.")
    if not description:
        errors.append("Resource description is required.")
    if len(description) > 5000:
        errors.append("Resource description must be at most 5000 characters.")
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc or len(url) > 500:
        errors.append("Resource URL must be a valid HTTP or HTTPS URL up to 500 characters.")
    if not objective or len(objective) > 255:
        errors.append("Learning objective is required and must be at most 255 characters.")
    if resource_type not in {"reading", "video", "practice", "lesson", "other"}:
        errors.append("Choose a valid resource type.")
    return not errors, errors


def create_learning_resource(payload: dict) -> LearningResource:
    valid, errors = validate_resource_payload(payload)
    if not valid:
        raise ValueError(" ".join(errors))
    resource = LearningResource(
        title=payload["title"].strip(),
        description=payload["description"].strip(),
        url=payload["url"].strip(),
        learning_objective=payload["learning_objective"].strip(),
        resource_type=payload["resource_type"].strip(),
    )
    db.session.add(resource)
    db.session.commit()
    return resource
