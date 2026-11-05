from __future__ import annotations

import ipaddress

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from extensions import db
from models import (
    Assessment, CanvasAssessmentMapping, CanvasConnection, CanvasGradeSync,
    CanvasStudentMapping, Student,
)
from services.canvas_api import CanvasAPIError
from services.canvas_crypto import TokenEncryptionError
from services.canvas_grades import (
    build_grade_preview, connect_canvas, get_canvas_client, list_assignments,
    map_assessment_assignment, map_assessment_course, retry_failed_grade_sync,
    synchronize_selected_grades, teacher_courses,
)

canvas_bp = Blueprint("canvas", __name__, url_prefix="/canvas")


def _local_teacher_only():
    try:
        if not ipaddress.ip_address(request.remote_addr or "").is_loopback:
            abort(403)
    except ValueError:
        abort(403)


def _connected_client():
    _local_teacher_only()
    return get_canvas_client(current_app.config)


@canvas_bp.route("")
def settings():
    _local_teacher_only()
    connection = db.session.get(CanvasConnection, 1)
    assessments = db.session.scalars(select(Assessment).order_by(Assessment.title)).all()
    mappings = {item.assessment_id: item for item in db.session.scalars(select(CanvasAssessmentMapping)).all()}
    history = db.session.scalars(select(CanvasGradeSync).order_by(CanvasGradeSync.created_at.desc()).limit(15)).all()
    return render_template("canvas_settings.html", connection=connection, assessments=assessments,
                           mappings=mappings, history=history,
                           encryption_available=bool(current_app.config.get("CANVAS_TOKEN_ENCRYPTION_KEY")) or __import__("os").name == "nt")


@canvas_bp.route("/connect", methods=["POST"])
def connect():
    _local_teacher_only()
    try:
        connect_canvas(request.form.get("base_url", ""), request.form.get("access_token", ""), current_app.config)
        flash("Canvas connection verified and the token was stored encrypted on this server.", "success")
    except (ValueError, CanvasAPIError, TokenEncryptionError) as error:
        db.session.rollback()
        flash(str(error), "error")
    except SQLAlchemyError:
        db.session.rollback()
        flash("Canvas connection could not be stored. Check the database migration through 20261105_0008.", "error")
    return redirect(url_for("canvas.settings"))


@canvas_bp.route("/disconnect", methods=["POST"])
def disconnect():
    _local_teacher_only()
    connection = db.session.get(CanvasConnection, 1)
    if connection:
        db.session.delete(connection)
        db.session.commit()
    flash("Canvas token removed from the server. Grade history is preserved.", "success")
    return redirect(url_for("canvas.settings"))


@canvas_bp.route("/assessments/<int:assessment_id>")
def assessment_setup(assessment_id: int):
    _local_teacher_only()
    assessment = db.get_or_404(Assessment, assessment_id)
    mapping = db.session.scalar(select(CanvasAssessmentMapping).where(CanvasAssessmentMapping.assessment_id == assessment_id))
    courses, assignments = [], []
    api_error = None
    if db.session.get(CanvasConnection, 1):
        try:
            client = _connected_client()
            courses = teacher_courses(client)
            if mapping:
                assignments = list_assignments(client, mapping.course_id)
        except (CanvasAPIError, TokenEncryptionError) as error:
            api_error = str(error)
    return render_template("canvas_assessment.html", assessment=assessment, mapping=mapping,
                           courses=courses, assignments=assignments, api_error=api_error)


@canvas_bp.route("/assessments/<int:assessment_id>/course", methods=["POST"])
def select_course(assessment_id: int):
    _local_teacher_only()
    db.get_or_404(Assessment, assessment_id)
    try:
        client = _connected_client()
        course_id = str(request.form.get("course_id", ""))
        course = next((item for item in teacher_courses(client) if str(item.get("id")) == course_id), None)
        if course is None:
            raise ValueError("Choose an active course available to this Canvas teacher account.")
        map_assessment_course(assessment_id, course)
        flash("Canvas course linked to this assessment. Select an ordinary graded assignment next.", "success")
    except (ValueError, CanvasAPIError, TokenEncryptionError) as error:
        db.session.rollback()
        flash(str(error), "error")
    return redirect(url_for("canvas.assessment_setup", assessment_id=assessment_id))


@canvas_bp.route("/assessments/<int:assessment_id>/assignment", methods=["POST"])
def select_assignment(assessment_id: int):
    _local_teacher_only()
    mapping = db.session.scalar(select(CanvasAssessmentMapping).where(CanvasAssessmentMapping.assessment_id == assessment_id))
    if mapping is None:
        abort(404)
    try:
        client = _connected_client()
        assignment_id = str(request.form.get("assignment_id", ""))
        assignment = next((item for item in list_assignments(client, mapping.course_id) if str(item.get("id")) == assignment_id), None)
        if assignment is None:
            raise ValueError("Choose an assignment from the selected Canvas course.")
        map_assessment_assignment(mapping, assignment)
        flash("Canvas assignment linked. Review student matches before uploading grades.", "success")
    except (ValueError, CanvasAPIError, TokenEncryptionError) as error:
        db.session.rollback()
        flash(str(error), "error")
    return redirect(url_for("canvas.assessment_setup", assessment_id=assessment_id))


@canvas_bp.route("/assessments/<int:assessment_id>/roster")
def roster(assessment_id: int):
    _local_teacher_only()
    assessment = db.get_or_404(Assessment, assessment_id)
    mapping = db.session.scalar(select(CanvasAssessmentMapping).where(CanvasAssessmentMapping.assessment_id == assessment_id))
    if mapping is None:
        flash("Select a Canvas course before matching students.", "error")
        return redirect(url_for("canvas.assessment_setup", assessment_id=assessment_id))
    try:
        client = _connected_client()
        canvas_users = client.roster(mapping.course_id)
        paparazzi_students = db.session.scalars(select(Student).where(Student.class_section == assessment.class_section).order_by(Student.student_number)).all()
        existing = {item.student_number: item for item in db.session.scalars(select(CanvasStudentMapping).where(CanvasStudentMapping.course_id == mapping.course_id)).all()}
        return render_template("canvas_roster.html", assessment=assessment, mapping=mapping,
                               canvas_users=canvas_users, students=paparazzi_students, existing=existing)
    except (CanvasAPIError, TokenEncryptionError) as error:
        flash(str(error), "error")
        return redirect(url_for("canvas.assessment_setup", assessment_id=assessment_id))


@canvas_bp.route("/assessments/<int:assessment_id>/roster/map", methods=["POST"])
def map_student(assessment_id: int):
    _local_teacher_only()
    assessment = db.get_or_404(Assessment, assessment_id)
    mapping = db.session.scalar(select(CanvasAssessmentMapping).where(CanvasAssessmentMapping.assessment_id == assessment_id))
    if mapping is None:
        abort(404)
    number = str(request.form.get("student_number", ""))
    canvas_user_id = str(request.form.get("canvas_user_id", ""))
    student = db.session.scalar(select(Student).where(Student.student_number == number, Student.class_section == assessment.class_section))
    if student is None:
        flash("That Paparazzi student ID is not in this assessment's class.", "error")
        return redirect(url_for("canvas.roster", assessment_id=assessment_id))
    try:
        client = _connected_client()
        roster = client.roster(mapping.course_id)
        user = next((item for item in roster if str(item.get("id")) == canvas_user_id), None)
        if user is None:
            raise ValueError("Choose a student from the selected Canvas course roster.")
        duplicate = db.session.scalar(select(CanvasStudentMapping).where(
            CanvasStudentMapping.course_id == mapping.course_id,
            CanvasStudentMapping.canvas_user_id == canvas_user_id,
            CanvasStudentMapping.student_number != number,
        ))
        if duplicate:
            raise ValueError("That Canvas student is already mapped to a different Paparazzi ID in this course.")
        row = db.session.scalar(select(CanvasStudentMapping).where(
            CanvasStudentMapping.course_id == mapping.course_id,
            CanvasStudentMapping.student_number == number,
        ))
        sis = next((str(user.get(key)).strip() for key in ("sis_user_id", "sis_login_id") if user.get(key)), None)
        if row is None:
            row = CanvasStudentMapping(course_id=mapping.course_id, student_number=number, canvas_user_id=canvas_user_id)
            db.session.add(row)
        row.canvas_user_id = canvas_user_id
        row.sis_identifier = sis
        row.verified_by = "local teacher"
        db.session.commit()
        flash(f"Paparazzi ID {number} mapped to a verified Canvas roster member.", "success")
    except (ValueError, CanvasAPIError, TokenEncryptionError) as error:
        db.session.rollback()
        flash(str(error), "error")
    except IntegrityError:
        db.session.rollback()
        flash("This Canvas student is already mapped to another Paparazzi ID in this course.", "error")
    return redirect(url_for("canvas.roster", assessment_id=assessment_id))


@canvas_bp.route("/assessments/<int:assessment_id>/preview")
def grade_preview(assessment_id: int):
    _local_teacher_only()
    try:
        preview = build_grade_preview(assessment_id, _connected_client())
        history = db.session.scalars(select(CanvasGradeSync).where(CanvasGradeSync.assessment_id == assessment_id).order_by(CanvasGradeSync.created_at.desc()).limit(30)).all()
        return render_template("canvas_preview.html", preview=preview, history=history)
    except (ValueError, CanvasAPIError, TokenEncryptionError) as error:
        flash(str(error), "error")
        return redirect(url_for("canvas.assessment_setup", assessment_id=assessment_id))


@canvas_bp.route("/assessments/<int:assessment_id>/submit", methods=["POST"])
def submit_grades(assessment_id: int):
    _local_teacher_only()
    if request.form.get("confirm_upload") != "yes":
        flash("Confirm that the preview is correct before sending grades.", "error")
        return redirect(url_for("canvas.grade_preview", assessment_id=assessment_id))
    selected = set(request.form.getlist("include"))
    overwrite = set(request.form.getlist("overwrite"))
    try:
        result = synchronize_selected_grades(assessment_id, selected, overwrite, current_app.config, client=_connected_client())
        succeeded = sum(item["status"] == "succeeded" for item in result)
        failed = sum(item["status"] == "failed" for item in result)
        flash(f"Canvas confirmed {succeeded} grade update(s); {failed} failed. Review the per-student sync results below.", "success" if failed == 0 else "error")
    except (ValueError, CanvasAPIError, TokenEncryptionError) as error:
        db.session.rollback()
        flash(str(error), "error")
    return redirect(url_for("canvas.grade_preview", assessment_id=assessment_id))


@canvas_bp.route("/sync/<int:sync_id>/retry", methods=["POST"])
def retry_sync(sync_id: int):
    _local_teacher_only()
    sync = db.get_or_404(CanvasGradeSync, sync_id)
    try:
        result = retry_failed_grade_sync(sync_id, current_app.config, client=_connected_client())
        flash("Canvas confirmed the retried grade." if result["status"] == "succeeded" else result["reason"],
              "success" if result["status"] == "succeeded" else "error")
    except (ValueError, CanvasAPIError, TokenEncryptionError) as error:
        db.session.rollback()
        flash(str(error), "error")
    return redirect(url_for("canvas.grade_preview", assessment_id=sync.assessment_id))


@canvas_bp.route("/history")
def sync_history():
    _local_teacher_only()
    history = db.session.scalars(select(CanvasGradeSync).order_by(CanvasGradeSync.created_at.desc()).limit(500)).all()
    return render_template("canvas_history.html", history=history)
