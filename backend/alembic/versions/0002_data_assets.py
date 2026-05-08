"""data assets: exercises, foods, knowledge_chunks

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-08
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "exercises",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name_zh", sa.String(100), nullable=False),
        sa.Column("name_en", sa.String(100), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("muscle_primary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("muscle_secondary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("equipment", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("difficulty", sa.String(10), nullable=False),
        sa.Column("movement_pattern", sa.String(20), nullable=True),
        sa.Column("rep_range_min", sa.Integer(), nullable=True),
        sa.Column("rep_range_max", sa.Integer(), nullable=True),
        sa.Column("sets_range_min", sa.Integer(), nullable=True),
        sa.Column("sets_range_max", sa.Integer(), nullable=True),
        sa.Column("technique_cues", sa.Text(), nullable=True),
        sa.Column("common_mistakes", sa.Text(), nullable=True),
        sa.Column("contraindications", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("video_url", sa.String(500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_exercises_category", "exercises", ["category"])
    op.create_index("ix_exercises_difficulty", "exercises", ["difficulty"])

    op.create_table(
        "foods",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name_zh", sa.String(100), nullable=False),
        sa.Column("name_en", sa.String(100), nullable=True),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("calories_per_100g", sa.Float(), nullable=False),
        sa.Column("protein_g", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("carbs_g", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("fat_g", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("fiber_g", sa.Float(), nullable=True),
        sa.Column("common_portion_g", sa.Float(), nullable=True),
        sa.Column("diet_tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_common_in_china", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_foods_category", "foods", ["category"])

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("source", sa.String(200), nullable=False),
        sa.Column("topic", sa.String(100), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(2048), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_chunks_topic", "knowledge_chunks", ["topic"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_chunks_topic", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
    op.drop_index("ix_foods_category", table_name="foods")
    op.drop_table("foods")
    op.drop_index("ix_exercises_difficulty", table_name="exercises")
    op.drop_index("ix_exercises_category", table_name="exercises")
    op.drop_table("exercises")
    op.execute("DROP EXTENSION IF EXISTS vector")
