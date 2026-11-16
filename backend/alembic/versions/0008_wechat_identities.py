"""add WeChat identities

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "wechat_identities",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("app_id", sa.String(32), nullable=False),
        sa.Column("open_id", sa.String(64), nullable=False),
        sa.Column("union_id", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_login_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "app_id", "open_id", name="uq_wechat_identity_app_openid"
        ),
    )
    op.create_index(
        op.f("ix_wechat_identities_user_id"),
        "wechat_identities",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_wechat_identities_union_id"),
        "wechat_identities",
        ["union_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_wechat_identities_union_id"), table_name="wechat_identities"
    )
    op.drop_index(
        op.f("ix_wechat_identities_user_id"), table_name="wechat_identities"
    )
    op.drop_table("wechat_identities")
