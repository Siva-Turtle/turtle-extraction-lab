"""Add denormalized run_logs.consistency JSON."""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0014_runlog_consistency"
down_revision = "0013_runlog_reused_from"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "run_logs",
        sa.Column("consistency", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("run_logs", "consistency")
