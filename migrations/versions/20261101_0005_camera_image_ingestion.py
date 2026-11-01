"""Persist camera API state and capture validation metadata."""
from alembic import op
import sqlalchemy as sa


revision = "20261101_0005"
down_revision = "20261101_0004"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_capture_job_source", "capture_jobs", type_="check")
    op.create_check_constraint(
        "ck_capture_job_source", "capture_jobs", "source IN ('esp32', 'manual', 'api')"
    )
    op.add_column("capture_jobs", sa.Column("camera_id", sa.String(length=100), nullable=True))
    op.add_column("capture_jobs", sa.Column("detection_metadata", sa.JSON(), nullable=False, server_default="{}"))
    op.create_index("ix_capture_jobs_camera_id", "capture_jobs", ["camera_id"])
    if not sa.inspect(op.get_bind()).has_table("camera_ingestion_state"):
        op.create_table(
            "camera_ingestion_state",
            sa.Column("camera_id", sa.String(length=100), primary_key=True),
            sa.Column("controller_state", sa.JSON(), nullable=False),
            sa.Column("received_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("rejected_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("accepted_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_received_at", sa.DateTime(timezone=True), nullable=True),
        )
    else:
        expected = {
            "camera_id", "controller_state", "received_count", "rejected_count",
            "accepted_count", "duplicate_count", "last_received_at",
        }
        actual = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("camera_ingestion_state")}
        if actual != expected:
            raise RuntimeError("Existing camera_ingestion_state does not match migration 20261101_0005.")


def downgrade():
    bind = op.get_bind()
    accepted_api_captures = bind.execute(sa.text("SELECT count(*) FROM capture_jobs WHERE source = 'api'")).scalar_one()
    camera_state_rows = bind.execute(sa.text("SELECT count(*) FROM camera_ingestion_state")).scalar_one()
    if accepted_api_captures or camera_state_rows:
        raise RuntimeError(
            "Cannot downgrade camera ingestion while API captures or per-camera state exist; "
            "archive/export them before considering a manual rollback."
        )
    op.drop_table("camera_ingestion_state")
    op.drop_index("ix_capture_jobs_camera_id", table_name="capture_jobs")
    op.drop_column("capture_jobs", "detection_metadata")
    op.drop_column("capture_jobs", "camera_id")
    op.drop_constraint("ck_capture_job_source", "capture_jobs", type_="check")
    op.create_check_constraint(
        "ck_capture_job_source", "capture_jobs", "source IN ('esp32', 'manual')"
    )
