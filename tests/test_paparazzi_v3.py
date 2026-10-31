from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pymupdf
import pytest

from services.paparazzi_v3_scanner import (
    CANONICAL_DPI,
    align_paparazzi_v3,
    classify_v3_bubbles,
    detect_v3_answers,
    detect_v3_student_id,
    get_answer_bubble_centers_mm,
    get_student_id_bubble_centers_mm,
    load_v3_profile,
)
from services.image_processor import ImageProcessingError

ASSET_DIR = Path(__file__).resolve().parents[1] / "static" / "answer_sheets" / "v3" / "Paparazzi_Answer_Sheets_V3_Package"
DPI = 240


def _render_template(question_count: int) -> np.ndarray:
    document = pymupdf.open(ASSET_DIR / f"Answer_Sheet_{question_count}Q_V3.pdf")
    page = document[0]
    pixels = page.get_pixmap(matrix=pymupdf.Matrix(DPI / 72, DPI / 72), alpha=False)
    rgb = np.frombuffer(pixels.samples, dtype=np.uint8).reshape(pixels.height, pixels.width, pixels.n)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _fill_profile_bubbles(image, profile, question_count, answers, student_id="1234567"):
    pixels_per_mm = DPI / 25.4
    id_centers = get_student_id_bubble_centers_mm(profile)
    radius = round(profile["student_id"]["bubble_radius_mm"] * pixels_per_mm * 0.72)
    for column, digit in enumerate(student_id):
        x_mm, y_mm = id_centers[column][int(digit)]
        cv2.circle(image, (round(x_mm * pixels_per_mm), round(y_mm * pixels_per_mm)), radius, (15, 15, 15), -1)

    answer_centers = get_answer_bubble_centers_mm(question_count, profile)
    radius = round(profile["answers"]["bubble_radius_mm"] * pixels_per_mm * 0.70)
    for question_number, selections in answers.items():
        for option in selections:
            x_mm, y_mm = answer_centers[question_number]["ABCDE".index(option)]
            cv2.circle(image, (round(x_mm * pixels_per_mm), round(y_mm * pixels_per_mm)), radius, (15, 15, 15), -1)
    return image


def test_v3_profile_geometry_matches_all_original_vector_pdfs():
    profile = load_v3_profile()
    assert profile["version"] == "PAPARAZZI-OMR-A4-V3"
    for count in (20, 50, 100):
        page = pymupdf.open(ASSET_DIR / f"Answer_Sheet_{count}Q_V3.pdf")[0]
        circles = [
            drawing["rect"]
            for drawing in page.get_drawings()
            if drawing["items"] and all(item[0] == "c" for item in drawing["items"])
        ]
        id_radius = profile["student_id"]["bubble_radius_mm"]
        answer_radius = profile["answers"]["bubble_radius_mm"]
        id_count = sum(abs(circle.width * 25.4 / 144 - id_radius) < 0.01 for circle in circles)
        answer_count = sum(abs(circle.width * 25.4 / 144 - answer_radius) < 0.01 for circle in circles)
        assert abs(page.rect.width * 25.4 / 72 - 210) < 0.01
        assert abs(page.rect.height * 25.4 / 72 - 297) < 0.01
        assert id_count == 70
        assert answer_count == count * 5
        assert len(get_answer_bubble_centers_mm(count, profile)) == count

    combined = pymupdf.open(ASSET_DIR / "Answer_Sheets_20_50_100_V3.pdf")
    assert len(combined) == 3
    assert all(abs(page.rect.width * 25.4 / 72 - 210) < 0.01 for page in combined)
    assert all(abs(page.rect.height * 25.4 / 72 - 297) < 0.01 for page in combined)


def test_actual_v3_pdf_render_detects_marks_student_id_and_answers_for_each_template():
    profile = load_v3_profile()
    for count in (20, 50, 100):
        answers = {number: "ABCDE"[(number - 1) % 5] for number in range(1, count + 1)}
        source = _fill_profile_bubbles(_render_template(count), profile, count, answers)
        aligned = align_paparazzi_v3(source, profile, question_count=count)
        assert len(aligned.detected_marks) == 4
        student = detect_v3_student_id(aligned.aligned_image, profile)
        assert student["status"] == "detected"
        assert student["student_id"] == "1234567"
        results = detect_v3_answers(aligned.aligned_image, count, profile)
        assert len(results) == count
        assert all(result["status"] == "detected" for result in results)
        assert [result["answer"] for result in results] == list(answers.values())


def test_v3_blank_and_multiple_answers_are_not_silently_selected():
    profile = load_v3_profile()
    blank_source = _render_template(20)
    aligned = align_paparazzi_v3(blank_source, profile, question_count=20)
    assert all(result["status"] == "blank" for result in detect_v3_answers(aligned.aligned_image, 20, profile))

    source = _fill_profile_bubbles(_render_template(20), profile, 20, {1: "AB"})
    aligned = align_paparazzi_v3(source, profile, question_count=20)
    result = detect_v3_answers(aligned.aligned_image, 20, profile)[0]
    assert result["status"] == "multiple_marks"
    assert result["answer"] is None


def test_v3_detection_handles_rotation_and_perspective_from_original_pdf():
    profile = load_v3_profile()
    answers = {1: "C", 20: "E"}
    source = _fill_profile_bubbles(_render_template(20), profile, 20, answers)
    height, width = source.shape[:2]
    src = np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]])
    dst = np.float32([[75, 35], [width - 50, 80], [width - 20, height - 35], [30, height - 80]])
    transform = cv2.getPerspectiveTransform(src, dst)
    photographed = cv2.warpPerspective(source, transform, (width + 50, height + 40), borderValue=(255, 255, 255))
    aligned = align_paparazzi_v3(photographed, profile, question_count=20)
    assert aligned.orientation == "rotation_0"
    assert detect_v3_student_id(aligned.aligned_image, profile)["student_id"] == "1234567"
    perspective_answers = detect_v3_answers(aligned.aligned_image, 20, profile)
    assert perspective_answers[0]["answer"] == "C"
    assert perspective_answers[-1]["answer"] == "E"

    rotated = cv2.rotate(source, cv2.ROTATE_180)
    rotated_aligned = align_paparazzi_v3(rotated, profile, question_count=20)
    assert rotated_aligned.orientation == "rotation_180"
    assert detect_v3_student_id(rotated_aligned.aligned_image, profile)["student_id"] == "1234567"

    sideways = cv2.rotate(source, cv2.ROTATE_90_CLOCKWISE)
    sideways_aligned = align_paparazzi_v3(sideways, profile, question_count=20)
    assert sideways_aligned.aligned_image.shape[:2] == (round(297 * CANONICAL_DPI / 25.4), round(210 * CANONICAL_DPI / 25.4))
    assert detect_v3_student_id(sideways_aligned.aligned_image, profile)["student_id"] == "1234567"


def test_v3_detector_rejects_unrelated_or_incomplete_pages():
    profile = load_v3_profile()
    unrelated = np.full((800, 600, 3), 255, dtype=np.uint8)
    with pytest.raises(ImageProcessingError, match="four V3 square registration marks"):
        align_paparazzi_v3(unrelated, profile, question_count=20)

    cropped = _render_template(20)
    cropped[:160, :160] = 255
    with pytest.raises(ImageProcessingError):
        align_paparazzi_v3(cropped, profile, question_count=20)


def test_v3_local_background_recognizes_light_fill_and_flags_uncertain_scores():
    profile = load_v3_profile()
    image = _render_template(20)
    centers = get_answer_bubble_centers_mm(20, profile)[1]
    pixels_per_mm = DPI / 25.4
    x_mm, y_mm = centers[0]
    cv2.circle(image, (round(x_mm * pixels_per_mm), round(y_mm * pixels_per_mm)), round(1.48 * pixels_per_mm * 0.70), (185, 185, 185), -1)
    aligned = align_paparazzi_v3(image, profile, question_count=20)
    assert detect_v3_answers(aligned.aligned_image, 20, profile)[0]["answer"] == "A"

    uncertain = classify_v3_bubbles({"A": 0.31, "B": 0.23, "C": 0.03, "D": 0.02, "E": 0.01})
    assert uncertain["status"] == "ambiguous"
    assert uncertain["answer"] is None
