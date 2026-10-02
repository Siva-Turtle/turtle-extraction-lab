"""Add denormalized run_logs.run_group_id."""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0012_runlog_run_group_id"
down_revision = "0011_runlog_reasoning_effort"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain denormalized snapshot — deliberately NO FK.
    # Old rows keep "" via the server default.
    op.add_column(
        "run_logs",
        sa.Column("run_group_id", sa.String(36), nullable=False, server_default=""),
    )
    op.create_index("ix_run_logs_run_group_id", "run_logs", ["run_group_id"])


def downgrade() -> None:
    op.drop_index("ix_run_logs_run_group_id", table_name="run_logs")
    op.drop_column("run_logs", "run_group_id")
