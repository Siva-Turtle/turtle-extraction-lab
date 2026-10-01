"""Add attributes.group_name (free-text group; API field `group`).

Revision ID: 0007_attribute_group
Revises: 0006_attribute_object_properties
Create Date: 2026-10-02
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0007_attribute_group"
down_revision = "0006_attribute_object_properties"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "attributes",
        sa.Column("group_name", sa.String(200), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("attributes", "group_name")
