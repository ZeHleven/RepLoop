"""add agent intent and model observability

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-19 10:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column(
            "clarification_required",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "intent_source",
            sa.String(length=20),
            server_default="rules",
            nullable=False,
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column("intent_confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "intent_attempt_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column("intent_fallback_reason", sa.String(length=80), nullable=True),
    )
    op.add_column("agent_runs", sa.Column("duration_ms", sa.Integer(), nullable=True))
    op.add_column("agent_runs", sa.Column("input_tokens", sa.Integer(), nullable=True))
    op.add_column("agent_runs", sa.Column("output_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "output_tokens")
    op.drop_column("agent_runs", "input_tokens")
    op.drop_column("agent_runs", "duration_ms")
    op.drop_column("agent_runs", "intent_fallback_reason")
    op.drop_column("agent_runs", "intent_attempt_count")
    op.drop_column("agent_runs", "intent_confidence")
    op.drop_column("agent_runs", "intent_source")
    op.drop_column("agent_runs", "clarification_required")
