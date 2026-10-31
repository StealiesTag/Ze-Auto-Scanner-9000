from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import select

from extensions import db
from models import Assessment
from services.assessment_service import create_assessment, parse_assessment_form, update_assessment

assessment_bp = Blueprint("assessment", __name__, url_prefix="/assessments")


@assessment_bp.route("")
def list_assessments():
    assessments = db.session.scalars(select(Assessment).order_by(Assessment.created_at.desc())).all()
    return render_template("assessments.html", assessments=assessments)


@assessment_bp.route("/new", methods=["GET", "POST"])
def new_assessment():
    if request.method == "POST":
        try:
            assessment = create_assessment(parse_assessment_form(request.form))
            flash("Assessment created.", "success")
            return redirect(url_for("assessment.assessment_detail", assessment_id=assessment.id))
        except ValueError as error:
            flash(str(error), "error")
    return render_template("assessment_form.html", assessment=None)


@assessment_bp.route("/<int:assessment_id>")
def assessment_detail(assessment_id: int):
    assessment = db.get_or_404(Assessment, assessment_id)
    return render_template("assessment_detail.html", assessment=assessment, submissions=assessment.submissions)


@assessment_bp.route("/<int:assessment_id>/edit", methods=["GET", "POST"])
def edit_assessment(assessment_id: int):
    assessment = db.get_or_404(Assessment, assessment_id)
    if request.method == "POST":
        try:
            update_assessment(assessment, parse_assessment_form(request.form))
            flash("Assessment and answer key updated.", "success")
            return redirect(url_for("assessment.assessment_detail", assessment_id=assessment.id))
        except ValueError as error:
            flash(str(error), "error")
    return render_template("assessment_form.html", assessment=assessment)
