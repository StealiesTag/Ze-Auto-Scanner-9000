from __future__ import annotations

import io
import os

import qrcode
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def generate_answer_sheet_pdf(student_name: str, student_number: str, assessment_title: str, assessment_id: int, output_path: str) -> str:
    qr = qrcode.make(f"{student_number}:{assessment_id}")
    qr_buffer = io.BytesIO()
    qr.save(qr_buffer, format="PNG")
    qr_buffer.seek(0)

    pdf_file = output_path
    os.makedirs(os.path.dirname(pdf_file) or ".", exist_ok=True)
    c = canvas.Canvas(pdf_file, pagesize=A4)
    width, height = A4

    c.setTitle(f"{assessment_title} - {student_name}")
    c.setFont("Helvetica-Bold", 18)
    c.drawString(50, height - 50, assessment_title)
    c.setFont("Helvetica", 12)
    c.drawString(50, height - 80, f"Student: {student_name}")
    c.drawString(50, height - 100, f"Student number: {student_number}")
    c.drawString(50, height - 120, f"Assessment ID: {assessment_id}")
    c.drawString(50, height - 150, "Instructions: mark one answer per question with a dark fill.")

    # A simplified layout with numbered questions and answer choices.
    start_y = height - 220
    for question_number in range(1, 11):
        y = start_y - (question_number - 1) * 35
        c.drawString(50, y, f"{question_number}.")
        c.drawString(100, y, "A")
        c.drawString(130, y, "B")
        c.drawString(160, y, "C")
        c.drawString(190, y, "D")
        c.circle(108, y + 4, 6, stroke=1, fill=0)
        c.circle(138, y + 4, 6, stroke=1, fill=0)
        c.circle(168, y + 4, 6, stroke=1, fill=0)
        c.circle(198, y + 4, 6, stroke=1, fill=0)

    c.drawImage(qr_buffer, width - 140, height - 170, 90, 90)
    c.save()

    return pdf_file
