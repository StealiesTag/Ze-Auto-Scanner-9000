from __future__ import annotations

import itertools
import json
import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from services.image_processor import ImageProcessingError

ASSET_DIR = Path(__file__).resolve().parents[1] / "static" / "answer_sheets" / "v3" / "Paparazzi_Answer_Sheets_V3_Package"
PROFILE_PATH = ASSET_DIR / "Paparazzi_Standard_V3_Profile.json"
ORIGINS_PATH = ASSET_DIR / "Paparazzi_Standard_V3_Vector_Origins.json"
LARGE_BUBBLE_50_PROFILE_PATH = ASSET_DIR / "Paparazzi_50Q_Large_Bubble_Profile.json"
V4_PROFILE_PATH = Path(__file__).resolve().parents[1] / "static" / "answer_sheets" / "v4" / "Paparazzi_Answer_Sheets_V4_Package" / "Paparazzi_Standard_V4_Profile.json"
CANONICAL_DPI = 300


@dataclass
class V3AlignmentResult:
    aligned_image: np.ndarray
    debug_image: np.ndarray
    detected_marks: list[tuple[float, float]]
    blur_score: float
    orientation: str
    warnings: list[str]
    layout_score: float = 0.0


@lru_cache(maxsize=2)
def load_v3_profile(profile_variant: str = "standard") -> dict:
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    origins = json.loads(ORIGINS_PATH.read_text(encoding="utf-8"))
    if profile.get("format") != "Paparazzi Standard" or profile.get("version") != origins.get("version"):
        raise RuntimeError("Paparazzi Standard V3 profile and measured vector origins do not match.")
    profile["student_id"].update(origins["student_id"])
    profile["answers"].update(origins["answers"])
    for question_count, column_origins in origins["answers"]["column_first_bubble_x_mm_by_question_count"].items():
        profile["answers"]["layouts"][question_count]["column_first_bubble_x_mm"] = column_origins
    if profile_variant == "50q_large_bubble":
        variant = json.loads(LARGE_BUBBLE_50_PROFILE_PATH.read_text(encoding="utf-8"))
        profile["profile_variant"] = variant["profile_variant"]
        profile["student_id"].update(variant.get("student_id", {}))
        profile["answers"]["bubble_radius_mm"] = variant["answers"]["bubble_radius_mm"]
        profile["answers"]["choice_pitch_mm"] = variant["answers"]["choice_pitch_mm"]
        profile["answers"]["row_pitch_mm"] = variant["answers"]["row_pitch_mm"]
        profile["answers"]["layouts"]["50"].update(variant["answers"]["layouts"]["50"])
    elif profile_variant != "standard":
        raise ValueError(f"Unknown Paparazzi V3 profile variant: {profile_variant}")
    else:
        profile["profile_variant"] = "standard"
    return profile


@lru_cache(maxsize=1)
def load_v4_profile() -> dict:
    return json.loads(V4_PROFILE_PATH.read_text(encoding="utf-8"))


def load_paparazzi_profile(version: str = "V4") -> dict:
    # Legacy callers that pass the former version string now receive the
    # default official layout. Scanning itself selects by question count.
    return load_official_profile(20)


@lru_cache(maxsize=2)
def load_official_profile(question_count: int) -> dict:
    """Return coordinates for the supplied official fixture answer sheets."""
    if question_count == 50:
        profile = json.loads(json.dumps(load_v3_profile("50q_large_bubble")))
        profile["version"] = "PAPARAZZI-OMR-A4-OFFICIAL"
        profile["source_description"] = "The official 50-question fixture sheet."
        profile["student_id"].update({"bubble_radius_mm": 3.81, "row_pitch_mm": 8.4})
        return profile
    if question_count != 20:
        raise ValueError("Official answer sheets are available for 20 or 50 questions.")

    profile = json.loads(json.dumps(load_v4_profile()))
    profile["version"] = "PAPARAZZI-OMR-A4-OFFICIAL"
    profile["source_description"] = "The official 20-question fixture sheet."
    # The supplied 20Q PDF has a single 20-row answer column and a large
    # seven-digit ID grid. Its answer centers match the first column of the
    # supplied large-bubble 50Q sheet.
    profile["student_id"].update({
        "bubble_radius_mm": 3.81,
        "column_pitch_mm": 11.25,
        "row_pitch_mm": 8.4,
        "first_bubble_center_mm": [21.0, 19.01],
    })
    profile["answers"].update({
        "bubble_radius_mm": 3.81,
        "choice_pitch_mm": 11.865,
        "row_pitch_mm": 8.67,
        "layouts": {
            "20": {
                "columns": 1,
                "rows_per_column": 20,
                "questions_per_column": [20],
                "column_first_bubble_x_mm": [24.342],
                "row_centers_y_mm_by_column": [
                    [
                        109.643, 118.279, 127.0, 135.636, 144.441,
                        153.077, 161.713, 170.434, 179.07, 187.791,
                        196.681, 205.317, 214.037, 222.673, 231.479,
                        240.115, 248.751, 257.471, 266.107, 274.828,
                    ]
                ],
            }
        },
    })
    return profile


def get_student_id_bubble_centers_mm(profile: dict | None = None) -> list[list[tuple[float, float]]]:
    profile = profile or load_official_profile(20)
    section = profile["student_id"]
    origin_x, origin_y = section["first_bubble_center_mm"]
    return [
        [
            (origin_x + column * section["column_pitch_mm"], origin_y + digit * section["row_pitch_mm"])
            for digit in section["digit_values"]
        ]
        for column in range(section["digits"])
    ]


def get_answer_bubble_centers_mm(question_count: int, profile: dict | None = None) -> dict[int, list[tuple[float, float]]]:
    profile = profile or load_official_profile(question_count)
    layout = profile["answers"]["layouts"].get(str(question_count))
    if layout is None:
        raise ValueError("Official answer sheets are available for 20 or 50 questions.")
    choices = profile["answers"]["choices"]
    column_x = layout["column_first_bubble_x_mm"]
    first_y = profile["answers"].get("first_row_center_y_mm", 0.0)
    row_pitch = profile["answers"]["row_pitch_mm"]
    choice_pitch = profile["answers"]["choice_pitch_mm"]
    result = {}
    for question_index in range(question_count):
        counts = layout.get("questions_per_column")
        if counts:
            column_index = 0
            offset = question_index
            while column_index < len(counts) - 1 and offset >= counts[column_index]:
                offset -= counts[column_index]
                column_index += 1
            row_index = offset
        else:
            column_index, row_index = divmod(question_index, layout["rows_per_column"])
        row_centers = layout.get("row_centers_y_mm_by_column")
        row_y = row_centers[column_index][row_index] if row_centers else first_y + row_index * row_pitch
        result[question_index + 1] = [
            (column_x[column_index] + choice_index * choice_pitch, row_y)
            for choice_index, _choice in enumerate(choices)
        ]
    return result


def _gray_image(image: np.ndarray) -> np.ndarray:
    if image is None or image.ndim not in (2, 3):
        raise ImageProcessingError("The uploaded image could not be decoded.")
    if image.ndim == 2:
        return image
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def preprocess_v3_image(image: np.ndarray) -> np.ndarray:
    """Normalize contrast and uneven lighting without changing image dimensions."""
    gray = _gray_image(image)
    h, w = gray.shape
    corrected = gray
    if _env_bool("PAPARAZZI_V3_ILLUMINATION_CORRECTION", True):
        sigma = max(15.0, min(h, w) / 24.0)
        background = cv2.GaussianBlur(corrected, (0, 0), sigmaX=sigma, sigmaY=sigma)
        corrected = cv2.divide(corrected, np.maximum(background, 1), scale=220)
    if _env_bool("PAPARAZZI_V3_CLAHE_ENABLED", True):
        clip_limit = _env_float("PAPARAZZI_V3_CLAHE_CLIP_LIMIT", 1.8, 0.1, 8.0)
        grid_size = int(_env_float("PAPARAZZI_V3_CLAHE_GRID_SIZE", 8, 2, 32))
        corrected = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(grid_size, grid_size)).apply(corrected)
    kernel = int(_env_float("PAPARAZZI_V3_DENOISE_MEDIAN_KERNEL", 3, 0, 9))
    if kernel > 1:
        kernel = kernel if kernel % 2 else kernel + 1
        corrected = cv2.medianBlur(corrected, kernel)
    # Only apply conservative unsharp masking when Laplacian variance indicates blur.
    amount = _env_float("PAPARAZZI_V3_SHARPEN_AMOUNT", 0.25, 0.0, 1.0)
    blur_threshold = _env_float("PAPARAZZI_V3_SHARPEN_BLUR_THRESHOLD", 45.0, 0.0, 500.0)
    if amount and float(cv2.Laplacian(corrected, cv2.CV_64F).var()) < blur_threshold:
        softened = cv2.GaussianBlur(corrected, (0, 0), 0.8)
        corrected = cv2.addWeighted(corrected, 1.0 + amount, softened, -amount, 0)
    return corrected


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def _find_square_candidates(gray: np.ndarray) -> list[dict]:
    height, width = gray.shape
    image_area = float(width * height)
    enhanced = preprocess_v3_image(gray)
    thresholds = []
    if _env_bool("PAPARAZZI_V3_THRESHOLD_OTSU", True):
        _, otsu = cv2.threshold(cv2.GaussianBlur(enhanced, (5, 5), 0), 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        thresholds.append(otsu)
    block_size = max(31, min(101, (min(width, height) // 12) | 1))
    configured_block = int(_env_float("PAPARAZZI_V3_ADAPTIVE_BLOCK_SIZE", block_size, 3, 201))
    block_size = configured_block if configured_block % 2 else configured_block + 1
    if block_size % 2 == 0:
        block_size += 1
    if _env_bool("PAPARAZZI_V3_THRESHOLD_ADAPTIVE_GAUSSIAN", True):
        gaussian_c = _env_float("PAPARAZZI_V3_ADAPTIVE_GAUSSIAN_C", 11.0, -20.0, 30.0)
        thresholds.append(cv2.adaptiveThreshold(enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, block_size, gaussian_c))
    if _env_bool("PAPARAZZI_V3_THRESHOLD_ADAPTIVE_MEAN", True):
        mean_c = _env_float("PAPARAZZI_V3_ADAPTIVE_MEAN_C", 7.0, -20.0, 30.0)
        thresholds.append(cv2.adaptiveThreshold(enhanced, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, block_size, mean_c))

    candidates = []
    for binary in thresholds:
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < image_area * 0.000015 or area > image_area * 0.006:
                continue
            perimeter = cv2.arcLength(contour, True)
            if perimeter <= 0:
                continue
            polygon = cv2.approxPolyDP(contour, 0.035 * perimeter, True)
            if len(polygon) != 4 or not cv2.isContourConvex(polygon):
                continue
            rotated = cv2.minAreaRect(contour)
            box_width, box_height = rotated[1]
            if min(box_width, box_height) <= 0:
                continue
            aspect = max(box_width, box_height) / min(box_width, box_height)
            if aspect > 1.38:
                continue
            side_ratio = area / max(1.0, box_width * box_height)
            if side_ratio < 0.72:
                continue
            vertices = polygon.reshape(4, 2).astype(np.float32)
            center = vertices.mean(axis=0)
            side = (box_width + box_height) / 2.0
            quality = side_ratio - abs(math.log(aspect)) * 0.25
            if not any(np.linalg.norm(center - item["center"]) < min(side, item["side"]) * 0.45 for item in candidates):
                candidates.append({"center": center, "vertices": vertices, "side": side, "quality": quality})

    # A registration square can touch its nearby header text after blur and
    # perspective, so contour polygon approximation may merge it with text.
    # Morphological opening recovers isolated, densely filled square marks.
    min_side = max(10, round(min(width, height) * 0.008))
    max_side = round(min(width, height) * 0.06)
    open_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    for threshold_value in (70, 90, 110):
        _, binary = cv2.threshold(enhanced, threshold_value, 255, cv2.THRESH_BINARY_INV)
        opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, open_kernel)
        component_count, _, stats, centroids = cv2.connectedComponentsWithStats(opened)
        for index in range(1, component_count):
            x, y, box_width, box_height, area = map(int, stats[index])
            side = (box_width + box_height) / 2.0
            if not min_side <= side <= max_side or min(box_width, box_height) == 0:
                continue
            aspect = max(box_width, box_height) / min(box_width, box_height)
            fill_ratio = area / float(box_width * box_height)
            if aspect > 1.38 or fill_ratio < 0.82:
                continue
            center = centroids[index].astype(np.float32)
            if not any(np.linalg.norm(center - item["center"]) < min(side, item["side"]) * 0.45 for item in candidates):
                vertices = np.asarray([
                    [x, y], [x + box_width, y],
                    [x + box_width, y + box_height], [x, y + box_height],
                ], dtype=np.float32)
                candidates.append({"center": center, "vertices": vertices, "side": side, "quality": fill_ratio})

    # Large, softly focused photos can produce many bubble and fabric
    # contours ahead of one of the page's registration marks. Retain enough
    # ranked candidates for the page-geometry scorer to recover that mark.
    return sorted(candidates, key=lambda item: item["quality"], reverse=True)[:48]


def _order_quad(points: np.ndarray) -> np.ndarray:
    sums = points.sum(axis=1)
    differences = points[:, 0] - points[:, 1]
    return np.asarray([
        points[np.argmin(sums)],
        points[np.argmax(differences)],
        points[np.argmax(sums)],
        points[np.argmin(differences)],
    ], dtype=np.float32)


def _combination_score(candidates: tuple[dict, ...], profile: dict, image_shape: tuple[int, int]) -> tuple[float, np.ndarray] | None:
    centers = np.asarray([candidate["center"] for candidate in candidates], dtype=np.float32)
    if len(np.unique(np.round(centers, 1), axis=0)) != 4:
        return None
    hull = cv2.convexHull(centers).reshape(-1, 2)
    if len(hull) != 4:
        return None
    ordered = _order_quad(hull)
    if abs(cv2.contourArea(hull)) < image_shape[0] * image_shape[1] * 0.06:
        return None
    top = float(np.linalg.norm(ordered[1] - ordered[0]))
    right = float(np.linalg.norm(ordered[2] - ordered[1]))
    bottom = float(np.linalg.norm(ordered[2] - ordered[3]))
    left = float(np.linalg.norm(ordered[3] - ordered[0]))
    if min(top, right, bottom, left) <= 0:
        return None
    width_ratio = top / bottom
    height_ratio = left / right
    if not 0.55 <= width_ratio <= 1.8 or not 0.55 <= height_ratio <= 1.8:
        return None
    aspect = ((top + bottom) / 2.0) / ((left + right) / 2.0)
    expected_aspect = profile["paper"]["width_mm"] / profile["paper"]["height_mm"]
    if not 0.42 <= aspect <= 1.8:
        return None

    mark_size = profile["registration_marks"]["size_mm"]
    page_width_between_centers = profile["paper"]["width_mm"] - 2 * (profile["registration_marks"]["inset_mm"] + mark_size / 2)
    page_height_between_centers = profile["paper"]["height_mm"] - 2 * (profile["registration_marks"]["inset_mm"] + mark_size / 2)
    frame_width = (top + bottom) / 2.0 if aspect <= 1.0 else (left + right) / 2.0
    frame_height = (left + right) / 2.0 if aspect <= 1.0 else (top + bottom) / 2.0
    expected_mark_side = mark_size * (frame_width / page_width_between_centers + frame_height / page_height_between_centers) / 2.0
    # Registration marks have a known printed size. Reject bubble outlines and
    # other page artifacts that happen to occupy the four expected corners.
    size_ratios = [candidate["side"] / expected_mark_side for candidate in candidates]
    if any(not 0.65 <= ratio <= 1.45 for ratio in size_ratios):
        return None
    side_error = sum(abs(math.log(candidate["side"] / expected_mark_side)) for candidate in candidates) / 4.0
    aspect_error = min(abs(math.log(aspect / expected_aspect)), abs(math.log(aspect * expected_aspect)))
    side_balance = abs(math.log(width_ratio)) + abs(math.log(height_ratio))
    score = aspect_error * 2.0 + side_error + side_balance * 0.7 - sum(item["quality"] for item in candidates) * 0.08
    return score, ordered


def _marker_confidence_from_geometry_score(score: float) -> float:
    """Map the marker-geometry residual to a bounded confidence value."""
    return float(max(0.0, min(1.0, 1.0 - float(score) / 0.75)))


def _profile_mark_centers(profile: dict) -> dict[str, tuple[float, float]]:
    paper = profile["paper"]
    mark = profile["registration_marks"]
    half = mark["size_mm"] / 2.0
    width_mid = paper["width_mm"] / 2.0
    height = paper["height_mm"]
    result = {}
    for x_bottom, y_bottom in mark["positions_bottom_left_origin_mm"]:
        x_center = x_bottom + half
        y_center = height - (y_bottom + half)
        label = ("top" if y_center < paper["height_mm"] / 2 else "bottom") + ("left" if x_center < width_mid else "right")
        result[label] = (x_center, y_center)
    if set(result) != {"topleft", "topright", "bottomleft", "bottomright"}:
        raise ValueError("V3 profile must define one registration mark at each page corner.")
    return result


def _mm_to_pixel(point: tuple[float, float], pixels_per_mm_x: float, pixels_per_mm_y: float) -> tuple[float, float]:
    return point[0] * pixels_per_mm_x, point[1] * pixels_per_mm_y


def _layout_match_score(gray: np.ndarray, question_count: int, profile: dict) -> float:
    px_x = gray.shape[1] / profile["paper"]["width_mm"]
    px_y = gray.shape[0] / profile["paper"]["height_mm"]
    id_centers = [] if not profile["student_id"].get("auto_detect_enabled", True) else [point for column in get_student_id_bubble_centers_mm(profile) for point in column]
    centers = list(id_centers)
    centers.extend(point for row in get_answer_bubble_centers_mm(question_count, profile).values() for point in row)
    radii = [profile["student_id"]["bubble_radius_mm"]] * len(id_centers) + [profile["answers"]["bubble_radius_mm"]] * (question_count * len(profile["answers"]["choices"]))
    values = []
    for (x_mm, y_mm), radius_mm in zip(centers, radii):
        x, y = round(x_mm * px_x), round(y_mm * px_y)
        radius = max(3, round(radius_mm * (px_x + px_y) / 2.0))
        x0, x1 = max(0, x - radius * 2), min(gray.shape[1], x + radius * 2 + 1)
        y0, y1 = max(0, y - radius * 2), min(gray.shape[0], y + radius * 2 + 1)
        region = gray[y0:y1, x0:x1]
        yy, xx = np.ogrid[y0:y1, x0:x1]
        distance = np.sqrt(((xx - x) / radius) ** 2 + ((yy - y) / radius) ** 2)
        ring = region[(distance >= 0.88) & (distance <= 1.12)]
        if ring.size:
            values.append(float(np.mean(ring < 185)))
    return float(np.mean(values)) if values else 0.0


def align_paparazzi_v3(
    image: np.ndarray,
    profile: dict | None = None,
    question_count: int = 20,
    *,
    min_marker_confidence: float = 0.10,
    marker_candidates: list[dict] | None = None,
) -> V3AlignmentResult:
    if not 0.0 <= min_marker_confidence <= 1.0:
        raise ValueError("Minimum marker confidence must be between 0 and 1.")
    profile = profile or load_official_profile(question_count)
    gray = _gray_image(image)
    normalized_gray = preprocess_v3_image(gray)
    scored = []
    if marker_candidates is not None:
        candidate_groups = [tuple(marker_candidates)] if len(marker_candidates) == 4 else []
    else:
        candidates = [
            candidate for candidate in _find_square_candidates(gray)
            if candidate["side"] >= max(12.0, min(gray.shape) * 0.015)
        ]
        if len(candidates) < 4:
            raise ImageProcessingError("Could not find the four registration marks. Include the whole page and all corners.")
        candidate_groups = itertools.combinations(candidates, 4)
    for combination in candidate_groups:
        result = _combination_score(combination, profile, gray.shape)
        if result is not None:
            scored.append((result[0], result[1]))
    if not scored:
        raise ImageProcessingError("Detected dark shapes do not match the official registration-mark geometry.")
    geometry_score, ordered = min(scored, key=lambda item: item[0])
    marker_confidence = _marker_confidence_from_geometry_score(geometry_score)
    if marker_confidence < min_marker_confidence:
        raise ImageProcessingError(
            "The four registration marks do not match the official sheet layout confidently. "
            "Retake the photo with all four corners sharp, evenly lit, and unobstructed."
        )

    page = profile["paper"]
    scale = CANONICAL_DPI / 25.4
    output_width = round(page["width_mm"] * scale)
    output_height = round(page["height_mm"] * scale)
    target_centers = _profile_mark_centers(profile)
    target = np.asarray([
        _mm_to_pixel(target_centers[key], output_width / page["width_mm"], output_height / page["height_mm"])
        for key in ("topleft", "topright", "bottomright", "bottomleft")
    ], dtype=np.float32)

    orientations = []
    for shift in range(4):
        source = np.roll(ordered, -shift, axis=0).astype(np.float32)
        transform = cv2.getPerspectiveTransform(source, target)
        aligned = cv2.warpPerspective(image, transform, (output_width, output_height), borderValue=(255, 255, 255))
        normalized_aligned = cv2.warpPerspective(normalized_gray, transform, (output_width, output_height), borderValue=255)
        match_score = _layout_match_score(normalized_aligned, question_count, profile)
        orientations.append((match_score, aligned, source, shift))
    orientations.sort(key=lambda result: result[0], reverse=True)
    match_score, aligned, source, shift = orientations[0]
    if match_score < 0.08:
        raise ImageProcessingError("The sheet could not be oriented from its registration marks and printed bubble layout.")
    if len(orientations) > 1 and match_score - orientations[1][0] < 0.002:
        raise ImageProcessingError("The page orientation is ambiguous; rotate the image upright and retry.")

    debug = image.copy()
    for point in source:
        cv2.circle(debug, tuple(np.round(point).astype(int)), 8, (0, 120, 255), 2)
    blur_score = float(cv2.Laplacian(_gray_image(aligned), cv2.CV_64F).var())
    warnings = []
    if blur_score < 18.0:
        warnings.append("The rectified page appears blurred; answers require careful manual review.")
    return V3AlignmentResult(aligned, debug, [tuple(map(float, point)) for point in source], blur_score, f"rotation_{shift * 90}", warnings, match_score)


def align_paparazzi_v3_auto(
    image: np.ndarray,
    question_count: int,
    *,
    min_marker_confidence: float = 0.10,
    marker_candidates: list[dict] | None = None,
) -> tuple[dict, V3AlignmentResult]:
    """Align using the official sheet profile for this assessment's question count."""
    profile = load_official_profile(question_count)
    alignment = align_paparazzi_v3(
        image, profile, question_count=question_count,
        min_marker_confidence=min_marker_confidence,
        marker_candidates=marker_candidates,
    )
    return profile, alignment


def _bubble_fill_score(
    gray: np.ndarray,
    center_mm: tuple[float, float],
    radius_mm: float,
    background_pitch_mm: float | None = None,
) -> float:
    pixels_per_mm_x = gray.shape[1] / 210.0
    pixels_per_mm_y = gray.shape[0] / 297.0
    x, y = round(center_mm[0] * pixels_per_mm_x), round(center_mm[1] * pixels_per_mm_y)
    radius_x = max(3, round(radius_mm * pixels_per_mm_x))
    radius_y = max(3, round(radius_mm * pixels_per_mm_y))
    x0, x1 = max(0, x - round(radius_x * 1.8)), min(gray.shape[1], x + round(radius_x * 1.8) + 1)
    y0, y1 = max(0, y - round(radius_y * 1.8)), min(gray.shape[0], y + round(radius_y * 1.8) + 1)
    region = gray[y0:y1, x0:x1]
    yy, xx = np.ogrid[y0:y1, x0:x1]
    normalized = ((xx - x) / radius_x) ** 2 + ((yy - y) / radius_y) ** 2
    interior = region[normalized <= 0.62**2]
    ring_inner_ratio = 1.02
    ring_outer_ratio = 1.72
    if background_pitch_mm is not None:
        ring_outer_ratio = min(ring_outer_ratio, max(0.0, (background_pitch_mm / 2.0 - 0.12) / radius_mm))
    if ring_outer_ratio <= ring_inner_ratio:
        return 0.0
    background = region[(normalized >= ring_inner_ratio**2) & (normalized <= ring_outer_ratio**2)]
    if interior.size == 0 or background.size == 0:
        return 0.0
    local_background = float(np.median(background))
    threshold = max(55.0, min(225.0, local_background - max(24.0, local_background * 0.12)))
    coverage = float(np.mean(interior < threshold))
    contrast = float(np.mean(np.maximum(local_background - interior.astype(np.float32), 0.0)) / 255.0)
    return min(1.0, max(0.0, coverage * 0.72 + contrast * 0.28))


def classify_v3_bubbles(values: dict[str, float]) -> dict:
    choices = list(load_official_profile(20)["answers"]["choices"])
    ordered = sorted(((float(values.get(choice, 0.0)), choice) for choice in choices), reverse=True)
    top_score, top_choice = ordered[0]
    second_score = ordered[1][0]
    marked = [choice for score, choice in ordered if score >= 0.24]
    if len(marked) > 1:
        return {"answer": None, "status": "multiple_marks", "confidence": 0.0, "values": values}
    if top_score < 0.12:
        return {"answer": None, "status": "blank", "confidence": min(1.0, 1.0 - top_score), "values": values}
    if top_score < 0.24 or top_score - second_score < 0.10:
        return {"answer": None, "status": "ambiguous", "confidence": min(1.0, max(0.0, top_score - second_score)), "values": values}
    confidence = min(1.0, max(0.0, (top_score - second_score) * 1.5 + top_score * 0.35))
    return {"answer": top_choice, "status": "detected", "confidence": confidence, "values": values}


def detect_v3_answers(aligned_image: np.ndarray, question_count: int, profile: dict | None = None) -> list[dict]:
    profile = profile or load_official_profile(question_count)
    centers = get_answer_bubble_centers_mm(question_count, profile)
    gray = preprocess_v3_image(aligned_image)
    results = []
    choices = profile["answers"]["choices"]
    for question_number, question_centers in centers.items():
        values = {
            choice: _bubble_fill_score(
                gray,
                question_centers[index],
                profile["answers"]["bubble_radius_mm"],
                min(profile["answers"]["choice_pitch_mm"], profile["answers"]["row_pitch_mm"]),
            )
            for index, choice in enumerate(choices)
        }
        results.append({
            "question_number": question_number,
            **classify_v3_bubbles(values),
            "bubble_centers_mm": question_centers,
        })
    return results


def detect_v3_student_id(aligned_image: np.ndarray, profile: dict | None = None) -> dict:
    profile = profile or load_official_profile(20)
    gray = preprocess_v3_image(aligned_image)
    section = profile["student_id"]
    if not section.get("auto_detect_enabled", True):
        return {
            "student_id": None,
            "status": "manual_required",
            "confidence": 0.0,
            "reason": section.get("manual_review_reason", "Student ID requires manual review."),
            "digits": [],
        }
    columns = get_student_id_bubble_centers_mm(profile)
    digits = []
    for column_number, centers in enumerate(columns, start=1):
        values = {
            str(digit): _bubble_fill_score(
                gray,
                centers[digit],
                section["bubble_radius_mm"],
                min(section["column_pitch_mm"], section["row_pitch_mm"]),
            )
            for digit in section["digit_values"]
        }
        ordered = sorted(((score, digit) for digit, score in values.items()), reverse=True)
        top_score, top_digit = ordered[0]
        second_score = ordered[1][0]
        marked = [digit for digit, score in values.items() if score >= 0.24]
        if len(marked) > 1:
            status, digit, confidence = "multiple_marks", None, 0.0
        elif top_score < 0.12:
            status, digit, confidence = "blank", None, min(1.0, 1.0 - top_score)
        elif top_score < 0.24 or top_score - second_score < 0.10:
            status, digit, confidence = "ambiguous", None, min(1.0, max(0.0, top_score - second_score))
        else:
            status, digit = "detected", top_digit
            confidence = min(1.0, max(0.0, (top_score - second_score) * 1.5 + top_score * 0.35))
        digits.append({"column": column_number, "digit": digit, "status": status, "confidence": confidence, "values": values})
    valid = all(column["status"] == "detected" for column in digits)
    overall_status = "detected" if valid else next((column["status"] for column in digits if column["status"] != "detected"), "ambiguous")
    return {
        "student_id": "".join(column["digit"] for column in digits) if valid else None,
        "status": overall_status,
        "confidence": min((column["confidence"] for column in digits), default=0.0),
        "digits": digits,
    }
