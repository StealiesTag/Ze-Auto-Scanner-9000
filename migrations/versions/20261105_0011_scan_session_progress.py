"""Persist scan session boundaries and counter baselines."""
from alembic import op
import sqlalchemy as sa


revision = "20261105_0011"
down_revision = "20261105_0010"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("scanner_control", sa.Column("session_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("scanner_control", sa.Column("session_ended_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("scanner_control", sa.Column("session_camera_baseline", sa.JSON(), nullable=False, server_default="{}"))
    op.alter_column("scanner_control", "session_camera_baseline", server_default=None)


def downgrade():
    op.drop_column("scanner_control", "session_camera_baseline")
    op.drop_column("scanner_control", "session_ended_at")
    op.drop_column("scanner_control", "session_started_at")
