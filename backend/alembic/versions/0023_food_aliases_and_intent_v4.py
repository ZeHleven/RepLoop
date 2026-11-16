"""Add auditable food aliases and the standard mixed-grain rice entry.

Revision ID: 0023
Revises: 0022
"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


_MIXED_GRAIN_ID = "fitness-food-mixed-grain-rice-v1"


def upgrade() -> None:
    op.alter_column(
        "agent_runs",
        "understanding_version",
        existing_type=sa.String(length=20),
        server_default="v4",
        existing_nullable=True,
    )
    op.add_column(
        "foods",
        sa.Column("source_name", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "foods",
        sa.Column("source_reference", sa.String(length=255), nullable=True),
    )
    op.create_table(
        "food_aliases",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("food_id", sa.String(), nullable=False),
        sa.Column("alias", sa.String(length=100), nullable=False),
        sa.Column("normalized_alias", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(
            ["food_id"], ["foods.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "normalized_alias", name="uq_food_aliases_normalized_alias"
        ),
    )
    op.create_index(
        "ix_food_aliases_food_id", "food_aliases", ["food_id"], unique=False
    )

    op.execute(sa.text("""
        INSERT INTO foods (
            id, name_zh, name_en, category,
            calories_per_100g, protein_g, carbs_g, fat_g,
            fiber_g, common_portion_g, diet_tags,
            is_common_in_china, is_active, source_name, source_reference
        )
        SELECT
            :food_id, '杂粮饭', 'Mixed Grain Rice', '碳水',
            130, 3, 28, 1,
            NULL, 100, '["whole-grain"]'::jsonb,
            TRUE, TRUE, 'Fitness Agent product catalog', 'product_catalog_v1'
        WHERE NOT EXISTS (
            SELECT 1 FROM foods WHERE lower(name_zh) = lower('杂粮饭')
        )
        ON CONFLICT (id) DO NOTHING
    """).bindparams(food_id=_MIXED_GRAIN_ID))
    op.execute(sa.text("""
        UPDATE foods
        SET source_name = COALESCE(source_name, 'Fitness Agent product catalog'),
            source_reference = COALESCE(source_reference, 'product_catalog_v1')
        WHERE lower(name_zh) = lower('杂粮饭')
    """))
    op.execute(sa.text("""
        INSERT INTO food_aliases (id, food_id, alias, normalized_alias)
        SELECT values_table.id, foods.id, values_table.alias, values_table.normalized
        FROM (
            VALUES
                ('fitness-food-alias-five-grain-rice', '五谷饭', '五谷饭'),
                ('fitness-food-alias-five-mixed-grain-rice', '五谷杂粮饭', '五谷杂粮饭'),
                ('fitness-food-alias-mixed-grain-cooked-rice', '杂粮米饭', '杂粮米饭')
        ) AS values_table(id, alias, normalized)
        CROSS JOIN LATERAL (
            SELECT id FROM foods
            WHERE lower(name_zh) = lower('杂粮饭')
            ORDER BY id
            LIMIT 1
        ) AS foods
        ON CONFLICT (normalized_alias) DO NOTHING
    """))


def downgrade() -> None:
    # Keep the canonical food row so historical meal items retain a stable
    # reference.  Only the v4 alias capability and provenance columns roll back.
    op.drop_index("ix_food_aliases_food_id", table_name="food_aliases")
    op.drop_table("food_aliases")
    op.drop_column("foods", "source_reference")
    op.drop_column("foods", "source_name")
    op.alter_column(
        "agent_runs",
        "understanding_version",
        existing_type=sa.String(length=20),
        server_default="v3",
        existing_nullable=True,
    )
