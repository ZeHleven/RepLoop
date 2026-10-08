from __future__ import annotations

from app.services.business_clock import business_today

from contextlib import nullcontext
from datetime import date
from unittest.mock import AsyncMock, patch

import bcrypt
import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.config import settings
from app.models.agent import AgentConversation, AgentProposal, AgentRun
from app.models.food import Food
from app.models.meal import MealItem, MealLog
from app.models.profile import UserProfile, WeightLog
from app.models.user import User
from app.schemas.plan_management_proposal import GenericProposalDecisionRequest
from app.services.agent_domain_proposals import (
    create_agent_meal_create_proposal,
    create_agent_profile_update_proposal,
    create_agent_weight_proposal,
    decide_agent_domain_proposal,
    read_owned_domain_proposal,
)
from app.services.agent_intent import (
    ChangeRequest,
    IntentResolution,
    IntentResolverOutcome,
    resolve_intent,
)
from app.services.auth import create_access_token
from app.services.agent_jobs import process_agent_run
from app.services.plan_management_proposals import PlanProposalError


async def _context(db_session, suffix: str):
    user = User(
        id=f"domain-user-{suffix}",
        email=f"domain-{suffix}@example.com",
        password_hash=bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode(),
    )
    db_session.add(user)
    await db_session.flush()
    profile = UserProfile(
        user_id=user.id,
        age=30,
        height_cm=170,
        weight_kg=65,
        training_days_per_week=3,
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
    await db_session.commit()
    return user, profile, conversation, run


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["chat_model", "queued_model", "persisted"])
@pytest.mark.parametrize("has_pending", [False, True])
async def test_proposal_consent_guard_covers_jobs_and_persisted_decisions(
    client, db_session, session_factory, entry, has_pending,
):
    user, profile, conversation, run = await _context(
        db_session, f"consent-{entry}-{has_pending}"
    )
    user_id, conversation_id = user.id, conversation.id
    reference = None
    if has_pending:
        reference = await create_agent_weight_proposal(
            db_session, enabled=True, user_id=user_id,
            conversation_id=conversation_id, run_id=run.id,
            changes=[ChangeRequest(
                resource="profile", operation="create",
                field_path="weight_log.weight_kg", value=67.5,
            )],
        )
    resolution = IntentResolution(
        primary_intent="general_qa", intent_domain="general",
        request_kind="proposal_decision", requested_effect="decide",
        resolved_query="确认这个提案", confidence=0.99,
        change_requests=[ChangeRequest(
            resource="general", operation="update",
            field_path="proposal.status", value="confirm",
        )],
    )
    message = "不要确认这个提案"
    if entry == "persisted":
        # An older run can persist a decision while asking which proposal to
        # handle. Selecting its object is NOT fresh permission to execute it.
        conversation.pending_clarification = {
            **resolution.model_dump(mode="json"),
            "missing_slots": ["要处理的待确认提案"],
            "clarification_question": "要处理哪个提案？",
        }
        message = "这一个"
    await db_session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(user_id)}"}
    resolver_patch = (
        nullcontext() if entry == "persisted" else patch(
            "app.services.agent_runtime.resolve_intent_with_fallback",
            new=AsyncMock(return_value=IntentResolverOutcome(
                resolution=resolution, source="model",
            )),
        )
    )
    with patch.object(settings, "AGENT_INTENT_MODEL_ENABLED", False), resolver_patch:
        if entry == "queued_model":
            response = await client.post(
                "/api/v1/agent/runs", headers=headers,
                json={"message": message, "conversation_id": conversation_id,
                      "client_request_id": f"consent-queued-{has_pending}"},
            )
            assert response.status_code == 202
            result_run_id = response.json()["run_id"]
            queued = await db_session.get(AgentRun, result_run_id)
            queued.status = "running"
            queued.attempt_count = 1
            await db_session.commit()
            await process_agent_run(session_factory, result_run_id)
            polled = await client.get(f"/api/v1/agent/runs/{result_run_id}", headers=headers)
            assert polled.json()["status"] == "completed"
        else:
            response = await client.post(
                "/api/v1/agent/chat", headers=headers,
                json={"message": message, "conversation_id": conversation_id},
            )
            assert response.status_code == 200
            result_run_id = response.json()["run_id"]
    db_session.expire_all()
    result_run = await db_session.get(AgentRun, result_run_id)
    assert result_run.execution_trace["termination_reason"] == "proposal_decision_not_explicit"
    await db_session.refresh(profile)
    assert profile.weight_kg == 65
    assert await db_session.scalar(select(WeightLog.id).where(WeightLog.user_id == user_id)) is None
    if reference:
        proposal = await db_session.get(AgentProposal, reference.id)
        assert proposal.status == "pending_confirmation"
        assert proposal.version == 1
        # A fresh explicit decision still works after the safe clarification;
        # neither model failure nor stale state can silently change its action.
        with patch.object(settings, "AGENT_INTENT_MODEL_ENABLED", False):
            confirmed = await client.post(
                "/api/v1/agent/chat", headers=headers,
                json={"message": "确认这个提案", "conversation_id": conversation_id},
            )
            repeated = await client.post(
                "/api/v1/agent/chat", headers=headers,
                json={"message": "确认这个提案", "conversation_id": conversation_id},
            )
        assert confirmed.status_code == repeated.status_code == 200
        assert "已确认并应用提案" in confirmed.json()["reply"]
        logs = list((await db_session.scalars(
            select(WeightLog).where(WeightLog.user_id == user_id)
        )).all())
        assert [log.weight_kg for log in logs] == [67.5]


@pytest.mark.asyncio
async def test_cancelling_query_clarification_keeps_existing_proposal_pending(
    client, db_session,
):
    user, profile, conversation, run = await _context(db_session, "cancel-query")
    reference = await create_agent_weight_proposal(
        db_session, enabled=True, user_id=user.id,
        conversation_id=conversation.id, run_id=run.id,
        changes=[ChangeRequest(
            resource="profile", operation="create",
            field_path="weight_log.weight_kg", value=67.5,
        )],
    )
    conversation.pending_clarification = {
        **resolve_intent("查看我的体重记录").model_dump(mode="json"),
        "missing_slots": ["时间范围"],
        "clarification_question": "查看哪个时间范围？",
    }
    await db_session.commit()
    answer = AsyncMock(return_value={
        "messages": [AIMessage(content="已取消本次查询。")],
    })
    with (
        patch.object(settings, "AGENT_INTENT_MODEL_ENABLED", False),
        patch("app.services.agent_runtime.invoke_langchain_agent", new=answer),
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            headers={"Authorization": f"Bearer {create_access_token(user.id)}"},
            json={"message": "取消", "conversation_id": conversation.id},
        )

    assert response.status_code == 200
    answer.assert_awaited_once()
    assert response.json()["reply"] == "已取消本次查询。"
    await db_session.refresh(conversation)
    assert conversation.pending_clarification == {}
    proposal = await db_session.get(AgentProposal, reference.id)
    await db_session.refresh(proposal)
    assert proposal.status == "pending_confirmation"
    assert proposal.version == 1
    await db_session.refresh(profile)
    assert profile.weight_kg == 65
    assert await db_session.scalar(select(WeightLog.id).where(WeightLog.user_id == user.id)) is None


def _decision(run_id: str) -> GenericProposalDecisionRequest:
    return GenericProposalDecisionRequest(
        expected_version=1,
        client_request_id=f"domain-decision-{run_id}",
    )


@pytest.mark.asyncio
async def test_profile_update_is_proposed_then_applied_atomically(db_session):
    user, profile, conversation, run = await _context(db_session, "profile")
    reference = await create_agent_profile_update_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        conversation_id=conversation.id,
        run_id=run.id,
        changes=[ChangeRequest(
            resource="profile",
            operation="update",
            field_path="profile.training_days_per_week",
            value=4,
        )],
    )
    await db_session.refresh(profile)
    assert profile.training_days_per_week == 3
    read = await read_owned_domain_proposal(
        db_session, user_id=user.id, proposal_id=reference.id
    )
    assert read is not None
    assert read.payload["before"]["training_days_per_week"] == 3
    assert read.payload["after"]["training_days_per_week"] == 4

    result = await decide_agent_domain_proposal(
        db_session,
        user_id=user.id,
        proposal_id=reference.id,
        action="confirm",
        request=_decision(run.id),
    )
    await db_session.refresh(profile)
    assert result.status == "applied"
    assert profile.training_days_per_week == 4

    replay = await decide_agent_domain_proposal(
        db_session,
        user_id=user.id,
        proposal_id=reference.id,
        action="confirm",
        request=_decision(run.id),
    )
    assert replay.result_data == result.result_data


@pytest.mark.asyncio
async def test_weight_proposal_creates_one_log_and_updates_bmi(db_session):
    user, profile, conversation, run = await _context(db_session, "weight")
    reference = await create_agent_weight_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        conversation_id=conversation.id,
        run_id=run.id,
        changes=[ChangeRequest(
            resource="profile",
            operation="create",
            field_path="weight_log.weight_kg",
            value=67.5,
        )],
    )
    assert await db_session.scalar(select(WeightLog.id).where(WeightLog.user_id == user.id)) is None

    result = await decide_agent_domain_proposal(
        db_session,
        user_id=user.id,
        proposal_id=reference.id,
        action="confirm",
        request=_decision(run.id),
    )
    logs = list((await db_session.execute(
        select(WeightLog).where(WeightLog.user_id == user.id)
    )).scalars().all())
    await db_session.refresh(profile)
    assert result.status == "applied"
    assert [item.weight_kg for item in logs] == [67.5]
    assert profile.weight_kg == 67.5
    assert profile.bmi is not None


@pytest.mark.asyncio
async def test_food_library_meal_proposal_recalculates_server_nutrition(db_session):
    user, _, conversation, run = await _context(db_session, "meal")
    food = Food(
        id="domain-food",
        name_zh="鸡胸肉-服务端营养重算",
        category="肉类",
        calories_per_100g=165,
        protein_g=31,
        carbs_g=0,
        fat_g=3.6,
        is_active=True,
    )
    db_session.add(food)
    await db_session.commit()
    reference = await create_agent_meal_create_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        conversation_id=conversation.id,
        run_id=run.id,
        changes=[ChangeRequest(
            resource="nutrition",
            operation="create",
            field_path="meal",
            value={
                "logged_at": business_today().isoformat(),
                "meal_type": "午餐",
                "items": [{
                    "food_id": food.id,
                    "food_name": "伪造名称",
                    "amount_g": 200,
                    "calories": 1,
                    "protein_g": 1,
                    "carbs_g": 1,
                    "fat_g": 1,
                }],
            },
        )],
    )
    assert await db_session.scalar(select(MealLog.id).where(MealLog.user_id == user.id)) is None

    await decide_agent_domain_proposal(
        db_session,
        user_id=user.id,
        proposal_id=reference.id,
        action="confirm",
        request=_decision(run.id),
    )
    meal = await db_session.scalar(select(MealLog).where(MealLog.user_id == user.id))
    item = await db_session.scalar(select(MealItem).where(MealItem.meal_id == meal.id))
    assert item.food_name == "鸡胸肉-服务端营养重算"
    assert item.calories == 330
    assert item.protein_g == 62


@pytest.mark.asyncio
async def test_meal_proposal_requires_explicit_meal_type(db_session):
    user, _, conversation, run = await _context(db_session, "meal-type-required")
    food = Food(
        id="domain-food-meal-type",
        name_zh="鸡胸肉-餐次校验",
        category="肉类",
        calories_per_100g=165,
        protein_g=31,
        carbs_g=0,
        fat_g=3.6,
        is_active=True,
    )
    db_session.add(food)
    await db_session.commit()

    with pytest.raises(PlanProposalError) as captured:
        await create_agent_meal_create_proposal(
            db_session,
            enabled=True,
            user_id=user.id,
            conversation_id=conversation.id,
            run_id=run.id,
            changes=[ChangeRequest(
                resource="nutrition",
                operation="create",
                field_path="meal",
                value={
                    "logged_at": business_today().isoformat(),
                    "items": [{"food_id": food.id, "amount_g": 150}],
                },
            )],
        )

    assert captured.value.code == "proposal_change_incomplete"
    assert "餐次" in captured.value.message
    assert await db_session.scalar(select(AgentProposal.id).where(
        AgentProposal.conversation_id == conversation.id,
    )) is None


@pytest.mark.asyncio
async def test_changed_food_data_stales_meal_proposal_without_writing(db_session):
    user, _, conversation, run = await _context(db_session, "meal-stale")
    food = Food(
        id="domain-food-stale",
        name_zh="燕麦",
        category="谷物",
        calories_per_100g=380,
        protein_g=13,
        carbs_g=68,
        fat_g=7,
        is_active=True,
    )
    db_session.add(food)
    await db_session.commit()
    reference = await create_agent_meal_create_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        conversation_id=conversation.id,
        run_id=run.id,
        changes=[ChangeRequest(
            resource="nutrition",
            operation="create",
            field_path="meal",
            value={
                "logged_at": business_today().isoformat(),
                "meal_type": "早餐",
                "items": [{"food_id": food.id, "amount_g": 100}],
            },
        )],
    )
    food.calories_per_100g = 400
    await db_session.commit()

    with pytest.raises(PlanProposalError, match="营养数据已变化"):
        await decide_agent_domain_proposal(
            db_session,
            user_id=user.id,
            proposal_id=reference.id,
            action="confirm",
            request=_decision(run.id),
        )

    assert await db_session.scalar(
        select(MealLog.id).where(MealLog.user_id == user.id)
    ) is None
    proposal = await db_session.scalar(
        select(AgentProposal).where(AgentProposal.id == reference.id)
    )
    assert proposal.status == "stale"
    assert proposal.last_error_code == "proposal_base_changed"


@pytest.mark.asyncio
async def test_chat_creates_and_confirms_one_meal_proposal_once(
    client,
    db_session,
):
    user, _, conversation, _ = await _context(db_session, "meal-chat")
    chicken = Food(
        id="domain-food-chat-chicken",
        name_zh="鸡胸肉",
        category="肉类",
        calories_per_100g=165,
        protein_g=31,
        carbs_g=0,
        fat_g=3.6,
        is_active=True,
    )
    rice = Food(
        id="domain-food-chat-rice",
        name_zh="杂粮饭",
        category="谷物",
        calories_per_100g=130,
        protein_g=3,
        carbs_g=28,
        fat_g=1,
        is_active=True,
    )
    db_session.add_all([chicken, rice])
    await db_session.commit()
    resolution = IntentResolution(
        primary_intent="nutrition_today_query",
        intent_domain="nutrition",
        request_kind="mutation",
        requested_effect="create",
        change_requests=[ChangeRequest(
            resource="nutrition",
            operation="create",
            field_path="meal",
            value={
                "logged_at": business_today().isoformat(),
                "meal_type": "晚餐",
                "items": [
                    {"food_name": "鸡胸肉", "amount_g": 150},
                    {"food_name": "杂粮饭", "amount_g": 100},
                ],
            },
        )],
        resolved_query="记录今天晚餐的鸡胸肉150克和杂粮饭100克",
        subtasks=["校验变更并形成待确认提案"],
        confidence=0.99,
    )
    token = create_access_token(user.id)
    headers = {"Authorization": f"Bearer {token}"}

    with (
        patch.object(settings, "AGENT_NUTRITION_PROPOSALS_ENABLED", True),
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(return_value=resolution),
        ),
    ):
        created = await client.post(
            "/api/v1/agent/chat",
            headers=headers,
            json={
                "message": "把今天晚餐记录为鸡胸肉150克、杂粮饭100克",
                "conversation_id": conversation.id,
            },
        )

    assert created.status_code == 200
    assert created.json()["proposal"]["proposal_type"] == "meal_log_create_v1"
    assert created.json()["proposal"]["status"] == "pending_confirmation"
    proposal = await db_session.get(
        AgentProposal,
        created.json()["proposal"]["id"],
    )
    assert proposal is not None
    assert proposal.conversation_id == conversation.id
    assert await db_session.scalar(
        select(MealLog.id).where(MealLog.user_id == user.id)
    ) is None

    with (
        patch.object(settings, "AGENT_NUTRITION_PROPOSALS_ENABLED", True),
        patch.object(settings, "AGENT_INTENT_MODEL_ENABLED", False),
    ):
        confirmed = await client.post(
            "/api/v1/agent/chat",
            headers=headers,
            json={
                "message": "确认提交这份提案",
                "conversation_id": conversation.id,
            },
        )
        replay = await client.post(
            "/api/v1/agent/chat",
            headers=headers,
            json={
                "message": "确认提交这份提案",
                "conversation_id": conversation.id,
            },
        )

    assert confirmed.status_code == 200
    assert "已确认并应用提案" in confirmed.json()["reply"]
    assert replay.status_code == 200
    assert "还没有待确认提案" in replay.json()["reply"]
    meals = list((await db_session.execute(
        select(MealLog).where(MealLog.user_id == user.id)
    )).scalars().all())
    assert len(meals) == 1
    items = list((await db_session.execute(
        select(MealItem).where(MealItem.meal_id == meals[0].id)
    )).scalars().all())
    assert {item.food_name for item in items} == {"鸡胸肉", "杂粮饭"}


@pytest.mark.asyncio
async def test_chat_natural_language_rejects_meal_proposal_without_writing(
    client,
    db_session,
):
    user, _, conversation, run = await _context(db_session, "meal-chat-reject")
    food = Food(
        id="domain-food-chat-reject",
        name_zh="鸡胸肉",
        category="肉类",
        calories_per_100g=165,
        protein_g=31,
        carbs_g=0,
        fat_g=3.6,
        is_active=True,
    )
    db_session.add(food)
    await db_session.commit()
    reference = await create_agent_meal_create_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        conversation_id=conversation.id,
        run_id=run.id,
        changes=[ChangeRequest(
            resource="nutrition",
            operation="create",
            field_path="meal",
            value={
                "logged_at": business_today().isoformat(),
                "meal_type": "晚餐",
                "items": [{"food_id": food.id, "amount_g": 150}],
            },
        )],
    )
    token = create_access_token(user.id)

    with patch.object(settings, "AGENT_INTENT_MODEL_ENABLED", False):
        response = await client.post(
            "/api/v1/agent/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "message": "拒绝这份饮食提案",
                "conversation_id": conversation.id,
            },
        )

    assert response.status_code == 200
    assert "已拒绝这份提案" in response.json()["reply"]
    proposal = await db_session.get(AgentProposal, reference.id)
    assert proposal is not None
    await db_session.refresh(proposal)
    assert proposal.status == "rejected"
    assert await db_session.scalar(
        select(MealLog.id).where(MealLog.user_id == user.id)
    ) is None


@pytest.mark.asyncio
async def test_chat_complete_meal_write_reports_disabled_feature_without_draft(
    client,
    db_session,
):
    user, _, conversation, _ = await _context(db_session, "meal-chat-disabled")
    resolution = IntentResolution(
        primary_intent="nutrition_today_query",
        intent_domain="nutrition",
        request_kind="mutation",
        requested_effect="create",
        change_requests=[ChangeRequest(
            resource="nutrition",
            operation="create",
            field_path="meal",
            value={
                "logged_at": business_today().isoformat(),
                "meal_type": "晚餐",
                "items": [{
                    "food_name": "未收录测试食品",
                    "amount_g": 100,
                }],
            },
        )],
        resolved_query="记录今天晚餐",
        subtasks=["校验变更并形成待确认提案"],
        confidence=0.99,
    )
    token = create_access_token(user.id)

    with (
        patch.object(settings, "AGENT_NUTRITION_PROPOSALS_ENABLED", False),
        patch(
            "app.services.agent_runtime.resolve_intent_with_fallback",
            new=AsyncMock(return_value=IntentResolverOutcome(
                resolution=resolution,
                source="model",
                attempt_count=1,
            )),
        ),
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "message": "记录这份晚餐",
                "conversation_id": conversation.id,
            },
        )

    assert response.status_code == 200
    assert response.json()["reply"] == (
        "饮食记录提案功能暂未开启。当前数据未作修改。"
    )
    assert "proposal" not in response.json()
    assert await db_session.scalar(select(AgentProposal.id).where(
        AgentProposal.conversation_id == conversation.id,
    )) is None


@pytest.mark.asyncio
async def test_agent_cannot_invent_nutrition_for_unknown_food(db_session):
    user, _, conversation, run = await _context(
        db_session, "meal-unknown-food"
    )

    with pytest.raises(PlanProposalError) as captured:
        await create_agent_meal_create_proposal(
            db_session,
            enabled=True,
            user_id=user.id,
            conversation_id=conversation.id,
            run_id=run.id,
            changes=[ChangeRequest(
                resource="nutrition",
                operation="create",
                field_path="meal",
                value={
                    "logged_at": "today",
                    "meal_type": "晚餐",
                    "items": [{
                        "food_name": "模型虚构食品",
                        "amount_g": 100,
                        "calories": 1,
                        "protein_g": 999,
                        "carbs_g": 999,
                        "fat_g": 999,
                    }],
                },
            )],
        )

    assert captured.value.code == "proposal_target_not_found"
    assert "尚未收录" in captured.value.message
    assert await db_session.scalar(select(AgentProposal.id).where(
        AgentProposal.conversation_id == conversation.id,
    )) is None


@pytest.mark.asyncio
async def test_chat_model_failure_does_not_persist_rule_extracted_write_slots(
    client,
    db_session,
):
    user, _, conversation, _ = await _context(
        db_session, "meal-chat-model-failure"
    )
    token = create_access_token(user.id)

    with patch.object(settings, "AGENT_INTENT_MODEL_ENABLED", False):
        response = await client.post(
            "/api/v1/agent/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "message": "把今天晚餐记录为鸡胸肉150克、杂粮饭100克",
                "conversation_id": conversation.id,
            },
        )

    assert response.status_code == 200
    assert response.json()["reply"] == (
        "这次暂时没能处理好你的请求，请稍后重试。"
        "本次没有读取或修改你的业务数据。"
    )
    assert "proposal" not in response.json()
    run = await db_session.get(AgentRun, response.json()["run_id"])
    assert run is not None
    assert run.understanding_version == "v6"
    assert run.change_requests == []
    assert run.error_code == "intent_understanding_unavailable"
    await db_session.refresh(conversation)
    assert conversation.pending_clarification == {}
    assert await db_session.scalar(select(AgentProposal.id).where(
        AgentProposal.conversation_id == conversation.id,
    )) is None


@pytest.mark.asyncio
async def test_chat_partial_meal_structure_is_filled_by_model_across_turns(
    client,
    db_session,
):
    user, _, conversation, _ = await _context(
        db_session, "meal-chat-multiturn"
    )
    food = Food(
        id="domain-food-chat-multiturn",
        name_zh="多轮鸡胸肉",
        category="肉类",
        calories_per_100g=165,
        protein_g=31,
        carbs_g=0,
        fat_g=3.6,
        is_active=True,
    )
    db_session.add(food)
    await db_session.commit()
    partial = IntentResolution(
        primary_intent="nutrition_today_query",
        intent_domain="nutrition",
        request_kind="mutation",
        requested_effect="create",
        change_requests=[ChangeRequest(
            resource="nutrition",
            operation="create",
            field_path="meal",
            value={
                "logged_at": "today",
                "meal_type": "晚餐",
                "items": [{"food_name": "多轮鸡胸肉"}],
            },
        )],
        resolved_query="记录今天晚餐的多轮鸡胸肉",
        confidence=0.96,
    )
    complete = partial.model_copy(update={
        "change_requests": [ChangeRequest(
            resource="nutrition",
            operation="create",
            field_path="meal",
            value={
                "logged_at": "today",
                "meal_type": "晚餐",
                "items": [{
                    "food_name": "多轮鸡胸肉",
                    "amount_g": 150,
                }],
            },
        )],
        "resolved_query": "记录今天晚餐的多轮鸡胸肉150克",
        "confidence": 0.99,
    })
    token = create_access_token(user.id)
    resolver = AsyncMock(side_effect=[partial, complete])

    with (
        patch.object(settings, "AGENT_NUTRITION_PROPOSALS_ENABLED", True),
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=resolver,
        ),
    ):
        first = await client.post(
            "/api/v1/agent/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "message": "帮我记录今天晚餐的多轮鸡胸肉",
                "conversation_id": conversation.id,
            },
        )
        second = await client.post(
            "/api/v1/agent/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "message": "150克",
                "conversation_id": conversation.id,
            },
        )

    assert first.status_code == 200
    assert first.json()["reply"] == "请补充多轮鸡胸肉的克数。"
    assert "proposal" not in first.json()
    assert second.status_code == 200
    assert second.json()["proposal"]["proposal_type"] == "meal_log_create_v1"
    assert resolver.await_count == 2
    pending = resolver.await_args_list[1].kwargs["pending_clarification"]
    assert pending["understanding_version"] == "v6"
    assert pending["change_requests"][0]["value"]["items"] == [
        {"food_name": "多轮鸡胸肉"}
    ]
    await db_session.refresh(conversation)
    assert conversation.pending_clarification == {}
