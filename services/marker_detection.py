"""Fast live-frame validation for the registered Paparazzi Standard V3 page."""
from __future__ import annotations

import itertools

import cv2
import numpy as np

from services.paparazzi_v3_scanner import (
    _combination_score,
    _find_square_candidates,
    _order_quad,
    _profile_mark_centers,
    load_v3_profile,
)


def detect_v3_registration_markers(frame: np.ndarray, profile: dict | None = None, *, min_page_area_ratio: float = 0.25) -> dict:
    """Find four markers and verify their spacing, expected size, and full-page framing.

    Candidate extraction uses adaptive/Otsu thresholding with CLAHE from the scanner's
    existing geometry code. Only the best 20 square candidates are combined to keep
    live-frame cost bounded.
    """
    profile = profile or load_v3_profile()
    if frame is None or not isinstance(frame, np.ndarray) or frame.ndim not in (2, 3) or frame.size == 0:
        return _empty_detection("invalid_frame")
    gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY if frame.shape[2] == 4 else cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    if width < 160 or height < 160:
        return _empty_detection("frame_too_small")
    candidates = _find_square_candidates(gray)[:20]
    if len(candidates) < 4:
        return _empty_detection("markers_not_found")

    combinations = []
    for group in itertools.combinations(candidates, 4):
        scored = _combination_score(group, profile, gray.shape)
        if scored is not None:
            combinations.append((scored[0], _order_quad(np.asarray([item["center"] for item in group], dtype=np.float32))))
    if not combinations:
        return _empty_detection("invalid_marker_geometry")
    score, points = min(combinations, key=lambda result: result[0])
    corner_names = ("top_left", "top_right", "bottom_right", "bottom_left")
    marker_coordinates = {name: [float(points[index][0]), float(points[index][1])] for index, name in enumerate(corner_names)}
    centers = _profile_mark_centers(profile)
    expected_mm = np.asarray([centers[name.replace("_", "")] for name in ("top_left", "top_right", "bottom_right", "bottom_left")], dtype=np.float32)
    observed = points.astype(np.float32)
    transform = cv2.getPerspectiveTransform(expected_mm, observed)
    paper = profile["paper"]
    page_mm = np.asarray([[0, 0], [paper["width_mm"], 0], [paper["width_mm"], paper["height_mm"]], [0, paper["height_mm"]]], dtype=np.float32).reshape(1, 4, 2)
    page_polygon = cv2.perspectiveTransform(page_mm, transform)[0]
    frame_margin_x, frame_margin_y = width * 0.015, height * 0.015
    page_inside_frame = bool(
        np.all(page_polygon[:, 0] >= -frame_margin_x)
        and np.all(page_polygon[:, 0] <= width + frame_margin_x)
        and np.all(page_polygon[:, 1] >= -frame_margin_y)
        and np.all(page_polygon[:, 1] <= height + frame_margin_y)
    )
    page_area = abs(cv2.contourArea(page_polygon.astype(np.float32)))
    frame_area_ratio = min(1.0, page_area / float(width * height))
    sufficiently_visible = page_inside_frame and frame_area_ratio >= min_page_area_ratio
    signature_transform = cv2.getPerspectiveTransform(observed, expected_mm)
    signature_scale = np.asarray([
        [96.0 / paper["width_mm"], 0.0, 0.0],
        [0.0, 136.0 / paper["height_mm"], 0.0],
        [0.0, 0.0, 1.0],
    ], dtype=np.float64)
    normalized = cv2.warpPerspective(gray, signature_scale @ signature_transform, (96, 136), borderValue=255)
    normalized = cv2.equalizeHist(normalized)
    adaptive = cv2.adaptiveThreshold(normalized, 1, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 15, 5)
    signature = cv2.resize(adaptive, (96, 136), interpolation=cv2.INTER_NEAREST)
    page_signature = adaptive.ravel().tolist()
    status = "valid" if sufficiently_visible else "page_not_fully_visible"
    bottom_visible = all(marker_coordinates[name] for name in ("bottom_left", "bottom_right"))
    return {
        "markers_visible": len(marker_coordinates) == 4,
        "marker_coordinates": marker_coordinates,
        "bottom_markers_visible": bottom_visible,
        "geometry_valid": True,
        "geometry_score": float(score),
        "confidence": float(max(0.0, min(1.0, 1.0 - score / 0.75))),
        "sheet_sufficiently_visible": sufficiently_visible,
        "page_inside_frame": page_inside_frame,
        "page_frame_area_ratio": frame_area_ratio,
        "page_signature": page_signature,
        "valid": sufficiently_visible,
        "status": status,
    }


def _empty_detection(status: str) -> dict:
    return {
        "markers_visible": False,
        "marker_coordinates": {},
        "bottom_markers_visible": False,
        "geometry_valid": False,
        "geometry_score": None,
        "confidence": 0.0,
        "sheet_sufficiently_visible": False,
        "page_inside_frame": False,
        "page_frame_area_ratio": 0.0,
        "valid": False,
        "status": status,
    }
