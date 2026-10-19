"""Explanations must survive routing/presentation without authorizing changes."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from sqlalchemy import func, select

from app.config import settings
from app.models.agent import AgentProposal, AgentRun, AgentToolCall
from app.models.user import User
from app.models.workout import WorkoutPlan
from app.services.agent_intent import IntentResolution, resolve_intent, route_tools
from app.services.agent_intent_model import IntentRouteDecision, resolve_intent_with_fallback
from app.services.agent_jobs import process_agent_run
from app.services.agent_runtime import _normalize_unpersisted_proposal_result
from app.services.agent_trace import build_initial_execution_trace


EXPLANATION_MESSAGES = [
    "记录里 3 * 8、1.5 kg、C# 这些符号分别是啥？",
    "讲讲训练记录中的 kg 和 reps。",
    "饮食记录里的 kcal 代表啥？",
    "请解释体重记录的单位。",
    "记录内的单位看不懂，给我讲讲。",
    "请分五条说明如何用训练记录回顾一周训练",
    "请分五条说明如何用训练记录回顾一周训练，每条两三句话。只给说明，不修改任何数据。",
    "请用训练记录回顾这一周的方法做个说明。",
    "请解释怎样根据饮食记录了解一周的摄入。",
    "请说说体重记录能说明哪些变化。",
    "请讲讲餐次记录有什么用。",
    "请解释运动记录与健康记录的区别。",
]
MIXED_MESSAGES = [
    "记录里的 kg 是啥？顺便帮我记录体重75公斤。",
    "解释记录里的单位，再把周三卧推休息改为180秒。",
    "帮我记录体重75公斤，再解释记录里的单位。",
    "记录里的 kcal 代表啥？保存今天午餐鸡蛋100克。",
    "记录里的 kg 代表啥？记录今天体重75公斤。",
    "请用训练记录回顾一周，再帮我记录体重75公斤。",
    "请解释训练记录，再把周三卧推休息改为180秒。",
    "请根据饮食记录说明摄入，再保存今天午餐鸡蛋100克。",
    "请说明训练记录的作用，并新增一条饮食记录。",
]
EXPLANATION_REPLIES = [
    "“确认这个提案”的意思是：同意提案里的修改。确认后系统才会应用；这里只解释含义，没有执行。",
    "“确认这个提案”通常是待确认操作卡片上的按钮。你点确认后，系统才会应用修改。",
    "待确认提案是尚未应用的建议，并不代表计划已经修改。",
    "“拒绝这个提案”表示不采纳当前待确认的变更，不等于删除已有记录。",
    "按钮写着“请确认这个提案”，这里的确认表示同意。这里只解释按钮，不执行操作。",
    "`确认这个提案` 是操作指令的示例。用户确认后系统才会写入。",
    "例如‘这是待确认的提案’是在说明一种状态，不说明现在有提案。",
    "“拒绝提案”是界面上的一个操作按钮文案：它表示你可以对某个待确认的提案选择不通过。点了之后，那条提案不会被应用。",
    "是否有待确认提案、确认后具体会改什么，要以实际卡片为准。",
    "“确认这个提案”的意思通常是：系统已经生成了一份待确认提案，如果用户确认，才会应用。没有卡片时就没有操作对象。",
    "“确认这个提案”的含义是你可以确认变更，之后才会应用。",
]
PHANTOM_REPLIES = [
    "以上是待确认饮食提案，尚未写入。",
    "这是待确认的调整提案。",
    "我已生成待确认提案，请核对。",
    "已为你创建了一份待确认的提案。",
    "当前提案处于待确认状态。",
    "提案已经生成，等待确认。",
    "待确认的训练计划提案",
    "需要我确认提交这份提案吗？",
    "请确认这份提案。",
    "请确认“这个提案”。",
    "你可以确认当前提案。",
    "确认这份提案吗？",
    "“确认这个提案”是同意修改的意思。我已经为你生成待确认提案。",
    "确认只是表示同意；请确认这份提案。",
    "“确认这个提案”是按钮文案。已生成“待确认提案”，请核对。",
    "当前有待确认的提案。",
    "这份提案仍然等待确认。",
    "提案待确认，尚未写入。",
    "已生成提案，等待确认。",
    "### 训练调整提案（待确认）",
    "确认的意思是同意修改，我已生成待确认提案。",
    "确认的意思是：我已为你生成一份待确认提案。",
    "“确认这个提案”表示同意。是否需要确认这份提案？",
]


def _explanation_route(message):
    return IntentRouteDecision(
        intent_domain="general", request_kind="query", requested_effect="read",
        requested_output="answer", read_targets=[], decision_action=None,
        artifact_action=None, normalized_request=message, risk_level="low", confidence=0.99,
    )


@pytest.mark.parametrize("message", EXPLANATION_MESSAGES)
@pytest.mark.asyncio
async def test_record_noun_does_not_override_model_explanation(message):
    assert resolve_intent(message).request_kind == "query"
    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "synthetic-explanation-key"),
        patch("app.services.agent_intent_model._invoke_model_route",
              new=AsyncMock(return_value=_explanation_route(message))) as route,
        patch("app.services.agent_intent_model._invoke_model_change_extraction",
              new=AsyncMock()) as extraction,
    ):
        result = await resolve_intent_with_fallback(message, use_model=True)
    route.assert_awaited_once()
    extraction.assert_not_awaited()
    assert result.source == "model" and not result.understanding_failed
    assert result.resolution.request_kind == "query"
    assert result.resolution.change_requests == []
    assert route_tools(result.resolution) == []


@pytest.mark.parametrize("message", MIXED_MESSAGES)
@pytest.mark.asyncio
async def test_explanation_does_not_hide_a_separate_write(message):
    assert resolve_intent(message).request_kind == "mutation"
    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "synthetic-explanation-key"),
        patch("app.services.agent_intent_model._invoke_model_route",
              new=AsyncMock(return_value=_explanation_route(message))) as route,
    ):
        result = await resolve_intent_with_fallback(message, use_model=True)
    assert route.await_count == 2
    assert result.understanding_failed
    assert result.error_category == "semantic_mutation_structure_missing"
    assert route_tools(result.resolution) == []


@pytest.mark.parametrize("message", [
    "帮我记录中午的饮食", "记录里程5公里", "记录体重75公斤", "请记录一下今天的午餐",
    "请把今天的训练记录下来", "请将今天饮食记录为燕麦120克",
    "请帮我把体重记录到档案里", "请把运动记录一下",
    "请记录训练：卧推3组8次", "请把今天的训练记录保存下来",
])
def test_verbal_recording_is_not_mistaken_for_a_locative_noun(message):
    assert resolve_intent(message).request_kind == "mutation"


@pytest.mark.parametrize("message", [EXPLANATION_MESSAGES[0], EXPLANATION_MESSAGES[5]])
@pytest.mark.parametrize("failure", ["disabled", "unconfigured", "timeout"])
@pytest.mark.asyncio
async def test_explanation_cannot_bypass_an_unavailable_intent_model(failure, message):
    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "" if failure == "unconfigured" else "synthetic-key"),
        patch("app.services.agent_intent_model._invoke_model_route",
              new=AsyncMock(side_effect=TimeoutError)) as route,
    ):
        result = await resolve_intent_with_fallback(message, use_model=failure != "disabled")
    assert result.understanding_failed
    assert route_tools(result.resolution) == []
    if failure != "timeout":
        route.assert_not_awaited()


def _normalize(reply, **kwargs):
    trace = build_initial_execution_trace(IntentResolution(
        primary_intent="general_qa", confidence=0.99,
    ), []).model_copy(update={"terminal_action": kwargs.pop("terminal_action", "answer")})
    return _normalize_unpersisted_proposal_result(
        reply=reply, execution_trace=trace, proposal_reference=None, **kwargs,
    )


@pytest.mark.parametrize("reply", EXPLANATION_REPLIES)
def test_generic_or_quoted_explanation_is_not_a_phantom_proposal(reply):
    normalized, trace = _normalize(reply)
    assert normalized == reply
    assert "proposal_creation_rejected_safe_answer" not in trace.mode_reasons


@pytest.mark.parametrize("reply", PHANTOM_REPLIES)
def test_current_proposal_claims_and_invitations_still_fail_closed(reply):
    normalized, trace = _normalize(reply)
    assert normalized != reply
    assert "proposal_creation_rejected_safe_answer" in trace.mode_reasons


@pytest.mark.parametrize("guard", [{"proposal_expected": True}, {"terminal_action": "proposal"}])
def test_explanation_never_hides_a_failed_proposal_creation(guard):
    reply = EXPLANATION_REPLIES[0]
    normalized, trace = _normalize(reply, **guard)
    assert normalized != reply
    assert trace.terminal_action == "answer"


@pytest.mark.parametrize("mode", ["chat", "async_run"])
@pytest.mark.parametrize(("message", "answer"), [
    (EXPLANATION_MESSAGES[0], "3 * 8 通常表示3组、每组8次；kg是千克。C#可能是编程语言名，要看上下文。"),
    ("“确认这个提案”这句话是什么意思？我没让你执行。", EXPLANATION_REPLIES[0]),
    (EXPLANATION_MESSAGES[5], "可以先核对训练日期，再比较动作和每组记录。这里只说明回顾方法，没有读取你的实际记录。"),
    (EXPLANATION_MESSAGES[6], "1. 核对训练日期。\n2. 按动作整理组数与次数。\n3. 查看每组重量。\n4. 留意休息记录。\n5. 结合实际感受回顾。"),
])
@pytest.mark.asyncio
async def test_explanation_survives_real_handlers_and_history_without_writes(
    client, db_session, session_factory, mode, message, answer,
):
    from uuid import uuid4
    email = f"explanation-{uuid4().hex}@example.com"
    registration = await client.post("/api/v1/auth/register", json={
        "email": email, "password": "synthetic-explanation-password",
    })
    assert registration.status_code == 201
    headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}
    user = await db_session.scalar(select(User).where(User.email == email))
    model = FakeMessagesListChatModel(responses=[AIMessage(content=answer)])
    with (
        patch.object(settings, "AGENT_ENABLED", True),
        patch.object(settings, "AGENT_INTENT_MODEL_ENABLED", True),
        patch.object(settings, "DEEPSEEK_API_KEY", "synthetic-explanation-key"),
        patch("app.services.agent_intent_model._invoke_model_route",
              new=AsyncMock(return_value=_explanation_route(message))) as route,
        patch("app.services.agent_intent_model._invoke_model_change_extraction",
              new=AsyncMock()) as extraction,
        patch("app.services.agent_runtime._build_model", return_value=model),
    ):
        if mode == "chat":
            response = await client.post("/api/v1/agent/chat", headers=headers, json={"message": message})
            assert response.status_code == 200
        else:
            response = await client.post("/api/v1/agent/runs", headers=headers, json={
                "message": message, "client_request_id": uuid4().hex,
            })
            assert response.status_code == 202
            run = await db_session.get(AgentRun, response.json()["run_id"])
            now = datetime.now(timezone.utc)
            run.status, run.attempt_count = "running", 1
            run.processing_started_at = now
            run.lease_expires_at = now + timedelta(seconds=settings.AGENT_RUN_LEASE_SECONDS)
            await db_session.commit()
            await process_agent_run(session_factory, run.id)
        run_id = response.json()["run_id"]
        result = (await client.get(f"/api/v1/agent/runs/{run_id}", headers=headers)).json()
    route.assert_awaited_once()
    extraction.assert_not_awaited()
    assert result["reply"] == answer
    assert result["status"] == "completed"
    assert result["tool_allowlist"] == []
    assert result["intent_error_category"] is None
    assert "proposal_creation_rejected_safe_answer" not in result["execution_trace"]["mode_reasons"]
    history = await client.get(
        f"/api/v1/agent/conversations/{response.json()['conversation_id']}/messages", headers=headers,
    )
    assert history.status_code == 200
    assert any(item["role"] == "assistant" and item["content"] == answer for item in history.json())
    assert await db_session.scalar(select(func.count()).select_from(AgentToolCall).where(AgentToolCall.run_id == run_id)) == 0
    for model_type in (WorkoutPlan, AgentProposal):
        assert await db_session.scalar(select(func.count()).select_from(model_type).where(model_type.user_id == user.id)) == 0
