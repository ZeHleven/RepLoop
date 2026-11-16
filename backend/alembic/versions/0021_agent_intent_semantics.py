"""Persist orthogonal Agent intent semantics.

Revision ID: 0021
Revises: 0020
"""

from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.alter_column(
        "agent_runs",
        "understanding_version",
        existing_type=sa.String(length=20),
        server_default="v3",
        existing_nullable=True,
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "intent_domain",
            sa.String(length=40),
            nullable=False,
            server_default="general",
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "request_kind",
            sa.String(length=30),
            nullable=False,
            server_default="query",
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "requested_effect",
            sa.String(length=20),
            nullable=False,
            server_default="read",
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "change_requests",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    op.execute(sa.text("""
        UPDATE agent_runs
        SET intent_domain = CASE primary_intent
            WHEN 'profile_query' THEN 'profile'
            WHEN 'health_query' THEN 'health'
            WHEN 'plan_query' THEN 'workout_plan'
            WHEN 'next_workout_query' THEN 'workout_session'
            WHEN 'active_workout_query' THEN 'workout_session'
            WHEN 'workout_history_query' THEN 'workout_history'
            WHEN 'workout_progress_query' THEN 'workout_progress'
            ELSE 'general'
        END,
        request_kind = 'query',
        requested_effect = 'read',
        change_requests = '[]'::jsonb
    """))

    op.create_check_constraint(
        "ck_agent_runs_intent_domain",
        "agent_runs",
        "intent_domain IN ('general', 'profile', 'health', 'workout_plan', "
        "'workout_session', 'workout_history', 'workout_progress', 'nutrition')",
    )
    op.create_check_constraint(
        "ck_agent_runs_request_kind",
        "agent_runs",
        "request_kind IN ('query', 'assessment', 'mutation', 'proposal_decision')",
    )
    op.create_check_constraint(
        "ck_agent_runs_requested_effect",
        "agent_runs",
        "requested_effect IN ('read', 'create', 'update', 'delete', 'decide')",
    )
    op.create_check_constraint(
        "ck_agent_runs_change_requests_array",
        "agent_runs",
        "jsonb_typeof(change_requests) = 'array'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_agent_runs_change_requests_array",
        "agent_runs",
        type_="check",
    )
    op.drop_constraint(
        "ck_agent_runs_requested_effect",
        "agent_runs",
        type_="check",
    )
    op.drop_constraint(
        "ck_agent_runs_request_kind",
        "agent_runs",
        type_="check",
    )
    op.drop_constraint(
        "ck_agent_runs_intent_domain",
        "agent_runs",
        type_="check",
    )
    op.drop_column("agent_runs", "change_requests")
    op.drop_column("agent_runs", "requested_effect")
    op.drop_column("agent_runs", "request_kind")
    op.drop_column("agent_runs", "intent_domain")
    op.alter_column(
        "agent_runs",
        "understanding_version",
        existing_type=sa.String(length=20),
        server_default="v2",
        existing_nullable=True,
    )
