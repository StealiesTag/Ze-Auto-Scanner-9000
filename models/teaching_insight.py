from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint, func

from extensions import db


class TeachingInsight(db.Model):
    __tablename__ = "teaching_insights"

    id = db.Column(db.Integer, primary_key=True)
    assessment_id = db.Column(db.Integer, ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False)
    input_hash = db.Column(db.String(64), nullable=False)
    duration_minutes = db.Column(db.Integer, nullable=True)
    selected_gaps = db.Column(db.JSON, nullable=False, default=list)
    output = db.Column(db.JSON, nullable=False)
    teacher_saved = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    assessment = db.relationship("Assessment", backref=db.backref("teaching_insights", cascade="all, delete-orphan"))

    __table_args__ = (
        CheckConstraint("kind IN ('insights', 'lesson', 'remediation')", name="ck_teaching_insights_kind"),
        CheckConstraint("duration_minutes IS NULL OR duration_minutes IN (15, 30, 45, 60)", name="ck_teaching_insights_duration"),
        UniqueConstraint("assessment_id", "kind", "input_hash", name="uq_teaching_insights_cache"),
    )
