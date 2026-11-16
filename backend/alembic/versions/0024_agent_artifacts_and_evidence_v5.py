"""Add v5 evidence routing and durable Agent artifacts.

Revision ID: 0024
Revises: 0023
"""

from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_agent_runs_request_kind", "agent_runs", type_="check"
    )
    op.create_check_constraint(
        "ck_agent_runs_request_kind",
        "agent_runs",
        "request_kind IN ('query', 'assessment', 'generation', "
        "'mutation', 'proposal_decision')",
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "evidence_requirements",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "requested_output",
            sa.String(length=40),
            server_default="answer",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_agent_runs_evidence_requirements_array",
        "agent_runs",
        "jsonb_typeof(evidence_requirements) = 'array'",
    )
    op.create_check_constraint(
        "ck_agent_runs_requested_output",
        "agent_runs",
        "requested_output IN ('answer', 'daily_meal_plan')",
    )
    op.alter_column(
        "agent_runs",
        "understanding_version",
        existing_type=sa.String(length=20),
        server_default="v5",
        existing_nullable=True,
    )

    op.create_table(
        "agent_artifacts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("source_run_id", sa.String(), nullable=True),
        sa.Column("artifact_type", sa.String(length=60), nullable=False),
        sa.Column("schema_version", sa.String(length=20), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default="active", nullable=False
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "payload_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("payload_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "context_fingerprints",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "artifact_type IN ('daily_meal_plan_v1')",
            name="ck_agent_artifacts_type",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'superseded', 'proposed', 'consumed', 'expired')",
            name="ck_agent_artifacts_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload_data) = 'object'",
            name="ck_agent_artifacts_payload_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(context_fingerprints) = 'object'",
            name="ck_agent_artifacts_context_fingerprints_object",
        ),
        sa.CheckConstraint(
            "payload_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_agent_artifacts_fingerprint",
        ),
        sa.CheckConstraint(
            "expires_at > created_at AND "
            "expires_at <= created_at + INTERVAL '24 hours'",
            name="ck_agent_artifacts_expiry_window",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["agent_conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id"], ["agent_runs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_artifacts_user_id", "agent_artifacts", ["user_id"]
    )
    op.create_index(
        "ix_agent_artifacts_conversation_id",
        "agent_artifacts",
        ["conversation_id"],
    )
    op.create_index(
        "ix_agent_artifacts_source_run_id", "agent_artifacts", ["source_run_id"]
    )
    op.create_index(
        "ix_agent_artifacts_artifact_type", "agent_artifacts", ["artifact_type"]
    )
    op.create_index(
        "ix_agent_artifacts_status", "agent_artifacts", ["status"]
    )
    op.create_index(
        "ix_agent_artifacts_user_conversation_status",
        "agent_artifacts",
        ["user_id", "conversation_id", "status"],
    )
    op.create_index(
        "uq_agent_artifacts_one_active_type_per_conversation",
        "agent_artifacts",
        ["conversation_id", "artifact_type"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_table("agent_artifacts")
    op.drop_constraint(
        "ck_agent_runs_requested_output", "agent_runs", type_="check"
    )
    op.drop_constraint(
        "ck_agent_runs_evidence_requirements_array", "agent_runs", type_="check"
    )
    op.drop_column("agent_runs", "requested_output")
    op.drop_column("agent_runs", "evidence_requirements")
    op.drop_constraint(
        "ck_agent_runs_request_kind", "agent_runs", type_="check"
    )
    op.execute(
        "UPDATE agent_runs "
        "SET request_kind = 'query', requested_effect = 'read' "
        "WHERE request_kind = 'generation'"
    )
    op.create_check_constraint(
        "ck_agent_runs_request_kind",
        "agent_runs",
        "request_kind IN ('query', 'assessment', 'mutation', 'proposal_decision')",
    )
    op.alter_column(
        "agent_runs",
        "understanding_version",
        existing_type=sa.String(length=20),
        server_default="v4",
        existing_nullable=True,
    )
