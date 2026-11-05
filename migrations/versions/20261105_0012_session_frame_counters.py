"""Track rejected and duplicate frames for the live-stream scan session."""
from alembic import op
import sqlalchemy as sa


revision = "20261105_0012"
down_revision = "20261105_0011"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("scanner_control", sa.Column("session_rejected_frames", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("scanner_control", sa.Column("session_duplicate_frames", sa.Integer(), nullable=False, server_default="0"))
    op.alter_column("scanner_control", "session_rejected_frames", server_default=None)
    op.alter_column("scanner_control", "session_duplicate_frames", server_default=None)


def downgrade():
    op.drop_column("scanner_control", "session_duplicate_frames")
    op.drop_column("scanner_control", "session_rejected_frames")
