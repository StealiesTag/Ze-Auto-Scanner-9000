"""Render a reviewed AI lesson plan as a teacher-friendly PDF."""
from __future__ import annotations

from html import escape
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem, KeepTogether


def lesson_plan_pdf_bytes(assessment_title: str, duration: int, plan: dict) -> bytes:
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=letter, rightMargin=0.7*inch, leftMargin=0.7*inch,
                            topMargin=0.65*inch, bottomMargin=0.65*inch, title=plan.get("lesson_title", "Lesson Review"))
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="LessonTitle", parent=styles["Title"], alignment=TA_CENTER, textColor=colors.HexColor("#173b57"), spaceAfter=8))
    styles.add(ParagraphStyle(name="SectionHeading", parent=styles["Heading2"], textColor=colors.HexColor("#173b57"), spaceBefore=12, spaceAfter=5))
    styles.add(ParagraphStyle(name="SmallMeta", parent=styles["Normal"], textColor=colors.HexColor("#52616b"), alignment=TA_CENTER, spaceAfter=14))
    story = [Paragraph(escape(str(plan.get("lesson_title") or "Lesson Review")), styles["LessonTitle"]),
             Paragraph(f"{escape(assessment_title)} · {int(duration)} minutes", styles["SmallMeta"])]

    def add_list(heading: str, items) -> None:
        values = [str(item).strip() for item in (items or []) if str(item).strip()]
        if not values:
            return
        story.append(Paragraph(escape(heading), styles["SectionHeading"]))
        story.append(ListFlowable([ListItem(Paragraph(escape(item), styles["BodyText"])) for item in values],
                                  bulletType="bullet", leftIndent=18, bulletFontName="Helvetica"))

    def add_text(heading: str, value) -> None:
        text = str(value or "").strip()
        if text:
            story.append(KeepTogether([Paragraph(escape(heading), styles["SectionHeading"]),
                                       Paragraph(escape(text).replace("\n", "<br/>"), styles["BodyText"])]))

    add_list("Learning goals", plan.get("expected_learning_outcomes"))
    add_list("Target objectives", plan.get("target_learning_objectives"))
    add_list("Identified learning gaps", plan.get("identified_learning_gaps"))
    add_list("Materials", plan.get("required_materials"))
    add_text("Introduction and review", plan.get("introduction_or_review_activity"))
    add_text("Teaching discussion", plan.get("main_teaching_discussion"))
    add_text("Guided practice", plan.get("guided_practice"))
    add_text("Independent or collaborative activity", plan.get("independent_or_collaborative_activity"))
    add_text("Assessment and evaluation", plan.get("assessment_or_evaluation"))

    def footer(canvas, _document):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#d8e0e5"))
        canvas.line(0.7*inch, 0.48*inch, 7.8*inch, 0.48*inch)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#52616b"))
        canvas.drawString(0.7*inch, 0.32*inch, "Teacher review copy · AI-generated suggestion")
        canvas.drawRightString(7.8*inch, 0.32*inch, f"Page {_document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()
