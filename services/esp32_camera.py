"""ESP32 MJPEG preview and still-image scanning client."""
from __future__ import annotations

from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import cv2
import numpy as np


class CameraError(RuntimeError):
    pass


class ESP32Camera:
    def __init__(self, stream_url: str, capture_url: str, timeout_seconds: float = 8.0):
        self.stream_url = (stream_url or "").strip()
        self.capture_url = (capture_url or "").strip()
        self.timeout_seconds = float(timeout_seconds)
        self._stream = None
        self._last_read_still: tuple[bytes, str] | None = None

    @property
    def configured(self) -> bool:
        return bool(self.stream_url and self.capture_url)

    def open_stream(self) -> None:
        if not self.stream_url:
            raise CameraError("Set ESP32_STREAM_URL before starting automatic scanning.")
        cap = cv2.VideoCapture()
        try:
            opened = cap.open(self.stream_url, cv2.CAP_FFMPEG, [
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(self.timeout_seconds * 1000),
                cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(self.timeout_seconds * 1000),
            ])
        except (cv2.error, TypeError):
            opened = cap.open(self.stream_url)
        if not opened or not cap.isOpened():
            cap.release()
            parsed = urlsplit(self.stream_url)
            host = parsed.hostname or "the configured camera"
            host_url = f"[{host}]" if ":" in host else host
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            if parsed.path in {"", "/"} and parsed.scheme == "http":
                stream_url = f"http://{host_url}:81/stream"
                raise CameraError(
                    f"The saved URL points to the ESP32 web page, not its MJPEG stream. "
                    f"For the standard CameraWebServer, use {stream_url}; also verify that "
                    f"the camera is online at {host_url}."
                )
            raise CameraError(
                f"Could not open the ESP32 MJPEG endpoint at {host_url}:{port}{parsed.path or '/'}; "
                "verify the camera is powered, connected to the same Wi-Fi, and using the "
                "saved IP. The standard CameraWebServer stream is usually on port 81 at /stream."
            )
        self._stream = cap

    def read_frame(self) -> np.ndarray:
        # The browser preview already owns the long-lived MJPEG connection on
        # some ESP32 CameraWebServer builds. Read scan frames from /capture so
        # the server preview and scanner do not compete for that single stream.
        self._last_read_still = self._fetch_still()
        payload, _extension = self._last_read_still
        frame = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None or frame.size == 0:
            self._last_read_still = None
            raise CameraError("The ESP32 capture endpoint returned an invalid image frame.")
        return frame

    def capture_still(self) -> tuple[bytes, str]:
        if self._last_read_still is not None:
            payload = self._last_read_still
            self._last_read_still = None
            return payload
        return self._fetch_still()

    def _fetch_still(self) -> tuple[bytes, str]:
        if not self.capture_url:
            raise CameraError("Set ESP32_CAPTURE_URL before requesting a high-resolution still.")
        request = Request(self.capture_url, headers={"Accept": "image/jpeg,image/png"}, method="GET")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                if response.status < 200 or response.status >= 300:
                    raise CameraError(f"ESP32 capture endpoint returned HTTP {response.status}.")
                content_type = response.headers.get_content_type().lower()
                if content_type not in {"image/jpeg", "image/png", "application/octet-stream"}:
                    raise CameraError("ESP32 capture endpoint did not return a JPEG or PNG image.")
                payload = response.read(32 * 1024 * 1024 + 1)
        except HTTPError as error:
            raise CameraError(f"ESP32 capture endpoint returned HTTP {error.code}.") from error
        except (URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            detail = str(reason).splitlines()[0][:180]
            raise CameraError(
                "Could not retrieve a high-resolution still from the ESP32 camera. "
                f"Check that the saved camera address is reachable from the Paparazzi computer ({detail})."
            ) from error
        if not payload or len(payload) > 32 * 1024 * 1024:
            raise CameraError("ESP32 capture response is empty or exceeds the 32 MB safety limit.")
        is_png = payload.startswith(b"\x89PNG\r\n\x1a\n")
        is_jpeg = payload.startswith(b"\xff\xd8\xff")
        if not (is_png or is_jpeg):
            raise CameraError("ESP32 capture response bytes are not a JPEG or PNG image.")
        if content_type == "image/png" and not is_png:
            raise CameraError("ESP32 response MIME type does not match its image contents.")
        if content_type == "image/jpeg" and not is_jpeg:
            raise CameraError("ESP32 response MIME type does not match its image contents.")
        decoded = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
        if decoded is None or decoded.shape[0] < 400 or decoded.shape[1] < 400:
            raise CameraError("ESP32 capture response is not a valid full-page image (minimum 400x400 pixels).")
        extension = ".png" if is_png else ".jpg"
        return payload, extension

    def close_stream(self) -> None:
        if self._stream is not None:
            self._stream.release()
            self._stream = None
        self._last_read_still = None
