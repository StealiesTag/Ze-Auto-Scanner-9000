"""Standardize all assessments and scanning on Paparazzi V4.

Revision ID: 20261105_0013
Revises: 20261105_0012
"""
from alembic import op
import sqlalchemy as sa


revision = "20261105_0013"
down_revision = "20261105_0012"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_assessment_sheet_version", "assessments", type_="check")
    op.execute("UPDATE assessments SET answer_sheet_version = 'V4' WHERE answer_sheet_version <> 'V4'")
    op.alter_column("assessments", "answer_sheet_version", server_default="V4")
    op.create_check_constraint("ck_assessment_sheet_version", "assessments", "answer_sheet_version = 'V4'")


def downgrade():
    op.drop_constraint("ck_assessment_sheet_version", "assessments", type_="check")
    op.execute("UPDATE assessments SET answer_sheet_version = 'V3'")
    op.alter_column("assessments", "answer_sheet_version", server_default="V3")
    op.create_check_constraint("ck_assessment_sheet_version", "assessments", "answer_sheet_version IN ('V3', 'V4')")
