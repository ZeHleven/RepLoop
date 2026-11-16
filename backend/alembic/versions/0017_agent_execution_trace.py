"""Persist Agent execution mode and structured execution trace.

Revision ID: 0017
Revises: 0016
"""

from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("execution_mode", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "execution_trace",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_agent_runs_execution_mode",
        "agent_runs",
        "execution_mode IS NULL OR execution_mode IN "
        "('direct', 'planned', 'clarify', 'safe_stop')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_agent_runs_execution_mode",
        "agent_runs",
        type_="check",
    )
    op.drop_column("agent_runs", "execution_trace")
    op.drop_column("agent_runs", "execution_mode")
