"""Add agents.description + agents.kind (extraction|identifier).

Revision ID: 0009_agent_description_kind
Revises: 0008_attribute_array_items
Create Date: 2026-10-02
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0009_agent_description_kind"
down_revision = "0008_attribute_array_items"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "agents",
        sa.Column("kind", sa.String(32), nullable=False, server_default="extraction"),
    )


def downgrade() -> None:
    op.drop_column("agents", "kind")
    op.drop_column("agents", "description")
