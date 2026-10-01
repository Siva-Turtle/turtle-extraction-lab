"""Add run_logs.requests snapshot (exact LLM request payload per agent).

Revision ID: 0005_runlog_requests
Revises: 0004_attribute_remodel
Create Date: 2026-10-01
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0005_runlog_requests"
down_revision = "0004_attribute_remodel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "run_logs",
        sa.Column("requests", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("run_logs", "requests")
