"""Set the default Agent understanding version to v6.

Revision ID: 0025
Revises: 0024
"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.alter_column(
        "agent_runs",
        "understanding_version",
        existing_type=sa.String(length=20),
        server_default="v6",
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "agent_runs",
        "understanding_version",
        existing_type=sa.String(length=20),
        server_default="v5",
        existing_nullable=True,
    )
