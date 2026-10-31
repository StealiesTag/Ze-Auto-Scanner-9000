"""Add Paparazzi V3 answer support and scan drafts.

Revision ID: 20261101_0002
Revises: 20261026_0001
Create Date: 2026-11-01
"""
from alembic import op
import sqlalchemy as sa

revision = "20261101_0002"
down_revision = "20261026_0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("questions", sa.Column("option_e", sa.String(length=500), nullable=False, server_default=""))
    op.alter_column("questions", "option_e", server_default=None)

    op.drop_constraint("ck_question_answer_valid", "questions", type_="check")
    op.create_check_constraint("ck_question_answer_valid", "questions", "correct_answer IN ('A', 'B', 'C', 'D', 'E')")

    op.drop_constraint("ck_response_detected_answer_valid", "responses", type_="check")
    op.drop_constraint("ck_response_answer_valid", "responses", type_="check")
    op.create_check_constraint(
        "ck_response_detected_answer_valid", "responses",
        "detected_answer IS NULL OR detected_answer IN ('A', 'B', 'C', 'D', 'E')",
    )
    op.create_check_constraint(
        "ck_response_answer_valid", "responses",
        "answer IS NULL OR answer IN ('A', 'B', 'C', 'D', 'E')",
    )

    op.drop_constraint("ck_correction_previous_answer", "answer_corrections", type_="check")
    op.drop_constraint("ck_correction_answer", "answer_corrections", type_="check")
    op.create_check_constraint(
        "ck_correction_previous_answer", "answer_corrections",
        "previous_answer IS NULL OR previous_answer IN ('A', 'B', 'C', 'D', 'E')",
    )
    op.create_check_constraint(
        "ck_correction_answer", "answer_corrections",
        "corrected_answer IS NULL OR corrected_answer IN ('A', 'B', 'C', 'D', 'E')",
    )

    op.create_table(
        "scan_drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("assessment_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=True),
        sa.Column("detected_student_number", sa.String(length=7), nullable=True),
        sa.Column("question_count", sa.Integer(), nullable=False),
        sa.Column("profile_version", sa.String(length=50), nullable=False),
        sa.Column("original_image", sa.String(length=255), nullable=False),
        sa.Column("aligned_image", sa.String(length=255), nullable=False),
        sa.Column("debug_image", sa.String(length=255), nullable=False),
        sa.Column("student_id_detection", sa.JSON(), nullable=False),
        sa.Column("recognized_answers", sa.JSON(), nullable=False),
        sa.Column("processing_notes", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["assessment_id"], ["assessments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="SET NULL"),
        sa.CheckConstraint("question_count IN (20, 50, 100)", name="ck_scan_draft_question_count"),
    )
    op.create_index("ix_scan_drafts_assessment_id", "scan_drafts", ["assessment_id"])


def downgrade():
    op.drop_index("ix_scan_drafts_assessment_id", table_name="scan_drafts")
    op.drop_table("scan_drafts")

    op.drop_constraint("ck_question_answer_valid", "questions", type_="check")
    op.create_check_constraint("ck_question_answer_valid", "questions", "correct_answer IN ('A', 'B', 'C', 'D')")

    op.drop_constraint("ck_response_detected_answer_valid", "responses", type_="check")
    op.drop_constraint("ck_response_answer_valid", "responses", type_="check")
    op.create_check_constraint(
        "ck_response_detected_answer_valid", "responses",
        "detected_answer IS NULL OR detected_answer IN ('A', 'B', 'C', 'D')",
    )
    op.create_check_constraint(
        "ck_response_answer_valid", "responses",
        "answer IS NULL OR answer IN ('A', 'B', 'C', 'D')",
    )

    op.drop_constraint("ck_correction_previous_answer", "answer_corrections", type_="check")
    op.drop_constraint("ck_correction_answer", "answer_corrections", type_="check")
    op.create_check_constraint(
        "ck_correction_previous_answer", "answer_corrections",
        "previous_answer IS NULL OR previous_answer IN ('A', 'B', 'C', 'D')",
    )
    op.create_check_constraint(
        "ck_correction_answer", "answer_corrections",
        "corrected_answer IS NULL OR corrected_answer IN ('A', 'B', 'C', 'D')",
    )

    op.drop_column("questions", "option_e")
