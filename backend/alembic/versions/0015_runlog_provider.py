"""Add denormalized run_logs.provider (requested OpenRouter provider, "" = Auto)."""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0015_runlog_provider"
down_revision = "0014_runlog_consistency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "run_logs",
        sa.Column("provider", sa.String(200), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("run_logs", "provider")
