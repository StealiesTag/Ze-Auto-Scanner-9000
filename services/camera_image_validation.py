"""Lightweight upload decoding and V3 registration-marker validation."""
from __future__ import annotations

import cv2
import numpy as np

from services.marker_detection import detect_v3_registration_markers


SUPPORTED_TYPES = {
    "image/jpeg": (".jpg", b"\xff\xd8\xff"),
    "image/png": (".png", b"\x89PNG\r\n\x1a\n"),
}


def validate_camera_image(
    image_bytes: bytes,
    content_type: str,
    app_config: dict,
) -> dict:
    """Decode bytes and validate four V3 markers; never touches OMR or the database."""
    normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
    image_format = SUPPORTED_TYPES.get(normalized_type)
    if image_format is None:
        return _invalid("unsupported_content_type", "Only JPEG and PNG camera images are supported.")
    extension, signature = image_format
    if not image_bytes:
        return _invalid("empty_image", "The uploaded image is empty.")
    if len(image_bytes) > int(app_config["MAX_CONTENT_LENGTH"]):
        return _invalid("image_too_large", "The uploaded image exceeds the configured size limit.")
    if not image_bytes.startswith(signature):
        return _invalid("invalid_image", "The image bytes do not match the declared content type.")
    image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return _invalid("corrupted_image", "The uploaded image could not be decoded.")
    height, width = image.shape[:2]
    if min(width, height) < 400 or max(width, height) > int(app_config["MAX_IMAGE_DIMENSION"]):
        return _invalid("invalid_dimensions", "The image must show a full page and remain within configured dimensions.")
    detection = detect_v3_registration_markers(
        image,
        min_page_area_ratio=float(app_config["CAMERA_MIN_PAGE_AREA_RATIO"]),
    )
    confidence = float(detection.get("confidence") or 0.0)
    confidence_valid = confidence >= float(app_config["CAMERA_MIN_MARKER_CONFIDENCE"])
    valid = bool(detection.get("valid") and detection.get("geometry_valid") and confidence_valid)
    reason = None
    if not detection.get("geometry_valid"):
        reason = "registration_markers_not_found" if detection.get("status") == "markers_not_found" else "invalid_marker_geometry"
    elif not detection.get("sheet_sufficiently_visible"):
        reason = "sheet_not_fully_visible"
    elif not confidence_valid:
        reason = "marker_confidence_too_low"
    return {
        "valid": valid,
        "reason": reason,
        "detected_markers": int(detection.get("detected_markers") or 0),
        "marker_coordinates": detection.get("marker_coordinates") or {},
        "geometry_valid": bool(detection.get("geometry_valid")),
        "confidence": confidence,
        "bottom_markers_visible": bool(detection.get("bottom_markers_visible")),
        "sheet_sufficiently_visible": bool(detection.get("sheet_sufficiently_visible")),
        "page_signature": detection.get("page_signature"),
        "extension": extension,
        "image": image,
    }


def _invalid(reason: str, message: str) -> dict:
    return {
        "valid": False,
        "reason": reason,
        "message": message,
        "detected_markers": 0,
        "marker_coordinates": {},
        "geometry_valid": False,
        "confidence": 0.0,
        "bottom_markers_visible": False,
        "sheet_sufficiently_visible": False,
        "page_signature": None,
        "extension": None,
        "image": None,
    }
