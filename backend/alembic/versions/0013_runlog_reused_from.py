"""Add denormalized run_logs.reused_from_log_id + reused_from_created_at."""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0013_runlog_reused_from"
down_revision = "0012_runlog_run_group_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain string, NO foreign key (log rows are frozen snapshots).
    # "" = a real model call; otherwise the id of the log whose output was copied.
    op.add_column(
        "run_logs",
        sa.Column("reused_from_log_id", sa.String(36), nullable=False, server_default=""),
    )
    op.add_column(
        "run_logs",
        sa.Column("reused_from_created_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("run_logs", "reused_from_created_at")
    op.drop_column("run_logs", "reused_from_log_id")
