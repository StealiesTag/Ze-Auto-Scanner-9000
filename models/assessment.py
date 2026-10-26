from sqlalchemy import CheckConstraint, UniqueConstraint, func

from extensions import db


class Assessment(db.Model):
    __tablename__ = "assessments"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    class_section = db.Column(db.String(100), nullable=False)
    total_questions = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (CheckConstraint("total_questions > 0", name="ck_assessment_questions_positive"),)

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
    )
    question_number = db.Column(db.Integer, nullable=False)
    prompt = db.Column(db.Text, nullable=False, default="")
    option_a = db.Column(db.String(500), nullable=False, default="")
    option_b = db.Column(db.String(500), nullable=False, default="")
    option_c = db.Column(db.String(500), nullable=False, default="")
    option_d = db.Column(db.String(500), nullable=False, default="")
    correct_answer = db.Column(db.String(1), nullable=False)
    points = db.Column(db.Numeric(8, 2), nullable=False, default=1.0)
    learning_objective = db.Column(db.String(255), nullable=False)

    assessment = db.relationship("Assessment", back_populates="questions")
    responses = db.relationship("Response", back_populates="question", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("assessment_id", "question_number", name="uq_assessment_question_number"),
        CheckConstraint("question_number > 0", name="ck_question_number_positive"),
        CheckConstraint("correct_answer IN ('A', 'B', 'C', 'D')", name="ck_question_answer_valid"),
        CheckConstraint("points > 0", name="ck_question_points_positive"),
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
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (CheckConstraint("resource_type IN ('reading', 'video', 'practice', 'lesson', 'other')", name="ck_resource_type"),)

    def __repr__(self):
        return f"<LearningResource {self.title}>"
