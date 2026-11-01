"""Run one camera producer plus one PostgreSQL-backed scan-draft consumer."""
from __future__ import annotations

import logging
import signal
import threading
import time

import cv2
import numpy as np
from sqlalchemy import select

from app import create_app, db
from models import Assessment, CaptureJob, ScannerControl
from services.capture_queue import (
    acquire_worker_lock,
    claim_next_job,
    ensure_queue_capacity,
    get_scanner_control,
    process_job,
    recover_interrupted_jobs,
    release_worker_lock,
    queue_image_bytes,
    QueueFullError,
)
from services.capture_state_machine import CaptureState, CaptureStateMachine
from services.esp32_camera import CameraError, ESP32Camera
from services.marker_detection import detect_v3_registration_markers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("paparazzi.scan_worker")


def _signature_for_job(job: CaptureJob, app_config: dict) -> list[int] | None:
    folder = app_config["UPLOAD_FOLDER"]
    image = cv2.imread(f"{folder}/{job.image_filename}", cv2.IMREAD_COLOR)
    if image is None:
        return None
    detection = detect_v3_registration_markers(image, min_page_area_ratio=float(app_config["CAMERA_MIN_PAGE_AREA_RATIO"]))
    return detection.get("page_signature") if detection.get("valid") else None


def _latest_capture(app_config: dict) -> CaptureJob | None:
    return db.session.scalar(select(CaptureJob).order_by(CaptureJob.captured_at.desc(), CaptureJob.id.desc()).limit(1))


def _consumer_loop(app, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            with app.app_context():
                job = claim_next_job()
                if job is not None:
                    logger.info("Processing capture %s", job.capture_id)
                    result = process_job(job.id, app.config)
                    logger.info("Capture %s processing status: %s", result.capture_id, result.status)
        except Exception:
            logger.exception("Processing worker iteration failed")
            with app.app_context():
                db.session.rollback()
        stop_event.wait(0.35)


def run_scan_worker(app=None, stop_event: threading.Event | None = None) -> None:
    app = app or create_app()
    stop_event = stop_event or threading.Event()
    lock_connection = None
    with app.app_context():
        lock_connection = acquire_worker_lock()
        if lock_connection is None:
            raise RuntimeError("Another Paparazzi scan worker already holds the PostgreSQL worker lock.")
        try:
            get_scanner_control()
            recovered = recover_interrupted_jobs()
            latest = _latest_capture(app.config)
            machine = CaptureStateMachine(
                stable_frames=int(app.config["CAMERA_STABLE_FRAME_COUNT"]),
                disappearance_frames=int(app.config["CAMERA_BOTTOM_MISSING_FRAME_COUNT"]),
                movement_tolerance_px=float(app.config["CAMERA_MARKER_MOVEMENT_TOLERANCE_PX"]),
                min_page_change_ratio=float(app.config["CAMERA_MIN_PAGE_CHANGE_RATIO"]),
            )
            if latest:
                machine.seed_locked(latest.capture_id, _signature_for_job(latest, app.config))
            if recovered:
                logger.warning("Recovered %s interrupted processing job(s).", recovered)
        except Exception:
            release_worker_lock(lock_connection)
            raise

    consumer = threading.Thread(target=_consumer_loop, args=(app, stop_event), name="paparazzi-scan-consumer", daemon=True)
    consumer.start()
    camera = ESP32Camera(app.config["ESP32_STREAM_URL"], app.config["ESP32_CAPTURE_URL"], app.config["ESP32_REQUEST_TIMEOUT_SECONDS"])
    last_control_state = None
    last_status_write = 0.0
    try:
        while not stop_event.is_set():
            try:
                with app.app_context():
                    control = get_scanner_control()
                    control_state = (control.active, control.paused, control.assessment_id)
                    if control_state != last_control_state and control.active:
                        latest = _latest_capture(app.config)
                        if latest and latest.capture_id != machine.current_capture_id:
                            machine.seed_locked(latest.capture_id, _signature_for_job(latest, app.config))
                    last_control_state = control_state
                    if not control.active:
                        camera.close_stream()
                        control.camera_connected = False
                        control.state = machine.state.value
                        db.session.commit()
                        stop_event.wait(0.5)
                        continue
                    if control.paused:
                        machine.pause()
                        camera.close_stream()
                        control.camera_connected = False
                        control.state = machine.state.value
                        control.stability_status = False
                        db.session.commit()
                        stop_event.wait(0.5)
                        continue
                    if machine.state == CaptureState.PAUSED:
                        machine.resume()
                    if not camera.configured:
                        control.camera_connected = False
                        control.last_error = "Set ESP32_STREAM_URL and ESP32_CAPTURE_URL to enable camera scanning."
                        control.state = machine.state.value
                        db.session.commit()
                        stop_event.wait(1.0)
                        continue
                    frame = camera.read_frame()
                    detection = detect_v3_registration_markers(
                        frame, min_page_area_ratio=float(app.config["CAMERA_MIN_PAGE_AREA_RATIO"])
                    )
                    capture_requested = machine.observe(detection)
                    if capture_requested:
                        control.state = CaptureState.CAPTURING.value
                        control.stability_status = True
                        db.session.commit()
                        try:
                            assessment = db.session.get(Assessment, control.assessment_id) if control.assessment_id else None
                            if (assessment is None or assessment.total_questions not in {20, 50, 100}
                                    or len(assessment.questions) != assessment.total_questions
                                    or any(not question.option_e.strip() for question in assessment.questions)):
                                raise ValueError("Select an existing V3 assessment with a complete A-E answer key before capture.")
                            ensure_queue_capacity(app.config)
                            image_bytes, extension = camera.capture_still()
                            machine.begin_queueing()
                            control.state = CaptureState.QUEUING_IMAGE.value
                            job = queue_image_bytes(
                                image_bytes, extension, control.assessment_id, app.config,
                                source="esp32",
                            )
                            machine.capture_and_queue_succeeded(job.capture_id, detection.get("page_signature"))
                            control.state = machine.state.value
                            control.last_error = None
                            logger.info("Captured and queued new V3 sheet %s", job.capture_id)
                        except Exception as error:
                            db.session.rollback()
                            machine.capture_failed(str(error))
                            control.state = machine.state.value
                            control.last_error = str(error)[:1000]
                            control.paused = True
                            logger.error("Automatic capture failed safely: %s", error)
                    control.camera_connected = True
                    control.sheet_detected = bool(detection.get("valid"))
                    control.marker_status = {key: value for key, value in detection.items() if key != "page_signature"}
                    control.stability_status = machine.state in {CaptureState.STABILIZING, CaptureState.NEW_SHEET_DETECTED}
                    control.state = machine.state.value
                    control.last_error = machine.error if machine.error else control.last_error if not detection.get("valid") else None
                    now = time.monotonic()
                    if now - last_status_write >= 0.75 or capture_requested or control.last_error:
                        db.session.commit()
                        last_status_write = now
                    else:
                        db.session.rollback()
            except CameraError as error:
                camera.close_stream()
                with app.app_context():
                    control = get_scanner_control()
                    control.camera_connected = False
                    control.sheet_detected = False
                    control.marker_status = {"status": "camera_disconnected", "geometry_valid": False}
                    control.last_error = str(error)[:1000]
                    control.state = machine.state.value
                    db.session.commit()
                stop_event.wait(1.0)
            except Exception as error:
                logger.exception("Camera monitoring iteration failed")
                camera.close_stream()
                with app.app_context():
                    db.session.rollback()
                    try:
                        control = get_scanner_control()
                        control.camera_connected = False
                        control.last_error = str(error)[:1000]
                        control.state = machine.state.value
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                stop_event.wait(1.0)
            stop_event.wait(float(app.config["CAMERA_POLL_INTERVAL_SECONDS"]))
    finally:
        camera.close_stream()
        stop_event.set()
        consumer.join()
        with app.app_context():
            db.session.remove()
            release_worker_lock(lock_connection)


def main() -> None:
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_args: stop.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_args: stop.set())
    run_scan_worker(stop_event=stop)


if __name__ == "__main__":
    main()
