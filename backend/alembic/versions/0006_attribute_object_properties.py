"""Add attributes.object_properties JSON (ordered sub-fields for type object).

Revision ID: 0006_attribute_object_properties
Revises: 0005_runlog_requests
Create Date: 2026-10-01
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0006_attribute_object_properties"
down_revision = "0005_runlog_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "attributes",
        sa.Column("object_properties", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("attributes", "object_properties")
