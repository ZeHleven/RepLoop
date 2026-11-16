"""workout execution lifecycle and plan snapshots

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-17
"""

from alembic import op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workout_sessions",
        sa.Column("day_of_week", sa.Integer(), nullable=True),
    )
    op.add_column(
        "workout_sessions",
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="completed",
        ),
    )
    op.add_column(
        "workout_sessions",
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column(
        "workout_sessions",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE workout_sessions "
        "SET started_at = created_at, completed_at = created_at "
        "WHERE completed_at IS NULL"
    )

    op.add_column(
        "session_exercises",
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "session_exercises",
        sa.Column("target_sets", sa.Integer(), nullable=True),
    )
    op.add_column(
        "session_exercises",
        sa.Column("target_reps", sa.String(20), nullable=True),
    )
    op.add_column(
        "session_exercises",
        sa.Column("rest_seconds", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("session_exercises", "rest_seconds")
    op.drop_column("session_exercises", "target_reps")
    op.drop_column("session_exercises", "target_sets")
    op.drop_column("session_exercises", "order_index")
    op.drop_column("workout_sessions", "completed_at")
    op.drop_column("workout_sessions", "started_at")
    op.drop_column("workout_sessions", "status")
    op.drop_column("workout_sessions", "day_of_week")
