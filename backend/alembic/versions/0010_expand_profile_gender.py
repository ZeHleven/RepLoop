"""expand profile gender for onboarding values

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-18 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "user_profiles",
        "gender",
        existing_type=sa.String(length=10),
        type_=sa.String(length=32),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "user_profiles",
        "gender",
        existing_type=sa.String(length=32),
        type_=sa.String(length=10),
        existing_nullable=True,
    )
