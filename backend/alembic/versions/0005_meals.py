"""meal logs and meal items

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-08
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meal_logs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("logged_at", sa.Date(), nullable=False),
        sa.Column("meal_type", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_meal_logs_user_id", "meal_logs", ["user_id"])
    op.create_index("ix_meal_logs_logged_at", "meal_logs", ["logged_at"])

    op.create_table(
        "meal_items",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("meal_id", sa.String(), sa.ForeignKey("meal_logs.id"), nullable=False),
        sa.Column("food_id", sa.String(), sa.ForeignKey("foods.id"), nullable=True),
        sa.Column("food_name", sa.String(100), nullable=False),
        sa.Column("amount_g", sa.Float(), nullable=False),
        sa.Column("calories", sa.Float(), nullable=False),
        sa.Column("protein_g", sa.Float(), nullable=False, server_default="0"),
        sa.Column("carbs_g", sa.Float(), nullable=False, server_default="0"),
        sa.Column("fat_g", sa.Float(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_meal_items_meal_id", "meal_items", ["meal_id"])


def downgrade() -> None:
    op.drop_index("ix_meal_items_meal_id", table_name="meal_items")
    op.drop_table("meal_items")
    op.drop_index("ix_meal_logs_logged_at", table_name="meal_logs")
    op.drop_index("ix_meal_logs_user_id", table_name="meal_logs")
    op.drop_table("meal_logs")
