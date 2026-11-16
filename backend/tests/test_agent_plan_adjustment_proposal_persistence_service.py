from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.models.agent import AgentConversation, AgentProposal, AgentRun
from app.models.user import User
from app.services.agent_plan_adjustment_proposal_persistence import (
    PlanAdjustmentProposalPersistenceRejected,
    persist_optional_plan_adjustment_proposal,
)
from app.services.agent_plan_adjustment_proposals import (
    build_validated_plan_adjustment_proposal,
    evaluate_plan_adjustment_proposal_creation,
)


_CASES_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "agent_plan_adjustment_proposal_contract_cases.json"
)
_CREATED_AT = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)


def _payload() -> dict:
    fixture = json.loads(_CASES_PATH.read_text(encoding="utf-8"))
    return copy.deepcopy(fixture["canonical_payloads"]["adherence"])


def _build(payload: dict | None = None):
    payload = payload or _payload()
    decision = evaluate_plan_adjustment_proposal_creation(
        feature_enabled=True,
        run_owned=True,
        selected_outcome="adjustment_proposal",
        terminal_action="proposal",
        intent_allows_adjustment=True,
        risk_level="low",
        clarification_required=False,
        evidence_state="complete",
        draft_state="valid",
        proposal_type="plan_adjustment_v1",
        requested_ttl_hours=None,
    )
    return build_validated_plan_adjustment_proposal(
        decision=decision,
        payload_data=payload,
        expected_base_plan_id=payload["target"]["base_plan_id"],
        expected_base_plan_fingerprint=payload["target"][
            "base_plan_fingerprint"
        ],
        created_at=_CREATED_AT,
    )


async def _add_running_context(db_session, suffix: str):
    user = User(
        id=f"optional-proposal-user-{suffix}",
        email=f"optional-proposal-{suffix}@example.com",
        password_hash="not-used-by-this-test",
    )
    db_session.add(user)
    await db_session.flush()
    conversation = AgentConversation(
        id=f"optional-proposal-conversation-{suffix}",
        user_id=user.id,
    )
    db_session.add(conversation)
    await db_session.flush()
    run = AgentRun(
        id=f"optional-proposal-run-{suffix}",
        conversation_id=conversation.id,
        user_id=user.id,
        status="running",
        attempt_count=1,
    )
    db_session.add(run)
    await db_session.flush()
    return user, conversation, run


def test_plan_adjustment_proposal_persistence_flag_defaults_off():
    assert Settings.model_fields[
        "AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED"
    ].default is False


@pytest.mark.asyncio
async def test_disabled_persistence_returns_before_touching_database():
    result = await persist_optional_plan_adjustment_proposal(
        None,  # type: ignore[arg-type]
        enabled=False,
        user_id="not-read",
        conversation_id="not-read",
        run_id="not-read",
        expected_attempt_count=1,
        built=None,
    )

    assert result.proposal is None
    assert result.created is False
    assert result.reason_code == "feature_disabled"


@pytest.mark.asyncio
async def test_enabled_persistence_flushes_one_pending_proposal_without_commit(
    db_session,
    session_factory,
):
    user, conversation, run = await _add_running_context(
        db_session,
        "create",
    )
    built = _build()

    result = await persist_optional_plan_adjustment_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        conversation_id=conversation.id,
        run_id=run.id,
        expected_attempt_count=1,
        built=built,
    )

    assert result.created is True
    assert result.reason_code is None
    assert result.proposal is not None
    assert result.proposal.status == "pending_confirmation"
    assert result.proposal.payload_fingerprint == built.payload_fingerprint
    assert result.proposal.payload_data == built.payload.model_dump(
        mode="json",
        exclude_unset=True,
    )
    async with session_factory() as other_session:
        outside_count = await other_session.scalar(
            select(func.count(AgentProposal.id)).where(
                AgentProposal.run_id == run.id
            )
        )
    assert outside_count == 0


@pytest.mark.asyncio
async def test_same_run_and_payload_replay_returns_existing_proposal(
    db_session,
):
    user, conversation, run = await _add_running_context(
        db_session,
        "replay",
    )
    built = _build()
    first = await persist_optional_plan_adjustment_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        conversation_id=conversation.id,
        run_id=run.id,
        expected_attempt_count=1,
        built=built,
    )
    replay = await persist_optional_plan_adjustment_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        conversation_id=conversation.id,
        run_id=run.id,
        expected_attempt_count=1,
        built=built,
    )
    count = await db_session.scalar(
        select(func.count(AgentProposal.id)).where(
            AgentProposal.run_id == run.id
        )
    )

    assert first.proposal is not None
    assert replay.proposal is first.proposal
    assert replay.created is False
    assert replay.reason_code is None
    assert count == 1


@pytest.mark.asyncio
async def test_same_run_with_changed_payload_is_an_idempotency_conflict(
    db_session,
):
    user, conversation, run = await _add_running_context(
        db_session,
        "conflict",
    )
    first_built = _build()
    changed_payload = _payload()
    changed_payload["after"]["exercises"][0]["sets"] = 2
    changed_payload["changes"][0]["after"]["sets"] = 2
    changed_built = _build(changed_payload)
    await persist_optional_plan_adjustment_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        conversation_id=conversation.id,
        run_id=run.id,
        expected_attempt_count=1,
        built=first_built,
    )

    with pytest.raises(
        PlanAdjustmentProposalPersistenceRejected,
        match="proposal_idempotency_conflict",
    ) as raised:
        await persist_optional_plan_adjustment_proposal(
            db_session,
            enabled=True,
            user_id=user.id,
            conversation_id=conversation.id,
            run_id=run.id,
            expected_attempt_count=1,
            built=changed_built,
        )

    assert raised.value.reason_code == "proposal_idempotency_conflict"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ownership_override",
    [
        {"user_id": "another-user"},
        {"conversation_id": "another-conversation"},
        {"expected_attempt_count": 2},
    ],
)
async def test_persistence_rejects_lost_or_mismatched_run_ownership(
    db_session,
    ownership_override,
):
    suffix = next(iter(ownership_override))
    user, conversation, run = await _add_running_context(
        db_session,
        f"ownership-{suffix}",
    )
    arguments = {
        "enabled": True,
        "user_id": user.id,
        "conversation_id": conversation.id,
        "run_id": run.id,
        "expected_attempt_count": 1,
        "built": _build(),
    }
    arguments.update(ownership_override)

    with pytest.raises(
        PlanAdjustmentProposalPersistenceRejected,
        match="run_ownership_lost",
    ) as raised:
        await persist_optional_plan_adjustment_proposal(
            db_session,
            **arguments,
        )

    assert raised.value.reason_code == "run_ownership_lost"
    count = await db_session.scalar(
        select(func.count(AgentProposal.id)).where(
            AgentProposal.run_id == run.id
        )
    )
    assert count == 0
