"""Persist privacy-safe intent model error categories.

Revision ID: 0019
Revises: 0018
"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column(
            "intent_error_category",
            sa.String(length=160),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "intent_error_category")
