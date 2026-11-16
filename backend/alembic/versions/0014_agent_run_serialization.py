"""serialize active agent runs per conversation

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-20 10:45:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A previous synchronous deployment could leave more than one interrupted
    # run marked as running for a conversation. Reconcile only the older
    # duplicates before adding the invariant; the newest run remains available
    # for the worker's legacy-run recovery path.
    op.execute(sa.text("""
        WITH ranked AS (
            SELECT
                id,
                row_number() OVER (
                    PARTITION BY conversation_id
                    ORDER BY
                        COALESCE(processing_started_at, started_at, queued_at) DESC,
                        id DESC
                ) AS position
            FROM agent_runs
            WHERE status = 'running'
        )
        UPDATE agent_runs AS target
        SET
            status = 'failed',
            error_code = 'migration_superseded_run',
            completed_at = now(),
            lease_expires_at = NULL
        FROM ranked
        WHERE target.id = ranked.id
          AND ranked.position > 1
    """))
    op.create_index(
        "uq_agent_runs_one_running_per_conversation",
        "agent_runs",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_agent_runs_one_running_per_conversation",
        table_name="agent_runs",
    )
