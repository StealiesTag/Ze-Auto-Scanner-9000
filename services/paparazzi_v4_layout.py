"""Canonical Paparazzi V4 PDF generator and scanner geometry profile."""
from __future__ import annotations

import json
from pathlib import Path

import pymupdf


ASSET_DIR = Path(__file__).resolve().parents[1] / "static" / "answer_sheets" / "v4" / "Paparazzi_Answer_Sheets_V4_Package"
PAGE_WIDTH_MM, PAGE_HEIGHT_MM = 210.0, 297.0
MARK_SIZE_MM, MARK_INSET_MM = 6.0, 7.0
BUBBLE_RADIUS_MM = 2.1
ID_X_MM, ID_Y_MM = 38.0, 29.0
ID_COLUMN_PITCH_MM, ID_ROW_PITCH_MM = 8.0, 5.5
CHOICE_PITCH_MM = 8.2


def _rows(start: float, pitch: float, count: int) -> list[float]:
    return [round(start + index * pitch, 4) for index in range(count)]


PROFILE = {
    "format": "Paparazzi Standard",
    "version": "PAPARAZZI-OMR-A4-V4",
    "source_description": "Generated from the Paparazzi V4 vector PDF generator in this repository.",
    "paper": {"size": "A4", "width_mm": PAGE_WIDTH_MM, "height_mm": PAGE_HEIGHT_MM, "orientation": "portrait"},
    "student_id": {
        "auto_detect_enabled": True,
        "digits": 7,
        "digit_values": list(range(10)),
        "columns_labeled": list(range(1, 8)),
        "encoding": "one digit per column, left to right; zero is the top row",
        "bubble_diameter_mm": 4.2,
        "bubble_radius_mm": BUBBLE_RADIUS_MM,
        "column_pitch_mm": ID_COLUMN_PITCH_MM,
        "row_pitch_mm": ID_ROW_PITCH_MM,
        "first_bubble_center_mm": [ID_X_MM, ID_Y_MM],
    },
    "answers": {
        "choices": ["A", "B", "C", "D", "E"],
        "bubble_diameter_mm": 4.2,
        "bubble_radius_mm": BUBBLE_RADIUS_MM,
        "choice_pitch_mm": CHOICE_PITCH_MM,
        "row_pitch_mm": 6.7,
        "layouts": {
            "20": {"columns": 2, "rows_per_column": 10, "questions_per_column": [10, 10],
                   "column_first_bubble_x_mm": [40.0, 126.0],
                   "row_centers_y_mm_by_column": [_rows(120.0, 9.5, 10), _rows(120.0, 9.5, 10)]},
            "50": {"columns": 2, "rows_per_column": 25, "questions_per_column": [25, 25],
                   "column_first_bubble_x_mm": [40.0, 126.0],
                   "row_centers_y_mm_by_column": [_rows(105.0, 6.7, 25), _rows(105.0, 6.7, 25)]},
            "100": {"columns": 4, "rows_per_column": 25, "questions_per_column": [25, 25, 25, 25],
                    "column_first_bubble_x_mm": [18.0, 67.0, 116.0, 165.0],
                    "row_centers_y_mm_by_column": [_rows(106.0, 6.7, 25) for _ in range(4)]},
        },
    },
    "registration_marks": {
        "shape": "square", "size_mm": MARK_SIZE_MM, "inset_mm": MARK_INSET_MM,
        "positions_top_left_origin_mm": [[7.0, 7.0], [197.0, 7.0], [7.0, 284.0], [197.0, 284.0]],
        "positions_bottom_left_origin_mm": [[7.0, 284.0], [197.0, 284.0], [7.0, 7.0], [197.0, 7.0]],
    },
}


def _pt(mm: float) -> float:
    return mm * 72.0 / 25.4


def _draw_bubble(page, x_mm: float, y_mm: float) -> None:
    radius = _pt(BUBBLE_RADIUS_MM)
    center = pymupdf.Point(_pt(x_mm), _pt(y_mm))
    page.draw_circle(center, radius, color=(0.12, 0.16, 0.20), width=0.8)


def _draw_header(page, question_count: int) -> None:
    page.insert_text((page.rect.width / 2 - _pt(32), _pt(14)), "PAPARAZZI  |  ANSWER SHEET V4",
                     fontsize=12, fontname="hebo", color=(0.09, 0.22, 0.34))
    page.insert_text((page.rect.width / 2 - _pt(25), _pt(20)), f"{question_count} QUESTIONS  |  A4 PORTRAIT",
                     fontsize=8, fontname="helv", color=(0.25, 0.32, 0.38))
    page.insert_text((_pt(29), _pt(18)), "STUDENT ID | 7 DIGITS", fontsize=8, fontname="hebo", color=(0.09, 0.22, 0.34))
    page.insert_text((_pt(29), _pt(22)), "Fill one digit in each column", fontsize=6.5, color=(0.28, 0.34, 0.39))
    for col in range(7):
        cx = ID_X_MM + col * ID_COLUMN_PITCH_MM
        page.insert_text((_pt(cx - 0.8), _pt(26)), str(col + 1), fontsize=6.5, fontname="hebo", color=(0.1, 0.2, 0.3))
        for digit in range(10):
            cy = ID_Y_MM + digit * ID_ROW_PITCH_MM
            if col == 0:
                page.insert_text((_pt(33), _pt(cy + 1.5),), str(digit), fontsize=6, color=(0.1, 0.15, 0.2))
            _draw_bubble(page, cx, cy)
    page.draw_rect(pymupdf.Rect(_pt(28), _pt(24), _pt(92), _pt(83)), color=(0.3, 0.37, 0.42), width=0.6)


def _draw_answers(page, question_count: int) -> None:
    layout = PROFILE["answers"]["layouts"][str(question_count)]
    question_number = 1
    for column, total in enumerate(layout["questions_per_column"]):
        x_first = layout["column_first_bubble_x_mm"][column]
        ys = layout["row_centers_y_mm_by_column"][column]
        number_x = x_first - (10 if question_count == 100 else 9)
        page.insert_text((_pt(number_x), _pt(ys[0] - 6.0)), f"Q{question_count}", fontsize=7.5,
                         fontname="hebo", color=(0.09, 0.22, 0.34))
        for choice_index, choice in enumerate("ABCDE"):
            x = x_first + choice_index * CHOICE_PITCH_MM
            page.insert_text((_pt(x - 1.0), _pt(ys[0] - 2.8)), choice, fontsize=6, fontname="hebo", color=(0.2, 0.28, 0.34))
        for row_index in range(total):
            y = ys[row_index]
            page.insert_text((_pt(number_x), _pt(y + 1.5)), str(question_number), fontsize=6.4, color=(0.1, 0.15, 0.2))
            for choice_index in range(5):
                _draw_bubble(page, x_first + choice_index * CHOICE_PITCH_MM, y)
            question_number += 1


def _new_pdf(question_count: int) -> pymupdf.Document:
    document = pymupdf.open()
    page = document.new_page(width=_pt(PAGE_WIDTH_MM), height=_pt(PAGE_HEIGHT_MM))
    page.draw_rect(pymupdf.Rect(0, 0, page.rect.width, page.rect.height), color=None, fill=(1, 1, 1))
    for x, y in PROFILE["registration_marks"]["positions_top_left_origin_mm"]:
        rect = pymupdf.Rect(_pt(x), _pt(y), _pt(x + MARK_SIZE_MM), _pt(y + MARK_SIZE_MM))
        page.draw_rect(rect, color=(0, 0, 0), fill=(0, 0, 0))
    _draw_header(page, question_count)
    _draw_answers(page, question_count)
    page.insert_text((_pt(8), _pt(280)), "Use a dark pencil or pen. Fill bubbles completely. Print A4 at 100% / Actual Size.", fontsize=6, color=(0.28, 0.34, 0.39))
    page.insert_text((_pt(160), _pt(280)), "PAPARAZZI V4", fontsize=6, color=(0.28, 0.34, 0.39))
    return document


def generate_v4_assets(output_dir: Path = ASSET_DIR) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "Paparazzi_Standard_V4_Profile.json").write_text(json.dumps(PROFILE, indent=2) + "\n", encoding="utf-8")
    combined = pymupdf.open()
    for count in (20, 50, 100):
        document = _new_pdf(count)
        path = output_dir / f"Answer_Sheet_{count}Q_V4.pdf"
        document.save(path, garbage=4, deflate=True)
        combined.insert_pdf(document)
        document.close()
    combined.save(output_dir / "Answer_Sheets_20_50_100_V4.pdf", garbage=4, deflate=True)
    combined.close()


if __name__ == "__main__":
    generate_v4_assets()
