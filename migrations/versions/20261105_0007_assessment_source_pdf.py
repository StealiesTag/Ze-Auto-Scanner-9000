"""Attach original question PDFs to assessments for AI lesson context."""
from alembic import op
import sqlalchemy as sa


revision = "20261105_0007"
down_revision = "20261101_0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("assessments", sa.Column("source_pdf_filename", sa.String(length=255), nullable=True))
    op.add_column("assessments", sa.Column("source_pdf_path", sa.String(length=500), nullable=True))
    op.add_column("assessments", sa.Column("source_pdf_text", sa.Text(), nullable=True))
    op.add_column("assessments", sa.Column("source_pdf_sha256", sa.String(length=64), nullable=True))
    op.add_column("assessments", sa.Column("source_pdf_page_count", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("assessments", "source_pdf_page_count")
    op.drop_column("assessments", "source_pdf_sha256")
    op.drop_column("assessments", "source_pdf_text")
    op.drop_column("assessments", "source_pdf_path")
    op.drop_column("assessments", "source_pdf_filename")
