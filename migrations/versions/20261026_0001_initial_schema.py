"""Create the initial PostgreSQL schema.

Revision ID: 20261026_0001
Revises:
Create Date: 2026-10-26
"""
from alembic import op
import sqlalchemy as sa

revision = "20261026_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "students",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_number", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("class_section", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("student_number", name="uq_students_student_number"),
    )
    op.create_table(
        "assessments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("subject", sa.String(length=100), nullable=False),
        sa.Column("class_section", sa.String(length=100), nullable=False),
        sa.Column("total_questions", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("total_questions > 0", name="ck_assessment_questions_positive"),
    )
    op.create_table(
        "learning_resources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("learning_objective", sa.String(length=255), nullable=False),
        sa.Column("resource_type", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("resource_type IN ('reading', 'video', 'practice', 'lesson', 'other')", name="ck_resource_type"),
    )
    op.create_table(
        "questions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("assessment_id", sa.Integer(), nullable=False),
        sa.Column("question_number", sa.Integer(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("option_a", sa.String(length=500), nullable=False),
        sa.Column("option_b", sa.String(length=500), nullable=False),
        sa.Column("option_c", sa.String(length=500), nullable=False),
        sa.Column("option_d", sa.String(length=500), nullable=False),
        sa.Column("correct_answer", sa.String(length=1), nullable=False),
        sa.Column("points", sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column("learning_objective", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(["assessment_id"], ["assessments.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("assessment_id", "question_number", name="uq_assessment_question_number"),
        sa.CheckConstraint("question_number > 0", name="ck_question_number_positive"),
        sa.CheckConstraint("correct_answer IN ('A', 'B', 'C', 'D')", name="ck_question_answer_valid"),
        sa.CheckConstraint("points > 0", name="ck_question_points_positive"),
    )
    op.create_table(
        "submissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("assessment_id", sa.Integer(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("score", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("total_points", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("percentage_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("processing_status", sa.String(length=50), nullable=False),
        sa.Column("review_status", sa.String(length=50), nullable=False),
        sa.Column("original_image", sa.String(length=255), nullable=True),
        sa.Column("aligned_image", sa.String(length=255), nullable=True),
        sa.Column("debug_image", sa.String(length=255), nullable=True),
        sa.Column("processing_notes", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assessment_id"], ["assessments.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("student_id", "assessment_id", name="uq_student_assessment_submission"),
        sa.CheckConstraint("score >= 0", name="ck_submission_score_nonnegative"),
        sa.CheckConstraint("total_points >= 0", name="ck_submission_total_nonnegative"),
        sa.CheckConstraint("percentage_score >= 0 AND percentage_score <= 100", name="ck_submission_percentage_range"),
        sa.CheckConstraint("processing_status IN ('pending', 'processed', 'failed')", name="ck_submission_processing_status"),
        sa.CheckConstraint("review_status IN ('not_required', 'required', 'complete')", name="ck_submission_review_status"),
    )
    op.create_index("ix_submissions_assessment_id", "submissions", ["assessment_id"])
    op.create_table(
        "responses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("submission_id", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("detected_answer", sa.String(length=1), nullable=True),
        sa.Column("answer", sa.String(length=1), nullable=True),
        sa.Column("detection_status", sa.String(length=30), nullable=False),
        sa.Column("diagnostic_values", sa.JSON(), nullable=False),
        sa.Column("correctness", sa.Boolean(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("manual_correction_flag", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("submission_id", "question_id", name="uq_submission_question_response"),
        sa.CheckConstraint("detected_answer IS NULL OR detected_answer IN ('A', 'B', 'C', 'D')", name="ck_response_detected_answer_valid"),
        sa.CheckConstraint("answer IS NULL OR answer IN ('A', 'B', 'C', 'D')", name="ck_response_answer_valid"),
        sa.CheckConstraint("detection_status IN ('detected', 'blank', 'multiple_marks', 'ambiguous')", name="ck_response_detection_status"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_response_confidence_range"),
    )
    op.create_table(
        "answer_corrections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("response_id", sa.Integer(), nullable=False),
        sa.Column("previous_answer", sa.String(length=1), nullable=True),
        sa.Column("corrected_answer", sa.String(length=1), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["response_id"], ["responses.id"], ondelete="CASCADE"),
        sa.CheckConstraint("previous_answer IS NULL OR previous_answer IN ('A', 'B', 'C', 'D')", name="ck_correction_previous_answer"),
        sa.CheckConstraint("corrected_answer IS NULL OR corrected_answer IN ('A', 'B', 'C', 'D')", name="ck_correction_answer"),
    )
    op.create_index("ix_responses_question_id", "responses", ["question_id"])
    op.create_index("ix_answer_corrections_response_id", "answer_corrections", ["response_id"])


def downgrade():
    op.drop_index("ix_answer_corrections_response_id", table_name="answer_corrections")
    op.drop_index("ix_submissions_assessment_id", table_name="submissions")
    op.drop_index("ix_responses_question_id", table_name="responses")
    op.drop_table("answer_corrections")
    op.drop_table("responses")
    op.drop_table("submissions")
    op.drop_table("questions")
    op.drop_table("learning_resources")
    op.drop_table("assessments")
    op.drop_table("students")
