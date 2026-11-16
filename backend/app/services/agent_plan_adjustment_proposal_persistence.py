"""Optional, transaction-neutral persistence for plan-adjustment proposals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import AgentProposal, AgentRun
from app.schemas.agent_plan_adjustment_proposal import (
    ValidatedPlanAdjustmentProposal,
)
from app.services.agent_plan_adjustment_proposals import (
    canonical_plan_adjustment_proposal_payload_data,
)


PlanAdjustmentProposalPersistenceReason = Literal[
    "feature_disabled",
    "run_ownership_lost",
    "proposal_idempotency_conflict",
]


@dataclass(frozen=True)
class OptionalPlanAdjustmentProposalPersistenceResult:
    proposal: AgentProposal | None
    created: bool
    reason_code: PlanAdjustmentProposalPersistenceReason | None = None


class PlanAdjustmentProposalPersistenceRejected(RuntimeError):
    def __init__(
        self,
        reason_code: PlanAdjustmentProposalPersistenceReason,
    ) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _matches_idempotent_replay(
    existing: AgentProposal,
    *,
    built: ValidatedPlanAdjustmentProposal,
    payload_data: dict,
) -> bool:
    if existing.created_at is None or existing.expires_at is None:
        return False
    return (
        existing.proposal_type == built.payload.proposal_type
        and existing.payload_data == payload_data
        and existing.payload_fingerprint == built.payload_fingerprint
        and existing.base_plan_id == built.payload.target.base_plan_id
        and existing.base_plan_fingerprint
        == built.payload.target.base_plan_fingerprint
        and existing.expires_at - existing.created_at
        == timedelta(hours=built.ttl_hours)
    )


async def persist_optional_plan_adjustment_proposal(
    db: AsyncSession,
    *,
    enabled: bool,
    user_id: str,
    conversation_id: str,
    run_id: str,
    expected_attempt_count: int,
    built: ValidatedPlanAdjustmentProposal | None,
) -> OptionalPlanAdjustmentProposalPersistenceResult:
    """Flush one proposal without committing the caller-owned transaction."""

    if not enabled:
        return OptionalPlanAdjustmentProposalPersistenceResult(
            proposal=None,
            created=False,
            reason_code="feature_disabled",
        )
    if built is None:
        raise ValueError("enabled proposal persistence requires a build result")

    with db.no_autoflush:
        owned_run_id = await db.scalar(
            select(AgentRun.id)
            .where(
                AgentRun.id == run_id,
                AgentRun.user_id == user_id,
                AgentRun.conversation_id == conversation_id,
                AgentRun.status == "running",
                AgentRun.attempt_count == expected_attempt_count,
            )
            .with_for_update()
        )
    if owned_run_id is None:
        raise PlanAdjustmentProposalPersistenceRejected(
            "run_ownership_lost"
        )

    payload_data = canonical_plan_adjustment_proposal_payload_data(
        built.payload
    )
    existing = await db.scalar(
        select(AgentProposal).where(
            AgentProposal.run_id == run_id,
            AgentProposal.proposal_type == built.payload.proposal_type,
        )
    )
    if existing is not None:
        if not _matches_idempotent_replay(
            existing,
            built=built,
            payload_data=payload_data,
        ):
            raise PlanAdjustmentProposalPersistenceRejected(
                "proposal_idempotency_conflict"
            )
        return OptionalPlanAdjustmentProposalPersistenceResult(
            proposal=existing,
            created=False,
        )

    proposal = AgentProposal(
        user_id=user_id,
        conversation_id=conversation_id,
        run_id=run_id,
        proposal_type=built.payload.proposal_type,
        origin="agent_chat",
        target_kind="workout_plan",
        target_id=built.payload.target.base_plan_id,
        payload_data=payload_data,
        payload_fingerprint=built.payload_fingerprint,
        base_plan_id=built.payload.target.base_plan_id,
        base_plan_fingerprint=(
            built.payload.target.base_plan_fingerprint
        ),
        status=built.initial_status,
        version=1,
        expires_at=built.expires_at,
        created_at=built.created_at,
    )
    db.add(proposal)
    await db.flush()
    return OptionalPlanAdjustmentProposalPersistenceResult(
        proposal=proposal,
        created=True,
    )
