from sqlalchemy import UniqueConstraint, func

from app import db


class Assessment(db.Model):
    __tablename__ = "assessments"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    class_section = db.Column(db.String(100), nullable=False)
    total_questions = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)

    questions = db.relationship(
        "Question",
        back_populates="assessment",
        cascade="all, delete-orphan",
    )
    submissions = db.relationship(
        "Submission",
        back_populates="assessment",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<Assessment {self.id}: {self.title}>"


class Question(db.Model):
    __tablename__ = "questions"

    id = db.Column(db.Integer, primary_key=True)
    assessment_id = db.Column(
        db.Integer,
        db.ForeignKey("assessments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    question_number = db.Column(db.Integer, nullable=False)
    correct_answer = db.Column(db.String(1), nullable=False)
    points = db.Column(db.Float, nullable=False, default=1.0)
    learning_objective = db.Column(db.String(255), nullable=False)

    assessment = db.relationship("Assessment", back_populates="questions")
    responses = db.relationship("Response", back_populates="question", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("assessment_id", "question_number", name="uq_assessment_question_number"),
        db.Index("ix_question_assessment_number", "assessment_id", "question_number"),
    )

    def __repr__(self):
        return f"<Question {self.assessment_id}:{self.question_number}>"


class LearningResource(db.Model):
    __tablename__ = "learning_resources"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    url = db.Column(db.String(500), nullable=False)
    learning_objective = db.Column(db.String(255), nullable=False)
    resource_type = db.Column(db.String(50), nullable=False, default="read")

    def __repr__(self):
        return f"<LearningResource {self.title}>"
