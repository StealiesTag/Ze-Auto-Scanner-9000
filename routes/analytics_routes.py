import json

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from models import Assessment, TeachingInsight
from models import LearningResource
from services.recommendation_service import create_learning_resource
from services.analytics_service import build_analytics
from extensions import db
from services.teaching_insights_service import (
    TeachingAIError,
    TeachingAIUnavailable,
    analytics_fingerprint,
    build_teaching_payload,
    detect_learning_gaps,
    find_cached_output,
    generate_teaching_output,
    output_fingerprint,
    validate_generated_output,
)

analytics_bp = Blueprint("analytics", __name__, url_prefix="/analytics")


@analytics_bp.route("")
def index():
    raw_id = request.args.get("assessment_id", "")
    assessment_id = int(raw_id) if raw_id.isdigit() else None
    assessments = db.session.scalars(select(Assessment).order_by(Assessment.title)).all()
    resources = db.session.scalars(select(LearningResource).order_by(LearningResource.learning_objective, LearningResource.title)).all()
    analytics = build_analytics(assessment_id=assessment_id,
                                recommendation_threshold=current_app.config["RECOMMENDATION_ERROR_THRESHOLD"])
    ai_payload = ai_gaps = ai_hash = None
    ai_outputs = []
    ai_storage_ready = assessment_id is None
    if assessment_id is not None:
        try:
            ai_payload = build_teaching_payload(assessment_id)
            ai_gaps = detect_learning_gaps(ai_payload)
            ai_hash = analytics_fingerprint(ai_payload)
            candidates = db.session.scalars(select(TeachingInsight).where(
                TeachingInsight.assessment_id == assessment_id,
            ).order_by(TeachingInsight.updated_at.desc())).all()
            ai_outputs = [item for item in candidates if item.input_hash == output_fingerprint(ai_hash, item.kind, item.duration_minutes, item.selected_gaps)]
            ai_storage_ready = True
        except ValueError:
            flash("The selected assessment could not be found.", "error")
        except SQLAlchemyError:
            db.session.rollback()
            ai_payload = ai_gaps = ai_hash = None
            flash("AI teaching-suggestion storage is not installed yet. Apply migration 20261101_0003 after backup and approval; deterministic analytics remain available.", "error")
    return render_template("analytics.html", analytics=analytics, assessments=assessments,
                           resources=resources, selected_assessment=assessment_id,
                           ai_payload=ai_payload, ai_gaps=ai_gaps, ai_hash=ai_hash, ai_outputs=ai_outputs,
                           ai_storage_ready=ai_storage_ready,
                           ai_configured=bool(current_app.config.get("AI_API_KEY") and current_app.config.get("AI_MODEL")))


@analytics_bp.route("/ai/generate", methods=["POST"])
def generate_ai_output():
    assessment_id = request.form.get("assessment_id", type=int)
    kind = request.form.get("kind", "insights")
    if not assessment_id or kind not in {"insights", "lesson", "remediation"}:
        flash("Select an assessment and a valid suggestion type.", "error")
        return redirect(url_for("analytics.index", assessment_id=assessment_id))
    duration = request.form.get("duration_minutes", type=int) if kind == "lesson" else None
    if kind == "lesson" and duration not in {15, 30, 45, 60}:
        flash("Choose a lesson duration of 15, 30, 45, or 60 minutes.", "error")
        return redirect(url_for("analytics.index", assessment_id=assessment_id))
    try:
        # Fail before an API request when the additive persistence migration is pending.
        db.session.scalars(select(TeachingInsight.id).limit(1)).first()
        payload = build_teaching_payload(assessment_id)
        gaps = detect_learning_gaps(payload)
        base_hash = analytics_fingerprint(payload)
        selected_gaps = request.form.getlist("selected_gap") if kind == "remediation" else []
        available_objectives = {gap.get("learning_objective") for gap in gaps["gaps"] if gap.get("learning_objective")}
        selected_gaps = [objective for objective in selected_gaps if objective in available_objectives]
        input_hash = output_fingerprint(base_hash, kind, duration, selected_gaps)
        cached = find_cached_output(assessment_id, kind, input_hash, duration, selected_gaps)
        if cached and request.form.get("regenerate") != "yes":
            flash("Showing the cached suggestion for these finalized results.", "success")
        else:
            output = generate_teaching_output(kind, payload, gaps, current_app.config, duration=duration, selected_gaps=selected_gaps)
            if cached is None:
                cached = TeachingInsight(assessment_id=assessment_id, kind=kind, input_hash=input_hash,
                                         duration_minutes=duration, selected_gaps=selected_gaps, output=output)
                db.session.add(cached)
            else:
                cached.output = output
                cached.teacher_saved = False
            db.session.commit()
            flash("AI-generated suggestion created. Review it before use.", "success")
    except TeachingAIUnavailable as error:
        flash(str(error), "error")
    except (TeachingAIError, ValueError) as error:
        db.session.rollback()
        flash(str(error), "error")
    except SQLAlchemyError:
        db.session.rollback()
        flash("AI teaching-suggestion storage is not installed yet. Apply migration 20261101_0003 after backup and approval.", "error")
    return redirect(url_for("analytics.index", assessment_id=assessment_id))


@analytics_bp.route("/ai/<int:insight_id>/save", methods=["POST"])
def save_ai_output(insight_id: int):
    insight = db.get_or_404(TeachingInsight, insight_id)
    try:
        output = json.loads(request.form.get("output_json", ""))
        insight.output = validate_generated_output(insight.kind, output)
        insight.teacher_saved = True
        db.session.commit()
        flash("Edited suggestion saved for this assessment.", "success")
    except (json.JSONDecodeError, TeachingAIError) as error:
        db.session.rollback()
        flash(f"The suggestion was not saved: {error}", "error")
    return redirect(url_for("analytics.index", assessment_id=insight.assessment_id))


@analytics_bp.route("/resources", methods=["POST"])
def add_resource():
    try:
        create_learning_resource(request.form)
        flash("Learning resource saved.", "success")
    except ValueError as error:
        flash(str(error), "error")
    return redirect(url_for("analytics.index"))
