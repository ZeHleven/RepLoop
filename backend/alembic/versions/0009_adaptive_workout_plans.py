"""add workout feedback and adaptive targets

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-18 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "planned_exercises",
        sa.Column("recommended_weight_kg", sa.Float(), nullable=True),
    )
    op.add_column(
        "session_exercises",
        sa.Column("target_weight_kg", sa.Float(), nullable=True),
    )
    op.add_column(
        "workout_sessions",
        sa.Column(
            "feedback_data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "workout_sessions",
        sa.Column(
            "adjustments_data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("workout_sessions", "adjustments_data")
    op.drop_column("workout_sessions", "feedback_data")
    op.drop_column("session_exercises", "target_weight_kg")
    op.drop_column("planned_exercises", "recommended_weight_kg")
