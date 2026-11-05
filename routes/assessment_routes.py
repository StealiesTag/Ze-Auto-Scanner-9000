from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from sqlalchemy import select

from extensions import db
from models import Assessment
from services.assessment_service import create_assessment, parse_assessment_form, update_assessment
from services.assessment_pdf_service import attach_assessment_pdf, read_assessment_pdf
from services.analytics_service import build_analytics

assessment_bp = Blueprint("assessment", __name__, url_prefix="/assessments")


@assessment_bp.route("")
def list_assessments():
    assessments = db.session.scalars(select(Assessment).order_by(Assessment.created_at.desc())).all()
    return render_template("assessments.html", assessments=assessments)


@assessment_bp.route("/new", methods=["GET", "POST"])
def new_assessment():
    if request.method == "POST":
        try:
            source_pdf = _uploaded_source_pdf()
            if source_pdf is None:
                raise ValueError("Attach the original test PDF so teaching suggestions can interpret missed questions.")
            assessment = create_assessment(parse_assessment_form(request.form))
            if source_pdf:
                attach_assessment_pdf(assessment, source_pdf, current_app.config["UPLOAD_FOLDER"])
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
            source_pdf = _uploaded_source_pdf()
            update_assessment(assessment, parse_assessment_form(request.form))
            if source_pdf:
                attach_assessment_pdf(assessment, source_pdf, current_app.config["UPLOAD_FOLDER"])
            flash("Assessment and answer key updated.", "success")
            return redirect(url_for("assessment.assessment_detail", assessment_id=assessment.id))
        except ValueError as error:
            flash(str(error), "error")
    return render_template("assessment_form.html", assessment=assessment)


def _uploaded_source_pdf() -> dict | None:
    upload = request.files.get("source_pdf")
    if upload is None or not upload.filename:
        return None
    limit = min(
        int(current_app.config.get("MAX_ASSESSMENT_PDF_BYTES", 12 * 1024 * 1024)),
        int(current_app.config.get("MAX_CONTENT_LENGTH", 16 * 1024 * 1024)),
    )
    return read_assessment_pdf(upload, limit)


@assessment_bp.route("/<int:assessment_id>/source-pdf")
def source_pdf(assessment_id: int):
    assessment = db.get_or_404(Assessment, assessment_id)
    if not assessment.source_pdf_path:
        abort(404)
    root = Path(current_app.config["UPLOAD_FOLDER"]).resolve()
    path = (root / assessment.source_pdf_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        abort(404)
    return send_file(path, mimetype="application/pdf", as_attachment=False,
                     download_name=assessment.source_pdf_filename or "assessment.pdf")


@assessment_bp.route("/<int:assessment_id>/question-mapping", methods=["GET", "POST"])
def question_mapping(assessment_id: int):
    assessment = db.get_or_404(Assessment, assessment_id)
    if not assessment.source_pdf_filename:
        flash("Attach an original test PDF before reviewing question mappings.", "error")
        return redirect(url_for("assessment.assessment_detail", assessment_id=assessment_id))
    questions = sorted(assessment.questions, key=lambda question: question.question_number)
    if request.method == "POST":
        ids = request.form.getlist("question_id")
        pages = request.form.getlist("source_pdf_page")
        excerpts = request.form.getlist("source_pdf_excerpt")
        if len(ids) != len(questions) or len(pages) != len(questions) or len(excerpts) != len(questions):
            flash("The question mapping form was incomplete; no changes were saved.", "error")
            return redirect(url_for("assessment.question_mapping", assessment_id=assessment_id))
        try:
            updates = {}
            for question_id, page_text, excerpt in zip(ids, pages, excerpts):
                question_id = int(question_id)
                page_number = int(page_text) if page_text.strip() else None
                excerpt = excerpt.strip()
                if len(excerpt) > 3000:
                    raise ValueError("Mapped question text must be 3,000 characters or fewer.")
                if page_number is not None and not 1 <= page_number <= int(assessment.source_pdf_page_count or 0):
                    raise ValueError("Each source page must be within the uploaded PDF's page range.")
                updates[question_id] = (page_number, excerpt or None)
            if set(updates) != {question.id for question in questions}:
                raise ValueError("The submitted question IDs do not match this assessment.")
            for question in questions:
                question.source_pdf_page, question.source_pdf_excerpt = updates[question.id]
                question.source_pdf_mapping_reviewed = True
            db.session.commit()
            flash("Question-to-PDF mappings saved. Future AI suggestions will use these reviewed excerpts.", "success")
            return redirect(url_for("assessment.question_mapping", assessment_id=assessment_id))
        except (ValueError, TypeError) as error:
            db.session.rollback()
            flash(str(error), "error")
    analytics = build_analytics(assessment_id=assessment_id)
    stats = {row["question_number"]: row for row in analytics["questions"]}
    return render_template("question_pdf_mapping.html", assessment=assessment, questions=questions, stats=stats)
