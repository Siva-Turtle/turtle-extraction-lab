"""Add denormalized run_logs.client + meeting_type + meeting_title.

Revision ID: 0010_runlog_meeting_fields
Revises: 0009_agent_description_kind
Create Date: 2026-10-02
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0010_runlog_meeting_fields"
down_revision = "0009_agent_description_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain denormalized snapshots — deliberately NO FK to meetings/clients.
    # Old rows keep "" via the server default (never backfilled via joins).
    op.add_column(
        "run_logs",
        sa.Column("client", sa.String(500), nullable=False, server_default=""),
    )
    op.add_column(
        "run_logs",
        sa.Column("meeting_type", sa.String(500), nullable=False, server_default=""),
    )
    op.add_column(
        "run_logs",
        sa.Column("meeting_title", sa.String(500), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("run_logs", "meeting_title")
    op.drop_column("run_logs", "meeting_type")
    op.drop_column("run_logs", "client")
