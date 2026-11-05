from pathlib import Path

import cv2
import numpy as np
import pymupdf

from services.paparazzi_v3_scanner import (
    align_paparazzi_v3,
    detect_v3_answers,
    detect_v3_student_id,
    get_answer_bubble_centers_mm,
    get_student_id_bubble_centers_mm,
    load_v4_profile,
)


ASSET_DIR = Path(__file__).resolve().parents[1] / "static" / "answer_sheets" / "v4" / "Paparazzi_Answer_Sheets_V4_Package"
DPI = 240


def _render(count):
    with pymupdf.open(ASSET_DIR / f"Answer_Sheet_{count}Q_V4.pdf") as document:
        pix = document[0].get_pixmap(matrix=pymupdf.Matrix(DPI / 72, DPI / 72), alpha=False)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def test_v4_pdf_geometry_and_bubble_reading_for_all_sheet_sizes():
    profile = load_v4_profile()
    assert profile["version"] == "PAPARAZZI-OMR-A4-V4"
    assert profile["student_id"]["auto_detect_enabled"] is True
    for count in (20, 50, 100):
        with pymupdf.open(ASSET_DIR / f"Answer_Sheet_{count}Q_V4.pdf") as document:
            page = document[0]
            assert abs(page.rect.width * 25.4 / 72 - 210) < 0.01
            assert abs(page.rect.height * 25.4 / 72 - 297) < 0.01
        image = _render(count)
        pixels_per_mm = DPI / 25.4
        for column, digit in enumerate("1234567"):
            x, y = get_student_id_bubble_centers_mm(profile)[column][int(digit)]
            cv2.circle(image, (round(x * pixels_per_mm), round(y * pixels_per_mm)), round(2.1 * pixels_per_mm * 0.72), (15, 15, 15), -1)
        expected = {number: "ABCDE"[(number - 1) % 5] for number in range(1, count + 1)}
        centers = get_answer_bubble_centers_mm(count, profile)
        for number, answer in expected.items():
            x, y = centers[number]["ABCDE".index(answer)]
            cv2.circle(image, (round(x * pixels_per_mm), round(y * pixels_per_mm)), round(2.1 * pixels_per_mm * 0.72), (15, 15, 15), -1)
        aligned = align_paparazzi_v3(image, profile, question_count=count)
        assert detect_v3_student_id(aligned.aligned_image, profile)["student_id"] == "1234567"
        results = detect_v3_answers(aligned.aligned_image, count, profile)
        assert [row["answer"] for row in results] == list(expected.values())

