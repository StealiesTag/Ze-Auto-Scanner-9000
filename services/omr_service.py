from __future__ import annotations

import cv2
import numpy as np

from services.sheet_layout import BUBBLE_INNER_RADIUS_MM, PIXELS_PER_MM_X, PIXELS_PER_MM_Y, bubble_center_mm

DEFAULT_FILL_THRESHOLD = 0.18
DEFAULT_AMBIGUITY_MARGIN = 0.10
DARK_PIXEL_THRESHOLD = 150


def classify_bubbles(
    bubble_values: dict[str, float],
    fill_threshold: float = DEFAULT_FILL_THRESHOLD,
    ambiguity_margin: float = DEFAULT_AMBIGUITY_MARGIN,
) -> dict:
    values = {option: float(bubble_values.get(option, 0.0)) for option in "ABCD"}
    marked = [option for option, value in values.items() if value >= fill_threshold]
    if len(marked) > 1:
        return {"answer": None, "status": "multiple_marks", "confidence": 0.0, "values": values}
    if not marked:
        return {"answer": None, "status": "blank", "confidence": 1.0 - max(values.values()), "values": values}

    answer = marked[0]
    ordered = sorted(values.values(), reverse=True)
    margin = ordered[0] - ordered[1]
    if margin < ambiguity_margin:
        return {"answer": None, "status": "ambiguous", "confidence": max(0.0, margin), "values": values}
    return {"answer": answer, "status": "detected", "confidence": min(1.0, margin), "values": values}


def _bubble_fill_ratio(gray: np.ndarray, center: tuple[int, int]) -> float:
    x, y = center
    radius_x = max(2, round(BUBBLE_INNER_RADIUS_MM * PIXELS_PER_MM_X))
    radius_y = max(2, round(BUBBLE_INNER_RADIUS_MM * PIXELS_PER_MM_Y))
    x0, x1 = max(0, x - radius_x), min(gray.shape[1], x + radius_x + 1)
    y0, y1 = max(0, y - radius_y), min(gray.shape[0], y + radius_y + 1)
    region = gray[y0:y1, x0:x1]
    yy, xx = np.ogrid[y0:y1, x0:x1]
    mask = ((xx - x) / radius_x) ** 2 + ((yy - y) / radius_y) ** 2 <= 1
    pixels = region[mask]
    if pixels.size == 0:
        return 0.0
    return float(np.count_nonzero(pixels < DARK_PIXEL_THRESHOLD) / pixels.size)


def detect_answers(
    aligned_image: np.ndarray,
    questions,
    fill_threshold: float = DEFAULT_FILL_THRESHOLD,
    ambiguity_margin: float = DEFAULT_AMBIGUITY_MARGIN,
) -> list[dict]:
    if aligned_image is None or aligned_image.shape[:2] != (1754, 1240):
        raise ValueError("OMR requires an aligned 1240 by 1754 pixel sheet.")
    gray = cv2.cvtColor(aligned_image, cv2.COLOR_BGR2GRAY) if aligned_image.ndim == 3 else aligned_image
    results = []
    for question in questions:
        number = int(question.question_number if hasattr(question, "question_number") else question)
        values = {}
        for option in "ABCD":
            center_mm = bubble_center_mm(number, option)
            center = (round(center_mm[0] * PIXELS_PER_MM_X), round(center_mm[1] * PIXELS_PER_MM_Y))
            values[option] = _bubble_fill_ratio(gray, center)
        result = classify_bubbles(values, fill_threshold, ambiguity_margin)
        results.append({"question_number": number, **result})
    return results
