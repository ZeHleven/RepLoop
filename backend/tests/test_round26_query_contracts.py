"""A specific scope must never be widened by a verified report shortcut."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import AIMessage

from app.services.agent_intent import IntentResolution, route_tools
from app.services.agent_query_reports import explicit_history_range, report_for_resolution
from app.services.agent_trace import select_execution_mode


def resolution(query, domain='nutrition'):
    nutrition = domain == 'nutrition'
    return IntentResolution(
        primary_intent='nutrition_history_query' if nutrition else 'workout_history_query',
        intent_domain=domain, evidence_requirements=['nutrition_history' if nutrition else 'workout_history'],
        resolved_query=query, confidence=1,
    )


@pytest.mark.parametrize('query', [
    '查询2026-09-01至2026-09-05的饮食记录',
    '查询2026-09-01的饮食记录',
    '查看昨晚的饮食记录',
    '查询今年的饮食记录',
    '查看晚餐的饮食记录',
    '查询2026/09/01至2026/09/05的饮食记录',
])
def test_specific_nutrition_scope_keeps_semantic_execution(query):
    current = resolution(query)
    tools = route_tools(current)
    assert report_for_resolution(current, tools) is None
    assert select_execution_mode(current, tools) == ('direct', ['single_goal_or_tool'])


@pytest.mark.parametrize('query', [
    '最近有记录的饮食日期和营养汇总',
    '查看最近的饮食记录',
    '请帮我列出已记录的饮食日期和营养汇总。',
    '查询我的饮食历史',
])
def test_unbounded_nutrition_listing_still_has_verified_report(query):
    current = resolution(query)
    report = report_for_resolution(current, route_tools(current))
    assert report.kind == 'nutrition_history'
    assert report.arguments == {'days': 30}


@pytest.mark.parametrize('query', [
    '列出2026-09-01和2026-09-05这两天已完成的训练记录',
    '列出2026-09-14和2026-09-20这两天已完成的训练记录',
    '列出2026年9月14日、2026年9月20日已完成的训练记录',
])
def test_discrete_dates_never_turn_into_continuous_range(query):
    current = resolution(query, 'workout_history')
    tools = route_tools(current)
    assert explicit_history_range(query) is None
    assert report_for_resolution(current, tools) is None
    assert select_execution_mode(current, tools) == ('direct', ['single_goal_or_tool'])


@pytest.mark.parametrize('query,start,end', [
    ('查询2026-09-01至2026-09-05已完成的训练记录', '2026-09-01', '2026-09-05'),
    ('查询2026年9月1日到9月5日已完成的训练记录', '2026-09-01', '2026-09-05'),
    ('查询2026-09-01 ~ 2026-09-05已完成的训练记录', '2026-09-01', '2026-09-05'),
    ('查询2026-09-01已完成的训练记录', '2026-09-01', '2026-09-01'),
])
def test_explicit_closed_range_and_single_day_still_work(query, start, end):
    current = resolution(query, 'workout_history')
    report = report_for_resolution(current, route_tools(current))
    assert report.kind == 'calendar_history'
    assert report.arguments == {'limit': 20, 'start_date': start, 'end_date': end, 'completed_only': True}


@pytest.mark.asyncio
@pytest.mark.parametrize('domain,query', [
    ('nutrition', '查询2026-09-01至2026-09-05的饮食记录'),
    ('nutrition', '查看昨晚的饮食记录'),
    ('workout_history', '列出2026-09-14和2026-09-20这两天已完成的训练记录'),
])
async def test_unsupported_report_scope_reaches_existing_model_path(monkeypatch, domain, query):
    from app.services import agent_runtime

    current = resolution(query, domain)
    expected = {'messages': [AIMessage(content='Synthetic semantic response; not a coverage claim.')]}
    invoke = AsyncMock(return_value=expected)
    create = Mock(return_value=SimpleNamespace(ainvoke=invoke))
    monkeypatch.setattr(agent_runtime, '_build_model', Mock(return_value=object()))
    monkeypatch.setattr(agent_runtime, 'create_agent', create)
    shortcut = AsyncMock(side_effect=AssertionError('Must not execute a widened report'))
    monkeypatch.setattr(agent_runtime, 'execute_query_report', shortcut)
    result = await agent_runtime.invoke_langchain_agent(
        None, user_id='synthetic-round26-query', history=[], user_message=query,
        tool_allowlist=route_tools(current), resolved_query=query, resolution=current,
    )
    assert result is expected
    shortcut.assert_not_awaited()
    invoke.assert_awaited_once()
    assert invoke.call_args.args[0]['messages'][-1] == {'role': 'user', 'content': query}
    assert [tool.name for tool in create.call_args.kwargs['tools']] == [name.replace('.', '_') for name in route_tools(current)]
