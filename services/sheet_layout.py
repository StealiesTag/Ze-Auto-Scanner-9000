"""Shared top-left-origin A4 geometry for PDF generation and image processing."""

PAGE_WIDTH_MM = 210.0
PAGE_HEIGHT_MM = 297.0
CANONICAL_WIDTH = 1240
CANONICAL_HEIGHT = 1754
PIXELS_PER_MM_X = CANONICAL_WIDTH / PAGE_WIDTH_MM
PIXELS_PER_MM_Y = CANONICAL_HEIGHT / PAGE_HEIGHT_MM

MARKER_SIZE_MM = 15.0
MARKER_ORIGINS_MM = {
    0: (10.0, 10.0),
    1: (185.0, 10.0),
    2: (185.0, 272.0),
    3: (10.0, 272.0),
}

QUESTION_LIMIT = 32
QUESTION_FIRST_Y_MM = 97.0
QUESTION_PITCH_MM = 5.2
BUBBLE_X_MM = {"A": 82.0, "B": 108.0, "C": 134.0, "D": 160.0}
BUBBLE_RADIUS_MM = 2.1
BUBBLE_INNER_RADIUS_MM = 1.45
QR_ORIGIN_MM = (158.0, 29.0)
QR_SIZE_MM = 34.0


def bubble_center_mm(question_number: int, option: str) -> tuple[float, float]:
    if question_number < 1 or question_number > QUESTION_LIMIT:
        raise ValueError(f"Question number must be between 1 and {QUESTION_LIMIT}.")
    if option not in BUBBLE_X_MM:
        raise ValueError("Option must be A, B, C, or D.")
    return BUBBLE_X_MM[option], QUESTION_FIRST_Y_MM + (question_number - 1) * QUESTION_PITCH_MM


def mm_to_pixel(point_mm: tuple[float, float]) -> tuple[int, int]:
    x_mm, y_mm = point_mm
    return round(x_mm * PIXELS_PER_MM_X), round(y_mm * PIXELS_PER_MM_Y)