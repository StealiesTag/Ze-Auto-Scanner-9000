from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy import case, func, select

from extensions import db
from models import Assessment, Question, Response, Submission, TeachingInsight


class TeachingAIUnavailable(RuntimeError):
    pass


class TeachingAIError(RuntimeError):
    pass


def build_teaching_payload(assessment_id: int) -> dict:
    assessment = db.session.get(Assessment, assessment_id)
    if assessment is None:
        raise ValueError("Assessment not found.")
    finalized = [
        Submission.assessment_id == assessment_id,
        Submission.processing_status == "processed",
        Submission.review_status != "required",
        Submission.total_points > 0,
        ~select(Response.id).where(Response.submission_id == Submission.id, Response.correctness.is_(None)).correlate(Submission).exists(),
    ]
    submissions = db.session.execute(
        select(Submission.id, Submission.percentage_score).where(*finalized).order_by(Submission.id)
    ).all()
    scores = [float(row.percentage_score) for row in submissions]
    distribution = {label: 0 for label in ("0-49", "50-59", "60-69", "70-79", "80-89", "90-100")}
    for score in scores:
        label = "0-49" if score < 50 else "50-59" if score < 60 else "60-69" if score < 70 else "70-79" if score < 80 else "80-89" if score < 90 else "90-100"
        distribution[label] += 1
    questions = db.session.scalars(select(Question).where(Question.assessment_id == assessment_id).order_by(Question.question_number)).all()
    response_rows = db.session.execute(
        select(Response.question_id, Response.answer, Response.correctness)
        .join(Submission, Submission.id == Response.submission_id)
        .where(*finalized, Response.correctness.is_not(None))
    ).all()
    by_question: dict[int, list] = defaultdict(list)
    for row in response_rows:
        by_question[row.question_id].append(row)
    question_payload = []
    for question in questions:
        rows = by_question[question.id]
        choices = Counter(row.answer for row in rows if row.answer is not None and row.answer in "ABCDE")
        correct = sum(row.correctness is True for row in rows)
        incorrect = sum(row.correctness is False and row.answer is not None for row in rows)
        blank = sum(row.answer is None for row in rows)
        nonblank = correct + incorrect
        question_payload.append({
            "question_number": question.question_number,
            "correct_answer": question.correct_answer,
            "correct_count": correct,
            "incorrect_count": incorrect,
            "blank_count": blank,
            "valid_answer_count": nonblank,
            "accuracy_percent": round(correct / nonblank * 100, 1) if nonblank else None,
            "blank_percent": round(blank / len(rows) * 100, 1) if rows else None,
            "answer_choice_distribution": {choice: choices[choice] for choice in "ABCDE"},
            "learning_objective": question.learning_objective.strip() or None,
        })
    assessment_data = {
        "assessment": {
            "name": assessment.title,
            "subject": assessment.subject,
            "grade_level": getattr(assessment, "grade_level", None),
            "question_count": len(questions),
            "finalized_submission_count": len(submissions),
            "assessment_date": assessment.created_at.isoformat() if assessment.created_at else None,
        },
        "class_performance": {
            "students_assessed": len(submissions),
            "average_score_percent": round(sum(scores) / len(scores), 1) if scores else None,
            "highest_score_percent": max(scores) if scores else None,
            "lowest_score_percent": min(scores) if scores else None,
            "score_distribution": distribution,
        },
        "questions": question_payload,
    }
    return assessment_data


def detect_learning_gaps(payload: dict, accuracy_threshold: float = 60.0, blank_threshold: float = 25.0) -> dict:
    questions = payload["questions"]
    gaps = []
    objective_totals: dict[str, dict] = defaultdict(lambda: {"correct": 0, "valid": 0, "questions": []})
    strengths = []
    for item in questions:
        objective = item.get("learning_objective")
        accuracy = item.get("accuracy_percent")
        valid = item.get("valid_answer_count", 0)
        if objective and valid:
            totals = objective_totals[objective]
            totals["correct"] += item["correct_count"]
            totals["valid"] += valid
            totals["questions"].append(item["question_number"])
        incorrect_choices = sorted(
            ((choice, count) for choice, count in item["answer_choice_distribution"].items() if choice != item["correct_answer"] and count),
            key=lambda pair: (-pair[1], pair[0]),
        )
        if accuracy is not None and accuracy < accuracy_threshold:
            gaps.append({
                "type": "question_accuracy", "question_numbers": [item["question_number"]],
                "learning_objective": objective, "accuracy_percent": accuracy,
                "common_incorrect_answers": [{"choice": c, "count": n} for c, n in incorrect_choices[:2]],
                "evidence": f"{item['correct_count']} correct of {valid} nonblank answers.",
            })
        blank_percent = item.get("blank_percent")
        if blank_percent is not None and blank_percent >= blank_threshold:
            gaps.append({
                "type": "high_blank_rate", "question_numbers": [item["question_number"]],
                "learning_objective": objective, "blank_percent": blank_percent,
                "evidence": f"{item['blank_count']} blank of {item['blank_count'] + valid} finalized answers.",
            })
        if accuracy is not None and accuracy >= 80.0 and valid:
            strengths.append({"question_number": item["question_number"], "learning_objective": objective, "accuracy_percent": accuracy})
    objectives = []
    for objective, totals in objective_totals.items():
        rate = round(totals["correct"] / totals["valid"] * 100, 1) if totals["valid"] else None
        entry = {"learning_objective": objective, "accuracy_percent": rate, "question_numbers": sorted(totals["questions"]), "valid_answers": totals["valid"]}
        if rate is not None and rate < accuracy_threshold:
            objectives.append(entry)
            gaps.append({"type": "objective_accuracy", **entry, "evidence": f"{totals['correct']} correct of {totals['valid']} nonblank answers across related questions."})
    gaps.sort(key=lambda gap: (gap.get("accuracy_percent", gap.get("blank_percent", 100)), gap.get("question_numbers", [0])[0]))
    missing_topics = [item["question_number"] for item in questions if not item.get("learning_objective")]
    return {"gaps": gaps, "objective_performance": objectives, "strengths": strengths, "missing_topic_question_numbers": missing_topics}


def analytics_fingerprint(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def output_fingerprint(payload_hash: str, kind: str, duration: int | None = None, selected_gaps: list | None = None) -> str:
    key = {"analytics": payload_hash, "kind": kind, "duration": duration, "selected_gaps": selected_gaps or []}
    return analytics_fingerprint(key)


def _request_llm(prompt: dict, config: dict) -> dict:
    api_key, model = config.get("AI_API_KEY"), config.get("AI_MODEL")
    if not api_key or not model:
        raise TeachingAIUnavailable("AI suggestions are unavailable. Configure AI_API_KEY and AI_MODEL to enable them.")
    body = json.dumps({"model": model, "temperature": 0.2, "response_format": {"type": "json_object"}, "messages": [
        {"role": "system", "content": "You are a teaching assistant. Use only supplied aggregate evidence. Separate observations from hypotheses. Return valid JSON matching the requested structure. Do not claim causes as facts."},
        {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
    ]}).encode("utf-8")
    url = config.get("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
    request = Request(url, data=body, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=float(config.get("AI_TIMEOUT_SECONDS", 30))) as response:
            result = json.loads(response.read().decode("utf-8"))
        return json.loads(result["choices"][0]["message"]["content"])
    except (HTTPError, URLError, TimeoutError, OSError, KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise TeachingAIError("The AI provider did not return a usable response. Your assessment data was not changed.") from error


def validate_generated_output(kind: str, output: dict) -> dict:
    if not isinstance(output, dict):
        raise TeachingAIError("The AI provider returned an invalid output structure.")
    if kind == "insights":
        required = {"overall_summary": str, "demonstrated_understanding": list, "areas_needing_instruction": list, "notable_patterns": list, "learning_gaps": list, "teaching_recommendations": list}
        for gap in output.get("learning_gaps", []):
            if not isinstance(gap, dict) or not all(key in gap for key in ("question_numbers", "learning_objectives", "observed_evidence", "possible_interpretations", "common_incorrect_answers", "confidence_limitations")):
                raise TeachingAIError("Each learning gap must distinguish observed evidence from possible interpretations and limitations.")
            if (not isinstance(gap["question_numbers"], list) or not isinstance(gap["learning_objectives"], list)
                    or not isinstance(gap["observed_evidence"], str)
                    or not isinstance(gap["possible_interpretations"], list)
                    or not isinstance(gap["common_incorrect_answers"], list)
                    or not isinstance(gap["confidence_limitations"], (str, list))):
                raise TeachingAIError("Each learning gap must use the required structured evidence fields.")
    elif kind == "lesson":
        required = {"lesson_title": str, "target_learning_objectives": list, "identified_learning_gaps": list, "duration_minutes": int, "required_materials": list, "introduction_or_review_activity": str, "main_teaching_discussion": str, "guided_practice": str, "independent_or_collaborative_activity": str, "assessment_or_evaluation": str, "expected_learning_outcomes": list}
        if output.get("duration_minutes") not in {15, 30, 45, 60}:
            raise TeachingAIError("The generated lesson plan used an unsupported duration.")
    elif kind == "remediation":
        required = {"activities": list}
        if any(not isinstance(item, dict) or not all(isinstance(item.get(field), str) and item[field].strip() for field in ("learning_objective", "prompt", "answer", "explanation")) for item in output.get("activities", [])):
            raise TeachingAIError("The remediation activities must include objectives, prompts, answer keys, and explanations.")
    else:
        raise TeachingAIError("Unknown teaching suggestion type.")
    if any(key not in output or not isinstance(output[key], expected) for key, expected in required.items()):
        raise TeachingAIError("The AI provider returned an incomplete output structure.")
    for field in ("demonstrated_understanding", "areas_needing_instruction", "notable_patterns", "teaching_recommendations", "target_learning_objectives", "identified_learning_gaps", "required_materials", "expected_learning_outcomes"):
        if field in output and any(not isinstance(item, str) for item in output[field]):
            raise TeachingAIError(f"The generated {field.replace('_', ' ')} must be a list of text items.")
    return output


def generate_teaching_output(kind: str, payload: dict, gaps: dict, config: dict, *, duration: int | None = None, selected_gaps: list | None = None) -> dict:
    if kind not in {"insights", "lesson", "remediation"}:
        raise ValueError("Unsupported teaching suggestion type.")
    prompt = {"kind": kind, "analytics": payload, "deterministic_learning_gaps": gaps, "duration_minutes": duration, "selected_learning_gaps": selected_gaps or []}
    output = validate_generated_output(kind, _request_llm(prompt, config))
    if kind == "lesson" and output["duration_minutes"] != duration:
        raise TeachingAIError("The generated lesson plan duration does not match your selection.")
    if kind == "remediation" and not output["activities"]:
        raise TeachingAIError("The AI provider returned no remediation activities.")
    return output


def find_cached_output(assessment_id: int, kind: str, input_hash: str, duration: int | None = None, selected_gaps: list | None = None):
    return db.session.scalar(select(TeachingInsight).where(
        TeachingInsight.assessment_id == assessment_id,
        TeachingInsight.kind == kind,
        TeachingInsight.input_hash == input_hash,
        TeachingInsight.duration_minutes == duration,
    ))


__all__ = ["TeachingAIError", "TeachingAIUnavailable", "analytics_fingerprint", "build_teaching_payload", "detect_learning_gaps", "find_cached_output", "generate_teaching_output", "output_fingerprint", "validate_generated_output"]
