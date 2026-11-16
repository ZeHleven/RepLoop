"""Persist workout-completion adaptive proposal references.

Revision ID: 0026
Revises: 0025
"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_agent_proposals_origin",
        "agent_proposals",
        type_="check",
    )
    op.create_check_constraint(
        "ck_agent_proposals_origin",
        "agent_proposals",
        "origin IN ('agent_chat', 'manual_editor', 'workout_completion') AND "
        "((origin = 'agent_chat' AND conversation_id IS NOT NULL) OR "
        "(origin IN ('manual_editor', 'workout_completion') AND "
        "conversation_id IS NULL AND run_id IS NULL))",
    )
    op.add_column(
        "workout_sessions",
        sa.Column("adaptive_proposal_id", sa.String(), nullable=True),
    )
    op.create_foreign_key(
        "fk_workout_sessions_adaptive_proposal",
        "workout_sessions",
        "agent_proposals",
        ["adaptive_proposal_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_workout_sessions_adaptive_proposal_id",
        "workout_sessions",
        ["adaptive_proposal_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workout_sessions_adaptive_proposal_id",
        table_name="workout_sessions",
    )
    op.drop_constraint(
        "fk_workout_sessions_adaptive_proposal",
        "workout_sessions",
        type_="foreignkey",
    )
    op.drop_column("workout_sessions", "adaptive_proposal_id")
    op.drop_constraint(
        "ck_agent_proposals_origin",
        "agent_proposals",
        type_="check",
    )
    op.execute(
        "UPDATE agent_proposals SET origin = 'manual_editor' "
        "WHERE origin = 'workout_completion'"
    )
    op.create_check_constraint(
        "ck_agent_proposals_origin",
        "agent_proposals",
        "origin IN ('agent_chat', 'manual_editor') AND "
        "((origin = 'agent_chat' AND conversation_id IS NOT NULL) OR "
        "(origin = 'manual_editor' AND conversation_id IS NULL AND run_id IS NULL))",
    )
