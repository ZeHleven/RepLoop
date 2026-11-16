"""Add durable plan-adjustment proposal lifecycle constraints.

Revision ID: 0020
Revises: 0019
"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column(
        "agent_proposals",
        sa.Column("payload_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "agent_proposals",
        sa.Column("base_plan_id", sa.String(), nullable=True),
    )
    op.add_column(
        "agent_proposals",
        sa.Column(
            "base_plan_fingerprint",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.add_column(
        "agent_proposals",
        sa.Column("decision_action", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "agent_proposals",
        sa.Column(
            "decision_client_request_id",
            sa.String(length=120),
            nullable=True,
        ),
    )
    op.add_column(
        "agent_proposals",
        sa.Column(
            "confirmed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "agent_proposals",
        sa.Column(
            "rejected_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "agent_proposals",
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "agent_proposals",
        sa.Column("result_plan_id", sa.String(), nullable=True),
    )
    op.add_column(
        "agent_proposals",
        sa.Column(
            "result_plan_fingerprint",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.add_column(
        "agent_proposals",
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
    )

    op.execute(sa.text("""
        UPDATE agent_proposals
        SET status = 'pending_confirmation'
        WHERE status = 'pending'
    """))
    op.alter_column(
        "agent_proposals",
        "status",
        existing_type=sa.String(length=20),
        server_default="pending_confirmation",
        existing_nullable=False,
    )

    op.create_index(
        "ix_agent_proposals_base_plan_id",
        "agent_proposals",
        ["base_plan_id"],
    )
    op.create_index(
        "ix_agent_proposals_result_plan_id",
        "agent_proposals",
        ["result_plan_id"],
    )
    op.create_index(
        "ix_agent_proposals_pending_expiry",
        "agent_proposals",
        ["expires_at"],
        postgresql_where=sa.text("status = 'pending_confirmation'"),
    )
    op.create_unique_constraint(
        "uq_agent_proposals_run_type",
        "agent_proposals",
        ["run_id", "proposal_type"],
    )
    op.create_unique_constraint(
        "uq_agent_proposals_user_decision_request",
        "agent_proposals",
        ["user_id", "decision_client_request_id"],
    )

    op.create_check_constraint(
        "ck_agent_proposals_status",
        "agent_proposals",
        "status IN ('pending_confirmation', 'applied', 'rejected', "
        "'expired', 'stale', 'failed')",
    )
    op.create_check_constraint(
        "ck_agent_proposals_version_positive",
        "agent_proposals",
        "version >= 1",
    )
    op.create_check_constraint(
        "ck_agent_proposals_payload_object",
        "agent_proposals",
        "jsonb_typeof(payload_data) = 'object'",
    )
    op.create_check_constraint(
        "ck_agent_proposals_fingerprints",
        "agent_proposals",
        "(payload_fingerprint IS NULL OR "
        "payload_fingerprint ~ '^[0-9a-f]{64}$') AND "
        "(base_plan_fingerprint IS NULL OR "
        "base_plan_fingerprint ~ '^[0-9a-f]{64}$') AND "
        "(result_plan_fingerprint IS NULL OR "
        "result_plan_fingerprint ~ '^[0-9a-f]{64}$')",
    )
    op.create_check_constraint(
        "ck_agent_proposals_expiry_window",
        "agent_proposals",
        "expires_at IS NULL OR "
        "(expires_at > created_at AND "
        "expires_at <= created_at + INTERVAL '72 hours')",
    )
    op.create_check_constraint(
        "ck_agent_proposals_decision_fields",
        "agent_proposals",
        "(decision_action IS NULL AND "
        "decision_client_request_id IS NULL AND "
        "confirmed_at IS NULL AND rejected_at IS NULL) OR "
        "(decision_action = 'confirm' AND "
        "decision_client_request_id IS NOT NULL AND "
        "confirmed_at IS NOT NULL AND rejected_at IS NULL) OR "
        "(decision_action = 'reject' AND "
        "decision_client_request_id IS NOT NULL AND "
        "confirmed_at IS NULL AND rejected_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_agent_proposals_pending_clean",
        "agent_proposals",
        "status <> 'pending_confirmation' OR "
        "(decision_action IS NULL AND applied_at IS NULL AND "
        "result_plan_id IS NULL AND result_plan_fingerprint IS NULL AND "
        "last_error_code IS NULL)",
    )
    op.create_check_constraint(
        "ck_agent_proposals_applied_result",
        "agent_proposals",
        "(status = 'applied' AND decision_action = 'confirm' AND "
        "applied_at IS NOT NULL AND result_plan_id IS NOT NULL AND "
        "result_plan_fingerprint IS NOT NULL AND "
        "last_error_code IS NULL) OR "
        "(status <> 'applied' AND applied_at IS NULL AND "
        "result_plan_id IS NULL AND result_plan_fingerprint IS NULL)",
    )
    op.create_check_constraint(
        "ck_agent_proposals_rejection_state",
        "agent_proposals",
        "(status = 'rejected' AND decision_action = 'reject') OR "
        "(status <> 'rejected' AND "
        "(decision_action IS NULL OR decision_action <> 'reject'))",
    )
    op.create_check_constraint(
        "ck_agent_proposals_plan_adjustment_fields",
        "agent_proposals",
        "proposal_type <> 'plan_adjustment_v1' OR ("
        "payload_fingerprint IS NOT NULL AND "
        "base_plan_id IS NOT NULL AND "
        "base_plan_fingerprint IS NOT NULL AND "
        "expires_at IS NOT NULL AND "
        "payload_data ->> 'schema_version' IS NOT DISTINCT FROM '1.0.0' "
        "AND payload_data ->> 'proposal_type' "
        "IS NOT DISTINCT FROM proposal_type AND "
        "payload_data #>> '{target,base_plan_id}' "
        "IS NOT DISTINCT FROM base_plan_id AND "
        "payload_data #>> '{target,base_plan_fingerprint}' "
        "IS NOT DISTINCT FROM base_plan_fingerprint)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_agent_proposals_plan_adjustment_fields",
        "agent_proposals",
        type_="check",
    )
    op.drop_constraint(
        "ck_agent_proposals_rejection_state",
        "agent_proposals",
        type_="check",
    )
    op.drop_constraint(
        "ck_agent_proposals_applied_result",
        "agent_proposals",
        type_="check",
    )
    op.drop_constraint(
        "ck_agent_proposals_pending_clean",
        "agent_proposals",
        type_="check",
    )
    op.drop_constraint(
        "ck_agent_proposals_decision_fields",
        "agent_proposals",
        type_="check",
    )
    op.drop_constraint(
        "ck_agent_proposals_expiry_window",
        "agent_proposals",
        type_="check",
    )
    op.drop_constraint(
        "ck_agent_proposals_fingerprints",
        "agent_proposals",
        type_="check",
    )
    op.drop_constraint(
        "ck_agent_proposals_payload_object",
        "agent_proposals",
        type_="check",
    )
    op.drop_constraint(
        "ck_agent_proposals_version_positive",
        "agent_proposals",
        type_="check",
    )
    op.drop_constraint(
        "ck_agent_proposals_status",
        "agent_proposals",
        type_="check",
    )
    op.drop_constraint(
        "uq_agent_proposals_user_decision_request",
        "agent_proposals",
        type_="unique",
    )
    op.drop_constraint(
        "uq_agent_proposals_run_type",
        "agent_proposals",
        type_="unique",
    )
    op.drop_index(
        "ix_agent_proposals_pending_expiry",
        table_name="agent_proposals",
    )
    op.drop_index(
        "ix_agent_proposals_result_plan_id",
        table_name="agent_proposals",
    )
    op.drop_index(
        "ix_agent_proposals_base_plan_id",
        table_name="agent_proposals",
    )

    op.alter_column(
        "agent_proposals",
        "status",
        existing_type=sa.String(length=20),
        server_default="pending",
        existing_nullable=False,
    )
    op.execute(sa.text("""
        UPDATE agent_proposals
        SET status = 'pending'
        WHERE status = 'pending_confirmation'
    """))

    op.drop_column("agent_proposals", "last_error_code")
    op.drop_column("agent_proposals", "result_plan_fingerprint")
    op.drop_column("agent_proposals", "result_plan_id")
    op.drop_column("agent_proposals", "applied_at")
    op.drop_column("agent_proposals", "rejected_at")
    op.drop_column("agent_proposals", "confirmed_at")
    op.drop_column("agent_proposals", "decision_client_request_id")
    op.drop_column("agent_proposals", "decision_action")
    op.drop_column("agent_proposals", "base_plan_fingerprint")
    op.drop_column("agent_proposals", "base_plan_id")
    op.drop_column("agent_proposals", "payload_fingerprint")
