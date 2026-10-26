from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from services.sheet_layout import (
    CANONICAL_HEIGHT,
    CANONICAL_WIDTH,
    MARKER_ORIGINS_MM,
    MARKER_SIZE_MM,
    PIXELS_PER_MM_X,
    PIXELS_PER_MM_Y,
)


class ImageProcessingError(ValueError):
    """An upload cannot be reliably processed as an answer sheet."""


@dataclass
class AlignmentResult:
    aligned_image: np.ndarray
    debug_image: np.ndarray
    detected_marker_ids: list[int]
    blur_score: float
    warnings: list[str]


def validate_uploaded_image(file_storage, max_bytes: int = 16 * 1024 * 1024, max_dimension: int = 8000) -> dict:
    filename = file_storage.filename or ""
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in {"jpg", "jpeg", "png"}:
        raise ImageProcessingError("Only JPEG and PNG uploads are supported.")
    file_storage.seek(0)
    content = file_storage.read(max_bytes + 1)
    file_storage.seek(0)
    if len(content) > max_bytes:
        raise ImageProcessingError("The image exceeds the configured upload-size limit.")
    try:
        with Image.open(BytesIO(content)) as image:
            image_format = image.format
            width, height = image.size
            image.verify()
    except (UnidentifiedImageError, OSError) as error:
        raise ImageProcessingError("The upload is not a valid image file.") from error
    expected_format = "PNG" if extension == "png" else "JPEG"
    if image_format != expected_format:
        raise ImageProcessingError("The file extension does not match the image content.")
    if width < 400 or height < 400 or max(width, height) > max_dimension:
        raise ImageProcessingError("Image dimensions must be at least 400 pixels and no more than the configured maximum.")
    return {"filename": filename, "width": width, "height": height, "format": image_format, "size_bytes": len(content)}


def load_uploaded_image(file_storage, max_bytes: int, max_dimension: int) -> tuple[np.ndarray, dict]:
    metadata = validate_uploaded_image(file_storage, max_bytes, max_dimension)
    file_storage.seek(0)
    with Image.open(file_storage) as image:
        oriented = ImageOps.exif_transpose(image).convert("RGB")
        rgb = np.asarray(oriented)
    file_storage.seek(0)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), metadata


def _detect_markers(image: np.ndarray):
    if not hasattr(cv2, "aruco"):
        raise ImageProcessingError(
            "This OpenCV installation has no ArUco module. Install opencv-contrib-python-headless."
        )
    aruco = cv2.aruco
    dictionary = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
    parameters = aruco.DetectorParameters()
    if hasattr(aruco, "ArucoDetector"):
        detector = aruco.ArucoDetector(dictionary, parameters)
        return detector.detectMarkers(image)
    return aruco.detectMarkers(image, dictionary, parameters=parameters)


def align_sheet(image: np.ndarray) -> AlignmentResult:
    if image is None or image.ndim not in (2, 3):
        raise ImageProcessingError("The uploaded image could not be decoded.")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    corners, identifiers, _ = _detect_markers(gray)
    debug_image = image.copy()
    if identifiers is None:
        raise ImageProcessingError("No ArUco markers were detected. Include all four printed page corners.")

    detected = {int(marker_id): marker_corners.reshape(4, 2) for marker_id, marker_corners in zip(identifiers.flatten(), corners)}
    missing = set(MARKER_ORIGINS_MM) - detected.keys()
    if missing:
        cv2.aruco.drawDetectedMarkers(debug_image, corners, identifiers)
        raise ImageProcessingError(f"The sheet is cropped or obscured; missing corner marker(s): {', '.join(map(str, sorted(missing)))}.")

    source_points = []
    target_points = []
    for marker_id, (x_mm, y_mm) in MARKER_ORIGINS_MM.items():
        source_points.extend(detected[marker_id])
        target_points.extend([
            (x_mm * PIXELS_PER_MM_X, y_mm * PIXELS_PER_MM_Y),
            ((x_mm + MARKER_SIZE_MM) * PIXELS_PER_MM_X, y_mm * PIXELS_PER_MM_Y),
            ((x_mm + MARKER_SIZE_MM) * PIXELS_PER_MM_X, (y_mm + MARKER_SIZE_MM) * PIXELS_PER_MM_Y),
            (x_mm * PIXELS_PER_MM_X, (y_mm + MARKER_SIZE_MM) * PIXELS_PER_MM_Y),
        ])
    homography, inliers = cv2.findHomography(
        np.asarray(source_points, dtype=np.float32),
        np.asarray(target_points, dtype=np.float32),
        method=cv2.RANSAC,
        ransacReprojThreshold=5.0,
    )
    if homography is None or inliers is None or int(inliers.sum()) < 12:
        raise ImageProcessingError("The page corners could not be aligned reliably. Retake the photo with the full page visible.")

    aligned = cv2.warpPerspective(image, homography, (CANONICAL_WIDTH, CANONICAL_HEIGHT), borderValue=(255, 255, 255))
    cv2.aruco.drawDetectedMarkers(debug_image, corners, identifiers)
    blur_region = aligned[round(85 * PIXELS_PER_MM_Y):round(265 * PIXELS_PER_MM_Y), :]
    blur_gray = cv2.cvtColor(blur_region, cv2.COLOR_BGR2GRAY)
    blur_score = float(cv2.Laplacian(blur_gray, cv2.CV_64F).var())
    warnings = []
    if blur_score < 20.0:
        warnings.append("The aligned page appears blurred; inspect answers before grading.")
    return AlignmentResult(aligned, debug_image, sorted(detected.keys()), blur_score, warnings)


def decode_qr(image: np.ndarray) -> str:
    decoded, _, _ = cv2.QRCodeDetector().detectAndDecode(image)
    if not decoded:
        raise ImageProcessingError("No readable QR code was found. Generate a fresh sheet and include its full QR code in the scan.")
    return decoded
