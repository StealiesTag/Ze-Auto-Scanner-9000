from sqlalchemy import CheckConstraint, ForeignKey, func

from extensions import db


class ScanDraft(db.Model):
    __tablename__ = "scan_drafts"

    id = db.Column(db.Integer, primary_key=True)
    assessment_id = db.Column(db.Integer, ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id = db.Column(db.Integer, ForeignKey("students.id", ondelete="SET NULL"), nullable=True)
    detected_student_number = db.Column(db.String(7), nullable=True)
    question_count = db.Column(db.Integer, nullable=False)
    profile_version = db.Column(db.String(50), nullable=False)
    original_image = db.Column(db.String(255), nullable=False)
    aligned_image = db.Column(db.String(255), nullable=False)
    debug_image = db.Column(db.String(255), nullable=False)
    student_id_detection = db.Column(db.JSON, nullable=False, default=dict)
    recognized_answers = db.Column(db.JSON, nullable=False, default=list)
    processing_notes = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)

    assessment = db.relationship("Assessment")
    student = db.relationship("Student")

    __table_args__ = (
        CheckConstraint("question_count IN (20, 50, 100)", name="ck_scan_draft_question_count"),
    )
