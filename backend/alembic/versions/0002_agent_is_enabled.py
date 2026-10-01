"""Add agents.is_enabled toggle.

Revision ID: 0002_agent_is_enabled
Revises: 0001_initial
Create Date: 2026-10-01
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0002_agent_is_enabled"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("agents", "is_enabled")
