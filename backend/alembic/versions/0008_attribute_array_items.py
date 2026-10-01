"""Add attributes.array_items JSON (item-shape config for type array).

Revision ID: 0008_attribute_array_items
Revises: 0007_attribute_group
Create Date: 2026-10-02
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0008_attribute_array_items"
down_revision = "0007_attribute_group"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "attributes",
        sa.Column("array_items", sa.JSON(), nullable=False,
                  server_default='{"kind": "string", "properties": []}'),
    )


def downgrade() -> None:
    op.drop_column("attributes", "array_items")
