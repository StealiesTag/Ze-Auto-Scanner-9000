"""Muse Spark locator for Paparazzi V4 registration markers.

The model proposes marker bounding boxes only. Callers must still validate the
four points against the printed V4 geometry before using them for alignment.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import cv2
import numpy as np

logger = logging.getLogger(__name__)
_LOCATOR_CACHE: dict[tuple, "MuseMarkerLocator"] = {}
_LOCATOR_CACHE_LOCK = threading.Lock()

_PROMPT = """Locate the four solid black square registration marks printed at the four outer corners of this official Paparazzi answer-sheet page. These are square fiducial marks, not filled answer bubbles, text, or furniture. Return exactly four markers when visible, each with corner set to top_left, top_right, bottom_right, or bottom_left according to its position in THIS IMAGE, and bbox as [x_min, y_min, x_max, y_max] on a normalized 0-1000 coordinate grid whose origin is the image's top-left. Return only JSON matching {\"markers\":[{\"corner\":\"top_left\",\"bbox\":[0,0,0,0]}]}. If any corner mark is not visible, return it as absent; never guess."""


class MuseMarkerLocatorError(RuntimeError):
    pass


class MuseMarkerLocator:
    """Call Muse Spark only when configured; cache repeated camera frames."""

    def __init__(self, config: dict):
        self.api_key = str(config.get("MUSE_API_KEY") or "").strip()
        self.base_url = str(config.get("MUSE_BASE_URL") or "https://api.meta.ai/v1").rstrip("/")
        self.model = str(config.get("MUSE_MODEL") or "muse-spark-1.3")
        self.timeout = float(config.get("MUSE_TIMEOUT_SECONDS", 12.0))
        self.min_interval = float(config.get("MUSE_MIN_INTERVAL_SECONDS", 2.0))
        self._last_call = 0.0
        self._last_thumb: np.ndarray | None = None
        self._cached_candidates: list[dict] | None = None
        self._cached_shape: tuple[int, int] | None = None
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def locate(self, image: np.ndarray) -> list[dict] | None:
        with self._lock:
            return self._locate_locked(image)

    def _locate_locked(self, image: np.ndarray) -> list[dict] | None:
        if not self.enabled:
            return None
        gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        thumb = cv2.resize(gray, (64, 64), interpolation=cv2.INTER_AREA)
        if (self._cached_candidates is not None and self._last_thumb is not None
                and self._cached_shape == gray.shape):
            if float(cv2.absdiff(thumb, self._last_thumb).mean()) <= 3.0:
                return [dict(candidate) for candidate in self._cached_candidates]
        now = time.monotonic()
        if now - self._last_call < self.min_interval:
            return None
        self._last_call = now
        try:
            candidates = self._request_candidates(gray)
        except (MuseMarkerLocatorError, HTTPError, URLError, TimeoutError, ValueError) as error:
            logger.warning("Muse Spark marker localization failed: %s", error)
            return None
        if candidates:
            self._last_thumb = thumb
            self._cached_shape = gray.shape
            self._cached_candidates = candidates
            return [dict(candidate) for candidate in candidates]
        self._last_thumb = None
        self._cached_shape = None
        self._cached_candidates = None
        return None

    def _request_candidates(self, gray: np.ndarray) -> list[dict]:
        success, encoded = cv2.imencode(".jpg", gray, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not success:
            raise MuseMarkerLocatorError("Could not encode the frame for marker localization.")
        image_data = base64.b64encode(encoded.tobytes()).decode("ascii")
        payload = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": _PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}},
                ],
            }],
        }
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise MuseMarkerLocatorError(f"Meta Model API returned HTTP {error.code}.") from error
        except (URLError, TimeoutError) as error:
            raise MuseMarkerLocatorError("Meta Model API could not be reached.") from error
        try:
            content = response_data["choices"][0]["message"]["content"]
            result = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            raise MuseMarkerLocatorError("Muse Spark returned an unreadable marker response.") from error
        markers = result.get("markers") if isinstance(result, dict) else None
        if not isinstance(markers, list):
            return []
        height, width = gray.shape
        by_corner = {}
        for marker in markers:
            if not isinstance(marker, dict) or marker.get("corner") not in {"top_left", "top_right", "bottom_right", "bottom_left"}:
                continue
            box = marker.get("bbox")
            if (not isinstance(box, list) or len(box) != 4
                    or any(not isinstance(value, (int, float)) or not 0 <= value <= 1000 for value in box)):
                continue
            x0, y0, x1, y1 = map(float, box)
            if x1 <= x0 or y1 <= y0:
                continue
            left, top = x0 * width / 1000.0, y0 * height / 1000.0
            right, bottom = x1 * width / 1000.0, y1 * height / 1000.0
            center = np.asarray([(left + right) / 2.0, (top + bottom) / 2.0], dtype=np.float32)
            side = (right - left + bottom - top) / 2.0
            vertices = np.asarray([[left, top], [right, top], [right, bottom], [left, bottom]], dtype=np.float32)
            by_corner[marker["corner"]] = {"center": center, "vertices": vertices, "side": side, "quality": 0.95}
        if len(by_corner) != 4:
            return []
        expected = ("top_left", "top_right", "bottom_right", "bottom_left")
        return [by_corner[name] for name in expected]


def configured_muse_locator(config: dict) -> MuseMarkerLocator | None:
    api_key = str(config.get("MUSE_API_KEY") or "").strip()
    if not api_key:
        return None
    cache_key = (
        hashlib.sha256(api_key.encode("utf-8")).hexdigest(),
        config.get("MUSE_BASE_URL", "https://api.meta.ai/v1"),
        config.get("MUSE_MODEL", "muse-spark-1.3"),
        config.get("MUSE_TIMEOUT_SECONDS", 12.0),
        config.get("MUSE_MIN_INTERVAL_SECONDS", 2.0),
    )
    with _LOCATOR_CACHE_LOCK:
        if cache_key not in _LOCATOR_CACHE:
            _LOCATOR_CACHE[cache_key] = MuseMarkerLocator(config)
        return _LOCATOR_CACHE[cache_key]
