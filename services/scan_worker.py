"""Consume queued scanner captures; the browser coordinates one-page feed steps."""
from __future__ import annotations

import logging
import signal
import threading

from sqlalchemy import select

from app import create_app, db
from models import CaptureJob
from services.capture_queue import (
    acquire_worker_lock,
    claim_next_job,
    get_scanner_control,
    process_job,
    recover_interrupted_jobs,
    release_worker_lock,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("paparazzi.scan_worker")


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
    """Run the queue consumer; capture timing is driven by the one-page printer loop."""
    app = app or create_app()
    stop_event = stop_event or threading.Event()
    with app.app_context():
        lock_connection = acquire_worker_lock()
        if lock_connection is None:
            raise RuntimeError("Another Paparazzi scan worker already holds the PostgreSQL worker lock.")
        try:
            get_scanner_control()
            recovered = recover_interrupted_jobs()
            if recovered:
                logger.warning("Recovered %s interrupted processing job(s).", recovered)
        except Exception:
            release_worker_lock(lock_connection)
            raise

    consumer = threading.Thread(target=_consumer_loop, args=(app, stop_event), name="paparazzi-scan-consumer", daemon=True)
    consumer.start()
    logger.info("Queue consumer started; browser handles each one-page print and still-capture step.")
    try:
        stop_event.wait()
    finally:
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
