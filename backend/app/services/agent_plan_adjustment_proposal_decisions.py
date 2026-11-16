"""Transaction-neutral read and decision services for Agent proposals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import AgentProposal
from app.schemas.agent_plan_adjustment_proposal import (
    PlanAdjustmentProposalPayload,
)
from app.schemas.agent_plan_adjustment_proposal_api import (
    PlanAdjustmentProposalAppliedResult,
    PlanAdjustmentProposalBusinessError,
    PlanAdjustmentProposalBusinessErrorCode,
    PlanAdjustmentProposalDecisionAction,
    PlanAdjustmentProposalDecisionRequest,
    PlanAdjustmentProposalDecisionResponse,
    PlanAdjustmentProposalReadResponse,
)
from app.services.agent_plan_adjustment_proposals import (
    plan_adjustment_proposal_payload_fingerprint,
)


DecisionServiceOutcome = Literal[
    "ready_to_apply",
    "completed",
    "replayed",
    "error",
]


_ERROR_MESSAGES: dict[PlanAdjustmentProposalBusinessErrorCode, str] = {
    "proposal_not_found": "调整提案不存在或已不可访问。",
    "proposal_not_pending": "调整提案当前状态不允许此操作。",
    "proposal_version_conflict": "调整提案已更新，请刷新后重试。",
    "proposal_expired": "调整提案已过期，请重新发起评估。",
    "proposal_feature_disabled": "调整提案操作暂时不可用。",
    "proposal_idempotency_conflict": "本次操作标识已被其他提案使用。",
    "proposal_base_plan_changed": "当前训练计划已变化，请重新发起评估。",
    "proposal_health_context_changed": "健康信息已变化，请重新发起评估。",
    "proposal_payload_invalid": "调整提案已失效，请重新发起评估。",
    "proposal_candidate_unavailable": "候选训练内容已不可用，请重新评估。",
    "proposal_execution_conflict": "训练计划正在被其他操作更新，请稍后重试。",
    "proposal_execution_failed": "调整未能完成，训练计划未发生变化。",
}

_ERROR_HTTP_STATUS: dict[PlanAdjustmentProposalBusinessErrorCode, int] = {
    "proposal_not_found": 404,
    "proposal_not_pending": 409,
    "proposal_version_conflict": 409,
    "proposal_expired": 409,
    "proposal_feature_disabled": 503,
    "proposal_idempotency_conflict": 409,
    "proposal_base_plan_changed": 409,
    "proposal_health_context_changed": 409,
    "proposal_payload_invalid": 409,
    "proposal_candidate_unavailable": 409,
    "proposal_execution_conflict": 409,
    "proposal_execution_failed": 500,
}


@dataclass(frozen=True)
class PreparedPlanAdjustmentProposalConfirmation:
    proposal: AgentProposal
    payload: PlanAdjustmentProposalPayload
    expected_version: int
    client_request_id: str
    confirmed_at: datetime


@dataclass(frozen=True)
class PlanAdjustmentProposalDecisionServiceResult:
    outcome: DecisionServiceOutcome
    response: PlanAdjustmentProposalDecisionResponse | None = None
    error: PlanAdjustmentProposalBusinessError | None = None
    confirmation: PreparedPlanAdjustmentProposalConfirmation | None = None
    state_changed: bool = False

    def __post_init__(self) -> None:
        populated = sum(
            value is not None
            for value in (self.response, self.error, self.confirmation)
        )
        if populated != 1:
            raise ValueError("decision result requires exactly one payload")
        if self.outcome == "ready_to_apply":
            if self.confirmation is None or self.state_changed:
                raise ValueError("ready confirmation cannot change state")
        elif self.outcome == "error":
            if self.error is None:
                raise ValueError("error result requires business error")
        elif self.response is None:
            raise ValueError("completed decision requires response")


def proposal_business_error_http_status(
    code: PlanAdjustmentProposalBusinessErrorCode,
) -> int:
    return _ERROR_HTTP_STATUS[code]


def proposal_business_error_result(
    code: PlanAdjustmentProposalBusinessErrorCode,
    *,
    state_changed: bool = False,
) -> PlanAdjustmentProposalDecisionServiceResult:
    return PlanAdjustmentProposalDecisionServiceResult(
        outcome="error",
        error=PlanAdjustmentProposalBusinessError(
            code=code,
            message=_ERROR_MESSAGES[code],
        ),
        state_changed=state_changed,
    )


async def query_owned_plan_adjustment_proposal(
    db: AsyncSession,
    *,
    user_id: str,
    proposal_id: str,
    for_update: bool = False,
) -> AgentProposal | None:
    """Query by proposal and owner together so foreign rows look missing."""

    statement = select(AgentProposal).where(
        AgentProposal.id == proposal_id,
        AgentProposal.user_id == user_id,
        AgentProposal.proposal_type == "plan_adjustment_v1",
    )
    if for_update:
        statement = statement.with_for_update()
    with db.no_autoflush:
        return await db.scalar(statement)


def _validated_payload(
    proposal: AgentProposal,
) -> PlanAdjustmentProposalPayload:
    try:
        payload = PlanAdjustmentProposalPayload.model_validate(
            proposal.payload_data
        )
    except ValidationError as exc:
        raise ValueError("stored proposal payload is invalid") from exc
    if (
        proposal.payload_fingerprint is None
        or proposal.payload_fingerprint
        != plan_adjustment_proposal_payload_fingerprint(payload)
        or proposal.base_plan_id != payload.target.base_plan_id
        or proposal.base_plan_fingerprint
        != payload.target.base_plan_fingerprint
    ):
        raise ValueError("stored proposal payload metadata does not match")
    return payload


def build_plan_adjustment_proposal_decision_response(
    proposal: AgentProposal,
) -> PlanAdjustmentProposalDecisionResponse:
    if proposal.payload_fingerprint is None:
        raise ValueError("decided proposal has no payload fingerprint")
    if proposal.status == "applied":
        decided_at = proposal.applied_at
        applied = True
    elif proposal.status == "rejected":
        decided_at = proposal.rejected_at
        applied = False
    else:
        raise ValueError("proposal has no successful decision response")
    if decided_at is None:
        raise ValueError("decided proposal has no decision timestamp")
    return PlanAdjustmentProposalDecisionResponse(
        id=proposal.id,
        proposal_type=proposal.proposal_type,
        status=proposal.status,
        version=proposal.version,
        applied=applied,
        payload_fingerprint=proposal.payload_fingerprint,
        result_plan_id=proposal.result_plan_id,
        result_plan_fingerprint=proposal.result_plan_fingerprint,
        decided_at=decided_at,
    )


def project_plan_adjustment_proposal_read_response(
    proposal: AgentProposal,
    *,
    now: datetime,
) -> PlanAdjustmentProposalReadResponse:
    """Project expiry for GET without mutating persistent lifecycle state."""

    if now.tzinfo is None:
        raise ValueError("proposal read time must include a timezone")
    if (
        proposal.created_at is None
        or proposal.updated_at is None
        or proposal.expires_at is None
        or proposal.payload_fingerprint is None
    ):
        raise ValueError("stored proposal lifecycle metadata is incomplete")
    payload = _validated_payload(proposal)
    effective_status = proposal.status
    if (
        effective_status == "pending_confirmation"
        and now >= proposal.expires_at
    ):
        effective_status = "expired"
    result = None
    if effective_status == "applied":
        if (
            proposal.result_plan_id is None
            or proposal.result_plan_fingerprint is None
            or proposal.applied_at is None
        ):
            raise ValueError("applied proposal result is incomplete")
        result = PlanAdjustmentProposalAppliedResult(
            plan_id=proposal.result_plan_id,
            plan_fingerprint=proposal.result_plan_fingerprint,
            applied_at=proposal.applied_at,
        )
    return PlanAdjustmentProposalReadResponse(
        id=proposal.id,
        proposal_type=proposal.proposal_type,
        status=effective_status,
        version=proposal.version,
        payload_fingerprint=proposal.payload_fingerprint,
        payload=payload,
        expires_at=proposal.expires_at,
        created_at=proposal.created_at,
        updated_at=proposal.updated_at,
        allowed_actions=(
            ("confirm", "reject")
            if effective_status == "pending_confirmation"
            else ()
        ),
        result=result,
    )


async def read_owned_plan_adjustment_proposal(
    db: AsyncSession,
    *,
    user_id: str,
    proposal_id: str,
    now: datetime,
) -> PlanAdjustmentProposalReadResponse | None:
    proposal = await query_owned_plan_adjustment_proposal(
        db,
        user_id=user_id,
        proposal_id=proposal_id,
    )
    if proposal is None:
        return None
    return project_plan_adjustment_proposal_read_response(
        proposal,
        now=now,
    )


async def _request_id_belongs_to_other_proposal(
    db: AsyncSession,
    *,
    user_id: str,
    proposal_id: str,
    client_request_id: str,
) -> bool:
    with db.no_autoflush:
        existing_id = await db.scalar(
            select(AgentProposal.id).where(
                AgentProposal.user_id == user_id,
                AgentProposal.decision_client_request_id
                == client_request_id,
            )
        )
    return existing_id is not None and existing_id != proposal_id


async def decide_plan_adjustment_proposal(
    db: AsyncSession,
    *,
    enabled: bool,
    user_id: str,
    proposal_id: str,
    action: PlanAdjustmentProposalDecisionAction,
    request: PlanAdjustmentProposalDecisionRequest,
    now: datetime,
) -> PlanAdjustmentProposalDecisionServiceResult:
    """Lock and decide; caller owns commit/rollback and confirm application."""

    if now.tzinfo is None:
        raise ValueError("proposal decision time must include a timezone")
    proposal = await query_owned_plan_adjustment_proposal(
        db,
        user_id=user_id,
        proposal_id=proposal_id,
        for_update=True,
    )
    if proposal is None:
        return proposal_business_error_result("proposal_not_found")
    if not enabled:
        return proposal_business_error_result("proposal_feature_disabled")

    if await _request_id_belongs_to_other_proposal(
        db,
        user_id=user_id,
        proposal_id=proposal.id,
        client_request_id=request.client_request_id,
    ):
        return proposal_business_error_result("proposal_idempotency_conflict")

    same_request_replay = (
        proposal.decision_client_request_id == request.client_request_id
        and proposal.decision_action == action
        and (
            (proposal.status == "applied" and action == "confirm")
            or (proposal.status == "rejected" and action == "reject")
        )
    )
    if same_request_replay:
        return PlanAdjustmentProposalDecisionServiceResult(
            outcome="replayed",
            response=build_plan_adjustment_proposal_decision_response(proposal),
        )
    if proposal.status == "applied" and action == "confirm":
        return PlanAdjustmentProposalDecisionServiceResult(
            outcome="replayed",
            response=build_plan_adjustment_proposal_decision_response(proposal),
        )
    if proposal.status != "pending_confirmation":
        return proposal_business_error_result("proposal_not_pending")
    if request.expected_version != proposal.version:
        return proposal_business_error_result("proposal_version_conflict")
    if proposal.expires_at is None or now >= proposal.expires_at:
        proposal.status = "expired"
        proposal.version += 1
        proposal.last_error_code = "proposal_expired"
        await db.flush()
        return proposal_business_error_result(
            "proposal_expired",
            state_changed=True,
        )

    if action == "reject":
        proposal.status = "rejected"
        proposal.version += 1
        proposal.decision_action = "reject"
        proposal.decision_client_request_id = request.client_request_id
        proposal.rejected_at = now
        proposal.last_error_code = None
        await db.flush()
        return PlanAdjustmentProposalDecisionServiceResult(
            outcome="completed",
            response=build_plan_adjustment_proposal_decision_response(proposal),
            state_changed=True,
        )

    try:
        payload = _validated_payload(proposal)
    except ValueError:
        proposal.status = "stale"
        proposal.version += 1
        proposal.last_error_code = "proposal_payload_invalid"
        await db.flush()
        return proposal_business_error_result(
            "proposal_payload_invalid",
            state_changed=True,
        )
    return PlanAdjustmentProposalDecisionServiceResult(
        outcome="ready_to_apply",
        confirmation=PreparedPlanAdjustmentProposalConfirmation(
            proposal=proposal,
            payload=payload,
            expected_version=request.expected_version,
            client_request_id=request.client_request_id,
            confirmed_at=now,
        ),
    )
