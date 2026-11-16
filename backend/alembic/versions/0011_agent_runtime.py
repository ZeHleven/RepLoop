"""add agent runtime persistence

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-19 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_conversations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_conversations_user_id", "agent_conversations", ["user_id"])

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="running", nullable=False),
        sa.Column("primary_intent", sa.String(length=60), nullable=True),
        sa.Column("expanded_intents", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("subtasks", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("missing_slots", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("tool_allowlist", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("risk_level", sa.String(length=20), server_default="low", nullable=False),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["agent_conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_runs_conversation_id", "agent_runs", ["conversation_id"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])
    op.create_index("ix_agent_runs_user_id", "agent_runs", ["user_id"])

    op.create_table(
        "agent_messages",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_data", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["agent_conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_messages_conversation_id", "agent_messages", ["conversation_id"])
    op.create_index("ix_agent_messages_run_id", "agent_messages", ["run_id"])

    op.create_table(
        "agent_tool_calls",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("call_id", sa.String(length=120), nullable=True),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("arguments_data", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("result_data", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="completed", nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_tool_calls_run_id", "agent_tool_calls", ["run_id"])
    op.create_index("ix_agent_tool_calls_tool_name", "agent_tool_calls", ["tool_name"])

    op.create_table(
        "agent_proposals",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=True),
        sa.Column("proposal_type", sa.String(length=60), nullable=False),
        sa.Column("payload_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["agent_conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_proposals_conversation_id", "agent_proposals", ["conversation_id"])
    op.create_index("ix_agent_proposals_proposal_type", "agent_proposals", ["proposal_type"])
    op.create_index("ix_agent_proposals_status", "agent_proposals", ["status"])
    op.create_index("ix_agent_proposals_user_id", "agent_proposals", ["user_id"])

    op.create_table(
        "agent_memories",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("memory_key", sa.String(length=120), nullable=False),
        sa.Column("value_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_message_id", sa.String(), nullable=True),
        sa.Column("confidence", sa.Float(), server_default="1", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["source_message_id"], ["agent_messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "memory_key", name="uq_agent_memory_user_key"),
    )
    op.create_index("ix_agent_memories_status", "agent_memories", ["status"])
    op.create_index("ix_agent_memories_user_id", "agent_memories", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_memories_user_id", table_name="agent_memories")
    op.drop_index("ix_agent_memories_status", table_name="agent_memories")
    op.drop_table("agent_memories")
    op.drop_index("ix_agent_proposals_user_id", table_name="agent_proposals")
    op.drop_index("ix_agent_proposals_status", table_name="agent_proposals")
    op.drop_index("ix_agent_proposals_proposal_type", table_name="agent_proposals")
    op.drop_index("ix_agent_proposals_conversation_id", table_name="agent_proposals")
    op.drop_table("agent_proposals")
    op.drop_index("ix_agent_tool_calls_tool_name", table_name="agent_tool_calls")
    op.drop_index("ix_agent_tool_calls_run_id", table_name="agent_tool_calls")
    op.drop_table("agent_tool_calls")
    op.drop_index("ix_agent_messages_run_id", table_name="agent_messages")
    op.drop_index("ix_agent_messages_conversation_id", table_name="agent_messages")
    op.drop_table("agent_messages")
    op.drop_index("ix_agent_runs_user_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_status", table_name="agent_runs")
    op.drop_index("ix_agent_runs_conversation_id", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index("ix_agent_conversations_user_id", table_name="agent_conversations")
    op.drop_table("agent_conversations")
