"""Regressions from the 2026-09-28 device acceptance, with synthetic data."""
from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.agent_query_reports import select_query_report, execute_query_report
from app.services.agent_runtime import _extract_agent_output, _tool_call_records
from app.services.workout_reporting import REPORT_DATE


@pytest.fixture(autouse=True)
def fixed_day():
    token = REPORT_DATE.set(date(2026, 9, 28))
    yield
    REPORT_DATE.reset(token)


def progress_data():
    rows = []
    for start, sessions in [('2026-09-14',2),('2026-09-21',3),('2026-09-28',1)]:
        rows.append(dict(week_start=start, week_end=(date.fromisoformat(start)+timedelta(days=6)).isoformat(),
            is_complete=start!='2026-09-28', sessions=sessions, sets=6*sessions,
            reps=60*sessions, volume_kg=2000*sessions))
    return dict(as_of='2026-09-28',timezone='Asia/Shanghai',weeks=3,weekly=rows,
        total_sessions=6,total_sets=36,total_reps=360,total_volume_kg=12000,
        averages_per_calendar_week={},average_denominator_weeks=3,daily=[],selected_week=None)


@pytest.mark.parametrize('query,expected', [('查询上周训练次数',3),('查询上上周训练次数',2),
    ('查询2026-09-14至2026-09-27训练次数',5),('查询本周训练次数',1)])
@pytest.mark.asyncio
async def test_text_and_card_share_requested_scope_without_changing_raw_audit(query, expected):
    raw=progress_data(); before=deepcopy(raw)
    report=select_query_report(query,['workout.get_progress'])
    tool=SimpleNamespace(name='workout_get_progress',ainvoke=AsyncMock(return_value=raw))
    result=await execute_query_report(report,[tool])
    answer,cards=_extract_agent_output(result)
    assert f'{expected}次训练' in answer
    assert cards[0]['data']['total_sessions']==expected
    assert cards[0]['data']['total_sets']==6*expected
    assert cards[0]['data']['range_start']==report.scope.start.isoformat()
    assert cards[0]['data']['range_end']==report.scope.end.isoformat()
    assert _tool_call_records(result,'test-run')[0].result_data['total_sessions']==6
    assert raw==before


@pytest.mark.asyncio
async def test_empty_previous_week_does_not_display_current_week_totals():
    raw=progress_data()
    raw['weekly'][1].update(sessions=0,sets=0,reps=0,volume_kg=0)
    report=select_query_report('上周训练情况',['workout.get_progress'])
    result=await execute_query_report(report,[SimpleNamespace(name='workout_get_progress',ainvoke=AsyncMock(return_value=raw))])
    answer,cards=_extract_agent_output(result)
    assert '没有已记录' in answer and cards[0]['data']['total_sessions']==0


@pytest.mark.parametrize('query', [
    '先不比动作了，查最近四周。',
    '查询我最近四周的训练进度（跨训练场次聚合的完成情况与趋势），不再比较单个动作。',
    '不用对比这个动作了，查询最近4周训练次数',
])
def test_cancelled_action_does_not_disable_week_report(query):
    report=select_query_report(query,['workout.get_progress'])
    assert report and report.kind=='progress' and report.arguments=={'weeks':4}


@pytest.mark.parametrize('query', [
    '比较深蹲最近三次，并汇总最近四周',
    '不要只比较动作，还要查最近四周',
    '不再比较单个动作，但要给我最近四周的训练建议',
    '不再比较单个动作，查最近四周每天的明细',
])
def test_live_action_advice_and_other_goals_are_not_discarded(query):
    assert select_query_report(query,['workout.get_progress','workout.list_history']) is None


def test_last_week_history_uses_calendar_bounds_in_tool_arguments():
    report=select_query_report('帮我看看上周的训练情况（已完成的具体训练场次和记录）',['workout.list_history'])
    assert report is not None
    assert report.arguments=={'limit':20,'start_date':'2026-09-21','end_date':'2026-09-27'}


@pytest.mark.parametrize('count',[0,15,25])
@pytest.mark.asyncio
async def test_history_date_filter_precedes_limit_and_counts_only_owned_finished_records(db_session,count):
    from app.models.user import User
    from app.models.workout import WorkoutSession
    from app.services.agent_tools import build_read_tools
    db_session.add_all([User(id='scope-u',email='scope-u@example.test',password_hash='unused'),
                        User(id='scope-v',email='scope-v@example.test',password_hash='unused')])
    await db_session.commit()
    for i in range(count):
        db_session.add(WorkoutSession(user_id='scope-u',trained_at=date(2026,9,23),status='ended_early' if i==0 else 'completed'))
    for i in range(25 if count else 0):
        db_session.add(WorkoutSession(user_id='scope-u',trained_at=date(2026,9,28),status='completed'))
    db_session.add_all([WorkoutSession(user_id='scope-v',trained_at=date(2026,9,23),status='completed'),
        WorkoutSession(user_id='scope-u',trained_at=date(2026,9,22),status='abandoned'),
        WorkoutSession(user_id='scope-u',trained_at=date(2026,9,14),status='completed')])
    await db_session.commit()
    tools=build_read_tools(db_session,user_id='scope-u',allowlist=['workout.list_history'])
    report=select_query_report('帮我看看上周练得怎么样',['workout.list_history'])
    assert report is not None
    result=await execute_query_report(report,tools)
    answer,cards=_extract_agent_output(result)
    data=cards[0]['data']
    assert data['total_count']==count and data['count']==min(count,20)
    assert data['truncated']==(count>20)
    assert all(row['trained_at']=='2026-09-23' for row in data['sessions'])
    assert '2026-09-21至2026-09-27' in answer
    if count==0: assert '没有' in answer
    if count>20: assert '仅展示' in answer and str(count) in answer
    assert '最近一条' not in answer


@pytest.mark.parametrize('arguments',[
    {'start_date':'2026-09-21'}, {'end_date':'2026-09-27'},
    {'start_date':'2026-09-28','end_date':'2026-09-21'},
    {'start_date':'20260921','end_date':'2026-09-27'},
    {'start_date':'2025-01-01','end_date':'2026-09-27'},
])
def test_history_range_rejects_ambiguous_or_unbounded_arguments(arguments):
    from pydantic import ValidationError
    from app.services.agent_tools import WorkoutHistoryArguments
    with pytest.raises(ValidationError):
        WorkoutHistoryArguments(**arguments)


@pytest.mark.asyncio
async def test_runtime_persists_scoped_card_and_original_audit_separately(db_session):
    from datetime import datetime, timezone
    from unittest.mock import patch
    from sqlalchemy import select
    from app.models.user import User
    from app.models.workout import WorkoutSession
    from app.models.agent import AgentConversation, AgentRun, AgentMessage, AgentToolCall
    from app.services.agent_intent import IntentResolution, IntentResolverOutcome
    from app.services.agent_runtime import execute_agent_run
    db_session.add(User(id='persist-u',email='persist@example.test',password_hash='unused'))
    await db_session.commit()
    conversation=AgentConversation(id='persist-c',user_id='persist-u')
    db_session.add(conversation)
    for day in (21,23,25,28):
        db_session.add(WorkoutSession(user_id='persist-u',trained_at=date(2026,9,day),status='completed'))
    await db_session.commit()
    run=AgentRun(id='persist-r',user_id='persist-u',conversation_id='persist-c',queued_at=datetime(2026,9,28,tzinfo=timezone.utc))
    db_session.add(run)
    await db_session.commit()
    resolution=IntentResolution(primary_intent='workout_progress_query',intent_domain='workout_progress',
        evidence_requirements=['workout_progress'],resolved_query='查询上周训练次数和组数',confidence=.99)
    with patch('app.services.agent_runtime.resolve_intent_with_fallback',new=AsyncMock(return_value=IntentResolverOutcome(resolution=resolution,source='model'))), patch(
            'app.services.agent_runtime._build_model',side_effect=AssertionError('unexpected free answer')):
        await execute_agent_run(db_session,run=run,conversation=conversation,user_message='查上周训练次数和组数')
    assert run.status=='completed'
    db_session.expunge_all()
    message=await db_session.scalar(select(AgentMessage).where(AgentMessage.run_id=='persist-r',AgentMessage.role=='assistant'))
    audit=await db_session.scalar(select(AgentToolCall).where(AgentToolCall.run_id=='persist-r'))
    assert '3次训练' in message.content
    assert message.content_data['cards'][0]['data']['total_sessions']==3
    assert audit.result_data['total_sessions']==4 and audit.arguments_data=={'weeks':2}
