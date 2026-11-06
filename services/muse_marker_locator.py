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

_PROMPT_VERSION = "paparazzi-profile-pixel-boxes-3"
_PROMPT = """TASK: locate registration marks on the official Paparazzi answer sheet. The input is one complete camera image with exact dimensions {width} pixels wide by {height} pixels high. The sheet is {paper_width} x {paper_height} mm, and the registration marks are {marker_size} mm square.

WHAT TO FIND: exactly four solid black SQUARE registration marks printed on the paper, one near each page corner. Find the actual square ink marks. They are the large, filled, dark squares at the extreme corners of the page; they are larger than the answer bubbles. Their expected page-relative centers are: {corner_centers} mm from the page's top-left. The sheet may be upside down or rotated in the photo and may occupy only part of the frame. Follow the page's four corners, not the image's orientation.

WHAT NOT TO FIND: never mark answer bubbles, the student-ID grid, the rectangular outline around the ID grid, printed text, page edges, objects, shadows, patterns, or dark areas on the table/background. Answer bubbles are hollow circles. The student-ID grid has many circles and is NOT a registration mark. A registration mark is a solid square at a page corner.
{sheet_hint}

COORDINATES: return ABSOLUTE PIXEL coordinates in the original unresized image, with (0,0) at the top-left. Do NOT return normalized 0-1000 coordinates, percentages, or coordinates for a cropped/resized image. Each bbox must tightly enclose the OUTER EDGES of exactly one black square, including all four sides but no surrounding page/background: [left_px, top_px, right_px, bottom_px].

LABELS: return exactly four boxes, one each for top_left, top_right, bottom_right, and bottom_left according to their geometric positions in the full image. The four box centers must form one large quadrilateral around the same complete sheet and match the relative corner positions described above. Boxes must be similar in size and must not overlap. If you cannot see all four actual squares, return an empty markers list. Never estimate, extrapolate, or invent a missing square.

Return only JSON in this form: {\"markers\":[{\"corner\":\"top_left\",\"bbox_px\":[left,top,right,bottom]}]}."""


def _profile_prompt(profile: dict | None, width: int, height: int) -> str:
    paper = (profile or {}).get("paper", {})
    paper_width = float(paper.get("width_mm", 210.0))
    paper_height = float(paper.get("height_mm", 297.0))
    marks = (profile or {}).get("registration_marks", {})
    marker_size = float(marks.get("size_mm", 9.03))
    centers = {}
    for x_bottom, y_bottom in marks.get("positions_bottom_left_origin_mm", []):
        center_x = float(x_bottom) + marker_size / 2.0
        center_y = paper_height - (float(y_bottom) + marker_size / 2.0)
        corner = ("top" if center_y < paper_height / 2 else "bottom") + ("_left" if center_x < paper_width / 2 else "_right")
        centers[corner] = (round(center_x, 1), round(center_y, 1))
    corner_text = "; ".join(
        f"{corner} ({centers[corner][0]}, {centers[corner][1]})"
        for corner in ("top_left", "top_right", "bottom_right", "bottom_left")
        if corner in centers
    ) or "at the four extreme corners"
    sheet_hint = ""
    if "20Q_Big" in str((profile or {}).get("source_description", "")):
        sheet_hint = (
            " FORMAT NOTE: this 20Q_Big sheet has a large outlined seven-column student-ID grid in its upper-left area "
            "and a single vertical column of 20 answer rows. Those outlines and circles are not corner marks."
        )
    return (
        _PROMPT.replace("{width}", str(width)).replace("{height}", str(height))
        .replace("{paper_width}", f"{paper_width:g}").replace("{paper_height}", f"{paper_height:g}")
        .replace("{marker_size}", f"{marker_size:g}").replace("{corner_centers}", corner_text)
        .replace("{sheet_hint}", sheet_hint)
    )


class MuseMarkerLocatorError(RuntimeError):
    pass


class MuseMarkerLocator:
    """Call Muse Spark only when configured; cache repeated camera frames."""

    def __init__(self, config: dict, profile: dict | None = None):
        self.api_key = str(config.get("MUSE_API_KEY") or "").strip()
        self.base_url = str(config.get("MUSE_BASE_URL") or "https://api.meta.ai/v1").rstrip("/")
        self.model = str(config.get("MUSE_MODEL") or "muse-spark-1.3")
        self.timeout = float(config.get("MUSE_TIMEOUT_SECONDS", 12.0))
        self.min_interval = float(config.get("MUSE_MIN_INTERVAL_SECONDS", 2.0))
        self.profile = profile
        self._last_call = 0.0
        self._last_thumb: np.ndarray | None = None
        self._cached_candidates: list[dict] | None = None
        self._cached_shape: tuple[int, int] | None = None
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def locate(self, image: np.ndarray, profile: dict | None = None) -> list[dict] | None:
        with self._lock:
            if profile is not None:
                if profile != self.profile:
                    self._last_thumb = None
                    self._cached_shape = None
                    self._cached_candidates = None
                self.profile = profile
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
            candidates = self._request_candidates(gray, self.profile)
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

    def _request_candidates(self, gray: np.ndarray, profile: dict | None = None) -> list[dict]:
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
                    {"type": "text", "text": _profile_prompt(profile, gray.shape[1], gray.shape[0])},
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
            if marker["corner"] in by_corner:
                return []
            box = marker.get("bbox_px")
            if (not isinstance(box, list) or len(box) != 4
                    or any(not isinstance(value, (int, float)) or not np.isfinite(value) for value in box)):
                continue
            x0, y0, x1, y1 = map(float, box)
            if x0 < 0 or y0 < 0 or x1 > width or y1 > height or x1 <= x0 or y1 <= y0:
                continue
            left, top, right, bottom = x0, y0, x1, y1
            box_width, box_height = right - left, bottom - top
            if min(box_width, box_height) <= 0 or max(box_width, box_height) / min(box_width, box_height) > 2.0:
                continue
            center = np.asarray([(left + right) / 2.0, (top + bottom) / 2.0], dtype=np.float32)
            side = (box_width + box_height) / 2.0
            vertices = np.asarray([[left, top], [right, top], [right, bottom], [left, bottom]], dtype=np.float32)
            by_corner[marker["corner"]] = {"center": center, "vertices": vertices, "side": side, "quality": 0.95}
        if len(by_corner) != 4:
            return []
        expected = ("top_left", "top_right", "bottom_right", "bottom_left")
        return [by_corner[name] for name in expected]


def configured_muse_locator(config: dict, profile: dict | None = None) -> MuseMarkerLocator | None:
    api_key = str(config.get("MUSE_API_KEY") or "").strip()
    if not api_key:
        return None
    marks = (profile or {}).get("registration_marks", {})
    profile_key = (
        (profile or {}).get("version"),
        (profile or {}).get("paper", {}).get("width_mm"),
        (profile or {}).get("paper", {}).get("height_mm"),
        marks.get("size_mm"),
        tuple(tuple(point) for point in marks.get("positions_bottom_left_origin_mm", [])),
    )
    cache_key = (
        hashlib.sha256(api_key.encode("utf-8")).hexdigest(),
        config.get("MUSE_BASE_URL", "https://api.meta.ai/v1"),
        config.get("MUSE_MODEL", "muse-spark-1.3"),
        _PROMPT_VERSION,
        profile_key,
        config.get("MUSE_TIMEOUT_SECONDS", 12.0),
        config.get("MUSE_MIN_INTERVAL_SECONDS", 2.0),
    )
    with _LOCATOR_CACHE_LOCK:
        if cache_key not in _LOCATOR_CACHE:
            _LOCATOR_CACHE[cache_key] = MuseMarkerLocator(config, profile)
        return _LOCATOR_CACHE[cache_key]
