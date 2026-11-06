from hmac import compare_digest
from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import (
    Assessment, CameraIngestionState, CanvasAssessmentMapping, CanvasConnection,
    CaptureJob, ScanDraft, ScannerControl, Student,
)
from services.capture_queue import (
    QueueFullError,
    clear_capture_queue,
    delete_capture_job,
    ensure_queue_capacity,
    get_queue_counts,
    get_scanner_control,
    queue_image_bytes,
    recent_capture_jobs,
    retry_failed_job,
)
from services.esp32_camera import CameraError, ESP32Camera
from services.camera_setup import get_camera_setup, save_camera_setup
from services.camera_poller import camera_poller_worker_running
from services.marker_detection import detect_v3_registration_markers
from services.paparazzi_v3_scanner import (
    _bubble_fill_score,
    _profile_mark_centers,
    get_answer_bubble_centers_mm,
    get_student_id_bubble_centers_mm,
    load_official_profile,
    preprocess_v3_image,
)
from services.paparazzi_v3_submission_service import confirm_v3_scan_draft


automatic_scanning_bp = Blueprint("automatic_scanning", __name__, url_prefix="/scanning/automatic")


def _print_step_signature(frame) -> list[int]:
    """Create a position-preserving fallback signature when marker alignment fails."""
    import cv2
    import numpy as np

    if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
        return []
    gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY if frame.shape[2] == 4 else cv2.COLOR_BGR2GRAY)
    thumbnail = cv2.resize(gray, (96, 136), interpolation=cv2.INTER_AREA)
    thumbnail = cv2.equalizeHist(thumbnail)
    signature = cv2.adaptiveThreshold(thumbnail, 1, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 15, 5)
    return signature.ravel().tolist()


def _response_signature(frame, detection: dict, profile: dict, question_count: int) -> list[str] | None:
    """Build a lighting-tolerant signature from the student's actual marks."""
    import cv2
    import numpy as np

    coordinates = detection.get("marker_coordinates") or {}
    names = ("top_left", "top_right", "bottom_right", "bottom_left")
    if not detection.get("geometry_valid") or any(name not in coordinates for name in names):
        return None
    try:
        mark_centers = _profile_mark_centers(profile)
        source = np.asarray([coordinates[name] for name in names], dtype=np.float32)
        paper = profile["paper"]
        scale = 3.0
        target = np.asarray([
            (mark_centers[name.replace("_", "")][0] * scale,
             mark_centers[name.replace("_", "")][1] * scale)
            for name in names
        ], dtype=np.float32)
        transform = cv2.getPerspectiveTransform(source, target)
        aligned = cv2.warpPerspective(
            frame, transform,
            (round(paper["width_mm"] * scale), round(paper["height_mm"] * scale)),
            borderValue=(255, 255, 255),
        )
        gray = preprocess_v3_image(aligned)

        def selected_mark(centers, radius, pitch_x, pitch_y=None):
            values = [
                _bubble_fill_score(gray, center, radius, min(pitch_x, pitch_y or pitch_x))
                for center in centers
            ]
            marked = [index for index, value in enumerate(values) if value >= 0.24]
            return ",".join(map(str, marked)) if marked else "-"

        result = []
        student_id = profile["student_id"]
        if student_id.get("auto_detect_enabled", True):
            for centers in get_student_id_bubble_centers_mm(profile):
                result.append(selected_mark(
                    centers, student_id["bubble_radius_mm"],
                    student_id["column_pitch_mm"], student_id["row_pitch_mm"],
                ))
        answers = profile["answers"]
        for centers in get_answer_bubble_centers_mm(question_count, profile).values():
            result.append(selected_mark(
                centers, answers["bubble_radius_mm"],
                answers["choice_pitch_mm"], answers["row_pitch_mm"],
            ))
        return result
    except (KeyError, TypeError, ValueError, cv2.error):
        return None


def _student_number_from_response(frame, detection: dict, profile: dict) -> str | None:
    """Read an ID only when every digit column has exactly one clear mark."""
    import cv2
    import numpy as np

    section = profile.get("student_id", {})
    coordinates = detection.get("marker_coordinates") or {}
    names = ("top_left", "top_right", "bottom_right", "bottom_left")
    if (not section.get("auto_detect_enabled", True)
            or not detection.get("geometry_valid")
            or any(name not in coordinates for name in names)):
        return None
    try:
        mark_centers = _profile_mark_centers(profile)
        source = np.asarray([coordinates[name] for name in names], dtype=np.float32)
        paper = profile["paper"]
        scale = 3.0
        target = np.asarray([
            (mark_centers[name.replace("_", "")][0] * scale,
             mark_centers[name.replace("_", "")][1] * scale)
            for name in names
        ], dtype=np.float32)
        aligned = cv2.warpPerspective(
            frame, cv2.getPerspectiveTransform(source, target),
            (round(paper["width_mm"] * scale), round(paper["height_mm"] * scale)),
            borderValue=(255, 255, 255),
        )
        gray = preprocess_v3_image(aligned)
        digits = section["digit_values"]
        selected_digits = []
        for centers in get_student_id_bubble_centers_mm(profile):
            values = [
                _bubble_fill_score(
                    gray, center, section["bubble_radius_mm"],
                    min(section["column_pitch_mm"], section["row_pitch_mm"]),
                )
                for center in centers
            ]
            marked = [index for index, value in enumerate(values) if value >= 0.24]
            if len(marked) != 1:
                return None
            selected_digits.append(str(digits[marked[0]]))
        student_number = "".join(selected_digits)
        return student_number if student_number.isdigit() else None
    except (KeyError, TypeError, ValueError, cv2.error):
        return None


def _assessment_ready(assessment_id: int) -> Assessment | None:
    assessment = db.session.get(Assessment, assessment_id)
    if (assessment is None or assessment.total_questions not in {20, 50}
            or assessment.answer_sheet_version != "V4"):
        return None
    questions = sorted(assessment.questions, key=lambda question: question.question_number)
    if (len(questions) != assessment.total_questions
            or [question.question_number for question in questions] != list(range(1, assessment.total_questions + 1))
            or any(question.correct_answer not in {"A", "B", "C", "D", "E"} for question in questions)):
        return None
    return assessment


def _automatic_drafts(assessment_id: int) -> list[ScanDraft]:
    return db.session.scalars(
        select(ScanDraft)
        .join(CaptureJob, CaptureJob.draft_id == ScanDraft.id)
        .where(
            CaptureJob.assessment_id == assessment_id,
            CaptureJob.source == "esp32",
            CaptureJob.status == "NEEDS_REVIEW",
        )
        .order_by(CaptureJob.captured_at, CaptureJob.id)
    ).all()


def _all_automatic_drafts() -> list[ScanDraft]:
    return db.session.scalars(
        select(ScanDraft)
        .join(CaptureJob, CaptureJob.draft_id == ScanDraft.id)
        .where(CaptureJob.source == "esp32", CaptureJob.status == "NEEDS_REVIEW")
        .order_by(CaptureJob.captured_at, CaptureJob.id)
    ).all()


def _batch_review_rows(drafts: list[ScanDraft]) -> list[dict]:
    rows = []
    for draft in drafts:
        detected_student = None
        if draft.detected_student_number and draft.student_id_detection.get("status") == "detected":
            detected_student = db.session.scalar(
                select(Student).where(Student.student_number == draft.detected_student_number)
            )
        extra_ids = {value for value in (draft.student_id, detected_student.id if detected_student else None) if value}
        student_filter = Student.class_section == draft.assessment.class_section
        if extra_ids:
            student_filter = or_(student_filter, Student.id.in_(extra_ids))
        students = db.session.scalars(select(Student).where(student_filter).order_by(Student.name)).all()
        rows.append({
            "draft": draft,
            "students": students,
            "detected_student": detected_student,
            "selected_student_id": detected_student.id if detected_student else draft.student_id,
        })
    return rows


def _camera_id() -> str:
    return get_camera_setup(current_app.config)["camera_id"]


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
        "delete_url": url_for("automatic_scanning.delete_capture", job_id=job.id)
        if job.status not in {"QUEUED", "PROCESSING"} else None,
    }


@automatic_scanning_bp.route("")
def dashboard():
    assessments = db.session.scalars(
        select(Assessment).where(Assessment.total_questions.in_((20, 50))).order_by(Assessment.title)
    ).all()
    selected_assessment = request.args.get("assessment_id", type=int)
    try:
        control = get_scanner_control()
        camera_setup = get_camera_setup(current_app.config)
        poller = db.session.get(CameraIngestionState, camera_setup["camera_id"])
        jobs = recent_capture_jobs()
        counts = get_queue_counts()
        current_assessment_id = selected_assessment or control.assessment_id
        pending_batch_count = 0
        if current_assessment_id:
            pending_batch_count = db.session.scalar(
                select(func.count(ScanDraft.id))
                .join(CaptureJob, CaptureJob.draft_id == ScanDraft.id)
                .where(
                    CaptureJob.assessment_id == current_assessment_id,
                    CaptureJob.source == "esp32",
                    CaptureJob.status == "NEEDS_REVIEW",
                )
            ) or 0
        all_pending_batch_count = db.session.scalar(
            select(func.count(ScanDraft.id))
            .join(CaptureJob, CaptureJob.draft_id == ScanDraft.id)
            .where(CaptureJob.source == "esp32", CaptureJob.status == "NEEDS_REVIEW")
        ) or 0
        return render_template(
            "automatic_scanning.html", control=control, jobs=jobs, counts=counts, assessments=assessments,
            selected_assessment=current_assessment_id, pending_batch_count=pending_batch_count,
            all_pending_batch_count=all_pending_batch_count,
            session_progress=_session_progress(control),
            stream_url=camera_setup["stream_url"], migration_pending=False,
            camera_setup=camera_setup,
            poller=poller, camera_url=current_app.config.get("PAPARAZZI_CAMERA_URL", ""),
            camera_id=camera_setup["camera_id"],
            camera_control_configured=bool(current_app.config.get("CAMERA_CONTROL_TOKEN")),
            feed_job=request.args.get("feed_job") == "1",
        )
    except SQLAlchemyError:
        db.session.rollback()
        return render_template(
            "automatic_scanning.html", control=None, jobs=[], counts={}, assessments=assessments,
            selected_assessment=selected_assessment, stream_url=current_app.config.get("ESP32_STREAM_URL", ""),
            all_pending_batch_count=0,
            migration_pending=True, poller=None, camera_url=current_app.config.get("PAPARAZZI_CAMERA_URL", ""),
            camera_id=current_app.config["PAPARAZZI_CAMERA_ID"],
            camera_control_configured=bool(current_app.config.get("CAMERA_CONTROL_TOKEN")),
            feed_job=False, camera_setup={"camera_id": current_app.config.get("PAPARAZZI_CAMERA_ID", ""),
                "stream_url": current_app.config.get("ESP32_STREAM_URL", ""),
                "capture_url": current_app.config.get("ESP32_CAPTURE_URL", ""),
                "timeout_seconds": current_app.config.get("ESP32_REQUEST_TIMEOUT_SECONDS", 8)},
        )


@automatic_scanning_bp.route("/camera-setup", methods=["POST"])
def update_camera_setup():
    try:
        control = get_scanner_control()
        if control.active:
            raise ValueError("Stop automatic scanning before changing the saved camera setup.")
        poller = db.session.get(CameraIngestionState, _camera_id())
        if poller and poller.polling_enabled:
            raise ValueError("Stop CameraWebServer polling before changing the saved camera setup.")
        if poller and poller.poller_running and not camera_poller_worker_running(poller.camera_id):
            poller.poller_running = False
            poller.connection_status = "stopped"
            db.session.commit()
        save_camera_setup(request.form.get("camera_id", ""), request.form.get("stream_url", ""),
                          request.form.get("capture_url", ""), request.form.get("timeout_seconds", 8))
        # Camera setup is a singleton; clear any old camera-ID toggle so a
        # previously enabled row cannot start polling after the ID changes.
        for state in db.session.scalars(select(CameraIngestionState)).all():
            state.polling_enabled = False
        db.session.commit()
        flash("Camera setup saved for future scanning sessions.", "success")
    except ValueError as error:
        db.session.rollback()
        flash(str(error), "error")
    except SQLAlchemyError:
        db.session.rollback()
        flash("Camera setup storage is unavailable. Apply migrations through 20261105_0012.", "error")
    return redirect(url_for("automatic_scanning.dashboard"))


@automatic_scanning_bp.route("/print-step")
def print_step():
    if not request.args.get("step", type=int) or request.args.get("step", type=int) < 1:
        abort(400)
    return render_template("blank_feed_print.html", page_count=1, step=request.args.get("step", type=int))


@automatic_scanning_bp.route("/print-step/started", methods=["POST"])
def print_step_started():
    control = get_scanner_control()
    step = request.form.get("step", type=int)
    marker_state = dict(control.marker_status or {})
    expected_step = int(marker_state.get("printer_step", 0)) + 1
    if not control.active or control.paused:
        return jsonify({"ok": False, "error": "Scanning is not active."}), 409
    if step != expected_step:
        return jsonify({"ok": False, "error": "This printer step is stale or already started."}), 409
    if marker_state.get("print_started_step") == step and marker_state.get("print_started_at"):
        return jsonify({"ok": True, "print_started_at": marker_state["print_started_at"]})
    started_at = datetime.now(timezone.utc).isoformat()
    marker_state["print_started_step"] = step
    marker_state["print_started_at"] = started_at
    control.marker_status = marker_state
    control.state = "WAITING_AFTER_PRINT_START"
    db.session.commit()
    return jsonify({"ok": True, "print_started_at": started_at})


@automatic_scanning_bp.route("/capture-step", methods=["POST"])
def capture_step():
    control = get_scanner_control()
    step = request.form.get("step", type=int)
    marker_state = dict(control.marker_status or {})
    current_step = int(marker_state.get("printer_step", 0))
    retry_step = marker_state.get("retry_step")
    retry_current_page = bool(marker_state.get("retry_current_page")) and step == int(
        retry_step if retry_step is not None else current_step
    )
    expected_step = current_step + 1
    if not control.active or control.paused:
        return jsonify({"ok": False, "error": "Scanning is not active."}), 409
    if step != expected_step and not retry_current_page:
        return jsonify({"ok": False, "error": "This printer step is stale or already captured."}), 409
    print_started_at = marker_state.get("print_started_at")
    if not print_started_at or marker_state.get("print_started_step") != step:
        return jsonify({"ok": False, "error": "No print start was recorded for this step. Stop and restart scanning before capturing."}), 409
    try:
        started_time = datetime.fromisoformat(str(print_started_at))
        if started_time.tzinfo is None:
            started_time = started_time.replace(tzinfo=timezone.utc)
        required_delay = 16.0 if step == 1 else 12.0
        elapsed = (datetime.now(timezone.utc) - started_time.astimezone(timezone.utc)).total_seconds()
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "The print start time is invalid. Stop and restart scanning before capturing."}), 409
    if elapsed < required_delay:
        retry_after_ms = max(1, int((required_delay - elapsed) * 1000))
        return jsonify({"ok": False, "too_early": True, "retry_after_ms": retry_after_ms,
                        "error": "The printer wait time has not elapsed yet."}), 425
    assessment = _assessment_ready(control.assessment_id or 0)
    if assessment is None:
        control.paused = True
        control.last_error = "The selected assessment or answer key is no longer ready."
        db.session.commit()
        return jsonify({"ok": False, "error": control.last_error}), 409
    try:
        camera_setup = get_camera_setup(current_app.config)
        ensure_queue_capacity(current_app.config)
        camera = ESP32Camera(camera_setup["stream_url"], camera_setup["capture_url"], camera_setup["timeout_seconds"])
        image_bytes, extension = camera.capture_still()
        import cv2
        import numpy as np
        frame = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        profile = load_official_profile(assessment.total_questions)
        detection = detect_v3_registration_markers(
            frame, profile,
            min_page_area_ratio=float(current_app.config["CAMERA_MIN_PAGE_AREA_RATIO"]),
            min_confidence=float(current_app.config["CAMERA_MIN_MARKER_CONFIDENCE"]),
        )
        # Compare the student's marked responses after marker alignment. This
        # ignores the visible stack and changing room lighting while still
        # distinguishing sheets by their ID and answer marks. Fall back to the
        # normalized page, then the full camera view, when alignment fails.
        response_signature = _response_signature(frame, detection, profile, assessment.total_questions)
        detected_student_number = _student_number_from_response(frame, detection, profile)
        student_id_counts = dict(marker_state.get("session_student_id_counts") or {})
        duplicate_student_id = False
        if detected_student_number:
            student_id_count = int(student_id_counts.get(detected_student_number, 0)) + 1
            student_id_counts[detected_student_number] = student_id_count
            duplicate_student_id = student_id_count >= 2
        page_signature = detection.get("page_signature") or _print_step_signature(frame)
        previous_response = marker_state.get("last_response_signature") or []
        previous_page = marker_state.get("last_page_signature") or []
        duplicate_tolerance = min(1.0, max(0.0, float(current_app.config["SCAN_DUPLICATE_PAGE_MAX_CHANGE_RATIO"])))
        same_response = bool(response_signature and previous_response and response_signature == previous_response)
        same_page_pixels = bool(page_signature and previous_page and len(previous_page) == len(page_signature)
                                 and sum(left != right for left, right in zip(previous_page, page_signature)) / len(page_signature) <= duplicate_tolerance)
        same_page = same_response if response_signature and previous_response else same_page_pixels
        now = datetime.now(timezone.utc)
        previous_signature_at = marker_state.get("last_page_signature_at")
        signature_time_valid = False
        try:
            previous_signature_time = datetime.fromisoformat(str(previous_signature_at))
            if previous_signature_time.tzinfo is None:
                previous_signature_time = previous_signature_time.replace(tzinfo=timezone.utc)
            duplicate_elapsed = max(0.0, (now - previous_signature_time.astimezone(timezone.utc)).total_seconds())
            signature_time_valid = True
        except (TypeError, ValueError):
            duplicate_elapsed = 0.0
        duplicate_stop_seconds = max(0.0, float(current_app.config.get("SCAN_DUPLICATE_STOP_SECONDS", 8)))
        duplicate_confirmed = same_page and duplicate_elapsed >= duplicate_stop_seconds
        signature_anchor = previous_signature_at if same_page and signature_time_valid else now.isoformat()
        control.marker_status = {
            **{key: value for key, value in detection.items() if key != "page_signature"},
            "printer_step": step,
            "last_response_signature": previous_response if same_page and response_signature else response_signature,
            "last_page_signature": previous_page if same_page else page_signature,
            "last_page_signature_at": signature_anchor,
            "duplicate_candidate_since": signature_anchor if same_page and not duplicate_confirmed else None,
            "session_student_id_counts": student_id_counts,
            "alignment_pending": not bool(detection.get("valid")),
            "retry_current_page": False,
            "retry_step": None,
        }
        control.camera_connected = True
        control.sheet_detected = True
        control.stability_status = True
        if duplicate_student_id and same_page:
            control.session_duplicate_frames += 1
            control.active = False
            control.paused = False
            control.state = "AUTO_STOPPED_DUPLICATE_STUDENT_ID"
            control.session_ended_at = datetime.now(timezone.utc)
            control.last_error = "The same student ID appeared on two captured pages. Automatic scanning stopped."
            db.session.commit()
            return jsonify({"ok": True, "done": True, "duplicate_student_id": True,
                            "message": control.last_error})

        if duplicate_confirmed:
            control.session_duplicate_frames += 1
            control.active = False
            control.paused = False
            control.state = "AUTO_STOPPED_DUPLICATE_PAGE"
            control.session_ended_at = datetime.now(timezone.utc)
            control.last_error = "The same aligned answer sheet remained unchanged for at least the duplicate confirmation interval. The last capture was treated as the printer's empty-feed signal; scanning stopped."
            db.session.commit()
            return jsonify({"ok": True, "done": True, "duplicate": True, "message": control.last_error})

        if same_page:
            # A repeated image inside the confirmation window is not another
            # student's submission. Advance this print step and let the next
            # scheduled capture confirm whether the printer has stopped.
            control.session_duplicate_frames += 1
            control.state = "CONFIRMING_DUPLICATE_PAGE"
            control.last_error = None
            db.session.commit()
            return jsonify({"ok": True, "done": False, "duplicate_candidate": True, "step": step,
                            "message": "The sheet matches the previous capture; waiting for another scheduled capture to confirm printer idle."})

        job = queue_image_bytes(
            image_bytes, extension, assessment.id, current_app.config,
            source="esp32", camera_id=camera_setup["camera_id"], detection_metadata={
                **{key: value for key, value in detection.items() if key != "page_signature"},
                "alignment_pending": not bool(detection.get("valid")),
                "alignment_retry": "muse" if not detection.get("valid") else None,
            },
        )
        if duplicate_student_id:
            control.session_duplicate_frames += 1
            control.active = False
            control.paused = False
            control.state = "AUTO_STOPPED_DUPLICATE_STUDENT_ID"
            control.session_ended_at = datetime.now(timezone.utc)
            control.last_error = "The same student ID appeared on two captured pages. Automatic scanning stopped; the latest page was queued for review."
            db.session.commit()
            return jsonify({"ok": True, "done": True, "duplicate_student_id": True,
                            "capture_id": job.capture_id, "message": control.last_error})

        control.state = "QUEUED_ALIGNMENT_RETRY" if not detection.get("valid") else "WAITING_FOR_PRINTER_STEP"
        control.last_error = None
        db.session.commit()
        return jsonify({
            "ok": True, "done": False, "step": step, "capture_id": job.capture_id,
            "alignment_pending": not bool(detection.get("valid")),
            "message": "Local marker alignment was inconclusive; the image was queued for Muse-assisted alignment." if not detection.get("valid") else None,
        })
    except (CameraError, QueueFullError, ValueError, OSError) as error:
        db.session.rollback()
        control = get_scanner_control()
        control.paused = True
        control.state = "CAPTURE_ERROR"
        control.last_error = str(error)[:1000]
        db.session.commit()
        return jsonify({"ok": False, "error": control.last_error}), 503
    except Exception as error:
        db.session.rollback()
        control = get_scanner_control()
        control.paused = True
        control.state = "CAPTURE_ERROR"
        control.last_error = f"Capture processing failed: {error}"[:1000]
        db.session.commit()
        return jsonify({"ok": False, "error": control.last_error}), 503


@automatic_scanning_bp.route("/status")
def status():
    try:
        control = get_scanner_control()
        return jsonify({
            "ok": True,
            "camera_connected": control.camera_connected,
            "active": control.active,
            "paused": control.paused,
            "printer_step": int((control.marker_status or {}).get("printer_step", 0)),
            "state": control.state,
            "sheet_detected": control.sheet_detected,
            "marker_status": {
                key: value for key, value in (control.marker_status or {}).items()
                if key not in {"last_page_signature", "last_response_signature", "session_student_id_counts"}
            },
            "stability_status": control.stability_status,
            "last_error": control.last_error,
            "assessment_id": control.assessment_id,
            "session": _session_progress(control),
            "queue": {**get_queue_counts(), "capacity_limit": int(current_app.config["CAPTURE_QUEUE_CAPACITY"])},
            "ingestion": _ingestion_counts(),
            "camera_poller": _camera_poller_status(),
            "recent_captures": [_job_payload(job) for job in recent_capture_jobs(20)],
        })
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"ok": False, "error": "Apply the scanner queue database migration before using automatic scanning."}), 503


def _session_progress(control: ScannerControl) -> dict:
    started = control.session_started_at
    if started is None or control.assessment_id is None:
        return {"state": "READY", "started_at": None, "ended_at": None,
                "valid_sheets_captured": 0, "images_awaiting_processing": 0,
                "successfully_graded": 0, "review_required": 0, "rejected_images": 0,
                "duplicate_captures": 0, "failed_captures": 0, "last_successful_capture_at": None}
    rows = db.session.execute(
        select(CaptureJob.status, func.count(CaptureJob.id),
               func.sum(func.coalesce(CaptureJob.submission_id.is_not(None).cast(db.Integer), 0)),
               func.max(CaptureJob.captured_at))
        .where(CaptureJob.assessment_id == control.assessment_id, CaptureJob.captured_at >= started)
        .group_by(CaptureJob.status)
    ).all()
    statuses = {row[0]: int(row[1] or 0) for row in rows}
    captured = sum(statuses.values())
    awaiting = statuses.get("QUEUED", 0) + statuses.get("PROCESSING", 0)
    graded = sum(int(row[2] or 0) for row in rows if row[0] == "COMPLETED")
    needs_review = statuses.get("NEEDS_REVIEW", 0)
    failed = statuses.get("FAILED", 0)
    baseline = control.session_camera_baseline or {}
    camera_id = baseline.get("camera_id") or _camera_id()
    camera_state = db.session.get(CameraIngestionState, camera_id)
    rejected = max(0, int(camera_state.rejected_count if camera_state else 0) - int(baseline.get("rejected", 0)))
    duplicates = max(0, int(camera_state.duplicate_count if camera_state else 0) - int(baseline.get("duplicates", 0)))
    rejected += int(control.session_rejected_frames or 0)
    duplicates += int(control.session_duplicate_frames or 0)
    last_capture = max((row[3] for row in rows if row[0] == "COMPLETED" and row[3] is not None), default=None)
    if control.active and control.paused and str(control.state).startswith("INTERRUPTED_"):
        state = "INTERRUPTED"
    elif control.active and control.paused:
        state = "PAUSED"
    elif control.active and control.state == "PREPARING_PRINTER":
        state = "PREPARING_PRINTER"
    elif control.active:
        state = "SCANNING"
    elif awaiting:
        state = "PROCESSING_REMAINING_QUEUE"
    elif needs_review:
        state = "REVIEW_REQUIRED"
    elif failed:
        state = "FAILED"
    elif control.session_ended_at and (captured == 0 or graded == captured):
        state = "COMPLETED"
    else:
        state = "READY"
    return {
        "state": state, "started_at": started.isoformat(),
        "ended_at": control.session_ended_at.isoformat() if control.session_ended_at else None,
        "valid_sheets_captured": captured, "images_awaiting_processing": awaiting,
        "successfully_graded": graded, "review_required": needs_review,
        "rejected_images": rejected, "duplicate_captures": duplicates,
        "failed_captures": failed, "last_successful_capture_at": last_capture.isoformat() if last_capture else None,
    }


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
    camera_id = _camera_id()
    state = db.session.get(CameraIngestionState, camera_id)
    running = bool(state and state.poller_running)
    if running and not camera_poller_worker_running(camera_id):
        state.poller_running = False
        state.connection_status = "stopped"
        db.session.commit()
        running = False
    return {
        "configured": bool(current_app.config.get("PAPARAZZI_CAMERA_URL")),
        "camera_id": camera_id,
        "camera_url": current_app.config.get("PAPARAZZI_CAMERA_URL", ""),
        "enabled": bool(state and state.polling_enabled),
        "running": running,
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
    camera_id = _camera_id()
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
    started = False
    try:
        scanner_control = db.session.get(ScannerControl, 1)
        if scanner_control and scanner_control.active and not scanner_control.paused:
            flash("Pause or stop ESP32 stream scanning before starting CameraWebServer polling.", "error")
            return redirect(url_for("automatic_scanning.dashboard"))
        assessment_id = request.form.get("assessment_id", type=int) or (scanner_control.assessment_id if scanner_control else None)
        assessment = _assessment_ready(assessment_id or 0)
        if assessment is None:
            flash("Choose an assessment with a complete 20- or 50-question A-E answer key before starting polling.", "error")
            return redirect(url_for("automatic_scanning.dashboard"))
        if scanner_control is None:
            scanner_control = get_scanner_control()
        scanner_control.assessment_id = assessment.id
        state = _get_or_create_poller_state()
        state.polling_enabled = True
        state.last_error = None
        db.session.commit()
        started = True
        flash("Camera polling requested. The separate camera-poller process must be running.", "success")
    except SQLAlchemyError:
        db.session.rollback()
        flash("Camera poller storage migration is not installed.", "error")
    if started:
        return redirect(url_for("automatic_scanning.dashboard", assessment_id=assessment.id))
    return redirect(url_for("automatic_scanning.dashboard"))


@automatic_scanning_bp.route("/poller/stop", methods=["POST"])
def stop_poller():
    _authorize_poller_control()
    try:
        state = db.session.get(CameraIngestionState, _camera_id())
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
    if request.form.get("silent_print_configured") != "yes":
        flash("Configure kiosk printing on this computer, then confirm it below before starting the one-page feed cycle.", "error")
        return redirect(url_for("automatic_scanning.dashboard", assessment_id=request.form.get("assessment_id", type=int)))
    assessment = _assessment_ready(request.form.get("assessment_id", type=int) or 0)
    if assessment is None:
        flash("Choose an assessment with a complete 20- or 50-question answer key including choice E.", "error")
        return redirect(url_for("automatic_scanning.dashboard"))
    started = False
    try:
        camera_setup = get_camera_setup(current_app.config)
        if not camera_setup["stream_url"] or not camera_setup["capture_url"]:
            flash("Save the ESP32 stream URL and still-capture URL in Camera setup before starting.", "error")
            return redirect(url_for("automatic_scanning.dashboard", assessment_id=assessment.id))
        poller_state = db.session.get(CameraIngestionState, _camera_id())
        if poller_state and poller_state.polling_enabled:
            flash("Stop CameraWebServer polling before starting the ESP32 stream capture source.", "error")
            return redirect(url_for("automatic_scanning.dashboard", assessment_id=assessment.id))
        control = get_scanner_control()
        if control.active:
            flash("Resume or stop the existing scanning session before starting another one.", "error")
            return redirect(url_for("automatic_scanning.dashboard", assessment_id=control.assessment_id))
        control.assessment_id = assessment.id
        control.active = True
        control.paused = False
        control.session_started_at = datetime.now(timezone.utc)
        control.session_ended_at = None
        control.session_rejected_frames = 0
        control.session_duplicate_frames = 0
        camera_state = db.session.get(CameraIngestionState, camera_setup["camera_id"])
        control.session_camera_baseline = {
            "camera_id": camera_setup["camera_id"],
            "rejected": int(camera_state.rejected_count if camera_state else 0),
            "duplicates": int(camera_state.duplicate_count if camera_state else 0),
        }
        control.last_error = None
        control.marker_status = {
            "printer_step": 0,
            "last_page_signature": None,
            "last_response_signature": None,
            "last_page_signature_at": None,
            "session_student_id_counts": {},
        }
        control.state = "WAITING_FOR_PRINTER_STEP"
        db.session.commit()
        started = True
        flash("One-page scanning enabled. The app will wait 16 seconds after the first print, then 12 seconds after each later print before capturing.", "success")
    except SQLAlchemyError:
        db.session.rollback()
        flash("The scanner queue migration is not installed. Review required migrations through 20261105_0012.", "error")
    if started:
        return redirect(url_for("automatic_scanning.dashboard", assessment_id=assessment.id))
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
            if control.state in {"CAPTURE_REQUIRES_REVIEW", "CAPTURE_ERROR"}:
                marker_state = dict(control.marker_status or {})
                current_step = int(marker_state.get("printer_step", 0))
                marker_state["retry_current_page"] = True
                # A printer step is committed only after its image has been
                # queued. Alignment failures and camera capture errors both
                # leave the current physical sheet uncommitted, so resume
                # must capture that same sheet before requesting another page.
                marker_state["retry_step"] = int(marker_state.get("print_started_step") or (current_step + 1))
                control.marker_status = marker_state
            control.paused = False
            control.session_ended_at = None
            control.last_error = None
            db.session.commit()
            flash("Scanning resumed. The last sheet will be recaptured and queued before another blank page is requested.", "success")
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
        if control.session_started_at:
            control.session_ended_at = datetime.now(timezone.utc)
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
        poller_state = db.session.get(CameraIngestionState, _camera_id())
        if poller_state and poller_state.polling_enabled:
            flash("Stop CameraWebServer polling before using manual camera capture.", "error")
            return redirect(url_for("automatic_scanning.dashboard"))
        if control.active and not control.paused:
            flash("Pause automatic scanning before using manual camera capture.", "error")
            return redirect(url_for("automatic_scanning.dashboard"))
        assessment = _assessment_ready(request.form.get("assessment_id", type=int) or control.assessment_id or 0)
        if assessment is None:
            flash("Choose an assessment with a complete A-E answer key first.", "error")
            return redirect(url_for("automatic_scanning.dashboard"))
        camera_setup = get_camera_setup(current_app.config)
        camera = ESP32Camera(camera_setup["stream_url"], camera_setup["capture_url"], camera_setup["timeout_seconds"])
        ensure_queue_capacity(current_app.config)
        image_bytes, extension = camera.capture_still()
        job = queue_image_bytes(image_bytes, extension, assessment.id, current_app.config, source="manual")
        flash(f"Manual capture {job.capture_id} queued for Paparazzi review.", "success")
    except (CameraError, QueueFullError, ValueError, OSError) as error:
        db.session.rollback()
        flash(str(error), "error")
    except SQLAlchemyError:
        db.session.rollback()
        flash("The scanner queue migration is not installed. Review required migrations through 20261105_0012.", "error")
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


@automatic_scanning_bp.route("/batch-review/<int:assessment_id>")
def batch_review(assessment_id: int):
    assessment = _assessment_ready(assessment_id)
    if assessment is None:
        flash("Choose an assessment with a complete answer key before reviewing captured sheets.", "error")
        return redirect(url_for("automatic_scanning.dashboard"))
    drafts = _automatic_drafts(assessment.id)
    if not drafts:
        flash("There are no automatic captures awaiting review for this assessment.", "info")
        return redirect(url_for("automatic_scanning.dashboard", assessment_id=assessment.id))
    mapping = db.session.scalar(
        select(CanvasAssessmentMapping).where(CanvasAssessmentMapping.assessment_id == assessment.id)
    )
    canvas_ready = bool(db.session.get(CanvasConnection, 1) and mapping and mapping.assignment_id)
    return render_template(
        "automatic_scan_batch_review.html",
        assessment=assessment,
        review_rows=_batch_review_rows(drafts),
        canvas_ready=canvas_ready,
        all_assessments=False,
    )


@automatic_scanning_bp.route("/batch-review/all")
def batch_review_all():
    drafts = _all_automatic_drafts()
    if not drafts:
        flash("There are no automatic captures awaiting review.", "info")
        return redirect(url_for("automatic_scanning.dashboard"))
    return render_template(
        "automatic_scan_batch_review.html",
        assessment=None,
        review_rows=_batch_review_rows(drafts),
        canvas_ready=False,
        all_assessments=True,
    )


@automatic_scanning_bp.route("/batch-review/<int:assessment_id>/save", methods=["POST"])
def batch_review_save(assessment_id: int):
    assessment = _assessment_ready(assessment_id)
    if assessment is None:
        flash("Choose an assessment with a complete answer key before saving captured sheets.", "error")
        return redirect(url_for("automatic_scanning.dashboard"))
    if request.form.get("confirm_batch") != "yes":
        flash("Confirm that you reviewed every capture's student ID and answers.", "error")
        return redirect(url_for("automatic_scanning.batch_review", assessment_id=assessment.id))

    drafts = _automatic_drafts(assessment.id)
    if not drafts:
        flash("There are no automatic captures awaiting review for this assessment.", "info")
        return redirect(url_for("automatic_scanning.batch_review", assessment_id=assessment.id))

    saved = 0
    skipped = []
    for draft in drafts:
        prefix = f"draft_{draft.id}"
        student = db.session.get(Student, request.form.get(f"{prefix}_student_id", type=int))
        if student is None:
            skipped.append(f"Capture {draft.id}: select a student.")
            continue
        submitted_answers = [
            request.form.get(f"{prefix}_answer_{number}", "")
            for number in range(1, draft.question_count + 1)
        ]
        if any(answer not in {"__blank__", "A", "B", "C", "D", "E"} for answer in submitted_answers):
            skipped.append(f"Capture {draft.id}: select an answer or confirm blank for every question.")
            continue
        final_answers = [None if answer == "__blank__" else answer for answer in submitted_answers]
        try:
            confirm_v3_scan_draft(draft, student, final_answers)
            saved += 1
        except ValueError as error:
            skipped.append(f"Capture {draft.id}: {error}")
        except SQLAlchemyError:
            db.session.rollback()
            skipped.append(f"Capture {draft.id}: the database could not save this submission.")
            break

    if saved:
        flash(f"Saved {saved} capture(s) as graded submissions for analytics.", "success")
    if skipped:
        details = " ".join(skipped[:6])
        if len(skipped) > 6:
            details += f" Plus {len(skipped) - 6} more capture(s) that still need review."
        flash(details, "warning")
    if not saved and not skipped:
        flash("No capture was saved.", "info")

    if request.form.get("open_canvas_preview") == "yes" and saved:
        mapping = db.session.scalar(
            select(CanvasAssessmentMapping).where(CanvasAssessmentMapping.assessment_id == assessment.id)
        )
        connection = db.session.get(CanvasConnection, 1)
        if connection and mapping and mapping.assignment_id:
            return redirect(url_for("canvas.grade_preview", assessment_id=assessment.id))
        flash("Connect Canvas and map this assessment to a course assignment before previewing grades.", "info")
        return redirect(url_for("canvas.assessment_setup", assessment_id=assessment.id))
    return redirect(url_for("automatic_scanning.dashboard", assessment_id=assessment.id))


@automatic_scanning_bp.route("/batch-review/all/save", methods=["POST"])
def batch_review_all_save():
    if request.form.get("confirm_batch") != "yes":
        flash("Confirm that you reviewed every capture's student ID and answers.", "error")
        return redirect(url_for("automatic_scanning.batch_review_all"))
    drafts = _all_automatic_drafts()
    if not drafts:
        flash("There are no automatic captures awaiting review.", "info")
        return redirect(url_for("automatic_scanning.dashboard"))
    saved = 0
    skipped = []
    for draft in drafts:
        prefix = f"draft_{draft.id}"
        student = db.session.get(Student, request.form.get(f"{prefix}_student_id", type=int))
        if student is None:
            skipped.append(f"Capture {draft.id}: select a student.")
            continue
        answers = [request.form.get(f"{prefix}_answer_{number}", "") for number in range(1, draft.question_count + 1)]
        if any(answer not in {"__blank__", "A", "B", "C", "D", "E"} for answer in answers):
            skipped.append(f"Capture {draft.id}: select an answer or confirm blank for every question.")
            continue
        try:
            confirm_v3_scan_draft(draft, student, [None if answer == "__blank__" else answer for answer in answers])
            saved += 1
        except ValueError as error:
            skipped.append(f"Capture {draft.id}: {error}")
        except SQLAlchemyError:
            db.session.rollback()
            skipped.append(f"Capture {draft.id}: the database could not save this submission.")
            break
    if saved:
        flash(f"Saved {saved} capture(s) as graded submissions for analytics.", "success")
    if skipped:
        details = " ".join(skipped[:6])
        if len(skipped) > 6:
            details += f" Plus {len(skipped) - 6} more capture(s) that still need review."
        flash(details, "warning")
    return redirect(url_for("automatic_scanning.dashboard"))


@automatic_scanning_bp.route("/<int:job_id>/delete", methods=["POST"])
def delete_capture(job_id: int):
    try:
        outcome = delete_capture_job(job_id, current_app.config["UPLOAD_FOLDER"])
        if outcome["submission_saved"]:
            flash("Capture removed from recent captures. Its submitted grade and evidence were kept.", "success")
        elif outcome["draft_deleted"]:
            flash("Capture and its unsubmitted review draft were deleted.", "success")
        else:
            flash("Capture and its saved image were deleted.", "success")
        if outcome["cleanup_errors"]:
            current_app.logger.warning("Could not remove all files for deleted capture job %s", job_id)
    except ValueError as error:
        flash(str(error), "error")
    except SQLAlchemyError:
        db.session.rollback()
        flash("The capture could not be deleted. No database changes were saved.", "error")
    return redirect(url_for("automatic_scanning.dashboard"))


@automatic_scanning_bp.route("/clear-queue", methods=["POST"])
def clear_queue():
    try:
        control = get_scanner_control()
        if control.active:
            flash("Stop automatic scanning before clearing the queue.", "error")
            return redirect(url_for("automatic_scanning.dashboard"))
        camera_setup = get_camera_setup(current_app.config)
        poller = db.session.get(CameraIngestionState, camera_setup["camera_id"])
        if poller is not None and poller.polling_enabled:
            flash("Stop CameraWebServer polling before clearing the queue.", "error")
            return redirect(url_for("automatic_scanning.dashboard"))

        outcome = clear_capture_queue(current_app.config["UPLOAD_FOLDER"])
        removed = outcome["removed_queued"] + outcome["removed_finished"]
        if removed:
            message = (
                f"Cleared {removed} capture(s) ({outcome['removed_queued']} queued, "
                f"{outcome['removed_finished']} finished); unsubmitted drafts were deleted, "
                f"and {outcome['preserved_submissions']} submitted grade(s) were kept."
            )
            if outcome["processing_skipped"]:
                message += f" {outcome['processing_skipped']} in-progress capture(s) were left to finish."
            flash(message, "success")
        elif outcome["processing_skipped"]:
            flash(f"No removable captures were found; {outcome['processing_skipped']} capture(s) are still processing.", "info")
        else:
            flash("The capture queue is already empty.", "info")
        if outcome["cleanup_errors"]:
            current_app.logger.warning("Could not remove all files while clearing the capture queue")
    except SQLAlchemyError:
        db.session.rollback()
        flash("The capture queue could not be cleared. No database changes were saved.", "error")
    return redirect(url_for("automatic_scanning.dashboard"))
