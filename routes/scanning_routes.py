from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from sqlalchemy import or_, select

from extensions import db
from models import AnswerCorrection, Assessment, Question, Response, ScanDraft, Student, Submission
from services.grading_service import apply_manual_correction
from services.image_processor import ImageProcessingError
from services.paparazzi_v3_submission_service import confirm_v3_scan_draft, create_v3_scan_draft

scanning_bp = Blueprint("scanning", __name__, url_prefix="/scanning")


def _upload_context(selection):
    pending = db.session.scalars(
        select(Submission).where(Submission.review_status == "required").order_by(Submission.submitted_at.desc())
    ).all()
    assessments = db.session.scalars(
        select(Assessment).where(Assessment.total_questions.in_((20, 50))).order_by(Assessment.title)
    ).all()
    students = db.session.scalars(select(Student).order_by(Student.name)).all()
    drafts = db.session.scalars(select(ScanDraft).order_by(ScanDraft.created_at.desc())).all()
    return {"pending": pending, "drafts": drafts, "assessments": assessments, "students": students, "selection": selection}


@scanning_bp.route("/upload", methods=["GET", "POST"])
def upload_page():
    if request.method == "POST":
        uploads = [upload for upload in request.files.getlist("answer_sheet") if upload and upload.filename]
        if not uploads:
            flash("Choose a JPEG or PNG answer sheet.", "error")
        else:
            assessment_id = request.form.get("assessment_id", type=int)
            assessment = db.session.get(Assessment, assessment_id) if assessment_id else None
            if assessment is None:
                flash("Select the assessment whose answer key matches this sheet.", "error")
            elif assessment.answer_sheet_version != "V4":
                flash("This assessment is not configured for an official answer sheet. Edit and save it before scanning.", "error")
            elif assessment.total_questions not in {20, 50}:
                flash("Choose an assessment with exactly 20 or 50 questions for an official sheet.", "error")
            else:
                question_count = assessment.total_questions
                selected_student_id = request.form.get("student_id", type=int)
                if len(uploads) == 1:
                    try:
                        draft = create_v3_scan_draft(
                            uploads[0], current_app.config, assessment, question_count, selected_student_id
                        )
                        flash("Sheet detected. Review the student ID and every answer before grading.", "success")
                        return redirect(url_for("scanning.review_v3_draft", draft_id=draft.id))
                    except (ImageProcessingError, ValueError, OSError) as error:
                        flash(str(error), "error")
                else:
                    batch_results = []
                    for upload in uploads:
                        try:
                            draft = create_v3_scan_draft(
                                upload, current_app.config, assessment, question_count, selected_student_id
                            )
                            batch_results.append({
                                "filename": upload.filename,
                                "url": url_for("scanning.review_v3_draft", draft_id=draft.id),
                                "action": "Review detected sheet",
                            })
                        except (ImageProcessingError, ValueError, OSError) as error:
                            batch_results.append({"filename": upload.filename, "error": str(error)})
                    return render_template(
                        "upload.html", **_upload_context(request.form), batch_results=batch_results
                    )
    return render_template("upload.html", **_upload_context(request.form if request.method == "POST" else {}))

@scanning_bp.route("/submissions/<int:submission_id>")
def review_submission(submission_id: int):
    submission = db.get_or_404(Submission, submission_id)
    responses = db.session.scalars(
        select(Response).where(Response.submission_id == submission.id)
        .join(Question, Response.question_id == Question.id).order_by(Question.question_number)
    ).all()
    return render_template("submission_review.html", submission=submission, responses=responses)


@scanning_bp.route("/v3-drafts/<int:draft_id>")
def review_v3_draft(draft_id: int):
    draft = db.get_or_404(ScanDraft, draft_id)
    assessment = draft.assessment
    detected_student = None
    if draft.detected_student_number and draft.student_id_detection.get("status") == "detected":
        detected_student = db.session.scalar(
            select(Student).where(Student.student_number == draft.detected_student_number)
        )
    selected_student_id = detected_student.id if detected_student else draft.student_id
    extra_student_ids = [student_id for student_id in (selected_student_id, draft.student_id) if student_id]
    student_filter = Student.class_section == assessment.class_section
    if extra_student_ids:
        student_filter = or_(student_filter, Student.id.in_(set(extra_student_ids)))
    students = db.session.scalars(select(Student).where(student_filter).order_by(Student.name)).all()
    return render_template(
        "v3_draft_review.html", draft=draft, students=students,
        detected_student=detected_student, selected_student_id=selected_student_id,
        student_class_mismatch=bool(detected_student and detected_student.class_section != assessment.class_section),
    )


@scanning_bp.route("/v3-drafts/<int:draft_id>/images/<kind>")
def v3_draft_image(draft_id: int, kind: str):
    draft = db.get_or_404(ScanDraft, draft_id)
    field = {"original": draft.original_image, "aligned": draft.aligned_image, "debug": draft.debug_image}.get(kind)
    if not field:
        abort(404)
    upload_folder = Path(current_app.config["UPLOAD_FOLDER"]).resolve()
    image_path = (upload_folder / field).resolve()
    if image_path.parent != upload_folder or not image_path.is_file():
        abort(404)
    return send_file(image_path, mimetype="image/png", conditional=True)


@scanning_bp.route("/v3-drafts/<int:draft_id>/confirm", methods=["POST"])
def confirm_v3_draft(draft_id: int):
    draft = db.get_or_404(ScanDraft, draft_id)
    if request.form.get("confirm_v3") != "yes":
        flash("Confirm that you reviewed the student and final answers before grading.", "error")
        return redirect(url_for("scanning.review_v3_draft", draft_id=draft.id))
    selected_student_id = request.form.get("student_id", type=int)
    student = db.session.get(Student, selected_student_id) if selected_student_id else None
    if student is None:
        flash("Select a registered student before grading this sheet.", "error")
        return redirect(url_for("scanning.review_v3_draft", draft_id=draft.id))
    submitted_answers = request.form.getlist("answer")
    if len(submitted_answers) != draft.question_count or any(not answer for answer in submitted_answers):
        flash("Choose a final answer or explicitly confirm blank for every question.", "error")
        return redirect(url_for("scanning.review_v3_draft", draft_id=draft.id))
    final_answers = [None if answer == "__blank__" else answer for answer in submitted_answers]
    try:
        submission = confirm_v3_scan_draft(draft, student, final_answers)
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("scanning.review_v3_draft", draft_id=draft.id))
    flash("Reviewed answers graded and saved.", "success")
    return redirect(url_for("scanning.review_submission", submission_id=submission.id))


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
    if selected not in {"", "A", "B", "C", "D", "E"}:
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
