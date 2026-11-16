"""Prevent duplicate assistant results for an Agent run.

Revision ID: 0016
Revises: 0015
"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.execute(sa.text("""
        WITH duplicate_assistant_messages AS (
            SELECT id
            FROM (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY run_id
                        ORDER BY created_at ASC, id ASC
                    ) AS duplicate_rank
                FROM agent_messages
                WHERE role = 'assistant' AND run_id IS NOT NULL
            ) AS ranked
            WHERE duplicate_rank > 1
        )
        DELETE FROM agent_messages
        WHERE id IN (SELECT id FROM duplicate_assistant_messages)
    """))
    op.create_index(
        "uq_agent_messages_one_assistant_per_run",
        "agent_messages",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text(
            "role = 'assistant' AND run_id IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_agent_messages_one_assistant_per_run",
        table_name="agent_messages",
    )
