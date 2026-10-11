from sqlalchemy import func

from app import db


class Student(db.Model):
    __tablename__ = "students"

    id = db.Column(db.Integer, primary_key=True)
    student_number = db.Column(db.String(50), unique=True, nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    class_section = db.Column(db.String(100), nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)

    submissions = db.relationship(
        "Submission",
        back_populates="student",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<Student {self.student_number}: {self.name}>"
