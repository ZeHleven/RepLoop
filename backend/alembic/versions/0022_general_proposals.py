"""Generalize proposals for manual plan editing and domain writes.

Revision ID: 0022
Revises: 0021
"""

from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column(
        "agent_proposals",
        sa.Column(
            "origin",
            sa.String(length=20),
            nullable=False,
            server_default="agent_chat",
        ),
    )
    op.add_column(
        "agent_proposals",
        sa.Column("creation_client_request_id", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "agent_proposals",
        sa.Column("target_kind", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "agent_proposals",
        sa.Column("target_id", sa.String(), nullable=True),
    )
    op.add_column(
        "agent_proposals",
        sa.Column(
            "result_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.alter_column(
        "agent_proposals",
        "conversation_id",
        existing_type=sa.String(),
        nullable=True,
    )

    op.execute(sa.text("""
        UPDATE agent_proposals
        SET origin = 'agent_chat',
            target_kind = CASE
                WHEN proposal_type = 'plan_adjustment_v1' THEN 'workout_plan'
                ELSE NULL
            END,
            target_id = CASE
                WHEN proposal_type = 'plan_adjustment_v1' THEN base_plan_id
                ELSE NULL
            END
    """))

    op.create_unique_constraint(
        "uq_agent_proposals_user_creation_request",
        "agent_proposals",
        ["user_id", "creation_client_request_id"],
    )
    op.create_index(
        "ix_agent_proposals_target",
        "agent_proposals",
        ["user_id", "target_kind", "target_id", "status"],
    )
    op.create_check_constraint(
        "ck_agent_proposals_origin",
        "agent_proposals",
        "origin IN ('agent_chat', 'manual_editor') AND "
        "((origin = 'agent_chat' AND conversation_id IS NOT NULL) OR "
        "(origin = 'manual_editor' AND conversation_id IS NULL AND run_id IS NULL))",
    )

    op.drop_constraint(
        "ck_agent_proposals_pending_clean",
        "agent_proposals",
        type_="check",
    )
    op.create_check_constraint(
        "ck_agent_proposals_pending_clean",
        "agent_proposals",
        "status <> 'pending_confirmation' OR "
        "(decision_action IS NULL AND applied_at IS NULL AND "
        "result_plan_id IS NULL AND result_plan_fingerprint IS NULL AND "
        "result_data IS NULL AND last_error_code IS NULL)",
    )

    op.drop_constraint(
        "ck_agent_proposals_applied_result",
        "agent_proposals",
        type_="check",
    )
    op.create_check_constraint(
        "ck_agent_proposals_applied_result",
        "agent_proposals",
        "(status = 'applied' AND decision_action = 'confirm' AND "
        "applied_at IS NOT NULL AND last_error_code IS NULL AND ("
        "(proposal_type IN ('plan_adjustment_v1', 'plan_adjustment_v2') AND "
        "result_plan_id IS NOT NULL AND result_plan_fingerprint IS NOT NULL) OR "
        "(proposal_type NOT IN ('plan_adjustment_v1', 'plan_adjustment_v2') AND "
        "result_data IS NOT NULL))) OR "
        "(status <> 'applied' AND applied_at IS NULL AND "
        "result_plan_id IS NULL AND result_plan_fingerprint IS NULL AND "
        "result_data IS NULL)",
    )

    op.drop_constraint(
        "ck_agent_proposals_plan_adjustment_fields",
        "agent_proposals",
        type_="check",
    )
    op.create_check_constraint(
        "ck_agent_proposals_plan_adjustment_fields",
        "agent_proposals",
        "proposal_type NOT IN ('plan_adjustment_v1', 'plan_adjustment_v2', "
        "'plan_deletion_v1') OR ("
        "payload_fingerprint IS NOT NULL AND base_plan_id IS NOT NULL AND "
        "base_plan_fingerprint IS NOT NULL AND expires_at IS NOT NULL AND "
        "((proposal_type = 'plan_adjustment_v1' AND "
        "(target_kind IS NULL OR target_kind = 'workout_plan') AND "
        "(target_id IS NULL OR target_id = base_plan_id)) OR "
        "(proposal_type <> 'plan_adjustment_v1' AND "
        "target_kind IS NOT DISTINCT FROM 'workout_plan' AND "
        "target_id IS NOT DISTINCT FROM base_plan_id)) AND "
        "payload_data ->> 'proposal_type' IS NOT DISTINCT FROM proposal_type AND "
        "payload_data #>> '{target,base_plan_id}' IS NOT DISTINCT FROM base_plan_id AND "
        "payload_data #>> '{target,base_plan_fingerprint}' "
        "IS NOT DISTINCT FROM base_plan_fingerprint AND ("
        "(proposal_type = 'plan_adjustment_v1' AND "
        "payload_data ->> 'schema_version' IS NOT DISTINCT FROM '1.0.0') OR "
        "(proposal_type = 'plan_adjustment_v2' AND "
        "payload_data ->> 'schema_version' IS NOT DISTINCT FROM '2.0.0') OR "
        "(proposal_type = 'plan_deletion_v1' AND "
        "payload_data ->> 'schema_version' IS NOT DISTINCT FROM '1.0.0')))",
    )


def downgrade() -> None:
    # Older schemas can only represent the v1 Agent proposal lifecycle.
    op.execute(sa.text("""
        DELETE FROM agent_proposals
        WHERE proposal_type <> 'plan_adjustment_v1'
           OR origin = 'manual_editor'
    """))
    op.drop_constraint(
        "ck_agent_proposals_plan_adjustment_fields",
        "agent_proposals",
        type_="check",
    )
    op.create_check_constraint(
        "ck_agent_proposals_plan_adjustment_fields",
        "agent_proposals",
        "proposal_type <> 'plan_adjustment_v1' OR ("
        "payload_fingerprint IS NOT NULL AND base_plan_id IS NOT NULL AND "
        "base_plan_fingerprint IS NOT NULL AND expires_at IS NOT NULL AND "
        "payload_data ->> 'schema_version' IS NOT DISTINCT FROM '1.0.0' AND "
        "payload_data ->> 'proposal_type' IS NOT DISTINCT FROM proposal_type AND "
        "payload_data #>> '{target,base_plan_id}' IS NOT DISTINCT FROM base_plan_id AND "
        "payload_data #>> '{target,base_plan_fingerprint}' "
        "IS NOT DISTINCT FROM base_plan_fingerprint)",
    )

    op.drop_constraint("ck_agent_proposals_applied_result", "agent_proposals", type_="check")
    op.create_check_constraint(
        "ck_agent_proposals_applied_result",
        "agent_proposals",
        "(status = 'applied' AND decision_action = 'confirm' AND "
        "applied_at IS NOT NULL AND result_plan_id IS NOT NULL AND "
        "result_plan_fingerprint IS NOT NULL AND last_error_code IS NULL) OR "
        "(status <> 'applied' AND applied_at IS NULL AND "
        "result_plan_id IS NULL AND result_plan_fingerprint IS NULL)",
    )
    op.drop_constraint("ck_agent_proposals_pending_clean", "agent_proposals", type_="check")
    op.create_check_constraint(
        "ck_agent_proposals_pending_clean",
        "agent_proposals",
        "status <> 'pending_confirmation' OR "
        "(decision_action IS NULL AND applied_at IS NULL AND "
        "result_plan_id IS NULL AND result_plan_fingerprint IS NULL AND "
        "last_error_code IS NULL)",
    )

    op.drop_constraint("ck_agent_proposals_origin", "agent_proposals", type_="check")
    op.drop_index("ix_agent_proposals_target", table_name="agent_proposals")
    op.drop_constraint(
        "uq_agent_proposals_user_creation_request",
        "agent_proposals",
        type_="unique",
    )
    op.alter_column(
        "agent_proposals",
        "conversation_id",
        existing_type=sa.String(),
        nullable=False,
    )
    op.drop_column("agent_proposals", "result_data")
    op.drop_column("agent_proposals", "target_id")
    op.drop_column("agent_proposals", "target_kind")
    op.drop_column("agent_proposals", "creation_client_request_id")
    op.drop_column("agent_proposals", "origin")
