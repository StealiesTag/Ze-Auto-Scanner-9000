from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pymupdf
import pytest

from services.paparazzi_v3_scanner import (
    CANONICAL_DPI,
    _bubble_fill_score,
    align_paparazzi_v3,
    align_paparazzi_v3_auto,
    classify_v3_bubbles,
    detect_v3_answers,
    detect_v3_student_id,
    get_answer_bubble_centers_mm,
    get_student_id_bubble_centers_mm,
    load_official_profile,
    load_v3_profile,
    preprocess_v3_image,
)
from services.image_processor import ImageProcessingError
from services.marker_detection import detect_v3_registration_markers

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


def test_v3_profile_geometry_matches_revised_vector_pdfs_and_preserves_centers():
    profile = load_v3_profile()
    assert profile["version"] == "PAPARAZZI-OMR-A4-V3"
    originals = ASSET_DIR / "originals"
    original_profile = __import__("json").loads((originals / "Paparazzi_Standard_V3_Profile.json").read_text())
    assert original_profile["answers"]["bubble_radius_mm"] == 1.48
    assert profile["answers"]["bubble_radius_mm"] == 1.85
    assert profile["answers"]["bubble_radius_mm"] / original_profile["answers"]["bubble_radius_mm"] - 1 == pytest.approx(0.25)
    for count in (20, 50, 100):
        page_profile = load_v3_profile("50q_large_bubble") if count == 50 else profile
        path = ASSET_DIR / f"Answer_Sheet_{count}Q_V3.pdf"
        page = pymupdf.open(path)[0]
        old_page = pymupdf.open(originals / path.name)[0]
        circles = [
            drawing["rect"]
            for drawing in page.get_drawings()
            if drawing["items"] and all(item[0] == "c" for item in drawing["items"])
        ]
        id_radius = page_profile["student_id"]["bubble_radius_mm"]
        answer_radius = page_profile["answers"]["bubble_radius_mm"]
        id_count = sum(abs(circle.width * 25.4 / 144 - id_radius) < 0.01 for circle in circles)
        answer_count = sum(abs(circle.width * 25.4 / 144 - answer_radius) < 0.01 for circle in circles)
        old_circles = [d["rect"] for d in old_page.get_drawings() if abs(d["rect"].width * 25.4 / 144 - 1.48) < 0.01]
        answer_radius = page_profile["answers"]["bubble_radius_mm"]
        new_answer_circles = [d["rect"] for d in page.get_drawings() if abs(d["rect"].width * 25.4 / 144 - answer_radius) < 0.01]
        old_centers = sorted((round(r.x0 + r.width / 2, 3), round(r.y0 + r.height / 2, 3)) for r in old_circles)
        new_centers = sorted((round(r.x0 + r.width / 2, 3), round(r.y0 + r.height / 2, 3)) for r in new_answer_circles)
        assert abs(page.rect.width * 25.4 / 72 - 210) < 0.01
        assert abs(page.rect.height * 25.4 / 72 - 297) < 0.01
        if count == 50:
            id_centers = sorted((round(d["rect"].x0 + d["rect"].width / 2, 3), round(d["rect"].y0 + d["rect"].height / 2, 3)) for d in page.get_drawings() if abs(d["rect"].width * 25.4 / 144 - id_radius) < 0.01)
            assert len(id_centers) == 70
        else:
            assert id_count == 70
            assert answer_count == count * 5
            assert len(old_centers) == len(new_centers) == count * 5
            assert old_centers == new_centers
        if count != 50:
            old_id = sorted((round(d["rect"].x0 + d["rect"].width / 2, 3), round(d["rect"].y0 + d["rect"].height / 2, 3)) for d in old_page.get_drawings() if abs(d["rect"].width * 25.4 / 144 - 1.12) < 0.01)
            new_id = sorted((round(d["rect"].x0 + d["rect"].width / 2, 3), round(d["rect"].y0 + d["rect"].height / 2, 3)) for d in page.get_drawings() if abs(d["rect"].width * 25.4 / 144 - 1.12) < 0.01)
            assert len(old_id) == len(new_id) == 70
            assert old_id == new_id
        if count == 50:
            # The revised large-bubble sheet stores its corner markers in the page image.
            assert page.get_images(full=True)
        else:
            old_marks = sorted(tuple(round(v, 3) for v in (d["rect"].x0, d["rect"].y0, d["rect"].x1, d["rect"].y1)) for d in old_page.get_drawings() if abs(d["rect"].width * 25.4 / 72 - 6.0) < 0.01)
            new_marks = sorted(tuple(round(v, 3) for v in (d["rect"].x0, d["rect"].y0, d["rect"].x1, d["rect"].y1)) for d in page.get_drawings() if abs(d["rect"].width * 25.4 / 72 - 6.0) < 0.01)
            assert len(old_marks) == len(new_marks) == 4
            assert old_marks == new_marks
        spans = [pymupdf.Rect(span["bbox"]) for block in page.get_text("dict")["blocks"] if "lines" in block for line in block["lines"] for span in line["spans"] if span["text"].strip()]
        assert all(not (rect & span).get_area() for rect in new_answer_circles for span in spans)
        answer_centers = get_answer_bubble_centers_mm(count, page_profile)
        mm_points = [point for row in answer_centers.values() for point in row]
        closest = min(np.linalg.norm(np.asarray(a) - np.asarray(b)) for i, a in enumerate(mm_points) for b in mm_points[i + 1:])
        if count != 50:
            assert closest == pytest.approx(5.0)
            assert closest - 2 * answer_radius == pytest.approx(1.30)
        old_page.parent.close()
        page.parent.close()
        assert len(get_answer_bubble_centers_mm(count, page_profile)) == count

    combined = pymupdf.open(ASSET_DIR / "Answer_Sheets_20_50_100_V3.pdf")
    assert len(combined) == 3
    assert all(abs(page.rect.width * 25.4 / 72 - 210) < 0.01 for page in combined)
    assert all(abs(page.rect.height * 25.4 / 72 - 297) < 0.01 for page in combined)


def test_actual_v3_pdf_render_detects_marks_student_id_and_answers_for_each_template():
    for count in (20, 50, 100):
        profile = load_v3_profile("50q_large_bubble") if count == 50 else load_v3_profile()
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

    for angle, rotation in ((90, cv2.ROTATE_90_CLOCKWISE), (180, cv2.ROTATE_180), (270, cv2.ROTATE_90_COUNTERCLOCKWISE)):
        rotated_aligned = align_paparazzi_v3(cv2.rotate(source, rotation), profile, question_count=20)
        assert detect_v3_student_id(rotated_aligned.aligned_image, profile)["student_id"] == "1234567"
        assert detect_v3_answers(rotated_aligned.aligned_image, 20, profile)[0]["answer"] == "C"


def test_v3_detector_rejects_unrelated_or_incomplete_pages():
    profile = load_v3_profile()
    unrelated = np.full((800, 600, 3), 255, dtype=np.uint8)
    with pytest.raises(ImageProcessingError, match="four registration marks"):
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
    cv2.circle(image, (round(x_mm * pixels_per_mm), round(y_mm * pixels_per_mm)), round(profile["answers"]["bubble_radius_mm"] * pixels_per_mm * 0.70), (185, 185, 185), -1)
    aligned = align_paparazzi_v3(image, profile, question_count=20)
    assert detect_v3_answers(aligned.aligned_image, 20, profile)[0]["answer"] == "A"

    uncertain = classify_v3_bubbles({"A": 0.31, "B": 0.23, "C": 0.03, "D": 0.02, "E": 0.01})
    assert uncertain["status"] == "ambiguous"
    assert uncertain["answer"] is None


def test_v3_preprocessing_handles_low_contrast_uneven_light_noise_and_mild_blur():
    profile = load_v3_profile()
    answers = {1: "A", 2: "C", 20: "E"}
    source = _fill_profile_bubbles(_render_template(20), profile, 20, answers)
    height, width = source.shape[:2]
    gradient = np.linspace(-35, 35, width, dtype=np.float32)[None, :, None]
    low_contrast = np.clip(source.astype(np.float32) * 0.66 + 76 + gradient, 0, 255).astype(np.uint8)
    rng = np.random.default_rng(713)
    noisy = np.clip(low_contrast.astype(np.int16) + rng.normal(0, 3.0, low_contrast.shape), 0, 255).astype(np.uint8)
    photographed = cv2.GaussianBlur(noisy, (3, 3), 0.55)
    aligned = align_paparazzi_v3(photographed, profile, question_count=20)
    enhanced = preprocess_v3_image(aligned.aligned_image)
    assert enhanced.shape == aligned.aligned_image.shape[:2]
    recognized = detect_v3_answers(aligned.aligned_image, 20, profile)
    assert [recognized[n - 1]["answer"] for n in answers] == list(answers.values())
    baseline_gray = cv2.cvtColor(aligned.aligned_image, cv2.COLOR_BGR2GRAY)
    centers = get_answer_bubble_centers_mm(20, profile)
    baseline = []
    for number, points in centers.items():
        scores = {choice: _bubble_fill_score(baseline_gray, points[index], profile["answers"]["bubble_radius_mm"]) for index, choice in enumerate("ABCDE")}
        baseline.append(classify_v3_bubbles(scores))
    baseline_correct = sum(baseline[number - 1]["answer"] == answers[number] for number in answers)
    enhanced_correct = sum(recognized[number - 1]["answer"] == answers[number] for number in answers)
    assert enhanced_correct >= baseline_correct
    assert detect_v3_student_id(aligned.aligned_image, profile)["student_id"] == "1234567"


def test_supplied_camera_capture_remains_rejected_when_v3_geometry_is_absent():
    path = Path(__file__).resolve().parents[1] / "test_fixtures" / "paparazzi_v3_camera_capture.jpg"
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert image is not None
    assert image.shape[:2] == (1536, 2048)
    detection = detect_v3_registration_markers(image, load_v3_profile())
    assert detection["geometry_valid"] is False
    with pytest.raises(ImageProcessingError):
        align_paparazzi_v3(image, load_v3_profile(), question_count=20)


def test_v3_preprocessing_stages_are_configurable(monkeypatch):
    image = np.arange(64 * 80, dtype=np.uint8).reshape(64, 80)
    monkeypatch.setenv("PAPARAZZI_V3_ILLUMINATION_CORRECTION", "false")
    monkeypatch.setenv("PAPARAZZI_V3_CLAHE_ENABLED", "false")
    monkeypatch.setenv("PAPARAZZI_V3_DENOISE_MEDIAN_KERNEL", "0")
    monkeypatch.setenv("PAPARAZZI_V3_SHARPEN_AMOUNT", "0")
    assert np.array_equal(preprocess_v3_image(image), image)


def _render_user_large_bubble_50q(dpi=240):
    source = pymupdf.open(Path(__file__).resolve().parents[1] / "test_fixtures" / "Paparazzi_50Q_Large_Bubble_User.pdf")
    pixels = source[0].get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72), alpha=False)
    rgb = np.frombuffer(pixels.samples, dtype=np.uint8).reshape(pixels.height, pixels.width, pixels.n)
    source.close()
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _render_official_20q_big(dpi=240):
    source = pymupdf.open(Path(__file__).resolve().parents[1] / "static" / "answer_sheets" / "official" / "Answer_Sheet_20Q_Big_Official.pdf")
    pixels = source[0].get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72), alpha=False)
    rgb = np.frombuffer(pixels.samples, dtype=np.uint8).reshape(pixels.height, pixels.width, pixels.n)
    source.close()
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def test_official_20q_big_sheet_aligns_and_reads_its_large_bubbles():
    image = _render_official_20q_big()
    profile = load_official_profile(20)
    assert profile["registration_marks"]["size_mm"] == pytest.approx(9.03, abs=0.02)
    assert profile["answers"]["layouts"]["20"]["columns"] == 1
    detection = detect_v3_registration_markers(image, profile)
    assert detection["valid"] is True

    pixels_per_mm_x = image.shape[1] / profile["paper"]["width_mm"]
    pixels_per_mm_y = image.shape[0] / profile["paper"]["height_mm"]
    id_centers = get_student_id_bubble_centers_mm(profile)
    for column, digit in enumerate("1234567"):
        x_mm, y_mm = id_centers[column][int(digit)]
        radius = round(profile["student_id"]["bubble_radius_mm"] * min(pixels_per_mm_x, pixels_per_mm_y) * 0.78)
        cv2.circle(image, (round(x_mm * pixels_per_mm_x), round(y_mm * pixels_per_mm_y)), radius, (15, 15, 15), -1)

    expected = {number: "ABCDE"[(number - 1) % 5] for number in range(1, 21)}
    answer_centers = get_answer_bubble_centers_mm(20, profile)
    for number, answer in expected.items():
        x_mm, y_mm = answer_centers[number]["ABCDE".index(answer)]
        radius = round(profile["answers"]["bubble_radius_mm"] * min(pixels_per_mm_x, pixels_per_mm_y) * 0.78)
        cv2.circle(image, (round(x_mm * pixels_per_mm_x), round(y_mm * pixels_per_mm_y)), radius, (15, 15, 15), -1)

    aligned = align_paparazzi_v3(image, profile, question_count=20)
    assert len(aligned.detected_marks) == 4
    assert aligned.layout_score > 0.08
    assert detect_v3_student_id(aligned.aligned_image, profile)["student_id"] == "1234567"
    results = detect_v3_answers(aligned.aligned_image, 20, profile)
    assert all(result["status"] == "detected" for result in results)
    assert [result["answer"] for result in results] == list(expected.values())


def test_user_large_bubble_50q_pdf_selects_its_profile_and_requires_id_review_when_blank():
    image = _render_user_large_bubble_50q()
    profile, aligned = align_paparazzi_v3_auto(image, 50)
    assert profile["profile_variant"] == "50q_large_bubble"
    assert aligned.orientation == "rotation_0"
    assert aligned.layout_score > 0.4
    assert len(aligned.detected_marks) == 4
    id_result = detect_v3_student_id(aligned.aligned_image, profile)
    assert id_result["student_id"] is None
    assert id_result["status"] in {"blank", "ambiguous", "multiple_marks"}
    answers = detect_v3_answers(aligned.aligned_image, 50, profile)
    assert len(answers) == 50
    assert all(answer["status"] == "blank" for answer in answers)


def test_user_large_bubble_50q_synthetic_fills_are_read_at_measured_centers():
    image = _render_user_large_bubble_50q(dpi=300)
    profile = load_v3_profile("50q_large_bubble")
    expected = {number: "ABCDE"[(number - 1) % 5] for number in range(1, 51)}
    centers = get_answer_bubble_centers_mm(50, profile)
    pixels_per_mm_x = image.shape[1] / profile["paper"]["width_mm"]
    pixels_per_mm_y = image.shape[0] / profile["paper"]["height_mm"]
    for number, answer in expected.items():
        x_mm, y_mm = centers[number]["ABCDE".index(answer)]
        radius = round(profile["answers"]["bubble_radius_mm"] * min(pixels_per_mm_x, pixels_per_mm_y) * 0.68)
        cv2.circle(image, (round(x_mm * pixels_per_mm_x), round(y_mm * pixels_per_mm_y)), radius, (25, 25, 25), -1)
    chosen_profile, aligned = align_paparazzi_v3_auto(image, 50)
    results = detect_v3_answers(aligned.aligned_image, 50, chosen_profile)
    assert chosen_profile["profile_variant"] == "50q_large_bubble"
    assert all(result["status"] == "detected" for result in results)
    assert [result["answer"] for result in results] == list(expected.values())


def test_auto_profile_selection_uses_revised_large_bubble_v3_50q_layout():
    image = _render_template(50)
    profile, aligned = align_paparazzi_v3_auto(image, 50)
    assert profile["profile_variant"] == "50q_large_bubble"
    assert aligned.layout_score > 0.4
