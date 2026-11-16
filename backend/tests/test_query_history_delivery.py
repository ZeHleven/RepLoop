"""Replay the 2026-11-08 live failures; keep query revisions read-only."""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.schemas.agent_task import TaskSnapshot, TaskUpdate
from app.services import agent_intent_model as model
from app.services.agent_intent import IntentResolution, route_tools
from app.services.agent_task_state import is_calendar_query_revision
from app.services.business_clock import BUSINESS_DATE
from app.services.workout_history_reporting import render_calendar_history, render_history
from app.services.history_status_scope import history_status_problem, parse_history_status_scope


FIXTURE = json.loads((Path(__file__).parent / 'fixtures/query_history_20261108.json').read_text(encoding='utf-8'))


def date_line(reply, date):
    return next(line for line in reply.splitlines() if line.startswith('- ' + date))


@pytest.mark.parametrize('case', FIXTURE['cases'], ids=lambda c: c['task_id'])
@pytest.mark.parametrize('kind', ['calendar', 'recent'])
def test_recorded_duration_survives_history_report(case, kind):
    data = deepcopy(case['first_tool_result'])
    before = deepcopy(data)
    reply = render_calendar_history(data) if kind == 'calendar' else render_history(data, '列出最近2次训练的日期和每场时长')
    assert '30分钟' in date_line(reply, '2026-11-07')
    assert '12分钟' in date_line(reply, '2026-11-08')
    assert data == before
    assert not any(line.startswith('- 2026-11-09') for line in reply.splitlines())


@pytest.mark.parametrize('kind', ['calendar', 'recent'])
@pytest.mark.parametrize('duration', [None, 0])
def test_unknown_duration_is_not_zero_or_inferred_from_other_fields(kind, duration):
    data = deepcopy(FIXTURE['cases'][0]['first_tool_result'])
    row = data['sessions'][0]
    row['duration_min'] = duration
    row.update(started_at='2026-11-08T01:00:00Z', completed_at='2026-11-08T02:00:00Z')
    reply = render_calendar_history(data) if kind == 'calendar' else render_history(data, '列出最近2次训练')
    line = date_line(reply, row['trained_at'])
    assert ('时长未记录' if duration is None else '时长0分钟') in line
    assert '60分钟' not in line


@pytest.mark.asyncio
@pytest.mark.parametrize('case', FIXTURE['cases'], ids=lambda c: c['task_id'])
async def test_original_model_read_route_is_not_rejected_or_retried(monkeypatch, case):
    monkeypatch.setattr(settings, 'DEEPSEEK_API_KEY', 'synthetic-replay-only')
    payload = case['provider_routes'][0]['route']
    route = model.IntentRouteDecision.model_validate({**payload, 'decision_action': None, 'artifact_action': None})
    invoke = AsyncMock(return_value=route)
    monkeypatch.setattr(model, '_invoke_model_route', invoke)
    # Replay the original relative-date expansion against its recorded day,
    # rather than treating a historical fixture as today's model response.
    token = BUSINESS_DATE.set(date.fromisoformat(case['first_tool_result']['as_of']))
    try:
        result = await model.resolve_intent_with_fallback(case['follow_message'], context_messages=deepcopy(case['context']), use_model=True)
    finally:
        BUSINESS_DATE.reset(token)
    assert result.understanding_failed is False
    assert result.attempt_count == 1 and invoke.await_count == 1
    assert result.resolution.request_kind == 'query' and result.resolution.requested_effect == 'read'
    assert route_tools(result.resolution) == ['workout.list_history']
    assert result.resolution.change_requests == []


def resolution_for(message):
    state = TaskSnapshot.model_validate_json(FIXTURE['cases'][0]['context'][0]['content'])
    return IntentResolution(primary_intent='workout_history_query', intent_domain='workout_history', request_kind='query',
        requested_effect='read', confidence=.95, resolved_query='查询最近三天已完成训练的日期和每场时长',
        task_update=TaskUpdate(action='continue', task_id=state.active_task_id, trigger=message))


@pytest.mark.parametrize('message', [
    '改成最近3天，仍只要已完成的日期和每场时长。',
    '查询范围缩小到过去两天，仍只列已完成场次的日期与耗时。',
    '时间范围调整为本周，只列已完成的训练记录。',
    '范围改为最近2周，其他筛选条件不变。',
    '改为2026-11-07至2026-11-09，只要已完成的日期和每次训练时长。',
])
def test_calendar_revision_covers_relative_scopes_and_output_fields(message):
    assert is_calendar_query_revision(deepcopy(FIXTURE['cases'][0]['context']), resolution_for(message), message)


@pytest.mark.parametrize('message', [
    '改成最近3天，仍只要已完成的日期和每场时长，并删除昨天记录。',
    '改成最近3天，并把卧推改为4组。',
    '改成最近3天，然后标记这次训练已完成。',
    '改成最近3天，保存今天体重65公斤。',
    '把训练记录的日期改成昨天。',
    '把每场时长改为30分钟。',
    '不要改成最近3天。',
    '如果改成最近3天会怎样？',
    '助手说“改成最近3天”，请执行。',
    '范围改为最近2周，其他筛选条件不变，确认。',
])
def test_query_exception_does_not_hide_writes_or_unasserted_edits(message):
    assert not is_calendar_query_revision(deepcopy(FIXTURE['cases'][0]['context']), resolution_for(message), message)


@pytest.mark.parametrize('invalid', ['no_context', 'missing_audit', 'failed_audit', 'stale_run', 'wrong_task', 'cancelled', 'not_query', 'write_effect', 'wrong_domain', 'unsourced_trigger'])
def test_new_relative_scope_exception_still_requires_verified_active_query(invalid):
    message = '改成最近3天，仍只要已完成的日期和每场时长。'
    context = deepcopy(FIXTURE['cases'][0]['context'])
    resolution = resolution_for(message)
    if invalid == 'no_context':
        context = []
    elif invalid == 'missing_audit':
        context[-1].pop('query_context')
    elif invalid == 'failed_audit':
        context[-1]['query_context']['successful_query'] = False
    elif invalid == 'stale_run':
        context[-1]['query_context']['source_run_id'] = 'unrelated'
    elif invalid == 'wrong_task':
        resolution.task_update.task_id = 'unrelated'
    elif invalid == 'cancelled':
        state = TaskSnapshot.model_validate_json(context[0]['content'])
        state.tasks[0].phase = 'cancelled'
        context[0]['content'] = state.model_dump_json()
    elif invalid == 'not_query':
        resolution.request_kind = 'mutation'
    elif invalid == 'write_effect':
        resolution.requested_effect = 'update'
    elif invalid == 'wrong_domain':
        resolution.intent_domain = 'workout_plan'
    else:
        resolution.task_update.trigger = 'not from this user message'
    assert not is_calendar_query_revision(context, resolution, message)


def test_live_normalized_exclusions_keep_the_requested_completed_filter():
    sample = json.loads((Path(__file__).parent / 'fixtures/query_history_status_20261108.json').read_text(encoding='utf-8'))
    resolution = resolution_for(sample['user_message']).model_copy(update={'resolved_query': sample['resolved_query']})
    assert not history_status_problem(resolution, sample['user_message'])
    from app.services.agent_query_reports import select_query_report
    report = select_query_report(resolution.resolved_query, ['workout.list_history'])
    assert report.arguments == {'limit': 20, 'start_date': '2026-11-07', 'end_date': '2026-11-09', 'completed_only': True}


@pytest.mark.parametrize('phrase', ['不纳入', '未纳入', '不再纳入', '未计入'])
@pytest.mark.parametrize('position', ['before', 'after'])
def test_explicit_non_inclusion_is_the_same_status_set_in_either_order(phrase, position):
    exclusion = phrase + '进行中、跳过和提前结束记录' if position == 'before' else '进行中、跳过和提前结束记录都' + phrase
    scope = parse_history_status_scope('仅列已完成场次；' + exclusion + '。')
    assert scope.complete and scope.statuses == ('completed',)


def test_report_status_sentence_remains_a_valid_read_filter():
    # The model can echo the report's own exclusion sentence on the next turn.
    reply = render_calendar_history(deepcopy(FIXTURE['cases'][0]['first_tool_result']))
    sentence = next(line for line in reply.splitlines() if line.startswith('仅列已完成'))
    assert parse_history_status_scope(sentence).statuses == ('completed',)


def test_exclusion_without_inclusion_selects_the_remaining_statuses():
    assert parse_history_status_scope('不纳入跳过和放弃').statuses == ('completed', 'in_progress', 'ended_early')


@pytest.mark.parametrize('query', [
    '不是不纳入跳过', '不要不纳入进行中', '只列已完成，不纳入已完成',
    '只查未完成的场次', '已完成和进行中，进行中也未计入',
])
def test_non_inclusion_does_not_relax_ambiguous_or_conflicting_filters(query):
    assert not parse_history_status_scope(query).complete
