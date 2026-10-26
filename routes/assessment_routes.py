from io import BytesIO

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from sqlalchemy import select

from extensions import db
from models import Assessment, Student
from services.assessment_service import create_assessment, parse_assessment_form, update_assessment
from services.sheet_generator import generate_answer_sheet_pdf

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
    students = db.session.scalars(select(Student).where(Student.class_section == assessment.class_section).order_by(Student.name)).all()
    return render_template("assessment_detail.html", assessment=assessment, students=students,
                           submissions=assessment.submissions)


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


@assessment_bp.route("/<int:assessment_id>/sheets/<int:student_id>.pdf")
def answer_sheet(assessment_id: int, student_id: int):
    assessment = db.get_or_404(Assessment, assessment_id)
    student = db.get_or_404(Student, student_id)
    if student.class_section != assessment.class_section:
        abort(400, "The selected student is not assigned to this assessment's class section.")
    output = BytesIO()
    try:
        generate_answer_sheet_pdf(student, assessment, output)
    except ValueError as error:
        abort(400, str(error))
    output.seek(0)
    return send_file(output, mimetype="application/pdf", as_attachment=True,
                     download_name=f"assessment-{assessment.id}-student-{student.id}.pdf")
