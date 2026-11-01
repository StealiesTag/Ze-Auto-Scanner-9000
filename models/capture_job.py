from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint, func

from extensions import db


class ScannerControl(db.Model):
    __tablename__ = "scanner_control"

    id = db.Column(db.Integer, primary_key=True)
    active = db.Column(db.Boolean, nullable=False, default=False)
    paused = db.Column(db.Boolean, nullable=False, default=False)
    assessment_id = db.Column(db.Integer, ForeignKey("assessments.id", ondelete="SET NULL"), nullable=True)
    state = db.Column(db.String(50), nullable=False, default="WAITING_FOR_FIRST_SHEET")
    camera_connected = db.Column(db.Boolean, nullable=False, default=False)
    sheet_detected = db.Column(db.Boolean, nullable=False, default=False)
    marker_status = db.Column(db.JSON, nullable=False, default=dict)
    stability_status = db.Column(db.Boolean, nullable=False, default=False)
    last_error = db.Column(db.String(1000), nullable=True)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class CaptureJob(db.Model):
    __tablename__ = "capture_jobs"

    id = db.Column(db.Integer, primary_key=True)
    capture_id = db.Column(db.String(64), nullable=False)
    assessment_id = db.Column(db.Integer, ForeignKey("assessments.id", ondelete="RESTRICT"), nullable=False, index=True)
    draft_id = db.Column(db.Integer, ForeignKey("scan_drafts.id", ondelete="SET NULL"), nullable=True)
    submission_id = db.Column(db.Integer, ForeignKey("submissions.id", ondelete="SET NULL"), nullable=True)
    image_filename = db.Column(db.String(255), nullable=False)
    source = db.Column(db.String(20), nullable=False, default="esp32")
    camera_id = db.Column(db.String(100), nullable=True, index=True)
    detection_metadata = db.Column(db.JSON, nullable=False, default=dict)
    status = db.Column(db.String(20), nullable=False, default="QUEUED", index=True)
    retry_count = db.Column(db.Integer, nullable=False, default=0)
    error_message = db.Column(db.Text, nullable=True)
    captured_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    assessment = db.relationship("Assessment")
    draft = db.relationship("ScanDraft")
    submission = db.relationship("Submission")

    __table_args__ = (
        CheckConstraint("status IN ('QUEUED', 'PROCESSING', 'COMPLETED', 'NEEDS_REVIEW', 'FAILED')", name="ck_capture_job_status"),
        CheckConstraint("source IN ('esp32', 'manual', 'api')", name="ck_capture_job_source"),
        CheckConstraint("retry_count >= 0", name="ck_capture_job_retry_count"),
        UniqueConstraint("capture_id", name="uq_capture_job_capture_id"),
        UniqueConstraint("draft_id", name="uq_capture_job_draft_id"),
    )


class CameraIngestionState(db.Model):
    """Persist replacement-detection state and basic ingestion counts per camera."""

    __tablename__ = "camera_ingestion_state"

    camera_id = db.Column(db.String(100), primary_key=True)
    controller_state = db.Column(db.JSON, nullable=False, default=dict)
    received_count = db.Column(db.Integer, nullable=False, default=0)
    rejected_count = db.Column(db.Integer, nullable=False, default=0)
    accepted_count = db.Column(db.Integer, nullable=False, default=0)
    duplicate_count = db.Column(db.Integer, nullable=False, default=0)
    deferred_count = db.Column(db.Integer, nullable=False, default=0)
    frames_fetched = db.Column(db.Integer, nullable=False, default=0)
    polling_enabled = db.Column(db.Boolean, nullable=False, default=False)
    poller_running = db.Column(db.Boolean, nullable=False, default=False)
    connection_status = db.Column(db.String(20), nullable=False, default="stopped")
    last_received_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_fetch_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_error = db.Column(db.String(1000), nullable=True)
