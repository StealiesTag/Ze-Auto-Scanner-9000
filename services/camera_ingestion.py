"""Shared camera validation, replacement state, and queue transaction."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from extensions import db
from models import Assessment, CameraIngestionState, CaptureJob, ScannerControl
from services.camera_image_validation import validate_camera_image
from services.capture_queue import QueueFullError, queue_image_bytes
from services.capture_state_machine import CaptureStateMachine
from services.paparazzi_v3_scanner import load_official_profile


@dataclass
class IngestionResult:
    payload: dict
    http_status: int = 200


def configured_machine() -> CaptureStateMachine:
    cfg = current_app.config
    return CaptureStateMachine(
        stable_frames=int(cfg["CAMERA_STABLE_FRAME_COUNT"]),
        disappearance_frames=int(cfg["CAMERA_BOTTOM_MISSING_FRAME_COUNT"]),
        movement_tolerance_px=float(cfg["CAMERA_MARKER_MOVEMENT_TOLERANCE_PX"]),
        min_page_change_ratio=float(cfg["CAMERA_MIN_PAGE_CHANGE_RATIO"]),
    )


def lock_camera_state(camera_id: str) -> CameraIngestionState:
    db.session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:camera_key, 0))"),
        {"camera_key": f"paparazzi-camera:{camera_id}"},
    )
    state = db.session.execute(
        select(CameraIngestionState).where(CameraIngestionState.camera_id == camera_id).with_for_update()
    ).scalar_one_or_none()
    if state is None:
        state = CameraIngestionState(camera_id=camera_id, controller_state={})
        db.session.add(state)
        db.session.flush()
    return state


def record_poller_state(
    camera_id: str,
    *,
    connection_status: str | None = None,
    poller_running: bool | None = None,
    last_error: str | None = None,
    fetched: bool = False,
    malformed_frame: bool = False,
) -> None:
    """Persist poller status/counters without treating malformed bytes as a sheet observation."""
    state = lock_camera_state(camera_id)
    now = datetime.now(timezone.utc)
    if connection_status is not None:
        state.connection_status = connection_status
    if poller_running is not None:
        state.poller_running = poller_running
    state.last_error = (last_error or None)
    if fetched:
        state.frames_fetched += 1
        state.last_fetch_at = now
        state.last_received_at = now
    if malformed_frame:
        state.received_count += 1
        state.rejected_count += 1
    db.session.commit()


def ingest_camera_image_bytes(
    image_bytes: bytes,
    content_type: str,
    camera_id: str,
    capture_id: str | None = None,
    captured_at: datetime | None = None,
) -> IngestionResult:
    """Run the same transaction for API and locally polled camera images."""
    camera_id = (camera_id or "").strip()
    capture_id = (capture_id or uuid.uuid4().hex).strip()
    if not camera_id or len(camera_id) > 100 or any(ord(char) < 32 for char in camera_id):
        return IngestionResult({"success": False, "status": "error", "reason": "invalid_camera_id", "message": "camera_id must be 1-100 printable characters."}, 400)
    if len(capture_id) > 64 or not all(char.isalnum() or char in "-_" for char in capture_id):
        return IngestionResult({"success": False, "status": "error", "reason": "invalid_capture_id", "message": "capture_id may contain at most 64 letters, digits, hyphens, or underscores."}, 400)
    if captured_at is None:
        captured_at = datetime.now(timezone.utc)

    setup_control = db.session.get(ScannerControl, 1)
    setup_assessment = db.session.get(Assessment, setup_control.assessment_id) if setup_control and setup_control.assessment_id else None
    profile = load_official_profile(setup_assessment.total_questions) if setup_assessment and setup_assessment.total_questions in {20, 50} else load_official_profile(20)
    validation = validate_camera_image(image_bytes, content_type, current_app.config, profile=profile)
    try:
        camera_state = lock_camera_state(camera_id)
        camera_state.received_count += 1
        camera_state.last_received_at = captured_at
        existing = db.session.scalar(select(CaptureJob).where(CaptureJob.capture_id == capture_id))
        if existing is not None:
            camera_state.duplicate_count += 1
            db.session.commit()
            return IngestionResult({"success": True, "status": "duplicate", "capture_id": capture_id, "message": "This capture_id has already been accepted."})

        if not validation["valid"]:
            camera_state.rejected_count += 1
            machine = configured_machine()
            machine.restore(camera_state.controller_state)
            machine.observe({
                "valid": False, "markers_visible": False, "geometry_valid": False,
                "sheet_sufficiently_visible": False, "bottom_markers_visible": False,
                "marker_coordinates": {},
            })
            camera_state.controller_state = machine.to_dict()
            db.session.commit()
            return IngestionResult({
                "success": True, "status": "rejected",
                "reason": validation.get("reason") or "invalid_image",
                "message": validation.get("message") or "The image does not contain a confidently validated Paparazzi answer sheet.",
                "detected_markers": validation["detected_markers"],
                "marker_coordinates": validation["marker_coordinates"],
                "geometry_valid": validation["geometry_valid"], "confidence": validation["confidence"],
                "locator_source": validation.get("locator_source"),
            })

        control = setup_control
        assessment = setup_assessment
        if assessment is None or assessment.answer_sheet_version != "V4" or assessment.total_questions not in {20, 50}:
            db.session.rollback()
            return IngestionResult({"success": False, "status": "error", "reason": "assessment_not_selected", "message": "Select a complete 20- or 50-question assessment in Automatic Scanning before accepting images."}, 409)
        if len(assessment.questions) != assessment.total_questions:
            db.session.rollback()
            return IngestionResult({"success": False, "status": "error", "reason": "assessment_not_ready", "message": "The selected assessment needs a complete answer key."}, 409)

        machine = configured_machine()
        machine.restore(camera_state.controller_state)
        detector_result = {
            "valid": True, "markers_visible": validation["detected_markers"] == 4,
            "geometry_valid": validation["geometry_valid"],
            "sheet_sufficiently_visible": validation["sheet_sufficiently_visible"],
            "bottom_markers_visible": validation["bottom_markers_visible"],
            "marker_coordinates": validation["marker_coordinates"],
            "page_signature": validation["page_signature"],
        }
        should_capture = machine.observe(detector_result)
        if not should_capture:
            machine_state = machine.state.value
            is_locked_duplicate = machine_state in {"LOCKED", "WAITING_FOR_BOTTOM_MARKER_DISAPPEARANCE"}
            camera_state.duplicate_count += int(is_locked_duplicate)
            camera_state.deferred_count += int(not is_locked_duplicate)
            camera_state.controller_state = machine.to_dict()
            db.session.commit()
            return IngestionResult({
                "success": True, "status": "duplicate" if is_locked_duplicate else "deferred",
                "reason": "same_sheet_or_awaiting_stability",
                "message": "Image observed; waiting for a stable new sheet after bottom-marker disappearance.",
                "capture_id": capture_id, "detected_markers": validation["detected_markers"],
                "marker_coordinates": validation["marker_coordinates"],
                "geometry_valid": validation["geometry_valid"], "confidence": validation["confidence"],
                "locator_source": validation.get("locator_source"),
            })

        machine.begin_queueing()
        detection_metadata = {
            "valid": True, "detected_markers": validation["detected_markers"],
            "marker_coordinates": validation["marker_coordinates"],
            "geometry_valid": validation["geometry_valid"], "confidence": validation["confidence"],
            "sheet_sufficiently_visible": validation["sheet_sufficiently_visible"],
            "bottom_markers_visible": validation["bottom_markers_visible"],
            "locator_source": validation.get("locator_source"),
        }
        queued_file = Path(current_app.config["UPLOAD_FOLDER"]).resolve() / f"capture-{capture_id}{validation['extension']}"
        try:
            job = queue_image_bytes(
                image_bytes, validation["extension"], assessment.id, current_app.config,
                source="api", capture_id=capture_id, camera_id=camera_id,
                captured_at=captured_at, detection_metadata=detection_metadata, commit=False,
            )
            machine.capture_and_queue_succeeded(capture_id, validation["page_signature"])
            camera_state.controller_state = machine.to_dict()
            camera_state.accepted_count += 1
            db.session.commit()
        except Exception:
            db.session.rollback()
            queued_file.unlink(missing_ok=True)
            raise
        return IngestionResult({
            "success": True, "status": "queued", "capture_id": job.capture_id,
            "message": "Valid answer sheet accepted for processing.",
            "detected_markers": validation["detected_markers"],
            "marker_coordinates": validation["marker_coordinates"],
            "geometry_valid": validation["geometry_valid"], "confidence": validation["confidence"],
            "locator_source": validation.get("locator_source"),
            "job_id": job.id,
        }, 202)
    except QueueFullError as error:
        db.session.rollback()
        return IngestionResult({"success": False, "status": "error", "reason": "queue_full", "message": str(error)}, 429)
    except IntegrityError:
        db.session.rollback()
        return IngestionResult({"success": True, "status": "duplicate", "capture_id": capture_id, "message": "This capture_id was already accepted."})
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Camera image ingestion database failure")
        return IngestionResult({"success": False, "status": "error", "reason": "database_unavailable", "message": "Camera ingestion storage is unavailable; no image was queued."}, 503)
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Camera image ingestion failed")
        return IngestionResult({"success": False, "status": "error", "reason": "ingestion_failed", "message": "The image could not be safely accepted."}, 500)
