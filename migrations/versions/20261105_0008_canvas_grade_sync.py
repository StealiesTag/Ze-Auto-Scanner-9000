"""Persist optional Canvas configuration, mappings, and grade sync history."""
from alembic import op
import sqlalchemy as sa

revision = "20261105_0008"
down_revision = "20261105_0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("assessments", sa.Column("answer_sheet_version", sa.String(length=2), nullable=False, server_default="V3"))
    op.create_check_constraint("ck_assessment_sheet_version", "assessments", "answer_sheet_version IN ('V3', 'V4')")
    op.alter_column("assessments", "answer_sheet_version", server_default=None)
    op.create_table(
        "canvas_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("encrypted_access_token", sa.Text(), nullable=False),
        sa.Column("canvas_user_id", sa.String(length=100), nullable=False),
        sa.Column("connection_status", sa.String(length=20), nullable=False, server_default="connected"),
        sa.Column("authorized_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("connection_status IN ('connected', 'expired', 'disconnected')", name="ck_canvas_connection_status"),
    )
    op.create_table(
        "canvas_assessment_mappings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("assessment_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.String(length=100), nullable=False),
        sa.Column("course_name", sa.String(length=255), nullable=False),
        sa.Column("assignment_id", sa.String(length=100), nullable=True),
        sa.Column("assignment_name", sa.String(length=255), nullable=True),
        sa.Column("points_possible", sa.Numeric(10, 2), nullable=True),
        sa.Column("grading_type", sa.String(length=30), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["assessment_id"], ["assessments.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("assessment_id", name="uq_canvas_assessment_mappings_assessment_id"),
    )
    op.create_table(
        "canvas_student_mappings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("course_id", sa.String(length=100), nullable=False),
        sa.Column("student_number", sa.String(length=50), nullable=False),
        sa.Column("canvas_user_id", sa.String(length=100), nullable=False),
        sa.Column("sis_identifier", sa.String(length=100), nullable=True),
        sa.Column("verified_by", sa.String(length=100), server_default="local teacher", nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("course_id", "student_number", name="uq_canvas_map_course_student"),
        sa.UniqueConstraint("course_id", "canvas_user_id", name="uq_canvas_map_course_user"),
    )
    op.create_table(
        "canvas_grade_syncs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("assessment_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.String(length=100), nullable=False),
        sa.Column("assignment_id", sa.String(length=100), nullable=False),
        sa.Column("student_number", sa.String(length=50), nullable=False),
        sa.Column("canvas_user_id", sa.String(length=100), nullable=True),
        sa.Column("posted_grade", sa.Numeric(10, 2), nullable=True),
        sa.Column("previous_grade", sa.Numeric(10, 2), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_detail", sa.String(length=1000), nullable=True),
        sa.Column("authorized_by", sa.String(length=100), server_default="local teacher", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["assessment_id"], ["assessments.id"], ondelete="CASCADE"),
        sa.CheckConstraint("status IN ('succeeded', 'failed', 'skipped')", name="ck_canvas_grade_sync_status"),
    )
    op.create_index("ix_canvas_grade_syncs_assessment_id", "canvas_grade_syncs", ["assessment_id"])
    op.create_index("ix_canvas_grade_sync_lookup", "canvas_grade_syncs", ["assessment_id", "course_id", "assignment_id", "student_number", "created_at"])


def downgrade():
    op.drop_index("ix_canvas_grade_sync_lookup", table_name="canvas_grade_syncs")
    op.drop_index("ix_canvas_grade_syncs_assessment_id", table_name="canvas_grade_syncs")
    op.drop_table("canvas_grade_syncs")
    op.drop_table("canvas_student_mappings")
    op.drop_table("canvas_assessment_mappings")
    op.drop_table("canvas_connections")
    op.drop_constraint("ck_assessment_sheet_version", "assessments", type_="check")
    op.drop_column("assessments", "answer_sheet_version")
