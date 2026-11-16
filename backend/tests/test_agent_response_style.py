"""Exercise actual answer invocations; fakes replace model I/O, not routing."""
from unittest.mock import patch

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from app.schemas.agent_planning import FinalizationDecision
from app.services.agent_intent import IntentResolution
from app.services.agent_planner import ModelPlanningPolicy
from app.services.agent_runtime import _clarification_reply, invoke_langchain_agent


class CapturePrompt(BaseCallbackHandler):
    def __init__(self):
        self.calls = []

    def on_chat_model_start(self, serialized, messages, **kwargs):
        self.calls.append(messages[0])


@pytest.mark.asyncio
async def test_direct_answer_receives_style_without_extra_model_call_or_tools():
    capture = CapturePrompt()
    model = FakeMessagesListChatModel(
        responses=[AIMessage(content="不客气，想聊训练时随时来。")],
        callbacks=[capture],
    )
    with patch("app.services.agent_runtime._build_model", return_value=model):
        result = await invoke_langchain_agent(
            object(), user_id="synthetic-style", history=[],
            user_message="谢谢啦～", tool_allowlist=[],
        )
    assert len(capture.calls) == 1
    prompt = capture.calls[0][0].content
    assert "表达方式" in prompt
    assert "不进行医疗诊断" in prompt
    assert "只有服务端已经返回提案卡片" in prompt
    assert result["messages"][-1].content == "不客气，想聊训练时随时来。"
    assert not result["messages"][-1].tool_calls


@pytest.mark.asyncio
async def test_planned_answer_style_preserves_structured_outcome_and_evidence():
    class Model:
        def with_structured_output(self, schema, **kwargs):
            self.schema = schema
            return self

        async def ainvoke(self, messages):
            self.messages = messages
            decision = FinalizationDecision(outcome="informational_answer", reply="本周完成了两次训练。")
            return {"parsed": decision, "raw": AIMessage(content=decision.model_dump_json())}

    model = Model()
    result = await ModelPlanningPolicy(model).finalize(
        goal="最近练得怎么样？", steps=[], observations=[],
        allowed_outcomes=["informational_answer"],
    )
    assert model.schema is FinalizationDecision
    assert "表达方式" in model.messages[0]["content"]
    assert "用户私有事实只能来自工具观察" in model.messages[0]["content"]
    assert result.outcome == "informational_answer"
    assert result.terminal_action == "answer"
    assert result.proposal_draft is None


def test_vague_clarification_is_one_small_question_without_an_instruction_dump():
    resolution = IntentResolution(primary_intent="general_qa", confidence=0.2, clarification_required=True)
    reply = _clarification_reply(resolution)
    assert reply == "你想了解哪方面？可以先说说训练、饮食，或你遇到的具体问题。"


def test_clarification_does_not_expose_unknown_internal_field_names():
    resolution = IntentResolution(
        primary_intent="general_qa", confidence=0.2, clarification_required=True,
        missing_slots=["exercise.rest_seconds", "some.internal_field", "要比较的时间范围"],
    )
    reply = _clarification_reply(resolution)
    assert "组间休息秒数" in reply
    assert "要比较的时间范围" in reply
    assert "internal_field" not in reply
    assert "exercise.rest_seconds" not in reply


def test_specific_semantic_question_is_not_replaced_by_canned_noise_response():
    question = "你指的是周二还是周三的卧推？"
    resolution = IntentResolution(primary_intent="general_qa", confidence=0.6, clarification_question=question)
    assert _clarification_reply(resolution) == question
