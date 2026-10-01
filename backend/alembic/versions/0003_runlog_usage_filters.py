"""Add run_logs.usage + run_logs.filters snapshots.

Revision ID: 0003_runlog_usage_filters
Revises: 0002_agent_is_enabled
Create Date: 2026-10-01
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0003_runlog_usage_filters"
down_revision = "0002_agent_is_enabled"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "run_logs",
        sa.Column("usage", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "run_logs",
        sa.Column("filters", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("run_logs", "filters")
    op.drop_column("run_logs", "usage")
