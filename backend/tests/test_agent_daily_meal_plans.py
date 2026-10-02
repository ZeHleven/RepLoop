from __future__ import annotations

from app.services.business_clock import business_today

import copy
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import bcrypt
import pytest
from sqlalchemy import func, select

from app.config import settings
from app.models.agent import (
    AgentArtifact,
    AgentConversation,
    AgentProposal,
    AgentRun,
    AgentToolCall,
)
from app.models.food import Food
from app.models.meal import MealLog
from app.models.profile import UserProfile, WeightLog
from app.models.user import User
from app.schemas.plan_management_proposal import GenericProposalDecisionRequest
from app.services.agent_daily_meal_plans import (
    DAILY_MEAL_EVIDENCE,
    DailyMealDraft,
    DailyMealPlanError,
    calculate_nutrition_targets,
    calculate_daily_nutrition_totals,
    canonical_fingerprint,
    canonicalize_daily_meal_draft,
    collect_daily_meal_evidence,
    generate_daily_meal_artifact,
)
from app.services.daily_meal_optimizer import (
    DailyMealOptimizationError,
    nutrition_fit,
    optimize_daily_meal_amounts,
)
from app.services.agent_domain_proposals import (
    create_agent_daily_meal_proposal,
    decide_agent_domain_proposal,
)
from app.services.ai_client import StructuredCompletionResult
from app.services.agent_intent import (
    ChangeRequest,
    IntentResolverOutcome,
    IntentResolution,
    normalize_resolution,
    resolve_intent,
)
from app.services.plan_management_proposals import PlanProposalError
from app.services.auth import create_access_token


async def _deterministic_model_intent(message, **_kwargs):
    return IntentResolverOutcome(
        resolution=normalize_resolution(message, resolve_intent(message)),
        source="model",
        attempt_count=1,
    )


async def _daily_context(db_session, suffix: str):
    user = User(
        id=f"daily-meal-user-{suffix}",
        email=f"daily-meal-{suffix}@example.com",
        password_hash=bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode(),
    )
    db_session.add(user)
    await db_session.flush()
    profile = UserProfile(
        user_id=user.id,
        age=30,
        gender="prefer_not_to_say",
        height_cm=170,
        weight_kg=65,
        primary_goal="增肌",
        training_days_per_week=3,
        diet_restriction=None,
        injuries=[],
        chronic_conditions=[],
        onboarding_completed=True,
    )
    conversation = AgentConversation(user_id=user.id)
    db_session.add_all([profile, conversation])
    await db_session.flush()
    run = AgentRun(
        user_id=user.id,
        conversation_id=conversation.id,
        status="completed",
    )
    db_session.add(run)
    db_session.add(WeightLog(user_id=user.id, weight_kg=66))
    foods = [
        Food(
            id=f"daily-{suffix}-rice",
            name_zh=f"测试米饭{suffix}",
            category="grain",
            calories_per_100g=130,
            protein_g=3,
            carbs_g=28,
            fat_g=1,
            diet_tags=[],
            is_common_in_china=False,
            is_active=True,
        ),
        Food(
            id=f"daily-{suffix}-oil",
            name_zh=f"测试橄榄油{suffix}",
            category="oil",
            calories_per_100g=900,
            protein_g=0,
            carbs_g=0,
            fat_g=100,
            diet_tags=[],
            is_common_in_china=False,
            is_active=True,
        ),
        Food(
            id=f"daily-{suffix}-chicken",
            name_zh=f"测试鸡胸{suffix}",
            category="protein",
            calories_per_100g=165,
            protein_g=31,
            carbs_g=0,
            fat_g=3.6,
            diet_tags=[],
            is_common_in_china=False,
            is_active=True,
        ),
    ]
    db_session.add_all(foods)
    await db_session.commit()
    return user, conversation, run, foods


async def _artifact(db_session, *, user, conversation, run, foods):
    evidence = await collect_daily_meal_evidence(
        db_session,
        user_id=user.id,
        use_isolated_sessions=False,
    )
    draft = DailyMealDraft.model_validate({
        "meals": [
            {
                "meal_type": meal_type,
                "items": [
                    {"food_id": foods[0].id, "amount_g": 300},
                    {"food_id": foods[2].id, "amount_g": 100},
                    {"food_id": foods[1].id, "amount_g": 20},
                ],
            }
            for meal_type in ("早餐", "午餐", "晚餐")
        ],
    })
    meals = await canonicalize_daily_meal_draft(
        db_session,
        draft=draft,
        evidence=evidence,
    )
    targets = calculate_nutrition_targets(evidence)
    totals = calculate_daily_nutrition_totals(evidence, meals)
    payload = {
        "schema_version": "1.0.0",
        "artifact_type": "daily_meal_plan_v1",
        "target_date": business_today().isoformat(),
        "existing_meals": [],
        "meals": meals,
        "nutrition_targets": targets,
        "daily_totals": totals,
        "nutrition_fit": nutrition_fit(totals, targets),
        "rationale": [],
        "evidence_sources": list(DAILY_MEAL_EVIDENCE),
        "assumptions": [],
        "safety_notes": ["测试方案"],
    }
    artifact = AgentArtifact(
        user_id=user.id,
        conversation_id=conversation.id,
        source_run_id=run.id,
        artifact_type="daily_meal_plan_v1",
        schema_version="1.0.0",
        status="active",
        version=1,
        payload_data=payload,
        payload_fingerprint=canonical_fingerprint(payload),
        context_fingerprints=evidence.fingerprints,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=23),
    )
    db_session.add(artifact)
    await db_session.commit()
    return artifact


def _target_compliant_draft(foods: list[Food]) -> DailyMealDraft:
    return DailyMealDraft.model_validate({
        "meals": [
            {
                "meal_type": meal_type,
                "items": [
                    {"food_id": foods[0].id, "amount_g": 300},
                    {"food_id": foods[2].id, "amount_g": 100},
                    {"food_id": foods[1].id, "amount_g": 20},
                ],
            }
            for meal_type in ("早餐", "午餐", "晚餐")
        ],
        "rationale": ["测试结构化方案"],
    })


def _completion(
    payload: dict | None,
    *,
    parse_error: str | None = None,
    finish_reason: str = "stop",
) -> StructuredCompletionResult:
    return StructuredCompletionResult(
        payload=payload,
        raw_output="{}" if payload is not None else "",
        mode="deepseek_json_mode",
        finish_reason=finish_reason,
        duration_ms=12,
        output_chars=2 if payload is not None else 0,
        parse_error=parse_error,
        fallback_reason="custom_gateway",
    )


@pytest.mark.asyncio
async def test_evidence_coordinator_collects_exact_six_groups_without_identity_input(
    db_session,
):
    user, _, _, _ = await _daily_context(db_session, "evidence")
    evidence = await collect_daily_meal_evidence(
        db_session,
        user_id=user.id,
        use_isolated_sessions=False,
    )

    assert tuple(evidence.values) == DAILY_MEAL_EVIDENCE
    assert tuple(evidence.fingerprints) == DAILY_MEAL_EVIDENCE
    assert all(len(item.result_fingerprint) == 64 for item in evidence.audits)
    assert all("user" not in field for item in evidence.audits for field in item.fields)
    assert evidence.values["weight_history"]["records"][0]["weight_kg"] == 66


@pytest.mark.asyncio
async def test_generation_creates_reviewable_artifact_without_writing_meals(
    db_session,
):
    user, conversation, run, foods = await _daily_context(db_session, "generate")
    draft = _target_compliant_draft(foods)

    with patch(
        "app.services.agent_daily_meal_plans.structured_chat_completion",
        new=AsyncMock(return_value=_completion(draft.model_dump(mode="json"))),
    ):
        result = await generate_daily_meal_artifact(
            db_session,
            user_id=user.id,
            conversation_id=conversation.id,
            run_id=run.id,
            user_message="结合我的情况安排今天怎么吃",
        )

    assert result.artifact.status == "active"
    assert result.artifact.payload_data["target_date"] == business_today().isoformat()
    assert result.artifact.payload_data["evidence_sources"] == list(
        DAILY_MEAL_EVIDENCE
    )
    assert len(result.artifact.payload_data["meals"]) == 3
    assert result.card["type"] == "daily_meal_plan"
    assert len(result.audits) == 6
    assert await db_session.scalar(select(func.count(MealLog.id)).where(
        MealLog.user_id == user.id
    )) == 0


@pytest.mark.asyncio
async def test_original_chat_request_returns_artifact_and_six_audits_without_proposal(
    client,
    db_session,
):
    user, conversation, _, foods = await _daily_context(db_session, "chat")
    token = create_access_token(user.id)

    with (
        patch(
            "app.services.agent_runtime.resolve_intent_with_fallback",
            new=_deterministic_model_intent,
        ),
        patch(
            "app.services.agent_daily_meal_plans.structured_chat_completion",
            new=AsyncMock(return_value=_completion(
                _target_compliant_draft(foods).model_dump(mode="json")
            )),
        ),
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "conversation_id": conversation.id,
                "message": "结合我的情况安排今天怎么吃",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["artifact"]["artifact_type"] == "daily_meal_plan_v1"
    assert body["cards"][0]["type"] == "daily_meal_plan"
    assert "proposal" not in body
    run = await db_session.get(AgentRun, body["run_id"])
    assert run.request_kind == "generation"
    assert run.requested_effect == "read"
    assert run.requested_output == "daily_meal_plan"
    assert run.change_requests == []
    assert run.evidence_requirements == list(DAILY_MEAL_EVIDENCE)
    assert await db_session.scalar(select(func.count(AgentToolCall.id)).where(
        AgentToolCall.run_id == run.id,
        AgentToolCall.call_id.like("evidence:%"),
    )) == 6
    assert await db_session.scalar(select(func.count(AgentToolCall.id)).where(
        AgentToolCall.run_id == run.id,
        AgentToolCall.tool_name == "agent.daily_meal_generator",
    )) == 1
    optimizer_calls = list((await db_session.execute(select(AgentToolCall).where(
        AgentToolCall.run_id == run.id,
        AgentToolCall.tool_name == "agent.daily_meal_optimizer",
    ))).scalars().all())
    assert len(optimizer_calls) == 1
    assert optimizer_calls[0].status == "completed"
    assert optimizer_calls[0].result_data["solver_version"] == "highs_milp_v2"
    assert optimizer_calls[0].result_data["target_deviations"] == []
    assert "food_candidates" not in optimizer_calls[0].result_data
    assert await db_session.scalar(select(func.count(MealLog.id)).where(
        MealLog.user_id == user.id
    )) == 0


@pytest.mark.asyncio
async def test_generation_repairs_precise_schema_error_on_second_attempt(
    db_session,
):
    user, conversation, run, foods = await _daily_context(db_session, "repair-schema")
    valid = _target_compliant_draft(foods).model_dump(mode="json")
    invalid = _target_compliant_draft(foods).model_dump(mode="json")
    invalid["meals"][0]["items"][0]["food_name"] = "不应由模型提交"
    model_call = AsyncMock(side_effect=[
        _completion(invalid),
        _completion(valid),
    ])

    with patch(
        "app.services.agent_daily_meal_plans.structured_chat_completion",
        new=model_call,
    ):
        result = await generate_daily_meal_artifact(
            db_session,
            user_id=user.id,
            conversation_id=conversation.id,
            run_id=run.id,
            user_message="结合我的情况安排今天怎么吃",
        )

    assert model_call.await_count == 2
    assert [item.status for item in result.generation_attempts] == [
        "failed",
        "completed",
    ]
    first = result.generation_attempts[0]
    assert first.error_code == "schema_validation_failed"
    assert any("extra_forbidden" in path for path in first.validation_paths)
    second_messages = model_call.await_args_list[1].args[0]
    second_payload = second_messages[1]["content"]
    assert "schema_validation_failed" in second_payload
    assert "extra_forbidden" in second_payload
    assert "unexpected_field_count" in second_payload


@pytest.mark.asyncio
async def test_generation_deterministically_repairs_bad_model_amounts(
    db_session,
):
    user, conversation, run, foods = await _daily_context(db_session, "repair-target")
    invalid = DailyMealDraft.model_validate({
        "meals": [
            {
                "meal_type": meal_type,
                "items": [
                    {"food_id": foods[0].id, "amount_g": 5},
                    {"food_id": foods[2].id, "amount_g": 5},
                    {"food_id": foods[1].id, "amount_g": 1},
                ],
            }
            for meal_type in ("早餐", "午餐", "晚餐")
        ],
        "rationale": [],
    })
    model_call = AsyncMock(
        return_value=_completion(invalid.model_dump(mode="json"))
    )

    with patch(
        "app.services.agent_daily_meal_plans.structured_chat_completion",
        new=model_call,
    ):
        result = await generate_daily_meal_artifact(
            db_session,
            user_id=user.id,
            conversation_id=conversation.id,
            run_id=run.id,
            user_message="按今天训练量给我配三餐",
        )

    assert model_call.await_count == 1
    assert result.generation_attempts[0].status == "completed"
    assert result.optimization_attempts[-1].status == "completed"
    assert result.artifact.payload_data["nutrition_fit"]["status"] == "within_target"


@pytest.mark.asyncio
async def test_generation_reselects_foods_after_optimizer_reports_infeasible(
    db_session,
):
    user, conversation, run, foods = await _daily_context(
        db_session, "reselect-foods"
    )
    infeasible = {
        "meals": [
            {
                "meal_type": meal_type,
                "items": [{"food_id": foods[2].id, "amount_g": 150}],
            }
            for meal_type in ("早餐", "午餐", "晚餐")
        ],
        "rationale": ["第一次组合缺少碳水来源"],
    }
    valid = _target_compliant_draft(foods).model_dump(mode="json")
    model_call = AsyncMock(side_effect=[_completion(infeasible), _completion(valid)])

    with patch(
        "app.services.agent_daily_meal_plans.structured_chat_completion",
        new=model_call,
    ):
        result = await generate_daily_meal_artifact(
            db_session,
            user_id=user.id,
            conversation_id=conversation.id,
            run_id=run.id,
            user_message="结合我的情况安排今天怎么吃",
        )

    assert model_call.await_count == 2
    second_payload = model_call.await_args_list[1].args[0][1]["content"]
    assert "daily_meal_ideal_optimization_infeasible" in second_payload
    assert "target_gaps" in second_payload
    assert "infeasible_metrics" in second_payload
    assert result.artifact.payload_data["nutrition_fit"]["status"] == "within_target"
    assert any(
        item.status == "infeasible" for item in result.optimization_attempts
    )


@pytest.mark.asyncio
async def test_optimizer_timeout_without_solution_retries_next_food_candidate(
    db_session,
):
    user, conversation, run, foods = await _daily_context(
        db_session, "optimizer-timeout-retry"
    )
    valid = _target_compliant_draft(foods).model_dump(mode="json")
    model_call = AsyncMock(side_effect=[_completion(valid), _completion(valid)])
    optimizer_calls = 0

    def temporarily_timed_out(**kwargs):
        nonlocal optimizer_calls
        optimizer_calls += 1
        if optimizer_calls <= 2:
            raise DailyMealOptimizationError(
                "daily_meal_optimizer_timeout_no_solution",
                "当前候选未在时限内找到可行解",
                duration_ms=1,
            )
        return optimize_daily_meal_amounts(**kwargs)

    with (
        patch(
            "app.services.agent_daily_meal_plans.structured_chat_completion",
            new=model_call,
        ),
        patch(
            "app.services.agent_daily_meal_plans.optimize_daily_meal_amounts",
            new=temporarily_timed_out,
        ),
    ):
        result = await generate_daily_meal_artifact(
            db_session,
            user_id=user.id,
            conversation_id=conversation.id,
            run_id=run.id,
            user_message="结合我的情况安排今天怎么吃",
        )

    assert model_call.await_count == 2
    assert optimizer_calls >= 3
    assert any(
        item.error_code == "daily_meal_optimizer_timeout_no_solution"
        for item in result.optimization_attempts
    )
    assert result.artifact.payload_data["nutrition_fit"]["status"] == (
        "within_target"
    )


@pytest.mark.asyncio
async def test_generation_returns_acceptable_deviation_with_visible_fit(
    db_session,
):
    user, conversation, run, _ = await _daily_context(
        db_session, "acceptable-fit"
    )
    near_food = Food(
        id="daily-acceptable-fit-mixed",
        name_zh="测试均衡餐",
        category="混合",
        calories_per_100g=150,
        protein_g=8,
        carbs_g=25.5,
        fat_g=2.7,
        common_portion_g=400,
        diet_tags=[],
        is_common_in_china=True,
        is_active=True,
    )
    db_session.add(near_food)
    await db_session.commit()
    draft = {
        "meals": [
            {
                "meal_type": meal_type,
                "items": [{"food_id": near_food.id, "amount_g": 400}],
            }
            for meal_type in ("早餐", "午餐", "晚餐")
        ],
        "rationale": ["测试允许偏差"],
    }

    with patch(
        "app.services.agent_daily_meal_plans.structured_chat_completion",
        new=AsyncMock(side_effect=[_completion(draft), _completion(draft)]),
    ):
        result = await generate_daily_meal_artifact(
            db_session,
            user_id=user.id,
            conversation_id=conversation.id,
            run_id=run.id,
            user_message="给我安排今天三餐",
        )

    fit = result.artifact.payload_data["nutrition_fit"]
    assert fit["status"] == "acceptable_deviation"
    assert {item["metric"] for item in fit["deviations"]} == {
        "fat_energy_percent",
        "carb_energy_percent",
    }
    assert result.card["data"]["nutrition_fit"] == fit
    assert "少量营养指标" in result.reply
    reference = await create_agent_daily_meal_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        conversation_id=conversation.id,
        run_id=run.id,
    )
    proposal = await db_session.get(AgentProposal, reference.id)
    assert proposal.payload_data["after"]["nutrition_fit"] == fit
    assert any("核对偏差" in item for item in proposal.payload_data["safety_notes"])


@pytest.mark.asyncio
async def test_saved_diet_restriction_remains_a_hard_food_gate(db_session):
    user, _, _, foods = await _daily_context(db_session, "vegan-hard-gate")
    profile = await db_session.scalar(select(UserProfile).where(
        UserProfile.user_id == user.id
    ))
    profile.diet_restriction = "vegan"
    foods[0].diet_tags = ["vegan"]
    foods[1].diet_tags = ["vegan"]
    foods[2].diet_tags = []
    await db_session.commit()
    evidence = await collect_daily_meal_evidence(
        db_session,
        user_id=user.id,
        use_isolated_sessions=False,
    )
    draft = DailyMealDraft.model_validate({
        "meals": [
            {
                "meal_type": meal_type,
                "items": [{"food_id": foods[2].id, "amount_g": 150}],
            }
            for meal_type in ("早餐", "午餐", "晚餐")
        ],
        "rationale": [],
    })

    with pytest.raises(DailyMealPlanError) as raised:
        await canonicalize_daily_meal_draft(
            db_session,
            draft=draft,
            evidence=evidence,
        )

    assert raised.value.code == "food_candidate_invalid"


@pytest.mark.asyncio
async def test_two_invalid_generations_persist_safe_diagnostics_and_write_nothing(
    client,
    db_session,
):
    user, conversation, _, _ = await _daily_context(db_session, "invalid-twice")
    token = create_access_token(user.id)
    invalid = {"meals": "not-a-list", "rationale": []}
    model_call = AsyncMock(side_effect=[
        _completion(invalid),
        _completion(invalid),
    ])

    with (
        patch(
            "app.services.agent_runtime.resolve_intent_with_fallback",
            new=_deterministic_model_intent,
        ),
        patch(
            "app.services.agent_daily_meal_plans.structured_chat_completion",
            new=model_call,
        ),
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "conversation_id": conversation.id,
                "message": "结合我的情况安排今天怎么吃",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert "暂时未能形成有效结构" in body["reply"]
    assert "artifact" not in body
    run = await db_session.get(AgentRun, body["run_id"])
    assert run.execution_trace["termination_reason"] == "daily_meal_generation_invalid"
    evidence_calls = await db_session.scalar(select(func.count(AgentToolCall.id)).where(
        AgentToolCall.run_id == run.id,
        AgentToolCall.call_id.like("evidence:%"),
    ))
    model_calls = list((await db_session.execute(select(AgentToolCall).where(
        AgentToolCall.run_id == run.id,
        AgentToolCall.tool_name == "agent.daily_meal_generator",
    ).order_by(AgentToolCall.created_at))).scalars().all())
    assert evidence_calls == 6
    assert len(model_calls) == 2
    assert all(item.status == "failed" for item in model_calls)
    assert all(item.error_code == "schema_validation_failed" for item in model_calls)
    assert all("validation_paths" in item.result_data for item in model_calls)
    assert all("raw_output" not in item.result_data for item in model_calls)
    assert await db_session.scalar(select(func.count(AgentArtifact.id)).where(
        AgentArtifact.user_id == user.id
    )) == 0
    assert await db_session.scalar(select(func.count(AgentProposal.id)).where(
        AgentProposal.user_id == user.id
    )) == 0
    assert await db_session.scalar(select(func.count(MealLog.id)).where(
        MealLog.user_id == user.id
    )) == 0


@pytest.mark.asyncio
async def test_generation_missing_critical_profile_fields_only_clarifies(db_session):
    user, conversation, run, _ = await _daily_context(db_session, "missing")
    profile = await db_session.scalar(select(UserProfile).where(
        UserProfile.user_id == user.id
    ))
    profile.age = None
    profile.height_cm = None
    profile.onboarding_completed = False
    await db_session.commit()

    with patch(
        "app.services.agent_daily_meal_plans._extract_ephemeral_inputs",
        new=AsyncMock(side_effect=RuntimeError("model unavailable")),
    ):
        with pytest.raises(DailyMealPlanError) as raised:
            await generate_daily_meal_artifact(
                db_session,
                user_id=user.id,
                conversation_id=conversation.id,
                run_id=run.id,
                user_message="给我安排今天三餐",
            )

    assert raised.value.code == "daily_meal_critical_fields_missing"
    assert {"年龄", "身高"}.issubset(raised.value.missing_slots)
    assert await db_session.scalar(select(func.count(AgentArtifact.id)).where(
        AgentArtifact.user_id == user.id
    )) == 0


@pytest.mark.asyncio
async def test_daily_artifact_becomes_one_multi_meal_proposal_and_confirms_once(
    db_session,
):
    user, conversation, run, foods = await _daily_context(db_session, "happy")
    artifact = await _artifact(
        db_session,
        user=user,
        conversation=conversation,
        run=run,
        foods=foods,
    )

    reference = await create_agent_daily_meal_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        conversation_id=conversation.id,
        run_id=run.id,
    )
    await db_session.refresh(artifact)
    assert reference.proposal_type == "daily_meal_log_create_v1"
    assert artifact.status == "proposed"
    assert await db_session.scalar(select(func.count(MealLog.id)).where(
        MealLog.user_id == user.id
    )) == 0

    request = GenericProposalDecisionRequest(
        expected_version=1,
        client_request_id=f"daily-confirm-{run.id}",
    )
    result = await decide_agent_domain_proposal(
        db_session,
        user_id=user.id,
        proposal_id=reference.id,
        action="confirm",
        request=request,
    )
    assert result.status == "applied"
    assert result.result_data["meal_count"] == 3
    assert await db_session.scalar(select(func.count(MealLog.id)).where(
        MealLog.user_id == user.id
    )) == 3
    replay = await decide_agent_domain_proposal(
        db_session,
        user_id=user.id,
        proposal_id=reference.id,
        action="confirm",
        request=request,
    )
    assert replay.result_data == result.result_data
    assert await db_session.scalar(select(func.count(MealLog.id)).where(
        MealLog.user_id == user.id
    )) == 3


@pytest.mark.asyncio
async def test_artifact_card_action_creates_proposal_without_intent_model(
    client,
    db_session,
):
    user, conversation, source_run, foods = await _daily_context(
        db_session, "card-action"
    )
    artifact = await _artifact(
        db_session,
        user=user,
        conversation=conversation,
        run=source_run,
        foods=foods,
    )
    token = create_access_token(user.id)

    with (
        patch.object(settings, "AGENT_NUTRITION_PROPOSALS_ENABLED", True),
        patch(
            "app.services.agent_runtime.resolve_intent_with_fallback",
            new=AsyncMock(side_effect=AssertionError("card action used model")),
        ) as resolver,
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "conversation_id": conversation.id,
                "message": "保存这份方案",
                "artifact_action": {
                    "action": "save_as_proposal",
                    "artifact_id": artifact.id,
                    "expected_version": artifact.version,
                    "payload_fingerprint": artifact.payload_fingerprint,
                },
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["proposal"]["proposal_type"] == "daily_meal_log_create_v1"
    assert "artifact" not in body
    resolver.assert_not_awaited()
    await db_session.refresh(artifact)
    assert artifact.status == "proposed"
    proposal = await db_session.get(AgentProposal, body["proposal"]["id"])
    assert proposal is not None
    assert proposal.target_id == artifact.id
    assert await db_session.scalar(select(func.count(MealLog.id)).where(
        MealLog.user_id == user.id
    )) == 0


@pytest.mark.asyncio
async def test_natural_language_save_repairs_decision_misroute_and_creates_proposal(
    client,
    db_session,
):
    user, conversation, source_run, foods = await _daily_context(
        db_session, "natural-save"
    )
    await _artifact(
        db_session,
        user=user,
        conversation=conversation,
        run=source_run,
        foods=foods,
    )
    token = create_access_token(user.id)
    wrong_decision = IntentResolution(
        primary_intent="nutrition_today_query",
        intent_domain="nutrition",
        request_kind="proposal_decision",
        requested_effect="decide",
        change_requests=[ChangeRequest(
            resource="nutrition",
            operation="update",
            field_path="proposal.status",
            value="confirm",
        )],
        confidence=0.9,
    )
    repaired_save = IntentResolution(
        primary_intent="nutrition_today_query",
        intent_domain="nutrition",
        request_kind="mutation",
        requested_effect="create",
        change_requests=[ChangeRequest(
            resource="nutrition",
            operation="create",
            field_path="daily_meal_plan.save",
            target_reference="latest_active_artifact_in_conversation",
        )],
        confidence=0.98,
    )

    with (
        patch.object(settings, "AGENT_INTENT_MODEL_ENABLED", True),
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch.object(settings, "AGENT_NUTRITION_PROPOSALS_ENABLED", True),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(side_effect=[wrong_decision, repaired_save]),
        ) as invoke,
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "conversation_id": conversation.id,
                "message": "保存这份方案",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["proposal"]["proposal_type"] == "daily_meal_log_create_v1"
    assert invoke.await_count == 2
    assert invoke.await_args_list[0].kwargs["conversation_action_state"] == {
        "active_daily_meal_artifact_count": 1,
        "pending_proposal_count": 0,
    }
    run = await db_session.get(AgentRun, body["run_id"])
    assert run is not None
    assert run.request_kind == "mutation"
    assert run.intent_error_category == "semantic_artifact_save_requires_mutation"
    assert await db_session.scalar(select(func.count(MealLog.id)).where(
        MealLog.user_id == user.id
    )) == 0


@pytest.mark.asyncio
async def test_legacy_daily_artifact_without_fit_remains_proposable(db_session):
    user, conversation, run, foods = await _daily_context(db_session, "legacy-fit")
    artifact = await _artifact(
        db_session,
        user=user,
        conversation=conversation,
        run=run,
        foods=foods,
    )
    legacy_payload = copy.deepcopy(artifact.payload_data)
    legacy_payload.pop("nutrition_fit")
    artifact.payload_data = legacy_payload
    artifact.payload_fingerprint = canonical_fingerprint(legacy_payload)
    await db_session.commit()

    reference = await create_agent_daily_meal_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        conversation_id=conversation.id,
        run_id=run.id,
    )
    proposal = await db_session.get(AgentProposal, reference.id)
    assert proposal.payload_data["after"]["nutrition_fit"]["status"] == "within_target"


@pytest.mark.asyncio
async def test_daily_proposal_context_conflict_causes_zero_partial_writes(db_session):
    user, conversation, run, foods = await _daily_context(db_session, "conflict")
    await _artifact(
        db_session,
        user=user,
        conversation=conversation,
        run=run,
        foods=foods,
    )
    reference = await create_agent_daily_meal_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        conversation_id=conversation.id,
        run_id=run.id,
    )
    db_session.add(MealLog(
        user_id=user.id,
        logged_at=business_today(),
        meal_type="午餐",
    ))
    await db_session.commit()

    with pytest.raises(PlanProposalError) as raised:
        await decide_agent_domain_proposal(
            db_session,
            user_id=user.id,
            proposal_id=reference.id,
            action="confirm",
            request=GenericProposalDecisionRequest(
                expected_version=1,
                client_request_id=f"daily-conflict-{run.id}",
            ),
        )

    assert raised.value.code in {"artifact_context_changed", "daily_meal_conflict"}
    meals = list((await db_session.execute(select(MealLog).where(
        MealLog.user_id == user.id
    ))).scalars().all())
    assert [(item.meal_type, item.logged_at) for item in meals] == [
        ("午餐", business_today())
    ]


@pytest.mark.asyncio
async def test_revising_artifact_stales_existing_multi_meal_proposal(db_session):
    user, conversation, run, foods = await _daily_context(db_session, "revise")
    old_artifact = await _artifact(
        db_session,
        user=user,
        conversation=conversation,
        run=run,
        foods=foods,
    )
    reference = await create_agent_daily_meal_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        conversation_id=conversation.id,
        run_id=run.id,
    )
    await db_session.commit()

    with patch(
        "app.services.agent_daily_meal_plans.structured_chat_completion",
        new=AsyncMock(return_value=_completion(
            _target_compliant_draft(foods).model_dump(mode="json")
        )),
    ):
        replacement = await generate_daily_meal_artifact(
            db_session,
            user_id=user.id,
            conversation_id=conversation.id,
            run_id=run.id,
            user_message="调整这份方案的午餐",
            revise_latest=True,
        )
    await db_session.commit()

    await db_session.refresh(old_artifact)
    proposal = await db_session.get(AgentProposal, reference.id)
    assert old_artifact.status == "superseded"
    assert proposal.status == "stale"
    assert proposal.last_error_code == "artifact_superseded"
    assert replacement.artifact.status == "active"
    assert replacement.artifact.id != old_artifact.id
