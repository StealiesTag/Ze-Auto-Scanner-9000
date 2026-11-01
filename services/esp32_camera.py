"""ESP32 MJPEG preview and independent high-resolution still capture client."""
from __future__ import annotations

from urllib.error import HTTPError, URLError
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
            raise CameraError("Could not connect to the ESP32 MJPEG stream.")
        self._stream = cap

    def read_frame(self) -> np.ndarray:
        if self._stream is None or not self._stream.isOpened():
            self.open_stream()
        success, frame = self._stream.read()
        if not success or frame is None or frame.size == 0:
            self.close_stream()
            raise CameraError("The ESP32 live stream disconnected or returned an invalid frame.")
        return frame

    def capture_still(self) -> tuple[bytes, str]:
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
            raise CameraError("Could not retrieve a high-resolution still from the ESP32 camera.") from error
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
