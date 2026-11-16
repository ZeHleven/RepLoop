"""add conversation understanding and clarification state

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-20 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_conversations",
        sa.Column(
            "pending_clarification",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column("resolved_query", sa.Text(), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "references",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column("clarification_question", sa.Text(), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("understanding_version", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "understanding_version")
    op.drop_column("agent_runs", "clarification_question")
    op.drop_column("agent_runs", "references")
    op.drop_column("agent_runs", "resolved_query")
    op.drop_column("agent_conversations", "pending_clarification")
