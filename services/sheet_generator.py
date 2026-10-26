from __future__ import annotations

import io
import json
import os

import cv2
import qrcode
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from services.sheet_layout import (
    BUBBLE_RADIUS_MM,
    BUBBLE_X_MM,
    MARKER_ORIGINS_MM,
    MARKER_SIZE_MM,
    QUESTION_LIMIT,
    QUESTION_FIRST_Y_MM,
    QUESTION_PITCH_MM,
    QR_ORIGIN_MM,
    QR_SIZE_MM,
    bubble_center_mm,
)


def make_qr_payload(student_id: int, assessment_id: int) -> str:
    if student_id <= 0 or assessment_id <= 0:
        raise ValueError("Student and assessment identifiers must be positive.")
    return json.dumps({"s": student_id, "a": assessment_id}, separators=(",", ":"))


def _marker_png(marker_id: int) -> io.BytesIO:
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    marker = cv2.aruco.generateImageMarker(dictionary, marker_id, 256)
    output = io.BytesIO()
    Image.fromarray(marker).save(output, format="PNG")
    output.seek(0)
    return output


def _question_rows(assessment) -> list:
    questions = sorted(assessment.questions, key=lambda question: question.question_number)
    if not questions:
        raise ValueError("Cannot generate a sheet for an assessment without questions.")
    if len(questions) > QUESTION_LIMIT:
        raise ValueError(f"A4 answer sheets support at most {QUESTION_LIMIT} questions.")
    if [question.question_number for question in questions] != list(range(1, len(questions) + 1)):
        raise ValueError("Question numbers must be sequential, starting at 1.")
    if any(question.correct_answer not in {"A", "B", "C", "D"} for question in questions):
        raise ValueError("Every question must have a valid answer key before sheet generation.")
    if any(not question.prompt or any(not getattr(question, f"option_{letter.lower()}") for letter in "ABCD") for question in questions):
        raise ValueError("Every question must have a prompt and four choices before sheet generation.")
    return questions


def generate_answer_sheet_pdf(student, assessment, output_path: str) -> str:
    """Generate one A4 sheet using millimeters measured from the page top-left."""
    questions = _question_rows(assessment)
    if isinstance(output_path, (str, os.PathLike)):
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    pdf = canvas.Canvas(output_path, pagesize=A4)
    pdf.setTitle(f"{assessment.title} - {student.student_number}")

    def draw_text(x_mm: float, y_mm: float, text: str, size: int = 10, bold: bool = False):
        pdf.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        pdf.drawString(x_mm * mm, A4[1] - y_mm * mm, str(text))

    draw_text(28, 30, assessment.title[:55], 16, True)
    draw_text(28, 43, f"Student: {student.name[:42]}")
    draw_text(28, 52, f"Student number: {student.student_number}")
    draw_text(28, 61, f"Assessment ID: {assessment.id}")
    draw_text(28, 74, "Fill one bubble completely with a dark pencil or pen. Do not mark multiple choices.", 8)
    draw_text(28, 79, "Keep all four corner markers visible. Scan the full page without cropping.", 8)

    qr = qrcode.QRCode(box_size=8, border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(make_qr_payload(student.id, assessment.id))
    qr.make(fit=True)
    qr_buffer = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(qr_buffer, format="PNG")
    qr_buffer.seek(0)
    pdf.drawImage(ImageReader(qr_buffer), QR_ORIGIN_MM[0] * mm,
                  A4[1] - (QR_ORIGIN_MM[1] + QR_SIZE_MM) * mm,
                  width=QR_SIZE_MM * mm, height=QR_SIZE_MM * mm)

    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawString(27 * mm, A4[1] - 91 * mm, "Q")
    for option, x_mm in BUBBLE_X_MM.items():
        pdf.drawCentredString(x_mm * mm, A4[1] - 91 * mm, option)
    pdf.setLineWidth(0.5)
    for question in questions:
        _, y_mm = bubble_center_mm(question.question_number, "A")
        pdf.drawRightString(28 * mm, A4[1] - y_mm * mm - 2, f"{question.question_number}.")
        for option in BUBBLE_X_MM:
            bubble_x, bubble_y = bubble_center_mm(question.question_number, option)
            pdf.circle(bubble_x * mm, A4[1] - bubble_y * mm, BUBBLE_RADIUS_MM * mm, stroke=1, fill=0)

    for marker_id, (x_mm, y_mm) in MARKER_ORIGINS_MM.items():
        pdf.drawImage(ImageReader(_marker_png(marker_id)), x_mm * mm,
                      A4[1] - (y_mm + MARKER_SIZE_MM) * mm,
                      width=MARKER_SIZE_MM * mm, height=MARKER_SIZE_MM * mm)

    pdf.showPage()
    pdf.save()
    return output_path
