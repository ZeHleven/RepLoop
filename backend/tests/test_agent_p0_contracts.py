"""Contract regressions found in the 2026-09-29 review; synthetic inputs only."""
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from app.services.agent_daily_meal_plans import DailyMealDraft
from app.services.agent_intent import IntentResolution, normalize_resolution, route_tools
from app.services.agent_tools import build_read_tools
from app.services.workout_reporting import REPORT_DATE

from datetime import datetime, timezone
import asyncio
from pydantic import ValidationError
from app.services.business_clock import BUSINESS_DATE, business_today, request_day, wall_today
from app.services.agent_daily_meal_plans import DAILY_MEAL_DRAFT_SCHEMA
from app.services.agent_evidence_contract import build_evidence_contract, guard_read_completion
from app.services.agent_trace import build_initial_execution_trace
from app.schemas.agent_trace import AgentObservationTrace


def test_excess_explanations_preserve_every_word_without_invalidating_meals():
    reasons = [f"说明{i}：保留这个条件" for i in range(6)]
    payload = {"meals": [{"meal_type": "早餐", "items": [{"food_id": "synthetic", "amount_g": 100}]}],
               "rationale": reasons}
    draft = DailyMealDraft.model_validate(payload)
    assert draft.meals[0].items[0].amount_g == 100
    assert len(draft.rationale) <= 5
    assert all(reason in "\n".join(draft.rationale) for reason in reasons)
    assert payload["rationale"] == reasons


def test_read_budget_never_returns_a_silently_truncated_allowlist():
    resolution = IntentResolution(
        primary_intent="profile_query", intent_domain="profile", request_kind="assessment",
        confidence=1, resolved_query="结合资料、健康限制、体重、计划和训练进度评估状态",
        evidence_requirements=["profile_summary", "health_screening", "weight_history", "active_plan", "workout_progress"],
    )
    resolution = normalize_resolution(resolution.resolved_query, resolution)
    # The budget gate must decline the whole executable route, keeping the full
    # evidence requirements available for an explicit clarification.
    assert len(resolution.evidence_requirements) == 5
    assert route_tools(resolution) == []


@pytest.mark.asyncio
async def test_today_nutrition_uses_the_same_request_day_as_workout_reports():
    class HostDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 28)

    class Summary:
        meals = []

        def model_dump(self, **kwargs):
            return {"date": "2026-09-29"}

    loader = AsyncMock(return_value=Summary())
    token = REPORT_DATE.set(date(2026, 9, 29))
    try:
        with patch("app.services.agent_tools.date", HostDate), patch(
            "app.services.agent_tools.build_daily_nutrition_summary", loader
        ):
            tool = build_read_tools(None, user_id="synthetic", allowlist=["nutrition.get_today"])[0]
            await tool.ainvoke({})
        assert loader.call_args.kwargs["target_date"] == date(2026, 9, 29)
    finally:
        REPORT_DATE.reset(token)


@pytest.mark.parametrize("rationale", [["条件"] * 21, ["x" * 8001], [12], "不是数组"])
def test_explanation_repair_remains_bounded_and_typed(rationale):
    with pytest.raises(ValidationError):
        DailyMealDraft.model_validate({"meals": [{"meal_type": "早餐", "items": [
            {"food_id": "rice", "amount_g": 100}]}], "rationale": rationale})


@pytest.mark.parametrize("amount", [0, -10, 501, float("inf")])
def test_explanation_repair_never_relaxes_food_amounts(amount):
    with pytest.raises(ValidationError):
        DailyMealDraft.model_validate({"meals": [{"meal_type": "早餐", "items": [
            {"food_id": "rice", "amount_g": amount}]}], "rationale": ["说明"] * 6})


def test_schema_projection_keeps_constraints_visible_without_unsupported_keywords():
    import json
    serialized = json.dumps(DAILY_MEAL_DRAFT_SCHEMA, ensure_ascii=False)
    assert all(key not in serialized for key in ('"maxItems"', '"minItems"', '"$ref"', '"default"'))
    assert "最多条数：5" in DAILY_MEAL_DRAFT_SCHEMA["properties"]["rationale"]["description"]
    assert DAILY_MEAL_DRAFT_SCHEMA["additionalProperties"] is False
    assert set(DAILY_MEAL_DRAFT_SCHEMA["required"]) == {"meals", "rationale"}


@pytest.mark.asyncio
async def test_request_days_are_isolated_across_tasks_and_reset_after_exception():
    original = BUSINESS_DATE.get()
    async def execute(moment, expected):
        with request_day(moment):
            await asyncio.sleep(0)
            assert business_today() == expected
            raise RuntimeError("synthetic interruption")
    results = await asyncio.gather(
        execute(datetime(2026, 9, 28, 16, 30, tzinfo=timezone.utc), date(2026, 9, 29)),
        execute(datetime(2026, 9, 28, 15, 30, tzinfo=timezone.utc), date(2026, 9, 28)),
        return_exceptions=True)
    assert all(type(result) is RuntimeError for result in results)
    assert BUSINESS_DATE.get() == original


def test_frozen_read_day_cannot_extend_live_write_eligibility():
    real_day = wall_today()
    with request_day(datetime(2000, 1, 1, tzinfo=timezone.utc)):
        assert business_today() == date(2000, 1, 1)
        assert wall_today() == real_day


@pytest.mark.parametrize("mode", ["direct", "planned"])
def test_missing_evidence_cannot_end_as_a_complete_answer_or_proposal(mode):
    resolution = IntentResolution(primary_intent="profile_query", confidence=1,
                                  evidence_requirements=["profile_summary", "nutrition_today"])
    tools = ["profile.get_summary", "nutrition.get_today"]
    trace = build_initial_execution_trace(resolution, tools).model_copy(update={
        "execution_mode": mode, "terminal_action": "proposal", "status": "completed",
        "evidence_contract": build_evidence_contract(resolution, tools, budget=4),
        "observations": [AgentObservationTrace(sequence=1, action_sequence=1,
            tool_id=tools[0], status="success", result_fingerprint="0" * 64)],
    })
    reply, guarded = guard_read_completion("完整分析完毕，建议更新计划。", trace)
    assert guarded.evidence_contract.status == "partial"
    assert guarded.evidence_contract.missing_tools == ["nutrition.get_today"]
    assert guarded.terminal_action == "answer"
    assert guarded.termination_reason == "required_evidence_missing"
    assert "还没有完整取得" in reply


def test_registry_narrowing_does_not_erase_the_original_obligation():
    resolution = IntentResolution(primary_intent="profile_query", confidence=1,
                                  evidence_requirements=["profile_summary", "nutrition_today"])
    contract = build_evidence_contract(resolution, ["profile.get_summary"], budget=4)
    assert contract.status == "blocked"
    assert contract.reason == "authority_missing"
    assert contract.required_tools == ["profile.get_summary", "nutrition.get_today"]


def test_assessment_template_cannot_overwrite_extra_user_goals_or_truncate_union():
    resolution = normalize_resolution("结合体重、饮食和下一练评估计划", IntentResolution(
        primary_intent="plan_query", intent_domain="workout_plan", request_kind="assessment",
        confidence=1, evidence_requirements=["weight_history", "nutrition_today", "next_workout"],
    ))
    assert len(resolution.evidence_requirements) == 7
    contract = build_evidence_contract(resolution, route_tools(resolution), budget=4)
    assert len(contract.required_tools) == 7
    assert contract.status == "blocked"
    assert set(contract.required_tools) >= {"weight.list_history", "nutrition.get_today", "workout.get_next"}


def test_explicit_history_goal_is_not_satisfied_by_progress_as_a_fallback():
    resolution = IntentResolution(primary_intent="workout_progress_query", confidence=1,
                                  evidence_requirements=["workout_progress", "workout_history"])
    tools = route_tools(resolution)
    trace = build_initial_execution_trace(resolution, tools).model_copy(update={
        "terminal_action": "answer",
        "evidence_contract": build_evidence_contract(resolution, tools, budget=4),
        "observations": [AgentObservationTrace(sequence=1, action_sequence=1,
            tool_id="workout.get_progress", status="success", result_fingerprint="0" * 64)],
    })
    _, guarded = guard_read_completion("进度已取得", trace)
    assert guarded.evidence_contract.missing_tools == ["workout.list_history"]
