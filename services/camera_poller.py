"""Independent, guarded poller for ESP32 CameraWebServer `/capture` URLs."""
from __future__ import annotations

import ipaddress
import argparse
import logging
import signal
import socket
import threading
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import cv2
import numpy as np
from sqlalchemy import text

from extensions import db
from models import CameraIngestionState
from services.camera_ingestion import ingest_camera_image_bytes, record_poller_state


logger = logging.getLogger("paparazzi.camera_poller")
MAX_CAMERA_RESPONSE_BYTES = 32 * 1024 * 1024


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        # Never let a configured camera endpoint redirect the server elsewhere.
        return None


def _open_camera_without_redirects(request, timeout):
    return build_opener(_NoRedirectHandler()).open(request, timeout=timeout)


class CameraPollerError(RuntimeError):
    def __init__(self, message: str, *, malformed_frame: bool = False):
        super().__init__(message)
        self.malformed_frame = malformed_frame


def validate_camera_url(url: str) -> str:
    """Only permit explicitly configured HTTP(S) URLs with a literal local IP host."""
    parsed = urlsplit((url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("PAPARAZZI_CAMERA_URL must be an HTTP(S) URL without embedded credentials.")
    if parsed.fragment:
        raise ValueError("PAPARAZZI_CAMERA_URL must not contain a URL fragment.")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as error:
        raise ValueError("PAPARAZZI_CAMERA_URL must use a literal private/local IP address to prevent SSRF.") from error
    if address.is_multicast or address.is_unspecified or not (address.is_private or address.is_loopback or address.is_link_local):
        raise ValueError("PAPARAZZI_CAMERA_URL must target a private, loopback, or link-local device address.")
    try:
        parsed.port
    except ValueError as error:
        raise ValueError("PAPARAZZI_CAMERA_URL has an invalid port.") from error
    if parsed.query:
        raise ValueError("PAPARAZZI_CAMERA_URL must not contain a query string.")
    return url


def fetch_camera_jpeg(url: str, timeout_seconds: float, opener=_open_camera_without_redirects) -> bytes:
    """Fetch one uncached JPEG response, rejecting HTTP, MIME, and image corruption."""
    validate_camera_url(url)
    request = Request(
        url,
        headers={"Accept": "image/jpeg", "Cache-Control": "no-cache, no-store", "Pragma": "no-cache"},
        method="GET",
    )
    try:
        with opener(request, timeout=float(timeout_seconds)) as response:
            status = int(response.status)
            if not 200 <= status < 300:
                raise CameraPollerError(f"Camera returned HTTP {status}.")
            content_type = response.headers.get_content_type().lower()
            payload = response.read(MAX_CAMERA_RESPONSE_BYTES + 1)
    except HTTPError as error:
        raise CameraPollerError(f"Camera returned HTTP {error.code}.") from error
    except (URLError, TimeoutError, socket.timeout, OSError) as error:
        raise CameraPollerError("Could not retrieve a frame from the configured camera.") from error
    if content_type not in {"image/jpeg", "application/octet-stream"}:
        raise CameraPollerError("Camera response is not JPEG content.", malformed_frame=True)
    if not payload or len(payload) > MAX_CAMERA_RESPONSE_BYTES or not payload.startswith(b"\xff\xd8\xff"):
        raise CameraPollerError("Camera response does not contain a valid JPEG byte stream.", malformed_frame=True)
    decoded = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    if decoded is None or decoded.size == 0:
        raise CameraPollerError("Camera response JPEG is corrupted.", malformed_frame=True)
    return payload


class CameraPoller:
    def __init__(self, app, *, opener=_open_camera_without_redirects, stop_event: threading.Event | None = None):
        self.app = app
        self.url = validate_camera_url(app.config.get("PAPARAZZI_CAMERA_URL", ""))
        self.camera_id = app.config["PAPARAZZI_CAMERA_ID"]
        self.interval = float(app.config["PAPARAZZI_CAMERA_POLL_INTERVAL"])
        self.timeout = float(app.config["PAPARAZZI_CAMERA_TIMEOUT"])
        self.opener = opener
        self.stop_event = stop_event or threading.Event()
        if not self.camera_id or len(self.camera_id) > 100:
            raise ValueError("PAPARAZZI_CAMERA_ID must contain 1-100 characters.")
        if self.interval <= 0 or self.timeout <= 0:
            raise ValueError("Camera poll interval and timeout must be positive.")

    def poll_once(self) -> dict:
        with self.app.app_context():
            try:
                image_bytes = fetch_camera_jpeg(self.url, self.timeout, self.opener)
            except CameraPollerError as error:
                record_poller_state(
                    self.camera_id,
                    connection_status="connected" if error.malformed_frame else "disconnected",
                    poller_running=True,
                    last_error=str(error),
                    fetched=error.malformed_frame,
                    malformed_frame=error.malformed_frame,
                )
                logger.warning("Camera %s poll failed: %s", self.camera_id, error)
                return {"status": "error", "reason": str(error)}
            except Exception as error:
                record_poller_state(
                    self.camera_id, connection_status="disconnected", poller_running=True,
                    last_error="Unexpected camera polling error.",
                )
                logger.exception("Unexpected camera poller error")
                return {"status": "error", "reason": str(error)}

            record_poller_state(
                self.camera_id, connection_status="connected", poller_running=True,
                last_error=None, fetched=True,
            )
            result = ingest_camera_image_bytes(
                image_bytes, "image/jpeg", self.camera_id,
                capture_id=uuid.uuid4().hex,
            )
            if result.payload.get("status") == "error":
                record_poller_state(
                    self.camera_id, connection_status="connected", poller_running=True,
                    last_error=result.payload.get("message", "Image ingestion failed."),
                )
            return result.payload

    def run(self) -> None:
        lock_connection = None
        owns_lock_connection = False
        lock_key = f"paparazzi-camera-poller:{self.camera_id}"
        with self.app.app_context():
            engine_or_test_connection = db.engines[None]
            if hasattr(engine_or_test_connection, "connect"):
                lock_connection = engine_or_test_connection.connect()
                owns_lock_connection = True
            else:
                lock_connection = engine_or_test_connection
            acquired = lock_connection.scalar(
                text("SELECT pg_try_advisory_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": lock_key},
            )
            if not acquired:
                if owns_lock_connection:
                    lock_connection.close()
                raise RuntimeError(f"A poller already holds the lock for camera {self.camera_id}.")
            record_poller_state(self.camera_id, poller_running=True, connection_status="stopped", last_error=None)
        next_poll_at = time.monotonic()
        try:
            while not self.stop_event.is_set():
                try:
                    with self.app.app_context():
                        state = db.session.get(CameraIngestionState, self.camera_id)
                        enabled = bool(state and state.polling_enabled)
                        db.session.rollback()
                    if enabled:
                        self.poll_once()
                    else:
                        with self.app.app_context():
                            record_poller_state(self.camera_id, connection_status="stopped", poller_running=True, last_error=None)
                except Exception:
                    logger.exception("Camera poller loop failed; it will reconnect on the next interval")
                    with self.app.app_context():
                        db.session.rollback()
                        try:
                            record_poller_state(self.camera_id, connection_status="disconnected", poller_running=True, last_error="Poller loop failed; retrying.")
                        except Exception:
                            db.session.rollback()
                next_poll_at += self.interval
                now = time.monotonic()
                if next_poll_at < now:
                    # Skip missed intervals rather than building a catch-up burst.
                    next_poll_at = now
                self.stop_event.wait(max(0.0, next_poll_at - time.monotonic()))
        finally:
            with self.app.app_context():
                db.session.remove()
                try:
                    record_poller_state(self.camera_id, poller_running=False, connection_status="stopped")
                finally:
                    db.session.remove()
                    lock_connection.execute(
                        text("SELECT pg_advisory_unlock(hashtextextended(:lock_key, 0))"),
                        {"lock_key": lock_key},
                    )
                    if owns_lock_connection:
                        lock_connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll a configured local ESP32 CameraWebServer and queue V3 sheets.")
    parser.parse_args()
    from app import create_app

    app = create_app()
    stop_event = threading.Event()
    signal.signal(signal.SIGINT, lambda *_args: stop_event.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_args: stop_event.set())
    CameraPoller(app, stop_event=stop_event).run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
