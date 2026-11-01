from hmac import compare_digest

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import Assessment, CameraIngestionState, CaptureJob, ScannerControl
from services.capture_queue import (
    QueueFullError,
    ensure_queue_capacity,
    get_queue_counts,
    get_scanner_control,
    queue_image_bytes,
    recent_capture_jobs,
    retry_failed_job,
)
from services.esp32_camera import CameraError, ESP32Camera


automatic_scanning_bp = Blueprint("automatic_scanning", __name__, url_prefix="/scanning/automatic")


def _assessment_ready(assessment_id: int) -> Assessment | None:
    assessment = db.session.get(Assessment, assessment_id)
    if assessment is None or assessment.total_questions not in {20, 50, 100}:
        return None
    questions = sorted(assessment.questions, key=lambda question: question.question_number)
    if len(questions) != assessment.total_questions or any(not question.option_e.strip() for question in questions):
        return None
    return assessment


def _job_payload(job: CaptureJob) -> dict:
    draft = job.draft
    submission = job.submission
    student_number = draft.detected_student_number if draft else submission.student.student_number if submission else None
    return {
        "id": job.id,
        "capture_id": job.capture_id,
        "captured_at": job.captured_at.isoformat() if job.captured_at else None,
        "status": job.status,
        "source": job.source,
        "assessment": job.assessment.title if job.assessment else None,
        "student_number": student_number,
        "score": f"{submission.score}/{submission.total_points}" if submission else None,
        "error": job.error_message,
        "detection": job.detection_metadata or {},
        "image_url": url_for("camera_api.capture_image", capture_id=job.capture_id),
        "review_url": url_for("scanning.review_v3_draft", draft_id=draft.id) if draft else None,
        "retry_url": url_for("automatic_scanning.retry_capture", job_id=job.id) if job.status == "FAILED" else None,
    }


@automatic_scanning_bp.route("")
def dashboard():
    assessments = db.session.scalars(select(Assessment).order_by(Assessment.title)).all()
    selected_assessment = request.args.get("assessment_id", type=int)
    try:
        control = get_scanner_control()
        poller = db.session.get(CameraIngestionState, current_app.config["PAPARAZZI_CAMERA_ID"])
        jobs = recent_capture_jobs()
        counts = get_queue_counts()
        return render_template(
            "automatic_scanning.html", control=control, jobs=jobs, counts=counts, assessments=assessments,
            selected_assessment=selected_assessment or control.assessment_id,
            stream_url=current_app.config.get("ESP32_STREAM_URL", ""), migration_pending=False,
            poller=poller, camera_url=current_app.config.get("PAPARAZZI_CAMERA_URL", ""),
            camera_id=current_app.config["PAPARAZZI_CAMERA_ID"],
            camera_control_configured=bool(current_app.config.get("CAMERA_CONTROL_TOKEN")),
        )
    except SQLAlchemyError:
        db.session.rollback()
        return render_template(
            "automatic_scanning.html", control=None, jobs=[], counts={}, assessments=assessments,
            selected_assessment=selected_assessment, stream_url=current_app.config.get("ESP32_STREAM_URL", ""),
            migration_pending=True, poller=None, camera_url=current_app.config.get("PAPARAZZI_CAMERA_URL", ""),
            camera_id=current_app.config["PAPARAZZI_CAMERA_ID"],
            camera_control_configured=bool(current_app.config.get("CAMERA_CONTROL_TOKEN")),
        )


@automatic_scanning_bp.route("/status")
def status():
    try:
        control = get_scanner_control()
        return jsonify({
            "ok": True,
            "camera_connected": control.camera_connected,
            "active": control.active,
            "paused": control.paused,
            "state": control.state,
            "sheet_detected": control.sheet_detected,
            "marker_status": control.marker_status,
            "stability_status": control.stability_status,
            "last_error": control.last_error,
            "assessment_id": control.assessment_id,
            "queue": {**get_queue_counts(), "capacity_limit": int(current_app.config["CAPTURE_QUEUE_CAPACITY"])},
            "ingestion": _ingestion_counts(),
            "camera_poller": _camera_poller_status(),
            "recent_captures": [_job_payload(job) for job in recent_capture_jobs(20)],
        })
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"ok": False, "error": "Apply the scanner queue database migration before using automatic scanning."}), 503


def _ingestion_counts() -> dict:
    totals = db.session.execute(select(
        func.coalesce(func.sum(CameraIngestionState.received_count), 0),
        func.coalesce(func.sum(CameraIngestionState.rejected_count), 0),
        func.coalesce(func.sum(CameraIngestionState.accepted_count), 0),
        func.coalesce(func.sum(CameraIngestionState.duplicate_count), 0),
        func.coalesce(func.sum(CameraIngestionState.deferred_count), 0),
        func.coalesce(func.sum(CameraIngestionState.frames_fetched), 0),
        func.max(CameraIngestionState.last_received_at),
    )).one()
    return {
        "received": int(totals[0]), "rejected": int(totals[1]),
        "accepted": int(totals[2]), "duplicates": int(totals[3]),
        "deferred": int(totals[4]), "frames_fetched": int(totals[5]),
        "last_received_at": totals[6].isoformat() if totals[6] else None,
    }


def _camera_poller_status() -> dict:
    state = db.session.get(CameraIngestionState, current_app.config["PAPARAZZI_CAMERA_ID"])
    return {
        "configured": bool(current_app.config.get("PAPARAZZI_CAMERA_URL")),
        "camera_id": current_app.config["PAPARAZZI_CAMERA_ID"],
        "camera_url": current_app.config.get("PAPARAZZI_CAMERA_URL", ""),
        "enabled": bool(state and state.polling_enabled),
        "running": bool(state and state.poller_running),
        "connection_status": state.connection_status if state else "stopped",
        "last_fetch_at": state.last_fetch_at.isoformat() if state and state.last_fetch_at else None,
        "last_error": state.last_error if state else None,
        "frames_fetched": state.frames_fetched if state else 0,
        "accepted": state.accepted_count if state else 0,
        "rejected": state.rejected_count if state else 0,
        "duplicates": state.duplicate_count if state else 0,
        "deferred": state.deferred_count if state else 0,
    }


def _authorize_poller_control() -> None:
    configured_token = current_app.config.get("CAMERA_CONTROL_TOKEN", "")
    provided_token = request.form.get("camera_control_token", "")
    if not configured_token:
        abort(503, description="Set CAMERA_CONTROL_TOKEN to enable camera poller controls.")
    if not compare_digest(configured_token, provided_token):
        abort(403, description="Camera poller control token is invalid.")


def _get_or_create_poller_state() -> CameraIngestionState:
    camera_id = current_app.config["PAPARAZZI_CAMERA_ID"]
    state = db.session.get(CameraIngestionState, camera_id)
    if state is None:
        state = CameraIngestionState(camera_id=camera_id, controller_state={})
        db.session.add(state)
    return state


@automatic_scanning_bp.route("/poller/start", methods=["POST"])
def start_poller():
    _authorize_poller_control()
    if not current_app.config.get("PAPARAZZI_CAMERA_URL"):
        flash("Set PAPARAZZI_CAMERA_URL in the server environment before starting the poller.", "error")
        return redirect(url_for("automatic_scanning.dashboard"))
    try:
        scanner_control = db.session.get(ScannerControl, 1)
        if scanner_control and scanner_control.active and not scanner_control.paused:
            flash("Pause or stop ESP32 stream scanning before starting CameraWebServer polling.", "error")
            return redirect(url_for("automatic_scanning.dashboard"))
        assessment_id = request.form.get("assessment_id", type=int) or (scanner_control.assessment_id if scanner_control else None)
        assessment = _assessment_ready(assessment_id or 0)
        if assessment is None:
            flash("Choose an assessment with a complete 20, 50, or 100 question V3 A-E answer key before starting polling.", "error")
            return redirect(url_for("automatic_scanning.dashboard"))
        if scanner_control is None:
            scanner_control = get_scanner_control()
        scanner_control.assessment_id = assessment.id
        state = _get_or_create_poller_state()
        state.polling_enabled = True
        state.last_error = None
        db.session.commit()
        flash("Camera polling requested. The separate camera-poller process must be running.", "success")
    except SQLAlchemyError:
        db.session.rollback()
        flash("Camera poller storage migration is not installed.", "error")
    return redirect(url_for("automatic_scanning.dashboard"))


@automatic_scanning_bp.route("/poller/stop", methods=["POST"])
def stop_poller():
    _authorize_poller_control()
    try:
        state = db.session.get(CameraIngestionState, current_app.config["PAPARAZZI_CAMERA_ID"])
        if state:
            state.polling_enabled = False
            db.session.commit()
        flash("Camera polling stop requested.", "success")
    except SQLAlchemyError:
        db.session.rollback()
        flash("Camera poller storage migration is not installed.", "error")
    return redirect(url_for("automatic_scanning.dashboard"))


@automatic_scanning_bp.route("/start", methods=["POST"])
def start():
    assessment = _assessment_ready(request.form.get("assessment_id", type=int) or 0)
    if assessment is None:
        flash("Choose an assessment with a complete 20, 50, or 100 question V3 answer key including choice E.", "error")
        return redirect(url_for("automatic_scanning.dashboard"))
    if not current_app.config.get("ESP32_STREAM_URL") or not current_app.config.get("ESP32_CAPTURE_URL"):
        flash("Configure ESP32_STREAM_URL and ESP32_CAPTURE_URL before starting automatic capture.", "error")
        return redirect(url_for("automatic_scanning.dashboard", assessment_id=assessment.id))
    try:
        poller_state = db.session.get(CameraIngestionState, current_app.config["PAPARAZZI_CAMERA_ID"])
        if poller_state and poller_state.polling_enabled:
            flash("Stop CameraWebServer polling before starting the ESP32 stream capture source.", "error")
            return redirect(url_for("automatic_scanning.dashboard", assessment_id=assessment.id))
        control = get_scanner_control()
        if control.active and not control.paused:
            flash("Pause or stop automatic scanning before changing the selected assessment.", "error")
            return redirect(url_for("automatic_scanning.dashboard", assessment_id=control.assessment_id))
        control.assessment_id = assessment.id
        control.active = True
        control.paused = False
        control.last_error = None
        control.state = "WAITING_FOR_FIRST_SHEET"
        db.session.commit()
        flash("Automatic scanning enabled. Ensure the separate scan-worker process is running.", "success")
    except SQLAlchemyError:
        db.session.rollback()
        flash("The scanner queue migration is not installed. Review required migrations through 20261101_0006.", "error")
    return redirect(url_for("automatic_scanning.dashboard", assessment_id=assessment.id))


@automatic_scanning_bp.route("/pause", methods=["POST"])
def pause():
    try:
        control = get_scanner_control()
        if control.active:
            control.paused = True
            db.session.commit()
            flash("Automatic capture paused.", "success")
    except SQLAlchemyError:
        db.session.rollback()
        flash("The scanner queue migration is not installed yet.", "error")
    return redirect(url_for("automatic_scanning.dashboard"))


@automatic_scanning_bp.route("/resume", methods=["POST"])
def resume():
    try:
        control = get_scanner_control()
        if control.active and control.assessment_id:
            control.paused = False
            control.last_error = None
            db.session.commit()
            flash("Automatic capture resumed. The controller will revalidate the current sheet before a retry.", "success")
    except SQLAlchemyError:
        db.session.rollback()
        flash("The scanner queue migration is not installed yet.", "error")
    return redirect(url_for("automatic_scanning.dashboard"))


@automatic_scanning_bp.route("/stop", methods=["POST"])
def stop():
    try:
        control = get_scanner_control()
        control.active = False
        control.paused = False
        db.session.commit()
        flash("Automatic scanning stopped. Queued images will continue processing.", "success")
    except SQLAlchemyError:
        db.session.rollback()
        flash("The scanner queue migration is not installed yet.", "error")
    return redirect(url_for("automatic_scanning.dashboard"))


@automatic_scanning_bp.route("/manual-capture", methods=["POST"])
def manual_capture():
    try:
        control = get_scanner_control()
        poller_state = db.session.get(CameraIngestionState, current_app.config["PAPARAZZI_CAMERA_ID"])
        if poller_state and poller_state.polling_enabled:
            flash("Stop CameraWebServer polling before using manual camera capture.", "error")
            return redirect(url_for("automatic_scanning.dashboard"))
        if control.active and not control.paused:
            flash("Pause automatic scanning before using manual camera capture.", "error")
            return redirect(url_for("automatic_scanning.dashboard"))
        assessment = _assessment_ready(request.form.get("assessment_id", type=int) or control.assessment_id or 0)
        if assessment is None:
            flash("Choose an assessment with a complete V3 answer key first.", "error")
            return redirect(url_for("automatic_scanning.dashboard"))
        camera = ESP32Camera(current_app.config["ESP32_STREAM_URL"], current_app.config["ESP32_CAPTURE_URL"], current_app.config["ESP32_REQUEST_TIMEOUT_SECONDS"])
        ensure_queue_capacity(current_app.config)
        image_bytes, extension = camera.capture_still()
        job = queue_image_bytes(image_bytes, extension, assessment.id, current_app.config, source="manual")
        flash(f"Manual capture {job.capture_id} queued for V3 review.", "success")
    except (CameraError, QueueFullError, ValueError, OSError) as error:
        db.session.rollback()
        flash(str(error), "error")
    except SQLAlchemyError:
        db.session.rollback()
        flash("The scanner queue migration is not installed. Review required migrations through 20261101_0006.", "error")
    return redirect(url_for("automatic_scanning.dashboard"))


@automatic_scanning_bp.route("/<int:job_id>/retry", methods=["POST"])
def retry_capture(job_id: int):
    try:
        retry_failed_job(job_id)
        flash("Failed capture requeued with its original capture identifier; no new image was captured.", "success")
    except ValueError as error:
        flash(str(error), "error")
    except SQLAlchemyError:
        db.session.rollback()
        flash("The scanner queue migration is not installed yet.", "error")
    return redirect(url_for("automatic_scanning.dashboard"))
