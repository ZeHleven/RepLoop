from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.agent import AgentConversation, AgentProposal, AgentRun
from app.models.user import User


_PAYLOAD_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "agent_plan_adjustment_proposal_contract_cases.json"
)
_FINGERPRINT_A = "a" * 64
_FINGERPRINT_B = "b" * 64


def _payload() -> dict:
    fixture = json.loads(_PAYLOAD_PATH.read_text(encoding="utf-8"))
    return fixture["canonical_payloads"]["adherence"]


async def _add_context(db_session, suffix: str) -> tuple[User, AgentRun]:
    user = User(
        id=f"proposal-user-{suffix}",
        email=f"proposal-{suffix}@example.com",
        password_hash="not-used-by-this-test",
    )
    conversation = AgentConversation(
        id=f"proposal-conversation-{suffix}",
        user_id=user.id,
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add(conversation)
    await db_session.flush()
    run = AgentRun(
        id=f"proposal-run-{suffix}",
        conversation_id=conversation.id,
        user_id=user.id,
        status="completed",
    )
    db_session.add(run)
    await db_session.flush()
    return user, run


def _pending_proposal(
    *,
    user: User,
    run: AgentRun,
    suffix: str,
    now: datetime,
    **overrides,
) -> AgentProposal:
    payload = _payload()
    values = {
        "id": f"proposal-{suffix}",
        "user_id": user.id,
        "conversation_id": run.conversation_id,
        "run_id": run.id,
        "proposal_type": "plan_adjustment_v1",
        "payload_data": payload,
        "payload_fingerprint": _FINGERPRINT_A,
        "base_plan_id": payload["target"]["base_plan_id"],
        "base_plan_fingerprint": payload["target"][
            "base_plan_fingerprint"
        ],
        "expires_at": now + timedelta(hours=24),
        "created_at": now,
    }
    values.update(overrides)
    return AgentProposal(**values)


def test_agent_proposal_metadata_declares_lifecycle_constraints():
    table = AgentProposal.__table__
    column_names = set(table.columns.keys())
    constraint_names = {
        constraint.name for constraint in table.constraints
    }
    index_names = {index.name for index in table.indexes}

    assert {
        "payload_fingerprint",
        "base_plan_id",
        "base_plan_fingerprint",
        "decision_action",
        "decision_client_request_id",
        "confirmed_at",
        "rejected_at",
        "applied_at",
        "result_plan_id",
        "result_plan_fingerprint",
        "last_error_code",
    } <= column_names
    assert {
        "uq_agent_proposals_run_type",
        "uq_agent_proposals_user_decision_request",
        "ck_agent_proposals_status",
        "ck_agent_proposals_version_positive",
        "ck_agent_proposals_payload_object",
        "ck_agent_proposals_fingerprints",
        "ck_agent_proposals_expiry_window",
        "ck_agent_proposals_decision_fields",
        "ck_agent_proposals_pending_clean",
        "ck_agent_proposals_applied_result",
        "ck_agent_proposals_rejection_state",
        "ck_agent_proposals_plan_adjustment_fields",
    } <= constraint_names
    assert "ix_agent_proposals_pending_expiry" in index_names
    assert str(table.c.status.server_default.arg) == "pending_confirmation"


@pytest.mark.asyncio
async def test_valid_pending_and_applied_proposals_satisfy_constraints(
    db_session,
):
    user, run = await _add_context(db_session, "valid")
    now = datetime.now(timezone.utc)
    pending = _pending_proposal(
        user=user,
        run=run,
        suffix="valid-pending",
        now=now,
    )
    db_session.add(pending)
    await db_session.flush()

    assert pending.status == "pending_confirmation"
    assert pending.version == 1

    second_run = AgentRun(
        id="proposal-run-valid-applied",
        conversation_id=run.conversation_id,
        user_id=user.id,
        status="completed",
    )
    db_session.add(second_run)
    await db_session.flush()
    applied = _pending_proposal(
        user=user,
        run=second_run,
        suffix="valid-applied",
        now=now,
        status="applied",
        decision_action="confirm",
        decision_client_request_id="proposal-confirm-valid-applied",
        confirmed_at=now + timedelta(minutes=1),
        applied_at=now + timedelta(minutes=1),
        result_plan_id="plan-result-valid-applied",
        result_plan_fingerprint=_FINGERPRINT_B,
    )
    db_session.add(applied)
    await db_session.flush()

    assert applied.status == "applied"
    assert applied.result_plan_id == "plan-result-valid-applied"


@pytest.mark.asyncio
async def test_one_run_cannot_create_duplicate_proposal_type(db_session):
    user, run = await _add_context(db_session, "duplicate-run")
    now = datetime.now(timezone.utc)
    first = _pending_proposal(
        user=user,
        run=run,
        suffix="duplicate-run-first",
        now=now,
    )
    second = _pending_proposal(
        user=user,
        run=run,
        suffix="duplicate-run-second",
        now=now,
    )
    db_session.add_all([first, second])

    with pytest.raises(IntegrityError, match="uq_agent_proposals_run_type"):
        await db_session.flush()


@pytest.mark.asyncio
async def test_decision_request_id_is_idempotent_per_user(db_session):
    user, first_run = await _add_context(db_session, "decision-replay")
    second_run = AgentRun(
        id="proposal-run-decision-replay-second",
        conversation_id=first_run.conversation_id,
        user_id=user.id,
        status="completed",
    )
    db_session.add(second_run)
    await db_session.flush()
    now = datetime.now(timezone.utc)
    shared_request_id = "proposal-shared-decision-request"
    first = _pending_proposal(
        user=user,
        run=first_run,
        suffix="decision-replay-first",
        now=now,
        status="stale",
        decision_action="confirm",
        decision_client_request_id=shared_request_id,
        confirmed_at=now + timedelta(minutes=1),
        last_error_code="proposal_base_plan_changed",
    )
    second = _pending_proposal(
        user=user,
        run=second_run,
        suffix="decision-replay-second",
        now=now,
        status="failed",
        decision_action="confirm",
        decision_client_request_id=shared_request_id,
        confirmed_at=now + timedelta(minutes=1),
        last_error_code="proposal_execution_failed",
    )
    db_session.add_all([first, second])

    with pytest.raises(
        IntegrityError,
        match="uq_agent_proposals_user_decision_request",
    ):
        await db_session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("suffix", "overrides", "constraint_name"),
    [
        (
            "expiry-too-long",
            {"expires_at": datetime(2030, 1, 4, 1, tzinfo=timezone.utc)},
            "ck_agent_proposals_expiry_window",
        ),
        (
            "pending-has-error",
            {"last_error_code": "must-not-be-set"},
            "ck_agent_proposals_pending_clean",
        ),
        (
            "applied-without-result",
            {
                "status": "applied",
                "decision_action": "confirm",
                "decision_client_request_id": "proposal-invalid-applied",
                "confirmed_at": datetime(2030, 1, 1, 1, tzinfo=timezone.utc),
            },
            "ck_agent_proposals_applied_result",
        ),
        (
            "payload-target-mismatch",
            {"base_plan_id": "different-base-plan"},
            "ck_agent_proposals_plan_adjustment_fields",
        ),
    ],
)
async def test_invalid_lifecycle_rows_are_rejected_by_database(
    db_session,
    suffix,
    overrides,
    constraint_name,
):
    user, run = await _add_context(db_session, suffix)
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    proposal = _pending_proposal(
        user=user,
        run=run,
        suffix=suffix,
        now=now,
        **overrides,
    )
    db_session.add(proposal)

    with pytest.raises(IntegrityError, match=constraint_name):
        await db_session.flush()
