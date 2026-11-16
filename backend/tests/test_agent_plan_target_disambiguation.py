from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import bcrypt
import pytest

from app.config import settings
from app.models.agent import AgentConversation, AgentProposal
from app.models.exercise import Exercise
from app.models.profile import UserProfile
from app.models.user import User
from app.models.workout import PlannedExercise, WorkoutPlan
from app.services.agent_domain_proposals import _unique_planned_target
from app.services.agent_intent import (
    ChangeRequest,
    IntentResolution,
    IntentResolverOutcome,
)
from app.schemas.agent import AgentClarificationActionRequest
from app.services.agent_runtime import (
    _plan_target_choice_card,
    _resolve_plan_target_selection,
    _resolve_structured_plan_target_selection,
)
from app.services.auth import create_access_token
from app.services.plan_management_proposals import PlanProposalError


def _planned_items() -> list[dict]:
    return [
        {
            "item_key": "planned:bench-tuesday",
            "exercise_name": "卧推",
            "day_of_week": 2,
            "order_index": 0,
        },
        {
            "item_key": "planned:bench-thursday",
            "exercise_name": "卧推",
            "day_of_week": 4,
            "order_index": 0,
        },
    ]


def _changes(reference: str = "卧推") -> list[ChangeRequest]:
    return [
        ChangeRequest(
            resource="workout_plan",
            operation="update",
            field_path=field,
            target_reference=reference,
            value=value,
        )
        for field, value in (
            ("exercise.sets", 4),
            ("exercise.reps", "8"),
            ("exercise.rest_seconds", 120),
            ("exercise.recommended_weight_kg", 50),
        )
    ]


def _pending_clarification() -> dict:
    return {
        "primary_intent": "plan_query",
        "intent_domain": "workout_plan",
        "request_kind": "mutation",
        "requested_effect": "update",
        "requested_output": "answer",
        "resolved_query": "修改卧推训练目标",
        "change_requests": [item.model_dump(mode="json") for item in _changes()],
        "missing_slots": ["“卧推”的训练日"],
        "clarification_question": "请选择周二、周四或全部",
        "risk_level": "low",
        "confidence": 0.98,
        "understanding_version": "v6",
        "expires_at": (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat(),
        "clarification_context": {
            "clarification_type": "plan_exercise_occurrence",
            "target_reference": "卧推",
            "candidates": _planned_items(),
        },
    }


def test_duplicate_planned_action_reports_real_occurrences():
    with pytest.raises(PlanProposalError) as captured:
        _unique_planned_target(_planned_items(), "卧推")

    error = captured.value
    assert error.code == "proposal_target_ambiguous"
    assert "周二的卧推" in error.message
    assert "周四的卧推" in error.message
    assert error.details["clarification_type"] == "plan_exercise_occurrence"
    assert [item["item_key"] for item in error.details["candidates"]] == [
        "planned:bench-tuesday",
        "planned:bench-thursday",
    ]


def test_day_qualified_and_stable_references_select_one_occurrence():
    assert _unique_planned_target(
        _planned_items(), "周四的卧推"
    )["item_key"] == "planned:bench-thursday"
    assert _unique_planned_target(
        _planned_items(), "planned:bench-tuesday"
    )["day_of_week"] == 2


def test_missing_action_is_distinct_from_duplicate_action():
    with pytest.raises(PlanProposalError) as captured:
        _unique_planned_target(_planned_items(), "深蹲")

    assert captured.value.code == "proposal_target_not_found"
    assert "没有找到" in captured.value.message
    assert captured.value.details == {}


def test_stale_stable_reference_stops_instead_of_retargeting_by_name():
    with pytest.raises(PlanProposalError) as captured:
        _unique_planned_target(_planned_items(), "planned:old-plan-row")

    assert captured.value.code == "proposal_base_changed"
    assert captured.value.status_code == 409


def test_persisted_day_choice_reuses_every_original_change():
    outcome = _resolve_plan_target_selection("改周二的", _pending_clarification())

    assert outcome is not None
    assert outcome.fallback_reason == "persisted_plan_target_selection"
    assert outcome.resolution.clarification_required is False
    assert len(outcome.resolution.change_requests) == 4
    assert {
        item.target_reference for item in outcome.resolution.change_requests
    } == {"planned:bench-tuesday"}
    assert [item.value for item in outcome.resolution.change_requests] == [
        4, "8", 120, 50,
    ]


def test_structured_choice_card_and_action_are_bound_to_saved_candidates():
    pending = _pending_clarification()
    pending["origin_run_id"] = "origin-run"
    card = _plan_target_choice_card(
        pending["clarification_context"], origin_run_id="origin-run"
    )
    assert card is not None
    assert [item["label"] for item in card["data"]["options"]] == [
        "周二的卧推", "周四的卧推", "全部匹配动作",
    ]
    action = AgentClarificationActionRequest(
        action="select_plan_occurrences",
        origin_run_id="origin-run",
        choice_ids=["planned:bench-thursday"],
    )
    outcome = _resolve_structured_plan_target_selection(action, pending)
    assert outcome is not None
    assert {
        item.target_reference for item in outcome.resolution.change_requests
    } == {"planned:bench-thursday"}

    forged = action.model_copy(update={"choice_ids": ["planned:forged"]})
    assert _resolve_structured_plan_target_selection(forged, pending) is None

    expired = {
        **pending,
        "expires_at": (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat(),
    }
    assert _resolve_structured_plan_target_selection(action, expired) is None


@pytest.mark.parametrize("message", ["两个都调整", "周二和周四都改"])
def test_persisted_all_choice_expands_changes_for_each_occurrence(message: str):
    outcome = _resolve_plan_target_selection(message, _pending_clarification())

    assert outcome is not None
    assert len(outcome.resolution.change_requests) == 8
    assert {
        item.target_reference for item in outcome.resolution.change_requests
    } == {"planned:bench-tuesday", "planned:bench-thursday"}


@pytest.mark.parametrize(
    "message",
    ["周二训练是什么", "我想查看所有训练记录", "把周二改成休息日"],
)
def test_independent_request_does_not_consume_pending_choice(message: str):
    assert _resolve_plan_target_selection(
        message, _pending_clarification()
    ) is None


async def _seed_duplicate_bench_plan(db_session):
    user = User(
        id="plan-target-user",
        email="plan-target@example.com",
        password_hash=bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode(),
    )
    profile = UserProfile(
        user_id=user.id,
        age=30,
        height_cm=175,
        weight_kg=75,
        experience_level="intermediate",
        training_location="gym",
        injuries=[],
        chronic_conditions=[],
        onboarding_completed=True,
    )
    exercise = Exercise(
        id="plan-target-bench",
        name_zh="卧推",
        name_en="Bench Press Target Disambiguation",
        category="力量",
        difficulty="初级",
        equipment=[],
        contraindications=[],
        is_active=True,
    )
    plan = WorkoutPlan(
        id="plan-target-active",
        user_id=user.id,
        name="重复动作计划",
        goal="strength",
        duration_weeks=8,
        days_per_week=2,
        is_active=True,
    )
    conversation = AgentConversation(user_id=user.id)
    db_session.add(user)
    await db_session.flush()
    db_session.add_all([profile, exercise, plan, conversation])
    await db_session.flush()
    db_session.add_all([
        PlannedExercise(
            id="bench-tuesday",
            plan_id=plan.id,
            exercise_id=exercise.id,
            day_of_week=2,
            sets=3,
            reps="10",
            rest_seconds=90,
            recommended_weight_kg=20,
            order_index=0,
        ),
        PlannedExercise(
            id="bench-thursday",
            plan_id=plan.id,
            exercise_id=exercise.id,
            day_of_week=4,
            sets=3,
            reps="10",
            rest_seconds=90,
            recommended_weight_kg=20,
            order_index=0,
        ),
    ])
    await db_session.commit()
    return user, plan, conversation, exercise


@pytest.mark.asyncio
async def test_chat_clarifies_duplicate_action_then_creates_scoped_proposal(
    client,
    db_session,
):
    user, plan, conversation, exercise = await _seed_duplicate_bench_plan(
        db_session
    )
    resolution = IntentResolution(
        primary_intent="plan_query",
        intent_domain="workout_plan",
        request_kind="mutation",
        requested_effect="update",
        change_requests=_changes(),
        resolved_query="把卧推改为4组8次，休息120秒，建议重量50公斤",
        confidence=0.99,
    )
    resolver = AsyncMock(return_value=IntentResolverOutcome(
        resolution=resolution,
        source="model",
        attempt_count=1,
    ))
    headers = {"Authorization": f"Bearer {create_access_token(user.id)}"}

    with (
        patch.object(settings, "AGENT_PLAN_MANAGEMENT_PROPOSALS_ENABLED", True),
        patch(
            "app.services.agent_runtime.resolve_intent_with_fallback",
            new=resolver,
        ),
    ):
        first = await client.post(
            "/api/v1/agent/chat",
            headers=headers,
            json={
                "message": "把计划里的卧推改为4组，每组8次，组间休息120秒，建议重量50公斤",
                "conversation_id": conversation.id,
            },
        )
        second = await client.post(
            "/api/v1/agent/chat",
            headers=headers,
            json={
                "message": "选择周二的卧推",
                "conversation_id": conversation.id,
                "clarification_action": {
                    "action": "select_plan_occurrences",
                    "origin_run_id": first.json()["run_id"],
                    "choice_ids": ["planned:bench-tuesday"],
                },
            },
        )

    assert first.status_code == 200
    assert "周二的卧推" in first.json()["reply"]
    assert "周四的卧推" in first.json()["reply"]
    assert "proposal" not in first.json()
    assert first.json()["cards"][0]["type"] == "clarification_choices"
    assert len(first.json()["cards"][0]["data"]["options"]) == 3
    assert second.status_code == 200
    assert second.json()["proposal"]["proposal_type"] == "plan_adjustment_v2"
    assert resolver.await_count == 1

    proposal = await db_session.get(AgentProposal, second.json()["proposal"]["id"])
    assert proposal is not None
    after = {
        item["day_of_week"]: item
        for item in proposal.payload_data["after"]["exercises"]
    }
    assert (
        after[2]["sets"],
        after[2]["reps"],
        after[2]["rest_seconds"],
        after[2]["recommended_weight_kg"],
    ) == (4, "8", 120, 50)
    assert (
        after[4]["sets"],
        after[4]["reps"],
        after[4]["rest_seconds"],
        after[4]["recommended_weight_kg"],
    ) == (3, "10", 90, 20)
    assert plan.is_active is True
    await db_session.refresh(conversation)
    assert conversation.pending_clarification == {}
    # Exercises are global catalogue rows rather than user-owned data. This
    # suite commits realistic Proposal transactions, so explicitly retire its
    # fixture row before later router tests assert an empty active catalogue.
    exercise.is_active = False
    await db_session.commit()
