from unittest.mock import patch

import bcrypt
import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import PrivateAttr

from app.models.profile import UserProfile
from app.models.user import User
from app.services.agent_runtime import _audit_result_summary, invoke_langchain_agent
from app.services.agent_tool_registry_shadow_trace import (
    ToolRegistryShadowSession,
)


class ToolAwareFakeChatModel(FakeMessagesListChatModel):
    _bound_tool_names: list[str] = PrivateAttr(default_factory=list)

    def bind_tools(self, tools, **_kwargs):
        self._bound_tool_names = [item.name for item in tools]
        return self


@pytest.mark.asyncio
async def test_shadow_session_does_not_change_direct_agent_messages():
    async def invoke(model, shadow_session=None):
        with patch(
            "app.services.agent_runtime._build_model",
            return_value=model,
        ):
            return await invoke_langchain_agent(
                object(),
                user_id="direct-shadow-parity",
                history=[],
                user_message="我的训练目标是什么？",
                tool_allowlist=["profile.get_summary"],
                shadow_session=shadow_session,
            )

    baseline = await invoke(ToolAwareFakeChatModel(
        responses=[AIMessage(content="请先完善训练目标资料。")]
    ))
    session = ToolRegistryShadowSession(sample_bucket=11)
    shadowed = await invoke(
        ToolAwareFakeChatModel(
            responses=[AIMessage(content="请先完善训练目标资料。")]
        ),
        session,
    )

    def snapshot(result):
        return [
            {
                "type": message.type,
                "content": message.content,
                "tool_calls": getattr(message, "tool_calls", None),
            }
            for message in result["messages"]
        ]

    assert snapshot(shadowed) == snapshot(baseline)
    assert session.checks["constructed_tools"].status == "match"
    assert session.checks["argument_schema"].status == "match"


@pytest.mark.asyncio
async def test_real_langchain_loop_receives_only_allowlisted_tool(db_session):
    db_session.add(User(
        id="runtime-user-1",
        email="runtime-user-1@example.com",
        password_hash=bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode(),
    ))
    await db_session.commit()
    db_session.add(UserProfile(
        user_id="runtime-user-1",
        primary_goal="改善体能",
        injuries=[],
        chronic_conditions=[],
    ))
    await db_session.commit()

    model = ToolAwareFakeChatModel(responses=[
        AIMessage(
            content="",
            tool_calls=[{
                "name": "profile_get_summary",
                "args": {},
                "id": "runtime-call-1",
                "type": "tool_call",
            }],
        ),
        AIMessage(content="你当前的训练目标是改善体能。"),
    ])

    with patch("app.services.agent_runtime._build_model", return_value=model):
        result = await invoke_langchain_agent(
            db_session,
            user_id="runtime-user-1",
            history=[],
            user_message="我的训练目标是什么？",
            tool_allowlist=["profile.get_summary"],
        )

    assert model._bound_tool_names == ["profile_get_summary"]
    tool_messages = [
        message for message in result["messages"]
        if isinstance(message, ToolMessage)
    ]
    assert len(tool_messages) == 1
    assert "改善体能" in str(tool_messages[0].content)
    assert result["messages"][-1].content == "你当前的训练目标是改善体能。"


def test_health_tool_audit_stores_counts_not_sensitive_values():
    summary = _audit_result_summary(
        "health.get_screening_summary",
        {
            "found": True,
            "injuries": ["膝关节"],
            "chronic_conditions": ["高血压"],
            "screening_completed": True,
        },
    )

    assert summary == {
        "found": True,
        "injury_count": 1,
        "chronic_condition_count": 1,
        "screening_completed": True,
    }
    assert "膝关节" not in str(summary)
    assert "高血压" not in str(summary)


@pytest.mark.asyncio
async def test_resolved_query_and_subtasks_are_passed_to_agent_execution(db_session):
    model = ToolAwareFakeChatModel(responses=[
        AIMessage(content="我会按消解后的目标回答。"),
    ])

    with patch("app.services.agent_runtime._build_model", return_value=model):
        result = await invoke_langchain_agent(
            db_session,
            user_id="runtime-understanding-user",
            history=[],
            user_message="那这个呢？",
            resolved_query="比较深蹲最近四周的重量趋势",
            subtasks=["读取深蹲历史", "比较最近四周重量趋势"],
            tool_allowlist=[],
        )

    human_message = next(
        item for item in result["messages"] if isinstance(item, HumanMessage)
    )
    assert "比较深蹲最近四周的重量趋势" in human_message.content
    assert "读取深蹲历史" in human_message.content
