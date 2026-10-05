"""Add denormalized run_logs.fireflies_url + evidence + probabilities.

- fireflies_url: Mongo task `transcriptUrl` snapshot ("" when absent/old rows).
- evidence: {agent_id: {question_key: chunk_no:int}} (identifier agents only).
- probabilities: {agent_id: {question_key: float|null}} (Jev noul floats; Opus nulls).
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0019_log_evidence"
down_revision = "0018_attribute_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain denormalized snapshots — deliberately NO FKs.
    # Old rows keep "" / {} via the server defaults.
    op.add_column(
        "run_logs",
        sa.Column("fireflies_url", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "run_logs",
        sa.Column("evidence", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "run_logs",
        sa.Column("probabilities", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("run_logs", "probabilities")
    op.drop_column("run_logs", "evidence")
    op.drop_column("run_logs", "fireflies_url")
