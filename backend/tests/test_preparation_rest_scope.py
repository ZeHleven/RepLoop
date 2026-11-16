"""SQL and actual model-input contracts, not a live generated-answer evaluation."""
from datetime import date
from contextvars import ContextVar
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import PrivateAttr
from sqlalchemy import select

from app.models.exercise import Exercise
from app.models.user import User
from app.models.workout import PlannedExercise, WorkoutPlan
from app.schemas.agent_planning import FinalizationDecision
from app.services import agent_runtime as runtime
from app.services.agent_intent import IntentResolution
from app.services.agent_planner import ModelPlanningPolicy
from app.services.agent_tools import build_read_tools
from app.services import business_clock


@pytest.fixture(autouse=True)
def fixed_business_date(monkeypatch):
    # Async fixture teardown runs in another context in pytest-asyncio 0.24.
    monkeypatch.setattr(business_clock, 'BUSINESS_DATE',
                        ContextVar('rest_scope_business_date', default=date(2026, 11, 11)))


def save_evidence(name, value):
    if directory := os.environ.get('PREP_EVIDENCE_DIR'):
        (Path(directory) / f'rest-{name}.json').write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


@pytest_asyncio.fixture
async def rest_plan(db_session):
    tag = uuid4().hex
    owner = User(id=tag, email=tag + '@example.invalid', password_hash='synthetic')
    other = User(id=tag + '-other', email=tag + '-other@example.invalid', password_hash='synthetic')
    exercises = [Exercise(id=tag + str(i), name_zh=f'合成动作{i}', name_en=f'Synthetic {i}',
                          category='strength', difficulty='beginner') for i in range(2)]
    db_session.add_all([owner, other, *exercises])
    await db_session.flush()
    plan = WorkoutPlan(user_id=owner.id, name='不同组间休息合成计划')
    foreign = WorkoutPlan(user_id=other.id, name='other-user-secret')
    db_session.add_all([plan, foreign])
    await db_session.flush()
    for i, seconds in enumerate([45, 150]):
        db_session.add(PlannedExercise(plan_id=plan.id, exercise_id=exercises[i].id,
            day_of_week=3, order_index=i, sets=3, reps='10', rest_seconds=seconds))
    db_session.add(PlannedExercise(plan_id=foreign.id, exercise_id=exercises[0].id,
        day_of_week=3, rest_seconds=777))
    await db_session.commit()
    return owner.id, plan.id


def assert_rest_observation(result):
    items = result.get('plan', result)['exercises']
    assert [item['rest_seconds'] for item in items] == [45, 150]
    semantics = result['rest_seconds_semantics']
    assert semantics['unit'] == 'seconds'
    assert semantics['scope'] == 'between_sets_of_same_exercise'
    assert semantics['applies_to'] == 'each_planned_exercise'
    assert 'other-user-secret' not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize('tool_id', ['plan.get_active', 'workout.get_next'])
async def test_rest_scope_preserves_each_exercise_and_read_only_access(db_session, rest_plan, tool_id):
    owner_id, plan_id = rest_plan
    statement = select(PlannedExercise.id, PlannedExercise.rest_seconds).where(
        PlannedExercise.plan_id == plan_id).order_by(PlannedExercise.order_index)
    before = (await db_session.execute(statement)).all()
    read_tool = build_read_tools(db_session, user_id=owner_id, allowlist=[tool_id])[0]
    result = await read_tool.ainvoke({})
    after = (await db_session.execute(statement)).all()
    save_evidence(tool_id, {'result': result, 'before': before, 'after': after})
    assert before == after and not db_session.dirty
    assert_rest_observation(result)
    # Caller-local edits must not change the next observation's field meaning.
    result['rest_seconds_semantics']['scope'] = 'caller-local-mutation'
    assert_rest_observation(await read_tool.ainvoke({}))


@pytest.mark.asyncio
async def test_empty_active_plan_does_not_invent_a_next_workout(db_session):
    tag = uuid4().hex
    db_session.add(User(id=tag, email=tag + '@example.invalid', password_hash='synthetic'))
    await db_session.flush()
    db_session.add(WorkoutPlan(user_id=tag, name='空合成计划'))
    await db_session.commit()
    read_tool = build_read_tools(db_session, user_id=tag, allowlist=['workout.get_next'])[0]
    assert await read_tool.ainvoke({}) == {'found': False, 'reason': 'active_plan_has_no_exercises'}
    assert not db_session.dirty


class CapturingModel(FakeMessagesListChatModel):
    _inputs: list = PrivateAttr(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, *args, **kwargs):
        self._inputs.append(messages)
        return super()._generate(messages, *args, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['direct', 'completion', 'planned-finalizer'])
async def test_rest_facts_reach_actual_answer_input(db_session, rest_plan, monkeypatch, path):
    owner_id, _ = rest_plan
    request = '给我下一次训练的准备建议'
    allowlist = ['plan.get_active', 'workout.get_next']
    tools = build_read_tools(db_session, user_id=owner_id, allowlist=allowlist)
    if path == 'planned-finalizer':
        class StructuredModel:
            def with_structured_output(self, schema, **kwargs):
                return self

            async def ainvoke(self, messages):
                self.messages = messages
                return {'parsed': FinalizationDecision(outcome='informational_answer', reply='合成答复'),
                        'raw': SimpleNamespace(content='合成答复', response_metadata={'finish_reason': 'stop'},
                                               usage_metadata={'input_tokens': 10, 'output_tokens': 10})}
        model = StructuredModel()
        observations = []
        for tool_id, read_tool in zip(allowlist, tools):
            observations.append({'tool_id': tool_id, 'status': 'success', 'result': await read_tool.ainvoke({})})
        await ModelPlanningPolicy(model).finalize(goal=request, steps=[], observations=observations,
            allowed_outcomes=['informational_answer', 'insufficient_evidence'])
        save_evidence('input-' + path, model.messages)
        prompt = model.messages[0]['content']
        payload = json.loads(model.messages[1]['content'].removeprefix('输入：').removesuffix('\n请输出 JSON。'))
        results = [item['result'] for item in payload['tool_observations']]
    else:
        responses = [AIMessage(content='合成答复')]
        if path == 'direct':
            # Sequential tool calls use the actual SQL tools and single session.
            responses = [AIMessage(content='', tool_calls=[{'name': item.name, 'args': {}, 'id': str(i)}])
                         for i, item in enumerate(tools)] + responses
        model = CapturingModel(responses=responses)
        if path == 'direct':
            monkeypatch.setattr(runtime, '_build_model', lambda: model)
            await runtime.invoke_langchain_agent(db_session, user_id=owner_id, history=[],
                user_message=request, tool_allowlist=allowlist, resolution=None)
        else:
            prior = {'messages': [HumanMessage(content=request),
                AIMessage(content='', tool_calls=[{'name': tools[1].name, 'args': {}, 'id': 'next'}]),
                ToolMessage(name=tools[1].name, tool_call_id='next', content=json.dumps(await tools[1].ainvoke({}))),
                AIMessage(content='尚未取得全部资料的旧答复')]}
            resolution = IntentResolution(primary_intent='next_workout_query', request_kind='query',
                confidence=1, resolved_query=request, evidence_requirements=['active_plan', 'next_workout'])
            await runtime._complete_direct_required_reads(prior, db=db_session, model=model,
                tools=tools, resolution=resolution, tool_allowlist=allowlist)
        messages = model._inputs[-1]
        save_evidence('input-' + path, [message.model_dump() for message in messages])
        prompt = messages[0].content
        results = [json.loads(message.content) for message in messages if isinstance(message, ToolMessage)]
    assert len(results) == 2
    for result in results:
        assert_rest_observation(result)
    assert 'rest_seconds' in prompt and '同一动作相邻组之间' in prompt and '组间休息' in prompt
    assert '建议调整' in prompt and '计划原值' in prompt
