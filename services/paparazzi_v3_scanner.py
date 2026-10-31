from __future__ import annotations

import itertools
import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from services.image_processor import ImageProcessingError

ASSET_DIR = Path(__file__).resolve().parents[1] / "static" / "answer_sheets" / "v3" / "Paparazzi_Answer_Sheets_V3_Package"
PROFILE_PATH = ASSET_DIR / "Paparazzi_Standard_V3_Profile.json"
ORIGINS_PATH = ASSET_DIR / "Paparazzi_Standard_V3_Vector_Origins.json"
CANONICAL_DPI = 300


@dataclass
class V3AlignmentResult:
    aligned_image: np.ndarray
    debug_image: np.ndarray
    detected_marks: list[tuple[float, float]]
    blur_score: float
    orientation: str
    warnings: list[str]


@lru_cache(maxsize=1)
def load_v3_profile() -> dict:
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    origins = json.loads(ORIGINS_PATH.read_text(encoding="utf-8"))
    if profile.get("format") != "Paparazzi Standard" or profile.get("version") != origins.get("version"):
        raise RuntimeError("Paparazzi Standard V3 profile and measured vector origins do not match.")
    profile["student_id"].update(origins["student_id"])
    profile["answers"].update(origins["answers"])
    for question_count, column_origins in origins["answers"]["column_first_bubble_x_mm_by_question_count"].items():
        profile["answers"]["layouts"][question_count]["column_first_bubble_x_mm"] = column_origins
    return profile


def get_student_id_bubble_centers_mm(profile: dict | None = None) -> list[list[tuple[float, float]]]:
    profile = profile or load_v3_profile()
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
    profile = profile or load_v3_profile()
    layout = profile["answers"]["layouts"].get(str(question_count))
    if layout is None:
        raise ValueError("Paparazzi Standard V3 supports 20, 50, or 100 questions.")
    choices = profile["answers"]["choices"]
    column_x = layout["column_first_bubble_x_mm"]
    first_y = profile["answers"]["first_row_center_y_mm"]
    row_pitch = profile["answers"]["row_pitch_mm"]
    choice_pitch = profile["answers"]["choice_pitch_mm"]
    result = {}
    for question_index in range(question_count):
        column_index, row_index = divmod(question_index, layout["rows_per_column"])
        result[question_index + 1] = [
            (column_x[column_index] + choice_index * choice_pitch, first_y + row_index * row_pitch)
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


def _find_square_candidates(gray: np.ndarray) -> list[dict]:
    height, width = gray.shape
    image_area = float(width * height)
    enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    thresholds = []
    _, otsu = cv2.threshold(cv2.GaussianBlur(enhanced, (5, 5), 0), 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    thresholds.append(otsu)
    block_size = max(31, min(101, (min(width, height) // 12) | 1))
    if block_size % 2 == 0:
        block_size += 1
    thresholds.append(cv2.adaptiveThreshold(enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, block_size, 11))

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

    return sorted(candidates, key=lambda item: item["quality"], reverse=True)[:36]


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
    side_error = sum(abs(math.log(candidate["side"] / expected_mark_side)) for candidate in candidates) / 4.0
    aspect_error = min(abs(math.log(aspect / expected_aspect)), abs(math.log(aspect * expected_aspect)))
    side_balance = abs(math.log(width_ratio)) + abs(math.log(height_ratio))
    score = aspect_error * 2.0 + side_error + side_balance * 0.7 - sum(item["quality"] for item in candidates) * 0.08
    return score, ordered


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
    centers = [point for column in get_student_id_bubble_centers_mm(profile) for point in column]
    centers.extend(point for row in get_answer_bubble_centers_mm(question_count, profile).values() for point in row)
    radii = [profile["student_id"]["bubble_radius_mm"]] * 70 + [profile["answers"]["bubble_radius_mm"]] * (question_count * len(profile["answers"]["choices"]))
    values = []
    for (x_mm, y_mm), radius_mm in zip(centers, radii):
        x, y = round(x_mm * px_x), round(y_mm * px_y)
        radius = max(3, round(radius_mm * (px_x + px_y) / 2.0))
        x0, x1 = max(0, x - radius * 2), min(gray.shape[1], x + radius * 2 + 1)
        y0, y1 = max(0, y - radius * 2), min(gray.shape[0], y + radius * 2 + 1)
        region = gray[y0:y1, x0:x1]
        yy, xx = np.ogrid[y0:y1, x0:x1]
        distance = np.sqrt(((xx - x) / radius) ** 2 + ((yy - y) / radius) ** 2)
        ring = region[(distance >= 0.82) & (distance <= 1.18)]
        if ring.size:
            values.append(float(np.mean(ring < 185)))
    return float(np.mean(values)) if values else 0.0


def align_paparazzi_v3(image: np.ndarray, profile: dict | None = None, question_count: int = 20) -> V3AlignmentResult:
    profile = profile or load_v3_profile()
    gray = _gray_image(image)
    candidates = _find_square_candidates(gray)
    if len(candidates) < 4:
        raise ImageProcessingError("Could not find four V3 square registration marks. Include the whole page and all corners.")

    scored = []
    for combination in itertools.combinations(candidates, 4):
        result = _combination_score(combination, profile, gray.shape)
        if result is not None:
            scored.append((result[0], result[1]))
    if not scored:
        raise ImageProcessingError("Detected dark shapes do not form the expected V3 registration-mark geometry.")
    _, ordered = min(scored, key=lambda item: item[0])

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
        aligned_gray = _gray_image(aligned)
        match_score = _layout_match_score(aligned_gray, question_count, profile)
        orientations.append((match_score, aligned, source, shift))
    orientations.sort(key=lambda result: result[0], reverse=True)
    match_score, aligned, source, shift = orientations[0]
    if match_score < 0.08:
        raise ImageProcessingError("The V3 sheet could not be oriented from its registration marks and printed bubble layout.")
    if len(orientations) > 1 and match_score - orientations[1][0] < 0.002:
        raise ImageProcessingError("The V3 page orientation is ambiguous; rotate the image upright and retry.")

    debug = image.copy()
    for point in source:
        cv2.circle(debug, tuple(np.round(point).astype(int)), 8, (0, 120, 255), 2)
    blur_score = float(cv2.Laplacian(_gray_image(aligned), cv2.CV_64F).var())
    warnings = []
    if blur_score < 18.0:
        warnings.append("The rectified page appears blurred; answers require careful manual review.")
    return V3AlignmentResult(aligned, debug, [tuple(map(float, point)) for point in source], blur_score, f"rotation_{shift * 90}", warnings)


def _bubble_fill_score(gray: np.ndarray, center_mm: tuple[float, float], radius_mm: float) -> float:
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
    background = region[(normalized >= 1.28**2) & (normalized <= 1.72**2)]
    if interior.size == 0 or background.size == 0:
        return 0.0
    local_background = float(np.median(background))
    threshold = max(55.0, min(225.0, local_background - max(24.0, local_background * 0.12)))
    coverage = float(np.mean(interior < threshold))
    contrast = float(np.mean(np.maximum(local_background - interior.astype(np.float32), 0.0)) / 255.0)
    return min(1.0, max(0.0, coverage * 0.72 + contrast * 0.28))


def classify_v3_bubbles(values: dict[str, float]) -> dict:
    choices = list(load_v3_profile()["answers"]["choices"])
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
    profile = profile or load_v3_profile()
    centers = get_answer_bubble_centers_mm(question_count, profile)
    gray = _gray_image(aligned_image)
    results = []
    choices = profile["answers"]["choices"]
    for question_number, question_centers in centers.items():
        values = {
            choice: _bubble_fill_score(gray, question_centers[index], profile["answers"]["bubble_radius_mm"])
            for index, choice in enumerate(choices)
        }
        results.append({
            "question_number": question_number,
            **classify_v3_bubbles(values),
            "bubble_centers_mm": question_centers,
        })
    return results


def detect_v3_student_id(aligned_image: np.ndarray, profile: dict | None = None) -> dict:
    profile = profile or load_v3_profile()
    gray = _gray_image(aligned_image)
    section = profile["student_id"]
    columns = get_student_id_bubble_centers_mm(profile)
    digits = []
    for column_number, centers in enumerate(columns, start=1):
        values = {
            str(digit): _bubble_fill_score(gray, centers[digit], section["bubble_radius_mm"])
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
