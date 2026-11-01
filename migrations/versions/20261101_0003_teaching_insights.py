"""Persist generated teaching suggestions per assessment."""
from alembic import op
import sqlalchemy as sa

revision = "20261101_0003"
down_revision = "20261101_0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "teaching_insights",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("assessment_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("selected_gaps", sa.JSON(), nullable=False),
        sa.Column("output", sa.JSON(), nullable=False),
        sa.Column("teacher_saved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["assessment_id"], ["assessments.id"], ondelete="CASCADE"),
        sa.CheckConstraint("kind IN ('insights', 'lesson', 'remediation')", name="ck_teaching_insights_kind"),
        sa.CheckConstraint("duration_minutes IS NULL OR duration_minutes IN (15, 30, 45, 60)", name="ck_teaching_insights_duration"),
        sa.UniqueConstraint("assessment_id", "kind", "input_hash", name="uq_teaching_insights_cache"),
    )
    op.create_index("ix_teaching_insights_assessment_id", "teaching_insights", ["assessment_id"])


def downgrade():
    op.drop_index("ix_teaching_insights_assessment_id", table_name="teaching_insights")
    op.drop_table("teaching_insights")
