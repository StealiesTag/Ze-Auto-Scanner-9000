from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from models import Assessment
from models import LearningResource
from services.recommendation_service import create_learning_resource
from services.analytics_service import build_analytics
from extensions import db
from sqlalchemy import select

analytics_bp = Blueprint("analytics", __name__, url_prefix="/analytics")


@analytics_bp.route("")
def index():
    raw_id = request.args.get("assessment_id", "")
    assessment_id = int(raw_id) if raw_id.isdigit() else None
    assessments = db.session.scalars(select(Assessment).order_by(Assessment.title)).all()
    resources = db.session.scalars(select(LearningResource).order_by(LearningResource.learning_objective, LearningResource.title)).all()
    analytics = build_analytics(assessment_id=assessment_id,
                                recommendation_threshold=current_app.config["RECOMMENDATION_ERROR_THRESHOLD"])
    return render_template("analytics.html", analytics=analytics, assessments=assessments,
                           resources=resources, selected_assessment=assessment_id)


@analytics_bp.route("/resources", methods=["POST"])
def add_resource():
    try:
        create_learning_resource(request.form)
        flash("Learning resource saved.", "success")
    except ValueError as error:
        flash(str(error), "error")
    return redirect(url_for("analytics.index"))
