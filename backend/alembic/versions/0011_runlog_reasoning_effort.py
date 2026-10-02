"""Add denormalized run_logs.reasoning_effort.

Revision ID: 0011_runlog_reasoning_effort
Revises: 0010_runlog_meeting_fields
Create Date: 2026-10-02
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0011_runlog_reasoning_effort"
down_revision = "0010_runlog_meeting_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain denormalized snapshot — deliberately NO FK.
    # Old rows keep "" via the server default (never backfilled via joins).
    op.add_column(
        "run_logs",
        sa.Column("reasoning_effort", sa.String(32), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("run_logs", "reasoning_effort")
