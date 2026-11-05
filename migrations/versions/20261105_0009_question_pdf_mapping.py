"""Store reviewed links from answer-key questions to original PDF content."""
from alembic import op
import sqlalchemy as sa

revision = "20261105_0009"
down_revision = "20261105_0008"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("questions", sa.Column("source_pdf_page", sa.Integer(), nullable=True))
    op.add_column("questions", sa.Column("source_pdf_excerpt", sa.Text(), nullable=True))
    op.add_column("questions", sa.Column("source_pdf_mapping_reviewed", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column("questions", "source_pdf_mapping_reviewed", server_default=None)
    op.create_check_constraint("ck_question_source_pdf_page_positive", "questions", "source_pdf_page IS NULL OR source_pdf_page > 0")


def downgrade():
    op.drop_constraint("ck_question_source_pdf_page_positive", "questions", type_="check")
    op.drop_column("questions", "source_pdf_excerpt")
    op.drop_column("questions", "source_pdf_mapping_reviewed")
    op.drop_column("questions", "source_pdf_page")
