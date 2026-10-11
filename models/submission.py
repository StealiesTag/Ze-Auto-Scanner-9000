from sqlalchemy import UniqueConstraint, func

from app import db


class Submission(db.Model):
    __tablename__ = "submissions"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(
        db.Integer,
        db.ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    assessment_id = db.Column(
        db.Integer,
        db.ForeignKey("assessments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    submitted_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    score = db.Column(db.Float, nullable=False, default=0.0)
    total_points = db.Column(db.Float, nullable=False, default=0.0)
    processing_status = db.Column(db.String(50), nullable=False, default="pending")
    review_status = db.Column(db.String(50), nullable=False, default="not_required")

    student = db.relationship("Student", back_populates="submissions")
    assessment = db.relationship("Assessment", back_populates="submissions")
    responses = db.relationship("Response", back_populates="submission", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("student_id", "assessment_id", name="uq_student_assessment_submission"),
        db.Index("ix_submission_student_assessment", "student_id", "assessment_id"),
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
        index=True,
    )
    question_id = db.Column(
        db.Integer,
        db.ForeignKey("questions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    detected_answer = db.Column(db.String(1), nullable=True)
    correctness = db.Column(db.Boolean, nullable=False, default=False)
    confidence = db.Column(db.Float, nullable=False, default=0.0)
    manual_correction_flag = db.Column(db.Boolean, nullable=False, default=False)

    submission = db.relationship("Submission", back_populates="responses")
    question = db.relationship("Question", back_populates="responses")

    __table_args__ = (
        UniqueConstraint("submission_id", "question_id", name="uq_submission_question_response"),
        db.Index("ix_response_submission_question", "submission_id", "question_id"),
    )

    def __repr__(self):
        return f"<Response {self.question_id}: {self.detected_answer}>"
