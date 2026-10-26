from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from sqlalchemy import select

from extensions import db
from models import AnswerCorrection, Question, Response, Submission
from services.grading_service import apply_manual_correction
from services.image_processor import ImageProcessingError
from services.submission_service import DuplicateSubmissionError, process_answer_sheet

scanning_bp = Blueprint("scanning", __name__, url_prefix="/scanning")


@scanning_bp.route("/upload", methods=["GET", "POST"])
def upload_page():
    if request.method == "POST":
        upload = request.files.get("answer_sheet")
        if upload is None or not upload.filename:
            flash("Choose a JPEG or PNG answer sheet.", "error")
        else:
            try:
                submission = process_answer_sheet(upload, current_app.config)
                flash("Sheet processed and saved. Review the responses below.", "success")
                return redirect(url_for("scanning.review_submission", submission_id=submission.id))
            except DuplicateSubmissionError as error:
                flash(str(error), "error")
                return redirect(url_for("scanning.review_submission", submission_id=error.submission_id))
            except (ImageProcessingError, ValueError, OSError) as error:
                flash(str(error), "error")
    pending = db.session.scalars(
        select(Submission).where(Submission.review_status == "required").order_by(Submission.submitted_at.desc())
    ).all()
    return render_template("upload.html", pending=pending)


@scanning_bp.route("/submissions/<int:submission_id>")
def review_submission(submission_id: int):
    submission = db.get_or_404(Submission, submission_id)
    responses = db.session.scalars(
        select(Response).where(Response.submission_id == submission.id)
        .join(Question, Response.question_id == Question.id).order_by(Question.question_number)
    ).all()
    return render_template("submission_review.html", submission=submission, responses=responses)


@scanning_bp.route("/submissions/<int:submission_id>/images/<kind>")
def submission_image(submission_id: int, kind: str):
    submission = db.get_or_404(Submission, submission_id)
    field = {"original": submission.original_image, "aligned": submission.aligned_image, "debug": submission.debug_image}.get(kind)
    if not field:
        abort(404)
    upload_folder = Path(current_app.config["UPLOAD_FOLDER"]).resolve()
    image_path = (upload_folder / field).resolve()
    if image_path.parent != upload_folder or not image_path.is_file():
        abort(404)
    return send_file(image_path, mimetype="image/png", conditional=True)


@scanning_bp.route("/submissions/<int:submission_id>/responses/<int:response_id>/correct", methods=["POST"])
def correct_response(submission_id: int, response_id: int):
    submission = db.get_or_404(Submission, submission_id)
    response = db.session.scalar(
        select(Response).where(Response.id == response_id, Response.submission_id == submission.id)
    )
    if response is None:
        abort(404)
    selected = request.form.get("answer", "")
    if selected not in {"", "A", "B", "C", "D"}:
        flash("Choose a valid answer or mark the response blank.", "error")
        return redirect(url_for("scanning.review_submission", submission_id=submission.id))
    corrected_answer = selected or None
    previous_answer = apply_manual_correction(submission, response, corrected_answer)
    db.session.add(AnswerCorrection(
        response=response,
        previous_answer=previous_answer,
        corrected_answer=corrected_answer,
        note=request.form.get("note", "").strip()[:500],
    ))
    db.session.commit()
    flash(f"Question {response.question.question_number} correction saved and score recalculated.", "success")
    return redirect(url_for("scanning.review_submission", submission_id=submission.id))


@scanning_bp.route("/submissions/<int:submission_id>/finalize", methods=["POST"])
def finalize_review(submission_id: int):
    submission = db.get_or_404(Submission, submission_id)
    if request.form.get("confirm_finalization") != "yes":
        flash("Confirm that unresolved responses should remain ungraded before finalizing.", "error")
        return redirect(url_for("scanning.review_submission", submission_id=submission.id))
    submission.review_status = "complete"
    db.session.commit()
    flash("Review finalized. Unresolved answers remain excluded from response-accuracy analytics.", "success")
    return redirect(url_for("scanning.review_submission", submission_id=submission.id))
