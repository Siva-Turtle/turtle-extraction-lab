"""Initial schema: agents, attributes, runs, feedbacks, run_logs.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-01
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("system_instruction", sa.Text(), nullable=False, server_default=""),
        sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("input_types", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "attributes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("type", sa.String(64), nullable=False, server_default="string"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("json_schema", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("required", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("input_type", sa.String(32), nullable=False),
        sa.Column("input_data", sa.Text(), nullable=False),
        sa.Column("model", sa.String(200), nullable=False, server_default=""),
        sa.Column("agent_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("outputs", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "feedbacks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("agent_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("attribute_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("rating", sa.String(16), nullable=False),
        sa.Column("remarks", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "run_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("input_type", sa.String(32), nullable=False, server_default=""),
        sa.Column("input_data", sa.Text(), nullable=False, server_default=""),
        sa.Column("model", sa.String(200), nullable=False, server_default=""),
        sa.Column("agent_snapshot", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("attribute_snapshot", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("outputs", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("feedback", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("run_logs")
    op.drop_table("feedbacks")
    op.drop_table("runs")
    op.drop_table("attributes")
    op.drop_table("agents")
