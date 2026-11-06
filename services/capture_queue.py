"""Durable PostgreSQL queue shared by ESP32 captures and the single consumer."""
from __future__ import annotations

import uuid
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
from sqlalchemy import func, select, text
from werkzeug.datastructures import FileStorage

from extensions import db
from models import Assessment, CaptureJob, ScanDraft, ScannerControl, Student
from services.image_processor import ImageProcessingError
from services.paparazzi_v3_submission_service import confirm_v3_scan_draft, create_v3_scan_draft


PENDING_STATUSES = ("QUEUED", "PROCESSING")
_CAPTURE_CAPACITY_LOCK = 711304
_WORKER_LOCK = 711305


class QueueFullError(RuntimeError):
    pass


def get_scanner_control() -> ScannerControl:
    control = db.session.get(ScannerControl, 1)
    if control is None:
        control = ScannerControl(id=1)
        db.session.add(control)
        db.session.commit()
    return control


def queue_image_bytes(image_bytes: bytes, extension: str, assessment_id: int, app_config: dict, *, source: str = "esp32", capture_id: str | None = None, camera_id: str | None = None, captured_at: datetime | None = None, detection_metadata: dict | None = None, commit: bool = True) -> CaptureJob:
    if source not in {"esp32", "manual", "api"} or extension not in {".jpg", ".jpeg", ".png"}:
        raise ValueError("Invalid capture source or image type.")
    image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape[0] < 400 or image.shape[1] < 400 or max(image.shape[:2]) > int(app_config["MAX_IMAGE_DIMENSION"]):
        raise ImageProcessingError("Capture must be a valid full-page image within the configured dimension limit.")
    if len(image_bytes) > int(app_config["MAX_CONTENT_LENGTH"]):
        raise ImageProcessingError("The captured image exceeds the configured upload size limit.")
    assessment = db.session.get(Assessment, assessment_id)
    if assessment is None or assessment.total_questions not in {20, 50}:
        raise ValueError("Select an existing 20- or 50-question assessment.")
    capture_id = capture_id or uuid.uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", capture_id):
        raise ValueError("Capture identifiers may contain 1-64 letters, digits, hyphens, or underscores.")
    folder = Path(app_config["UPLOAD_FOLDER"]).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"capture-{capture_id}{extension}"
    path = folder / filename
    if path.exists():
        raise ValueError("This capture identifier already has an image.")
    path.write_bytes(image_bytes)
    try:
        # Serialize capacity checks across the producer and manual-capture endpoint.
        db.session.execute(text("SELECT pg_advisory_xact_lock(:capture_lock)"), {"capture_lock": _CAPTURE_CAPACITY_LOCK})
        pending = db.session.scalar(select(func.count(CaptureJob.id)).where(CaptureJob.status.in_(PENDING_STATUSES))) or 0
        if pending >= int(app_config["CAPTURE_QUEUE_CAPACITY"]):
            raise QueueFullError("The capture queue is full. Automatic capture was paused; process queued sheets before resuming.")
        job = CaptureJob(
            capture_id=capture_id, assessment_id=assessment_id, image_filename=filename,
            source=source, status="QUEUED", camera_id=camera_id,
            captured_at=captured_at, detection_metadata=detection_metadata or {},
        )
        db.session.add(job)
        if commit:
            db.session.commit()
        return job
    except Exception:
        db.session.rollback()
        path.unlink(missing_ok=True)
        raise


def ensure_queue_capacity(app_config: dict) -> None:
    """Reserve the capacity lock before doing a physical still capture."""
    db.session.execute(text("SELECT pg_advisory_xact_lock(:capture_lock)"), {"capture_lock": _CAPTURE_CAPACITY_LOCK})
    pending = db.session.scalar(select(func.count(CaptureJob.id)).where(CaptureJob.status.in_(PENDING_STATUSES))) or 0
    if pending >= int(app_config["CAPTURE_QUEUE_CAPACITY"]):
        db.session.rollback()
        raise QueueFullError("The capture queue is full. Automatic capture was paused; process queued sheets before resuming.")


def get_queue_counts() -> dict[str, int]:
    rows = db.session.execute(select(CaptureJob.status, func.count(CaptureJob.id)).group_by(CaptureJob.status)).all()
    counts = {status: 0 for status in ("QUEUED", "PROCESSING", "COMPLETED", "NEEDS_REVIEW", "FAILED")}
    counts.update({status: int(count) for status, count in rows})
    counts["capacity"] = sum(counts[status] for status in PENDING_STATUSES)
    return counts


def recent_capture_jobs(limit: int = 25) -> list[CaptureJob]:
    return db.session.scalars(select(CaptureJob).order_by(CaptureJob.captured_at.desc(), CaptureJob.id.desc()).limit(limit)).all()


def claim_next_job() -> CaptureJob | None:
    job_id = db.session.scalar(
        select(CaptureJob.id)
        .where(CaptureJob.status == "QUEUED")
        .order_by(CaptureJob.captured_at, CaptureJob.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job_id is None:
        return None
    job = db.session.get(CaptureJob, job_id)
    job.status = "PROCESSING"
    job.started_at = datetime.now(timezone.utc)
    job.completed_at = None
    job.retry_count += 1
    job.error_message = None
    db.session.commit()
    return job


def recover_interrupted_jobs() -> int:
    jobs = db.session.scalars(select(CaptureJob).where(CaptureJob.status == "PROCESSING").with_for_update()).all()
    for job in jobs:
        job.status = "QUEUED"
        job.error_message = "Recovered after the processing worker stopped unexpectedly."
        job.started_at = None
    count = len(jobs)
    if count:
        db.session.commit()
    return count


def process_job(job_id: int, app_config: dict) -> CaptureJob:
    job = db.session.get(CaptureJob, job_id)
    if job is None or job.status != "PROCESSING":
        raise ValueError("The processing job is no longer claimed.")
    try:
        existing_draft = db.session.scalar(
            select(ScanDraft).where(ScanDraft.processing_notes["capture_id"].as_string() == job.capture_id).limit(1)
        )
        if existing_draft is not None:
            job.draft_id = existing_draft.id
            job.status = "NEEDS_REVIEW"
            job.completed_at = datetime.now(timezone.utc)
            job.error_message = None
            db.session.commit()
            return job
        folder = Path(app_config["UPLOAD_FOLDER"]).resolve()
        image_path = (folder / job.image_filename).resolve()
        if image_path.parent != folder or not image_path.is_file():
            raise OSError("The preserved original capture file is missing.")
        image_bytes = image_path.read_bytes()
        assessment = db.session.get(Assessment, job.assessment_id)
        if assessment is None:
            raise ValueError("The selected assessment was deleted before this image was processed.")
        upload = FileStorage(stream=BytesIO(image_bytes), filename=job.image_filename)
        draft = create_v3_scan_draft(
            upload, app_config, assessment, assessment.total_questions,
            capture_id=job.capture_id, original_filename=job.image_filename,
        )
        student = db.session.get(Student, draft.student_id) if draft.student_id else None
        student_id_detection = draft.student_id_detection or {}
        answers = draft.recognized_answers or []
        minimum_confidence = float(app_config.get("CAMERA_AUTO_FINALIZE_MIN_CONFIDENCE", 0.80))
        can_auto_finalize = bool(
            student
            and student_id_detection.get("status") == "detected"
            and student_id_detection.get("student_id") == student.student_number
            and float(student_id_detection.get("confidence") or 0.0) >= minimum_confidence
            and not (draft.processing_notes or {}).get("warnings")
            and len(answers) == assessment.total_questions
            and all(
                item.get("status") in {"detected", "blank"}
                and float(item.get("confidence") or 0.0) >= minimum_confidence
                for item in answers
            )
        )
        if can_auto_finalize:
            confirm_v3_scan_draft(
                draft,
                student,
                [item["answer"] if item["status"] == "detected" else None for item in answers],
            )
            completed_job = db.session.get(CaptureJob, job_id)
            if completed_job is None:
                raise RuntimeError("The capture job disappeared during automatic grading.")
            return completed_job
        job.draft_id = draft.id
        job.status = "NEEDS_REVIEW"
        job.completed_at = datetime.now(timezone.utc)
        job.error_message = None
        db.session.commit()
        return job
    except Exception as error:
        db.session.rollback()
        job = db.session.get(CaptureJob, job_id)
        if job is not None:
            job.status = "FAILED"
            job.error_message = str(error)[:4000]
            job.completed_at = datetime.now(timezone.utc)
            db.session.commit()
            return job
        raise


def retry_failed_job(job_id: int) -> CaptureJob:
    job = db.session.scalar(select(CaptureJob).where(CaptureJob.id == job_id).with_for_update())
    if job is None or job.status != "FAILED":
        raise ValueError("Only failed capture jobs can be retried.")
    job.status = "QUEUED"
    job.error_message = None
    job.completed_at = None
    db.session.commit()
    return job


def delete_capture_job(job_id: int, upload_folder: str | Path) -> dict:
    """Remove a finished capture from recents without deleting submitted grades."""
    job = db.session.scalar(select(CaptureJob).where(CaptureJob.id == job_id).with_for_update())
    if job is None:
        raise ValueError("Capture not found.")
    if job.status in PENDING_STATUSES:
        raise ValueError("Queued or processing captures cannot be deleted yet.")

    submission_saved = job.submission_id is not None
    draft = job.draft
    draft_deleted = draft is not None and not submission_saved
    filenames: set[str] = set()
    if not submission_saved:
        filenames.add(job.image_filename)
        if draft_deleted:
            filenames.update((draft.original_image, draft.aligned_image, draft.debug_image))

    if draft_deleted:
        job.draft_id = None
        db.session.delete(draft)
    db.session.delete(job)
    db.session.commit()

    cleanup_errors = []
    folder = Path(upload_folder).resolve()
    for filename in filenames:
        if not filename or Path(filename).name != filename:
            continue
        image_path = (folder / filename).resolve()
        if image_path.parent != folder:
            continue
        try:
            image_path.unlink(missing_ok=True)
        except OSError:
            cleanup_errors.append(filename)
    return {
        "submission_saved": submission_saved,
        "draft_deleted": draft_deleted,
        "cleanup_errors": cleanup_errors,
    }


def clear_capture_queue(upload_folder: str | Path) -> dict:
    """Delete removable queue rows in one transaction; leave in-flight work alone."""
    jobs = db.session.scalars(
        select(CaptureJob)
        .where(CaptureJob.status != "PROCESSING")
        .order_by(CaptureJob.id)
        .with_for_update(skip_locked=True)
    ).all()
    filenames: set[str] = set()
    removed_queued = 0
    removed_finished = 0
    preserved_submissions = 0
    drafts_deleted = 0

    for job in jobs:
        if job.status == "QUEUED":
            removed_queued += 1
        else:
            removed_finished += 1
        if job.submission_id is not None:
            preserved_submissions += 1
        else:
            filenames.add(job.image_filename)
            if job.draft is not None:
                draft = job.draft
                filenames.update((draft.original_image, draft.aligned_image, draft.debug_image))
                job.draft_id = None
                db.session.delete(draft)
                drafts_deleted += 1
        db.session.delete(job)

    processing_skipped = db.session.scalar(
        select(func.count(CaptureJob.id)).where(CaptureJob.status == "PROCESSING")
    ) or 0
    db.session.commit()

    cleanup_errors = []
    folder = Path(upload_folder).resolve()
    for filename in filenames:
        if not filename or Path(filename).name != filename:
            continue
        image_path = (folder / filename).resolve()
        if image_path.parent != folder:
            continue
        try:
            image_path.unlink(missing_ok=True)
        except OSError:
            cleanup_errors.append(filename)
    return {
        "removed_queued": removed_queued,
        "removed_finished": removed_finished,
        "preserved_submissions": preserved_submissions,
        "drafts_deleted": drafts_deleted,
        "processing_skipped": int(processing_skipped),
        "cleanup_errors": cleanup_errors,
    }


def acquire_worker_lock():
    """Hold one PostgreSQL session advisory lock for this entire daemon lifetime."""
    connection = db.engine.connect()
    acquired = connection.scalar(select(func.pg_try_advisory_lock(_WORKER_LOCK)))
    if not acquired:
        connection.close()
        return None
    return connection


def release_worker_lock(connection) -> None:
    if connection is not None:
        try:
            connection.execute(select(func.pg_advisory_unlock(_WORKER_LOCK)))
        finally:
            connection.close()

