"""Payload/SQL contracts; scripted replies are not evidence of model answer quality."""
from datetime import date
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import PrivateAttr
from sqlalchemy import func, select

from app.models.exercise import Exercise
from app.models.user import User
from app.models.workout import PlannedExercise, WorkoutPlan, WorkoutSession
from app.schemas.agent_planning import FinalizationDecision
from app.services import agent_runtime as runtime
from app.services.agent_intent import IntentResolution
from app.services.agent_planner import ModelPlanningPolicy
from app.services.agent_tools import NoArguments, build_read_tools
from app.services.business_clock import BUSINESS_DATE


@pytest.mark.asyncio
@pytest.mark.parametrize('linked', [False, True], ids=['unrelated-history-only', 'two-sessions-one-day'])
async def test_active_plan_weekly_scope_is_not_all_workout_history(db_session, linked):
    tag = uuid4().hex
    owner = User(id=tag, email=tag + '@example.invalid', password_hash='synthetic')
    other = User(id=tag + '-other', email=tag + '-other@example.invalid', password_hash='synthetic')
    exercise = Exercise(id=tag + '-ex', name_zh='合成动作', name_en='Synthetic', category='strength', difficulty='beginner')
    db_session.add_all([owner, other, exercise])
    await db_session.flush()
    plan = WorkoutPlan(user_id=owner.id, name='当前合成计划')
    archived = WorkoutPlan(user_id=owner.id, name='其他历史计划', is_active=False)
    foreign = WorkoutPlan(user_id=other.id, name='other-user-secret')
    db_session.add_all([plan, archived, foreign])
    await db_session.flush()
    db_session.add(PlannedExercise(plan_id=plan.id, exercise_id=exercise.id, day_of_week=1, sets=3, reps='10'))
    db_session.add_all([
        WorkoutSession(user_id=owner.id, trained_at=date(2026, 11, 10), status='completed', duration_min=12),
        WorkoutSession(user_id=owner.id, plan_id=archived.id, plan_family_id=archived.family_id,
            week_start=date(2026, 11, 9), day_of_week=1, trained_at=date(2026, 11, 9), status='completed', duration_min=30),
        WorkoutSession(user_id=other.id, plan_id=foreign.id, plan_family_id=foreign.family_id,
            week_start=date(2026, 11, 9), day_of_week=1, trained_at=date(2026, 11, 9), status='completed', duration_min=99),
    ])
    if linked:
        for minutes in [15, 20]:
            db_session.add(WorkoutSession(user_id=owner.id, plan_id=plan.id, plan_family_id=plan.family_id,
                week_start=date(2026, 11, 9), day_of_week=1, trained_at=date(2026, 11, 9),
                status='completed', duration_min=minutes))
    await db_session.commit()
    before = await db_session.scalar(select(func.count()).select_from(WorkoutSession))
    clock = BUSINESS_DATE.set(date(2026, 11, 11))
    try:
        result = await build_read_tools(db_session, user_id=owner.id, allowlist=['plan.get_active'])[0].ainvoke({})
    finally:
        BUSINESS_DATE.reset(clock)
    assert result['plan']['weekly_completed_days'] == int(linked)
    scope = result['weekly_completion_scope']
    assert scope['week_start'] == '2026-11-09' and scope['plan_id'] == plan.id
    assert scope['unit'] == 'distinct_scheduled_training_days'
    assert scope['includes_unlinked_sessions'] is False
    assert scope['includes_other_plan_families'] is False
    assert scope['kind'] == 'active_plan_continuous_arrangement_current_week'
    assert await db_session.scalar(select(func.count()).select_from(WorkoutSession)) == before
    assert 'other-user-secret' not in json.dumps(result)
    assert not db_session.dirty


@pytest.mark.asyncio
async def test_no_active_plan_is_explicit_without_modifying_archived_plan(db_session):
    tag = uuid4().hex
    db_session.add(User(id=tag, email=tag + '@example.invalid', password_hash='synthetic'))
    await db_session.flush()
    plan = WorkoutPlan(user_id=tag, name='未启用合成计划', is_active=False)
    db_session.add(plan)
    await db_session.commit()
    tools = build_read_tools(db_session, user_id=tag, allowlist=['plan.get_active', 'workout.get_next'])
    assert await tools[0].ainvoke({}) == {'found': False}
    assert await tools[1].ainvoke({}) == {'found': False, 'reason': 'no_active_plan'}
    await db_session.refresh(plan)
    assert not plan.is_active and not db_session.dirty


class CapturingModel(FakeMessagesListChatModel):
    _inputs: list = PrivateAttr(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, *args, **kwargs):
        self._inputs.append(messages)
        return super()._generate(messages, *args, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['direct', 'completion', 'planned-finalizer'])
async def test_training_constraints_reach_each_actual_answer_input(monkeypatch, path):
    request = '给我下一次训练的准备建议'
    scope = {'kind': 'active_plan_continuous_arrangement_current_week', 'unit': 'distinct_scheduled_training_days',
             'plan_id': 'synthetic', 'week_start': '2026-11-09', 'includes_unlinked_sessions': False,
             'includes_other_plan_families': False}
    plan_result = {'found': True, 'plan': {'id': 'synthetic', 'weekly_completed_days': 0},
                   'weekly_completion_scope': scope}
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
        await ModelPlanningPolicy(model).finalize(goal=request, steps=[], observations=[
            {'tool_id': 'plan.get_active', 'status': 'success', 'result': plan_result}],
            allowed_outcomes=['informational_answer', 'insufficient_evidence'])
        prompt = model.messages[0]['content']
        payload = json.loads(model.messages[1]['content'].removeprefix('输入：').removesuffix('\n请输出 JSON。'))
        assert payload['tool_observations'][0]['result']['weekly_completion_scope'] == scope
    else:
        @tool('plan_get_active', args_schema=NoArguments, description='Read synthetic plan.')
        async def plan():
            return plan_result
        model = CapturingModel(responses=[AIMessage(content='合成答复')])
        if path == 'direct':
            monkeypatch.setattr(runtime, '_build_model', lambda: model)
            monkeypatch.setattr(runtime, 'build_read_tools', lambda *args, **kwargs: [])
            await runtime.invoke_langchain_agent(None, user_id='synthetic', history=[],
                user_message=request, tool_allowlist=[], resolution=None)
        else:
            prior = {'messages': [HumanMessage(content=request),
                AIMessage(content='', tool_calls=[{'name': 'workout_get_next', 'args': {}, 'id': 'next'}]),
                ToolMessage(name='workout_get_next', tool_call_id='next', content='{"found": true}'),
                AIMessage(content='尚未取得全部资料的旧答复')]}
            resolution = IntentResolution(primary_intent='next_workout_query', request_kind='query',
                confidence=1, resolved_query=request, evidence_requirements=['active_plan', 'next_workout'])
            await runtime._complete_direct_required_reads(prior, db=None, model=model, tools=[plan],
                resolution=resolution, tool_allowlist=['plan.get_active', 'workout.get_next'])
            observations = [json.loads(m.content) for m in model._inputs[-1] if isinstance(m, ToolMessage)]
            assert observations[-1]['weekly_completion_scope'] == scope
        prompt = model._inputs[-1][0].content
    # This checks delivery across different runtime paths, not generated quality.
    assert '绝对重量' in prompt and '个人强度' in prompt
    assert '动作变形' in prompt and '凑次数' in prompt
    assert 'weekly_completion_scope' in prompt
    assert '没有活动计划' in prompt
