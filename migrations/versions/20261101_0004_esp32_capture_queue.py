"""Add durable ESP32 capture control and processing queue."""
from alembic import op
import sqlalchemy as sa

revision = "20261101_0004"
down_revision = "20261101_0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "scanner_control",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("assessment_id", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(length=50), nullable=False, server_default="WAITING_FOR_FIRST_SHEET"),
        sa.Column("camera_connected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sheet_detected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("marker_status", sa.JSON(), nullable=False),
        sa.Column("stability_status", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["assessment_id"], ["assessments.id"], ondelete="SET NULL"),
    )
    op.create_table(
        "capture_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("capture_id", sa.String(length=64), nullable=False),
        sa.Column("assessment_id", sa.Integer(), nullable=False),
        sa.Column("draft_id", sa.Integer(), nullable=True),
        sa.Column("submission_id", sa.Integer(), nullable=True),
        sa.Column("image_filename", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="esp32"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="QUEUED"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["assessment_id"], ["assessments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["draft_id"], ["scan_drafts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="SET NULL"),
        sa.CheckConstraint("status IN ('QUEUED', 'PROCESSING', 'COMPLETED', 'NEEDS_REVIEW', 'FAILED')", name="ck_capture_job_status"),
        sa.CheckConstraint("source IN ('esp32', 'manual')", name="ck_capture_job_source"),
        sa.CheckConstraint("retry_count >= 0", name="ck_capture_job_retry_count"),
        sa.UniqueConstraint("capture_id", name="uq_capture_job_capture_id"),
        sa.UniqueConstraint("draft_id", name="uq_capture_job_draft_id"),
    )
    op.create_index("ix_capture_jobs_assessment_id", "capture_jobs", ["assessment_id"])
    op.create_index("ix_capture_jobs_status", "capture_jobs", ["status"])


def downgrade():
    op.drop_index("ix_capture_jobs_status", table_name="capture_jobs")
    op.drop_index("ix_capture_jobs_assessment_id", table_name="capture_jobs")
    op.drop_table("capture_jobs")
    op.drop_table("scanner_control")
