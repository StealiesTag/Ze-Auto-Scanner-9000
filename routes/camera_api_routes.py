"""Framework-neutral camera image ingestion API."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request, send_from_directory, url_for
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from extensions import csrf, db
from models import CameraIngestionState, CaptureJob
from services.camera_ingestion import ingest_camera_image_bytes


camera_api_bp = Blueprint("camera_api", __name__, url_prefix="/api/camera")
csrf.exempt(camera_api_bp)


def _error(reason: str, message: str, status: int):
    return jsonify(success=False, status="error", reason=reason, message=message), status


@camera_api_bp.post("/ingest")
def ingest():
    uploaded = request.files.get("image") or request.files.get("file")
    if uploaded is not None:
        image_bytes = uploaded.read()
        content_type = request.form.get("content_type") or uploaded.mimetype
    else:
        image_bytes = request.get_data(cache=False)
        content_type = request.form.get("content_type") or request.headers.get("Content-Type", "").split(";", 1)[0]
    if not image_bytes:
        return _error("empty_image", "Provide image bytes or a multipart image/file field.", 400)

    camera_id = (request.values.get("camera_id") or request.headers.get("X-Camera-ID") or "default-camera").strip()
    capture_id = (request.values.get("capture_id") or request.headers.get("X-Capture-ID") or "").strip() or uuid.uuid4().hex
    captured_at_value = request.values.get("captured_at") or request.headers.get("X-Captured-At")
    captured_at = None
    if captured_at_value:
        try:
            captured_at = datetime.fromisoformat(captured_at_value.replace("Z", "+00:00"))
            if captured_at.tzinfo is None:
                captured_at = captured_at.replace(tzinfo=timezone.utc)
            captured_at = captured_at.astimezone(timezone.utc)
        except ValueError:
            return _error("invalid_captured_at", "captured_at must be an ISO-8601 timestamp.", 400)

    result = ingest_camera_image_bytes(image_bytes, content_type, camera_id, capture_id, captured_at)
    return jsonify(result.payload), result.http_status


@camera_api_bp.get("/captures/<string:capture_id>")
def capture_details(capture_id: str):
    try:
        job = db.session.scalar(select(CaptureJob).where(CaptureJob.capture_id == capture_id))
    except SQLAlchemyError:
        db.session.rollback()
        return _error("migration_pending", "Apply the reviewed camera ingestion migrations before inspecting captures.", 503)
    if job is None:
        return _error("capture_not_found", "No accepted capture has that identifier.", 404)
    return jsonify(
        capture_id=job.capture_id, camera_id=job.camera_id, status=job.status,
        captured_at=job.captured_at.isoformat() if job.captured_at else None,
        detection=job.detection_metadata, image_url=url_for("camera_api.capture_image", capture_id=job.capture_id),
        error=job.error_message, draft_id=job.draft_id,
    )


@camera_api_bp.get("/captures/<string:capture_id>/image")
def capture_image(capture_id: str):
    try:
        job = db.session.scalar(select(CaptureJob).where(CaptureJob.capture_id == capture_id))
    except SQLAlchemyError:
        db.session.rollback()
        return _error("migration_pending", "Apply the reviewed camera ingestion migrations before inspecting captures.", 503)
    if job is None:
        return _error("capture_not_found", "No accepted capture has that identifier.", 404)
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], job.image_filename, conditional=True)


@camera_api_bp.get("/status")
def ingestion_status():
    try:
        rows = db.session.execute(select(
            func.coalesce(func.sum(CameraIngestionState.received_count), 0),
            func.coalesce(func.sum(CameraIngestionState.rejected_count), 0),
            func.coalesce(func.sum(CameraIngestionState.accepted_count), 0),
            func.coalesce(func.sum(CameraIngestionState.duplicate_count), 0),
            func.coalesce(func.sum(CameraIngestionState.deferred_count), 0),
            func.coalesce(func.sum(CameraIngestionState.frames_fetched), 0),
            func.max(CameraIngestionState.last_received_at),
        )).one()
    except SQLAlchemyError:
        db.session.rollback()
        return _error("migration_pending", "Apply the reviewed camera ingestion migrations before reading status.", 503)
    return jsonify({
        "images_received": int(rows[0]), "images_rejected": int(rows[1]),
        "images_accepted": int(rows[2]), "duplicates_ignored": int(rows[3]),
        "deferred_frames": int(rows[4]), "frames_fetched": int(rows[5]),
        "last_image_received_at": rows[6].isoformat() if rows[6] else None,
    })
