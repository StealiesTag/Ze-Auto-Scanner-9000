"""Persist the local ESP32 camera setup used by scanning sessions."""
from alembic import op
import sqlalchemy as sa


revision = "20261105_0010"
down_revision = "20261105_0009"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "camera_setup",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("camera_id", sa.String(length=100), nullable=False),
        sa.Column("stream_url", sa.String(length=500), nullable=False),
        sa.Column("capture_url", sa.String(length=500), nullable=False),
        sa.Column("timeout_seconds", sa.Float(), nullable=False, server_default="8.0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_camera_setup_singleton"),
        sa.CheckConstraint("length(camera_id) BETWEEN 1 AND 100", name="ck_camera_setup_camera_id_length"),
        sa.CheckConstraint("timeout_seconds > 0 AND timeout_seconds <= 30", name="ck_camera_setup_timeout"),
    )
    op.alter_column("camera_setup", "timeout_seconds", server_default=None)


def downgrade():
    op.drop_table("camera_setup")
