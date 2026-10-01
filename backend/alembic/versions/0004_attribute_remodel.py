"""Remodel attributes: many-to-many agent links, enum options, drop prompt-era fields.

Revision ID: 0004_attribute_remodel
Revises: 0003_runlog_usage_filters
Create Date: 2026-10-01
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0004_attribute_remodel"
down_revision = "0003_runlog_usage_filters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_attributes",
        sa.Column("agent_id", sa.String(36),
                  sa.ForeignKey("agents.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("attribute_id", sa.String(36),
                  sa.ForeignKey("attributes.id", ondelete="CASCADE"),
                  primary_key=True),
    )
    # Preserve existing single-agent links.
    op.execute(
        "INSERT INTO agent_attributes (agent_id, attribute_id) "
        "SELECT agent_id, id FROM attributes"
    )
    op.drop_constraint("attributes_agent_id_fkey", "attributes", type_="foreignkey")
    op.drop_column("attributes", "agent_id")
    op.drop_column("attributes", "required")
    op.drop_column("attributes", "json_schema")
    op.add_column(
        "attributes",
        sa.Column("enum_values", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("attributes", "enum_values")
    op.add_column(
        "attributes",
        sa.Column("json_schema", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column("attributes", sa.Column("required", sa.Boolean(), nullable=True))
    op.add_column(
        "attributes",
        sa.Column("agent_id", sa.String(36), nullable=True),
    )
    # Best-effort restore: first linked agent per attribute.
    op.execute(
        "UPDATE attributes SET agent_id = sub.agent_id FROM "
        "(SELECT DISTINCT ON (attribute_id) attribute_id, agent_id FROM agent_attributes) AS sub "
        "WHERE attributes.id = sub.attribute_id"
    )
    op.create_foreign_key("attributes_agent_id_fkey", "attributes", "agents",
                          ["agent_id"], ["id"])
    op.drop_table("agent_attributes")
