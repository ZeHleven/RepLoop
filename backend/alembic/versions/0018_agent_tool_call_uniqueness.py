"""Prevent duplicate tool audit events for one Agent run.

Revision ID: 0018
Revises: 0017
"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.execute(sa.text("""
        WITH duplicate_tool_calls AS (
            SELECT id
            FROM (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY run_id, call_id
                        ORDER BY created_at ASC, id ASC
                    ) AS duplicate_rank
                FROM agent_tool_calls
                WHERE call_id IS NOT NULL
            ) AS ranked
            WHERE duplicate_rank > 1
        )
        DELETE FROM agent_tool_calls
        WHERE id IN (SELECT id FROM duplicate_tool_calls)
    """))
    op.create_index(
        "uq_agent_tool_calls_run_call_id",
        "agent_tool_calls",
        ["run_id", "call_id"],
        unique=True,
        postgresql_where=sa.text("call_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_agent_tool_calls_run_call_id",
        table_name="agent_tool_calls",
    )
