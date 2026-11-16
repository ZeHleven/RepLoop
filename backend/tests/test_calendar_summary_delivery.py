"""Regression for the two failed, retained demo turns; real SQL, no paid model."""
from copy import deepcopy
from datetime import date
from unittest.mock import Mock

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.models.workout import WorkoutSession, SessionExercise
from app.services import agent_runtime as runtime
from app.services.agent_evidence_contract import build_evidence_contract, guard_read_completion
from app.services.agent_intent import IntentResolution
from app.services.agent_query_reports import report_for_resolution, narrow_report_evidence
from app.services.agent_tools import build_read_tools, WorkoutProgressArguments
from app.services.agent_trace import build_initial_execution_trace, complete_execution_trace
from app.services.business_clock import BUSINESS_DATE
from app.services.demo_seed import seed_demo_user


TODAY = date(2026, 11, 16)  # Monday: two calendar weeks must not replace 14 days.
TOOLS = ['workout.get_progress', 'workout.list_history']
QUERIES = [
    '总结用户最近两周（2026-11-03 至 2026-11-16）的训练情况，包括训练场次、动作、组数、容量与完成率等跨场次汇总。',
    '对比最近两周（2026-11-03 至 2026-11-16）与再之前两周（2026-10-20 至 2026-11-02）的训练情况，包括训练场次、动作、组数、容量与完成率等跨场次汇总对比。',
]


@pytest.fixture(autouse=True)
def frozen_day():
    token = BUSINESS_DATE.set(TODAY)
    yield
    BUSINESS_DATE.reset(token)


def resolution(query):
    return IntentResolution(primary_intent='workout_progress_query', intent_domain='workout_progress',
        request_kind='query', requested_effect='read', confidence=.9, resolved_query=query,
        evidence_requirements=['workout_progress', 'workout_history'])


class OmissionReplay(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@pytest.mark.asyncio
@pytest.mark.parametrize('index', [0, 1], ids=['D01-summary', 'D02-comparison'])
async def test_retained_omission_cannot_lose_summary_or_scope(db_session, monkeypatch, index):
    user = await seed_demo_user(db_session)
    await db_session.flush()
    before = [(s.id, s.duration_min, s.status) for s in (await db_session.scalars(
        select(WorkoutSession).where(WorkoutSession.user_id == user.id))).all()]
    # The old executor only called history and then answered (as in http-3/7).
    calls = [{'name': 'workout_list_history', 'args': {'start_date': '2026-11-03', 'end_date': '2026-11-16', 'limit': 20}, 'id': 'recent'}]
    if index:
        calls.append({'name': 'workout_list_history', 'args': {'start_date': '2026-10-20', 'end_date': '2026-11-02', 'limit': 20}, 'id': 'previous'})
    model = OmissionReplay(responses=[AIMessage(content='', tool_calls=calls), AIMessage(content='模型已经自行汇总，但漏查必需进度。')])
    build = Mock(return_value=model)
    monkeypatch.setattr(runtime, '_build_model', build)
    intent = resolution(QUERIES[index])
    result = await runtime.invoke_langchain_agent(db_session, user_id=user.id, history=[],
        user_message=['总结我最近两周的训练情况。', '和再之前的两周相比呢？'][index],
        resolved_query=intent.resolved_query, tool_allowlist=TOOLS, resolution=intent)
    trace = build_initial_execution_trace(intent, TOOLS).model_copy(update={
        'evidence_contract': build_evidence_contract(intent, TOOLS, budget=4)})
    trace = complete_execution_trace(trace, result, summarize_observation=runtime._audit_result_summary)
    reply, cards = runtime._extract_agent_output(result)
    reply, trace = guard_read_completion(reply, trace)
    assert '200分钟' in reply and '4,500kg' in reply
    assert trace.evidence_contract.status == 'complete'
    assert trace.budget_usage.tool_calls == (4 if index else 2)
    assert trace.budget_usage.model_calls == 0
    build.assert_not_called()  # The existing semantic router still runs in production.
    expected_ranges = {('2026-11-03', '2026-11-16')}
    if index:
        expected_ranges.add(('2026-10-20', '2026-11-02'))
        assert '210分钟' in reply and '4,320kg' in reply
        assert '训练场次减少1次' in reply and '负重容量增加180kg' in reply and '+4.17%' in reply
    assert {(c['data']['range_start'], c['data']['range_end']) for c in cards} == expected_ranges
    assert all(o.summary['range_start'] and o.summary['range_end'] for o in trace.observations)
    assert len(runtime._tool_call_records(result, 'synthetic')) == trace.budget_usage.tool_calls
    after = [(s.id, s.duration_min, s.status) for s in (await db_session.scalars(
        select(WorkoutSession).where(WorkoutSession.user_id == user.id))).all()]
    assert after == before
    assert narrow_report_evidence(intent, '总结训练').evidence_requirements == intent.evidence_requirements


@pytest.mark.asyncio
async def test_calendar_summary_all_records_boundaries_status_and_owner(db_session):
    from app.services.workout_queries import get_workout_range_summary
    first, other = await seed_demo_user(db_session), await seed_demo_user(db_session)
    # Add more than the display cap; include both closed interval endpoints.
    for index in range(23):
        session = WorkoutSession(user_id=first.id, trained_at=date(2026, 11, 3 if index == 0 else 16),
                                 status='completed', duration_min=None if index == 0 else 2)
        db_session.add(session)
        await db_session.flush()
        db_session.add(SessionExercise(session_id=session.id, exercise_id='demo-goblet-squat',
            exercise_name='高脚杯深蹲', target_sets=1, sets_data=[{'reps': 2, 'weight_kg': 5}]))
    excluded = WorkoutSession(user_id=first.id, trained_at=TODAY, status='skipped', duration_min=99)
    future = WorkoutSession(user_id=first.id, trained_at=date(2026, 11, 17), status='completed', duration_min=99)
    db_session.add_all([excluded, future])
    await db_session.flush()
    result = await get_workout_range_summary(db_session, user_id=first.id, start=date(2026, 11, 3), end=TODAY, statuses=('completed',))
    assert result['total_sessions'] == 28  # Other user's five sessions must not leak.
    assert result['total_sets'] == 68 and result['total_reps'] == 496
    assert result['total_volume_kg'] == 4730
    assert result['recorded_duration_min'] == 244 and result['duration_missing_sessions'] == 1
    assert result['truncated'] is False and result['scope_complete'] is True
    tools = {tool.name: tool for tool in build_read_tools(db_session, user_id=first.id, allowlist=TOOLS)}
    bounded = await tools['workout_get_progress'].ainvoke({'start_date': '2026-11-03', 'end_date': '2026-11-16', 'completed_only': True})
    assert bounded == result
    history = await tools['workout_list_history'].ainvoke({'start_date': '2026-11-03', 'end_date': '2026-11-16', 'limit': 20, 'completed_only': True})
    assert history['count'] == 20 and history['total_count'] == 28 and history['truncated'] is True


@pytest.mark.parametrize('query', [
    '统计2026-11-03至2026-11-16卧推的容量',
    '统计2026-11-03至2026-11-16推举的容量',
    '统计2026-11-03至2026-11-16重量超过10kg的训练',
    '总结2026-11-03至2026-11-16训练并修改计划',
    '总结2026-11-03至2026-11-16训练并给建议',
    '总结2026-11-03和2026-11-16两天的训练',
    '比较2026-11-03至2026-11-16与2026-11-01至2026-11-10训练',
    '统计2026-11-03至2026-11-17训练',
    '统计2025-01-01至2026-11-16训练',
    '统计2026-11-16至2026-11-03训练',
    '统计2026-11-03至2026-11-16未完成的训练',
])
def test_unsupported_scope_is_not_claimed_by_calendar_summary(query):
    from app.services.workout_range_reporting import calendar_summary
    assert calendar_summary(query, TOOLS, TODAY) is None


@pytest.mark.parametrize('changes', [{'start_date': '2026-11-03'}, {'end_date': '2026-11-16'},
    {'start_date': '2026-11-16', 'end_date': '2026-11-03'}, {'statuses': ['completed']},
    {'start_date': '2026-11-03', 'end_date': '2026-11-16', 'completed_only': True, 'statuses': ['skipped']}])
def test_invalid_progress_arguments_fail_closed(changes):
    with pytest.raises(ValueError):
        WorkoutProgressArguments(**changes)


@pytest.mark.asyncio
async def test_empty_period_missing_duration_and_zero_baseline(db_session):
    from app.services.workout_queries import get_workout_range_summary
    from app.services.workout_range_reporting import render_calendar_summary
    user = await seed_demo_user(db_session)
    await db_session.flush()
    old = await get_workout_range_summary(db_session, user_id=user.id, start=date(2026, 9, 1), end=date(2026, 9, 14), statuses=('completed',))
    recent = await get_workout_range_summary(db_session, user_id=user.id, start=date(2026, 11, 3), end=TODAY, statuses=('completed',))
    recent['duration_missing_sessions'] = 1
    reply = render_calendar_summary([recent, old])
    assert '无记录不等于没有运动' in reply and '不计算百分比变化' in reply
    assert '另有1次时长未记录' in reply and '记录时长增加' not in reply


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('range_start', '2026-11-09'), ('range_end', '2026-11-15'),
    ('status_filter', ['skipped']), ('scope_complete', False), ('truncated', True)])
async def test_wrong_or_partial_evidence_never_produces_a_report(db_session, field, value):
    from app.services.agent_query_reports import execute_query_report
    user = await seed_demo_user(db_session)
    await db_session.flush()
    tools = build_read_tools(db_session, user_id=user.id, allowlist=TOOLS)
    original = tools[0]
    class WrongScope:
        name = original.name
        async def ainvoke(self, arguments):
            data = deepcopy(await original.ainvoke(arguments))
            data[field] = value
            return data
    with pytest.raises(ValueError, match='calendar_summary'):
        await execute_query_report(report_for_resolution(resolution(QUERIES[0]), TOOLS), [WrongScope(), *tools[1:]])


@pytest.mark.asyncio
async def test_missing_authority_or_insufficient_budget_never_starts_reads(monkeypatch):
    from app.services.agent_query_reports import execute_query_report
    report = report_for_resolution(resolution(QUERIES[1]), TOOLS)
    class NeverRead:
        def __init__(self, name): self.name = name
        async def ainvoke(self, arguments): raise AssertionError('Must reject before any read')
    tools = [NeverRead(name.replace('.', '_')) for name in TOOLS]
    with pytest.raises(ValueError, match='authority_missing'):
        await execute_query_report(report, tools[:1])
    monkeypatch.setattr(runtime.settings, 'AGENT_MAX_TOOL_CALLS', 3)
    with pytest.raises(ValueError, match='tool_budget_exceeded'):
        await execute_query_report(report, tools)


def test_summary_never_grants_a_missing_tool():
    assert report_for_resolution(resolution(QUERIES[0]), ['workout.list_history']) is None
