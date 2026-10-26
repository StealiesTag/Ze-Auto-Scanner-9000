from sqlalchemy import CheckConstraint, UniqueConstraint, func

from extensions import db


class Submission(db.Model):
    __tablename__ = "submissions"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(
        db.Integer,
        db.ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
    )
    assessment_id = db.Column(
        db.Integer,
        db.ForeignKey("assessments.id", ondelete="CASCADE"),
        nullable=False,
    )
    submitted_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    score = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    total_points = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    percentage_score = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    processing_status = db.Column(db.String(50), nullable=False, default="pending")
    review_status = db.Column(db.String(50), nullable=False, default="not_required")
    original_image = db.Column(db.String(255), nullable=True)
    aligned_image = db.Column(db.String(255), nullable=True)
    debug_image = db.Column(db.String(255), nullable=True)
    processing_notes = db.Column(db.JSON, nullable=False, default=dict)

    student = db.relationship("Student", back_populates="submissions")
    assessment = db.relationship("Assessment", back_populates="submissions")
    responses = db.relationship("Response", back_populates="submission", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("student_id", "assessment_id", name="uq_student_assessment_submission"),
        db.Index("ix_submissions_assessment_id", "assessment_id"),
        CheckConstraint("score >= 0", name="ck_submission_score_nonnegative"),
        CheckConstraint("total_points >= 0", name="ck_submission_total_nonnegative"),
        CheckConstraint("percentage_score >= 0 AND percentage_score <= 100", name="ck_submission_percentage_range"),
        CheckConstraint("processing_status IN ('pending', 'processed', 'failed')", name="ck_submission_processing_status"),
        CheckConstraint("review_status IN ('not_required', 'required', 'complete')", name="ck_submission_review_status"),
    )

    def __repr__(self):
        return f"<Submission {self.id}: student={self.student_id} assessment={self.assessment_id}>"


class Response(db.Model):
    __tablename__ = "responses"

    id = db.Column(db.Integer, primary_key=True)
    submission_id = db.Column(
        db.Integer,
        db.ForeignKey("submissions.id", ondelete="CASCADE"),
        nullable=False,
    )
    question_id = db.Column(
        db.Integer,
        db.ForeignKey("questions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    detected_answer = db.Column(db.String(1), nullable=True)
    answer = db.Column(db.String(1), nullable=True)
    detection_status = db.Column(db.String(30), nullable=False, default="blank")
    diagnostic_values = db.Column(db.JSON, nullable=False, default=dict)
    correctness = db.Column(db.Boolean, nullable=True)
    confidence = db.Column(db.Float, nullable=False, default=0.0)
    manual_correction_flag = db.Column(db.Boolean, nullable=False, default=False)

    submission = db.relationship("Submission", back_populates="responses")
    question = db.relationship("Question", back_populates="responses")
    corrections = db.relationship(
        "AnswerCorrection",
        back_populates="response",
        cascade="all, delete-orphan",
        order_by="AnswerCorrection.created_at",
    )

    __table_args__ = (
        UniqueConstraint("submission_id", "question_id", name="uq_submission_question_response"),
        CheckConstraint("detected_answer IS NULL OR detected_answer IN ('A', 'B', 'C', 'D')", name="ck_response_detected_answer_valid"),
        CheckConstraint("answer IS NULL OR answer IN ('A', 'B', 'C', 'D')", name="ck_response_answer_valid"),
        CheckConstraint("detection_status IN ('detected', 'blank', 'multiple_marks', 'ambiguous')", name="ck_response_detection_status"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_response_confidence_range"),
    )

    def __repr__(self):
        return f"<Response {self.question_id}: {self.detected_answer}>"


class AnswerCorrection(db.Model):
    __tablename__ = "answer_corrections"

    id = db.Column(db.Integer, primary_key=True)
    response_id = db.Column(db.Integer, db.ForeignKey("responses.id", ondelete="CASCADE"), nullable=False, index=True)
    previous_answer = db.Column(db.String(1), nullable=True)
    corrected_answer = db.Column(db.String(1), nullable=True)
    note = db.Column(db.String(500), nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)

    response = db.relationship("Response", back_populates="corrections")

    __table_args__ = (
        CheckConstraint("previous_answer IS NULL OR previous_answer IN ('A', 'B', 'C', 'D')", name="ck_correction_previous_answer"),
        CheckConstraint("corrected_answer IS NULL OR corrected_answer IN ('A', 'B', 'C', 'D')", name="ck_correction_answer"),
    )
