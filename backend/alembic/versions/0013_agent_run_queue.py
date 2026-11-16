"""add durable agent run queue fields

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-20 09:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("idempotency_key", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.create_index(
        "ix_agent_runs_lease_expires_at",
        "agent_runs",
        ["lease_expires_at"],
    )
    op.create_unique_constraint(
        "uq_agent_run_user_idempotency",
        "agent_runs",
        ["user_id", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_agent_run_user_idempotency",
        "agent_runs",
        type_="unique",
    )
    op.drop_index("ix_agent_runs_lease_expires_at", table_name="agent_runs")
    op.drop_column("agent_runs", "attempt_count")
    op.drop_column("agent_runs", "lease_expires_at")
    op.drop_column("agent_runs", "processing_started_at")
    op.drop_column("agent_runs", "queued_at")
    op.drop_column("agent_runs", "idempotency_key")
