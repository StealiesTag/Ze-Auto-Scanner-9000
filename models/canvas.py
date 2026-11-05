from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint, func

from extensions import db


class CanvasConnection(db.Model):
    __tablename__ = "canvas_connections"
    id = db.Column(db.Integer, primary_key=True)
    base_url = db.Column(db.String(500), nullable=False)
    encrypted_access_token = db.Column(db.Text, nullable=False)
    canvas_user_id = db.Column(db.String(100), nullable=False)
    connection_status = db.Column(db.String(20), nullable=False, default="connected")
    authorized_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    __table_args__ = (CheckConstraint("connection_status IN ('connected', 'expired', 'disconnected')", name="ck_canvas_connection_status"),)


class CanvasAssessmentMapping(db.Model):
    __tablename__ = "canvas_assessment_mappings"
    id = db.Column(db.Integer, primary_key=True)
    assessment_id = db.Column(db.Integer, ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False)
    course_id = db.Column(db.String(100), nullable=False)
    course_name = db.Column(db.String(255), nullable=False)
    assignment_id = db.Column(db.String(100), nullable=True)
    assignment_name = db.Column(db.String(255), nullable=True)
    points_possible = db.Column(db.Numeric(10, 2), nullable=True)
    grading_type = db.Column(db.String(30), nullable=True)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    assessment = db.relationship("Assessment")
    __table_args__ = (UniqueConstraint("assessment_id", name="uq_canvas_assessment_mappings_assessment_id"),)


class CanvasStudentMapping(db.Model):
    __tablename__ = "canvas_student_mappings"
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.String(100), nullable=False)
    student_number = db.Column(db.String(50), nullable=False)
    canvas_user_id = db.Column(db.String(100), nullable=False)
    sis_identifier = db.Column(db.String(100), nullable=True)
    verified_by = db.Column(db.String(100), nullable=False, default="local teacher")
    verified_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        UniqueConstraint("course_id", "student_number", name="uq_canvas_map_course_student"),
        UniqueConstraint("course_id", "canvas_user_id", name="uq_canvas_map_course_user"),
    )


class CanvasGradeSync(db.Model):
    __tablename__ = "canvas_grade_syncs"
    id = db.Column(db.Integer, primary_key=True)
    assessment_id = db.Column(db.Integer, ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True)
    course_id = db.Column(db.String(100), nullable=False)
    assignment_id = db.Column(db.String(100), nullable=False)
    student_number = db.Column(db.String(50), nullable=False)
    canvas_user_id = db.Column(db.String(100), nullable=True)
    posted_grade = db.Column(db.Numeric(10, 2), nullable=True)
    previous_grade = db.Column(db.Numeric(10, 2), nullable=True)
    status = db.Column(db.String(20), nullable=False)
    error_detail = db.Column(db.String(1000), nullable=True)
    authorized_by = db.Column(db.String(100), nullable=False, default="local teacher")
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    verified_at = db.Column(db.DateTime(timezone=True), nullable=True)
    assessment = db.relationship("Assessment")
    __table_args__ = (
        CheckConstraint("status IN ('succeeded', 'failed', 'skipped')", name="ck_canvas_grade_sync_status"),
        db.Index("ix_canvas_grade_sync_lookup", "assessment_id", "course_id", "assignment_id", "student_number", "created_at"),
    )
