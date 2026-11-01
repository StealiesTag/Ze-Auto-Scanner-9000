"""Persist CameraWebServer poller state and health metrics."""
from alembic import op
import sqlalchemy as sa


revision = "20261101_0006"
down_revision = "20261101_0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("camera_ingestion_state", sa.Column("deferred_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("camera_ingestion_state", sa.Column("frames_fetched", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("camera_ingestion_state", sa.Column("polling_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("camera_ingestion_state", sa.Column("poller_running", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("camera_ingestion_state", sa.Column("connection_status", sa.String(length=20), nullable=False, server_default="stopped"))
    op.add_column("camera_ingestion_state", sa.Column("last_fetch_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("camera_ingestion_state", sa.Column("last_error", sa.String(length=1000), nullable=True))


def downgrade():
    op.drop_column("camera_ingestion_state", "last_error")
    op.drop_column("camera_ingestion_state", "last_fetch_at")
    op.drop_column("camera_ingestion_state", "connection_status")
    op.drop_column("camera_ingestion_state", "poller_running")
    op.drop_column("camera_ingestion_state", "polling_enabled")
    op.drop_column("camera_ingestion_state", "frames_fetched")
    op.drop_column("camera_ingestion_state", "deferred_count")
