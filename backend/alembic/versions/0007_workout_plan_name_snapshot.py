"""snapshot workout plan name on sessions

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-17
"""

from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workout_sessions",
        sa.Column("plan_name", sa.String(100), nullable=True),
    )
    op.execute(
        "UPDATE workout_sessions AS sessions "
        "SET plan_name = plans.name "
        "FROM workout_plans AS plans "
        "WHERE sessions.plan_id = plans.id"
    )


def downgrade() -> None:
    op.drop_column("workout_sessions", "plan_name")
