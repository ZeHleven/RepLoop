from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.models.agent import AgentConversation, AgentProposal, AgentRun
from app.models.user import User
from app.models.workout import WorkoutPlan
from app.schemas.agent_plan_adjustment_proposal import (
    PlanAdjustmentProposalPayload,
)
from app.schemas.agent_plan_adjustment_proposal_api import (
    PlanAdjustmentProposalDecisionRequest,
    PlanAdjustmentProposalDecisionResponse,
    PlanAdjustmentProposalReadResponse,
)
from app.services.agent_plan_adjustment_proposal_decisions import (
    decide_plan_adjustment_proposal,
    proposal_business_error_http_status,
    query_owned_plan_adjustment_proposal,
    read_owned_plan_adjustment_proposal,
)
from app.services.agent_plan_adjustment_proposals import (
    plan_adjustment_proposal_payload_fingerprint,
)


_CONTRACT_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "agent_plan_adjustment_proposal_contract_cases.json"
)
_API_CASES_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "agent_plan_adjustment_proposal_api_cases.json"
)
_NOW = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)
_RESULT_FINGERPRINT = "f" * 64


def _payload() -> dict:
    fixture = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
    return copy.deepcopy(fixture["canonical_payloads"]["adherence"])


async def _add_context(
    db_session,
    *,
    suffix: str,
    user: User | None = None,
) -> tuple[User, AgentConversation, AgentRun]:
    if user is None:
        user = User(
            id=f"decision-user-{suffix}",
            email=f"decision-{suffix}@example.com",
            password_hash="not-used-by-this-test",
        )
        db_session.add(user)
        await db_session.flush()
    conversation = AgentConversation(
        id=f"decision-conversation-{suffix}",
        user_id=user.id,
    )
    db_session.add(conversation)
    await db_session.flush()
    run = AgentRun(
        id=f"decision-run-{suffix}",
        conversation_id=conversation.id,
        user_id=user.id,
        status="completed",
    )
    db_session.add(run)
    await db_session.flush()
    return user, conversation, run


def _proposal(
    *,
    user: User,
    conversation: AgentConversation,
    run: AgentRun,
    suffix: str,
    status: str = "pending_confirmation",
    version: int = 1,
    expires_at: datetime | None = None,
    decision_action: str | None = None,
    decision_client_request_id: str | None = None,
    confirmed_at: datetime | None = None,
    rejected_at: datetime | None = None,
    applied_at: datetime | None = None,
    result_plan_id: str | None = None,
    result_plan_fingerprint: str | None = None,
) -> AgentProposal:
    payload_data = _payload()
    payload = PlanAdjustmentProposalPayload.model_validate(payload_data)
    return AgentProposal(
        id=f"decision-proposal-{suffix}",
        user_id=user.id,
        conversation_id=conversation.id,
        run_id=run.id,
        proposal_type="plan_adjustment_v1",
        payload_data=payload_data,
        payload_fingerprint=(
            plan_adjustment_proposal_payload_fingerprint(payload)
        ),
        base_plan_id=payload.target.base_plan_id,
        base_plan_fingerprint=payload.target.base_plan_fingerprint,
        status=status,
        version=version,
        expires_at=expires_at or _NOW + timedelta(hours=24),
        decision_action=decision_action,
        decision_client_request_id=decision_client_request_id,
        confirmed_at=confirmed_at,
        rejected_at=rejected_at,
        applied_at=applied_at,
        result_plan_id=result_plan_id,
        result_plan_fingerprint=result_plan_fingerprint,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _request(
    *,
    expected_version: int = 1,
    client_request_id: str = "decision-request-0001",
) -> PlanAdjustmentProposalDecisionRequest:
    return PlanAdjustmentProposalDecisionRequest(
        expected_version=expected_version,
        client_request_id=client_request_id,
    )


def test_api_schemas_match_the_fixed_transport_contract():
    fixture = json.loads(_API_CASES_PATH.read_text(encoding="utf-8"))
    contract = fixture["endpoint_contract"]

    assert list(PlanAdjustmentProposalDecisionRequest.model_fields) == (
        contract["decision_request"]["keys"]
    )
    assert list(PlanAdjustmentProposalReadResponse.model_fields) == (
        contract["read"]["response_keys"]
    )
    assert list(PlanAdjustmentProposalDecisionResponse.model_fields) == (
        contract["decision_response"]["keys"]
    )
    assert list(PlanAdjustmentProposalPayload.model_fields) == (
        contract["read"]["payload_keys"]
    )


def test_decision_request_schema_rejects_every_fixed_invalid_body():
    fixture = json.loads(_API_CASES_PATH.read_text(encoding="utf-8"))

    for case in fixture["request_validation_cases"]:
        with pytest.raises(ValidationError):
            PlanAdjustmentProposalDecisionRequest.model_validate(case["body"])


@pytest.mark.asyncio
async def test_owned_query_hides_foreign_and_missing_proposals(db_session):
    owner, conversation, run = await _add_context(
        db_session,
        suffix="ownership-owner",
    )
    foreign, _, _ = await _add_context(
        db_session,
        suffix="ownership-foreign",
    )
    proposal = _proposal(
        user=owner,
        conversation=conversation,
        run=run,
        suffix="ownership",
    )
    db_session.add(proposal)
    await db_session.flush()

    owned = await query_owned_plan_adjustment_proposal(
        db_session,
        user_id=owner.id,
        proposal_id=proposal.id,
    )
    hidden = await query_owned_plan_adjustment_proposal(
        db_session,
        user_id=foreign.id,
        proposal_id=proposal.id,
    )
    missing = await query_owned_plan_adjustment_proposal(
        db_session,
        user_id=owner.id,
        proposal_id="missing-proposal",
    )

    assert owned is proposal
    assert hidden is None
    assert missing is None


@pytest.mark.asyncio
async def test_read_projects_expiry_without_mutating_persistent_state(
    db_session,
):
    user, conversation, run = await _add_context(
        db_session,
        suffix="read-expired",
    )
    proposal = _proposal(
        user=user,
        conversation=conversation,
        run=run,
        suffix="read-expired",
        expires_at=_NOW + timedelta(hours=1),
    )
    db_session.add(proposal)
    await db_session.flush()

    response = await read_owned_plan_adjustment_proposal(
        db_session,
        user_id=user.id,
        proposal_id=proposal.id,
        now=_NOW + timedelta(hours=1),
    )

    assert response is not None
    assert response.status == "expired"
    assert response.allowed_actions == ()
    assert response.payload.before != response.payload.after
    assert proposal.status == "pending_confirmation"
    assert proposal.version == 1
    assert not db_session.dirty


@pytest.mark.asyncio
async def test_read_applied_proposal_returns_result_and_no_actions(db_session):
    user, conversation, run = await _add_context(
        db_session,
        suffix="read-applied",
    )
    proposal = _proposal(
        user=user,
        conversation=conversation,
        run=run,
        suffix="read-applied",
        status="applied",
        version=2,
        decision_action="confirm",
        decision_client_request_id="read-applied-request",
        confirmed_at=_NOW + timedelta(minutes=1),
        applied_at=_NOW + timedelta(minutes=1),
        result_plan_id="result-plan-read-applied",
        result_plan_fingerprint=_RESULT_FINGERPRINT,
    )
    db_session.add(proposal)
    await db_session.flush()

    response = await read_owned_plan_adjustment_proposal(
        db_session,
        user_id=user.id,
        proposal_id=proposal.id,
        now=_NOW + timedelta(minutes=2),
    )

    assert response is not None
    assert response.status == "applied"
    assert response.allowed_actions == ()
    assert response.result is not None
    assert response.result.plan_id == "result-plan-read-applied"


@pytest.mark.asyncio
async def test_confirm_prepares_locked_validated_payload_without_marking_applied(
    db_session,
):
    user, conversation, run = await _add_context(
        db_session,
        suffix="confirm-ready",
    )
    proposal = _proposal(
        user=user,
        conversation=conversation,
        run=run,
        suffix="confirm-ready",
    )
    db_session.add(proposal)
    await db_session.flush()

    result = await decide_plan_adjustment_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        proposal_id=proposal.id,
        action="confirm",
        request=_request(),
        now=_NOW + timedelta(minutes=1),
    )

    assert result.outcome == "ready_to_apply"
    assert result.confirmation is not None
    assert result.confirmation.proposal is proposal
    assert result.confirmation.payload.after != (
        result.confirmation.payload.before
    )
    assert result.confirmation.client_request_id == "decision-request-0001"
    assert result.state_changed is False
    assert proposal.status == "pending_confirmation"
    assert proposal.version == 1
    assert proposal.confirmed_at is None
    assert not db_session.dirty


@pytest.mark.asyncio
async def test_reject_flushes_terminal_state_without_business_write_or_commit(
    db_session,
    session_factory,
):
    user, conversation, run = await _add_context(
        db_session,
        suffix="reject",
    )
    proposal = _proposal(
        user=user,
        conversation=conversation,
        run=run,
        suffix="reject",
    )
    db_session.add(proposal)
    await db_session.commit()

    result = await decide_plan_adjustment_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        proposal_id=proposal.id,
        action="reject",
        request=_request(client_request_id="decision-reject-0001"),
        now=_NOW + timedelta(minutes=1),
    )

    assert result.outcome == "completed"
    assert result.response is not None
    assert result.response.status == "rejected"
    assert result.response.version == 2
    assert result.response.applied is False
    assert result.state_changed is True
    assert proposal.status == "rejected"
    assert proposal.decision_action == "reject"
    assert proposal.decision_client_request_id == "decision-reject-0001"
    plan_count = await db_session.scalar(
        select(func.count(WorkoutPlan.id)).where(
            WorkoutPlan.user_id == user.id
        )
    )
    assert plan_count == 0

    async with session_factory() as other_session:
        outside_status = await other_session.scalar(
            select(AgentProposal.status).where(AgentProposal.id == proposal.id)
        )
    assert outside_status == "pending_confirmation"


@pytest.mark.asyncio
async def test_same_reject_request_replays_exact_response(db_session):
    user, conversation, run = await _add_context(
        db_session,
        suffix="reject-replay",
    )
    proposal = _proposal(
        user=user,
        conversation=conversation,
        run=run,
        suffix="reject-replay",
    )
    db_session.add(proposal)
    await db_session.flush()
    request = _request(client_request_id="decision-reject-replay")

    first = await decide_plan_adjustment_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        proposal_id=proposal.id,
        action="reject",
        request=request,
        now=_NOW + timedelta(minutes=1),
    )
    replay = await decide_plan_adjustment_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        proposal_id=proposal.id,
        action="reject",
        request=request,
        now=_NOW + timedelta(minutes=2),
    )

    assert first.response is not None
    assert replay.outcome == "replayed"
    assert replay.response == first.response
    assert replay.state_changed is False
    assert proposal.version == 2


@pytest.mark.asyncio
async def test_different_confirm_after_applied_returns_original_result(
    db_session,
):
    user, conversation, run = await _add_context(
        db_session,
        suffix="confirm-replay",
    )
    applied_at = _NOW + timedelta(minutes=1)
    proposal = _proposal(
        user=user,
        conversation=conversation,
        run=run,
        suffix="confirm-replay",
        status="applied",
        version=2,
        decision_action="confirm",
        decision_client_request_id="original-confirm-request",
        confirmed_at=applied_at,
        applied_at=applied_at,
        result_plan_id="result-plan-confirm-replay",
        result_plan_fingerprint=_RESULT_FINGERPRINT,
    )
    db_session.add(proposal)
    await db_session.flush()

    result = await decide_plan_adjustment_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        proposal_id=proposal.id,
        action="confirm",
        request=_request(client_request_id="different-confirm-request"),
        now=_NOW + timedelta(minutes=2),
    )

    assert result.outcome == "replayed"
    assert result.response is not None
    assert result.response.applied is True
    assert result.response.result_plan_id == "result-plan-confirm-replay"
    assert result.response.decided_at == applied_at
    assert proposal.decision_client_request_id == "original-confirm-request"
    assert proposal.version == 2


@pytest.mark.asyncio
async def test_version_conflict_and_disabled_flag_are_write_free(db_session):
    user, conversation, run = await _add_context(
        db_session,
        suffix="write-free-errors",
    )
    proposal = _proposal(
        user=user,
        conversation=conversation,
        run=run,
        suffix="write-free-errors",
        version=2,
    )
    db_session.add(proposal)
    await db_session.flush()

    conflict = await decide_plan_adjustment_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        proposal_id=proposal.id,
        action="reject",
        request=_request(expected_version=1),
        now=_NOW + timedelta(minutes=1),
    )
    disabled = await decide_plan_adjustment_proposal(
        db_session,
        enabled=False,
        user_id=user.id,
        proposal_id=proposal.id,
        action="reject",
        request=_request(expected_version=2),
        now=_NOW + timedelta(minutes=1),
    )

    assert conflict.error is not None
    assert conflict.error.code == "proposal_version_conflict"
    assert proposal_business_error_http_status(conflict.error.code) == 409
    assert disabled.error is not None
    assert disabled.error.code == "proposal_feature_disabled"
    assert proposal_business_error_http_status(disabled.error.code) == 503
    assert conflict.state_changed is False
    assert disabled.state_changed is False
    assert proposal.status == "pending_confirmation"
    assert proposal.version == 2
    assert not db_session.dirty


@pytest.mark.asyncio
async def test_decision_at_server_expiry_flushes_expired_without_plan_write(
    db_session,
):
    user, conversation, run = await _add_context(
        db_session,
        suffix="decision-expired",
    )
    expires_at = _NOW + timedelta(hours=1)
    proposal = _proposal(
        user=user,
        conversation=conversation,
        run=run,
        suffix="decision-expired",
        expires_at=expires_at,
    )
    db_session.add(proposal)
    await db_session.flush()

    result = await decide_plan_adjustment_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        proposal_id=proposal.id,
        action="confirm",
        request=_request(),
        now=expires_at,
    )

    assert result.error is not None
    assert result.error.code == "proposal_expired"
    assert result.state_changed is True
    assert proposal.status == "expired"
    assert proposal.version == 2
    assert proposal.last_error_code == "proposal_expired"
    assert proposal.confirmed_at is None
    plan_count = await db_session.scalar(
        select(func.count(WorkoutPlan.id)).where(
            WorkoutPlan.user_id == user.id
        )
    )
    assert plan_count == 0


@pytest.mark.asyncio
async def test_foreign_decision_is_same_not_found_result_as_missing(db_session):
    owner, conversation, run = await _add_context(
        db_session,
        suffix="decision-owner",
    )
    foreign, _, _ = await _add_context(
        db_session,
        suffix="decision-foreign",
    )
    proposal = _proposal(
        user=owner,
        conversation=conversation,
        run=run,
        suffix="decision-owner",
    )
    db_session.add(proposal)
    await db_session.flush()

    foreign_result = await decide_plan_adjustment_proposal(
        db_session,
        enabled=True,
        user_id=foreign.id,
        proposal_id=proposal.id,
        action="confirm",
        request=_request(),
        now=_NOW + timedelta(minutes=1),
    )
    missing_result = await decide_plan_adjustment_proposal(
        db_session,
        enabled=True,
        user_id=owner.id,
        proposal_id="missing-proposal",
        action="confirm",
        request=_request(),
        now=_NOW + timedelta(minutes=1),
    )

    assert foreign_result.error == missing_result.error
    assert foreign_result.error is not None
    assert foreign_result.error.code == "proposal_not_found"
    assert proposal.status == "pending_confirmation"


@pytest.mark.asyncio
async def test_reused_request_id_on_another_proposal_is_conflict(db_session):
    user, first_conversation, first_run = await _add_context(
        db_session,
        suffix="request-conflict-first",
    )
    _, second_conversation, second_run = await _add_context(
        db_session,
        suffix="request-conflict-second",
        user=user,
    )
    first = _proposal(
        user=user,
        conversation=first_conversation,
        run=first_run,
        suffix="request-conflict-first",
        status="rejected",
        version=2,
        decision_action="reject",
        decision_client_request_id="shared-decision-request",
        rejected_at=_NOW + timedelta(minutes=1),
    )
    second = _proposal(
        user=user,
        conversation=second_conversation,
        run=second_run,
        suffix="request-conflict-second",
    )
    db_session.add_all([first, second])
    await db_session.flush()

    result = await decide_plan_adjustment_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        proposal_id=second.id,
        action="reject",
        request=_request(client_request_id="shared-decision-request"),
        now=_NOW + timedelta(minutes=2),
    )

    assert result.error is not None
    assert result.error.code == "proposal_idempotency_conflict"
    assert proposal_business_error_http_status(result.error.code) == 409
    assert result.state_changed is False
    assert second.status == "pending_confirmation"


@pytest.mark.asyncio
async def test_invalid_stored_fingerprint_marks_confirm_proposal_stale(
    db_session,
):
    user, conversation, run = await _add_context(
        db_session,
        suffix="invalid-payload",
    )
    proposal = _proposal(
        user=user,
        conversation=conversation,
        run=run,
        suffix="invalid-payload",
    )
    proposal.payload_fingerprint = "b" * 64
    db_session.add(proposal)
    await db_session.flush()

    result = await decide_plan_adjustment_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        proposal_id=proposal.id,
        action="confirm",
        request=_request(),
        now=_NOW + timedelta(minutes=1),
    )

    assert result.error is not None
    assert result.error.code == "proposal_payload_invalid"
    assert result.state_changed is True
    assert proposal.status == "stale"
    assert proposal.version == 2
    assert proposal.last_error_code == "proposal_payload_invalid"
    assert proposal.confirmed_at is None
