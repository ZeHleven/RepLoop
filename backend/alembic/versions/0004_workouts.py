"""workout plans and sessions

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workout_plans",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("goal", sa.String(50), nullable=True),
        sa.Column("duration_weeks", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("days_per_week", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("ai_generated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workout_plans_user_id", "workout_plans", ["user_id"])

    op.create_table(
        "planned_exercises",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("plan_id", sa.String(), sa.ForeignKey("workout_plans.id"), nullable=False),
        sa.Column("day_of_week", sa.Integer(), nullable=False),
        sa.Column("exercise_id", sa.String(), sa.ForeignKey("exercises.id"), nullable=False),
        sa.Column("sets", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("reps", sa.String(20), nullable=False, server_default="10"),
        sa.Column("rest_seconds", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_planned_exercises_plan_id", "planned_exercises", ["plan_id"])

    op.create_table(
        "workout_sessions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("plan_id", sa.String(), sa.ForeignKey("workout_plans.id"), nullable=True),
        sa.Column("trained_at", sa.Date(), nullable=False),
        sa.Column("duration_min", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workout_sessions_user_id", "workout_sessions", ["user_id"])
    op.create_index("ix_workout_sessions_trained_at", "workout_sessions", ["trained_at"])

    op.create_table(
        "session_exercises",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), sa.ForeignKey("workout_sessions.id"), nullable=False),
        sa.Column("exercise_id", sa.String(), sa.ForeignKey("exercises.id"), nullable=False),
        sa.Column("sets_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_session_exercises_session_id", "session_exercises", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_session_exercises_session_id", table_name="session_exercises")
    op.drop_table("session_exercises")
    op.drop_index("ix_workout_sessions_trained_at", table_name="workout_sessions")
    op.drop_index("ix_workout_sessions_user_id", table_name="workout_sessions")
    op.drop_table("workout_sessions")
    op.drop_index("ix_planned_exercises_plan_id", table_name="planned_exercises")
    op.drop_table("planned_exercises")
    op.drop_index("ix_workout_plans_user_id", table_name="workout_plans")
    op.drop_table("workout_plans")
