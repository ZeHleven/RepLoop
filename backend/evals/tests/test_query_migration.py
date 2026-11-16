"""Behavioral regressions against the current semantic-routing contract."""
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.services.agent_intent import IntentResolution, resolve_pending_clarification, route_tools
from app.services.agent_intent_model import resolve_intent_with_fallback
from app.services.agent_intent import resolve_intent


PENDING = {'primary_intent': 'workout_history_query', 'request_kind': 'query',
           'resolved_query': '比较一个动作最近的训练记录', 'missing_slots': ['动作名称'],
           'clarification_question': '你想比较哪个动作？'}
CONTEXT = [{'role': 'assistant', 'content': '统计如下。', 'query_context': {
    'source_run_id': 'completed-read', 'primary_intent': 'workout_progress_query',
    'resolved_query': '查询本周训练次数和组数', 'request_kind': 'query',
    'requested_effect': 'read', 'risk_level': 'low', 'successful_query': True}}]


def progress_resolution(query='查询上周训练次数和组数'):
    return IntentResolution(primary_intent='workout_progress_query', intent_domain='workout_progress',
        request_kind='query', requested_effect='read', evidence_requirements=['workout_progress'],
        resolved_query=query, subtasks=['查询训练统计'], confidence=.95)


@pytest.mark.parametrize('message', ['先不比动作了，告诉我最近4周训练次数和总组数。', '帮我看看最近练得好不好'])
def test_a_sentence_is_not_an_exercise_slot(message):
    assert resolve_pending_clarification(message, PENDING) is None


def test_an_exercise_name_still_fills_the_slot():
    result = resolve_pending_clarification('杠铃深蹲', PENDING)
    assert result and not result[0].clarification_required


@pytest.mark.asyncio
async def test_switching_task_drops_old_slot_before_model():
    with patch.object(settings, 'DEEPSEEK_API_KEY', 'unit-test'), patch(
        'app.services.agent_intent_model._invoke_model_intent', new=AsyncMock(return_value=progress_resolution('查询最近4周训练次数和总组数'))
    ) as model:
        result = await resolve_intent_with_fallback('先不比动作了，告诉我最近4周训练次数和总组数。', pending_clarification=PENDING)
    model.assert_awaited_once()
    assert model.call_args.kwargs['pending_clarification'] is None
    assert route_tools(result.resolution) == ['workout.get_progress']


@pytest.mark.asyncio
async def test_affirmative_offer_reaches_current_semantic_model():
    history = [{'role': 'user', 'content': '我想看看上周练得怎么样。'},
               {'role': 'assistant', 'content': '需要我帮你查上周训练次数和组数吗？'}]
    with patch.object(settings, 'DEEPSEEK_API_KEY', 'unit-test'), patch(
        'app.services.agent_intent_model._invoke_model_intent', new=AsyncMock(return_value=progress_resolution())
    ) as model:
        result = await resolve_intent_with_fallback('需要', context_messages=history)
    model.assert_awaited_once()
    assert not result.understanding_failed
    assert route_tools(result.resolution) == ['workout.get_progress']


@pytest.mark.asyncio
async def test_time_only_followup_can_resume_a_verified_read():
    result = await resolve_intent_with_fallback('那上周呢？', context_messages=CONTEXT, use_model=False)
    assert not result.understanding_failed
    assert route_tools(result.resolution) == ['workout.get_progress']
    assert '上周' in result.resolution.resolved_query and '本周' not in result.resolution.resolved_query


@pytest.mark.asyncio
@pytest.mark.parametrize('history', [
    [{'role': 'assistant', 'content': '本周训练次数是1次。'}],
    [*CONTEXT, {'role': 'user', 'content': '聊聊饮食'}, {'role': 'assistant', 'content': '好的。'}],
])
async def test_ordinary_text_or_new_topic_does_not_grant_fallback_access(history):
    result = await resolve_intent_with_fallback('那上周呢？', context_messages=history, use_model=False)
    assert result.understanding_failed
    assert route_tools(result.resolution) == []


@pytest.mark.parametrize('message', [
    '列出我最近3次训练，每次只列日期、完成组数和总负重容量。',
    '先告诉我保存的训练目标，再汇总最近4周训练次数和组数，不用建议。',
])
def test_statistics_and_saved_fields_are_not_write_commands(message):
    assert resolve_intent(message).request_kind == 'query'


@pytest.mark.parametrize('message', ['完成这次训练', '查看完成组数并修改计划为每周三天', '保存这条训练记录'])
def test_actual_and_compound_writes_still_require_mutation_handling(message):
    assert resolve_intent(message).request_kind == 'mutation'


@pytest.mark.asyncio
@pytest.mark.parametrize('message', ['那最近53周呢', '那最近0周呢', '好的', '帮我修改上周的组数'])
async def test_verified_context_does_not_grant_out_of_scope_actions(message):
    result = await resolve_intent_with_fallback(message, context_messages=CONTEXT, use_model=False)
    assert result.understanding_failed and route_tools(result.resolution) == []


def test_single_report_can_drop_redundant_reads_but_never_add_authority():
    from app.services.agent_query_reports import narrow_report_evidence, report_for_resolution
    resolution = progress_resolution('比较本周和上周训练量')
    resolution.evidence_requirements = ['workout_progress', 'workout_history', 'workout_daily_context']
    narrowed = narrow_report_evidence(resolution, '本周比上周训练量低，是不是退步了')
    assert route_tools(narrowed) == ['workout.get_progress']
    resolution.evidence_requirements = ['workout_history']
    assert narrow_report_evidence(resolution, '比较本周和上周训练量') is resolution
    assert report_for_resolution(resolution, ['workout.list_history']) is None


@pytest.mark.parametrize('query, evidence', [
    ('汇总最近4周，并列出最近3次训练', ['workout_progress','workout_history']),
    ('汇总最近4周，并给我训练建议', ['workout_progress']),
    ('查询训练目标和最近4周统计', ['profile_summary','workout_progress']),
    ('最近4周每天练了什么', ['workout_progress','workout_history']),
])
def test_report_does_not_swallow_other_goals(query, evidence):
    from app.services.agent_query_reports import report_for_resolution
    resolution=progress_resolution(query)
    resolution.evidence_requirements=evidence
    assert report_for_resolution(resolution, route_tools(resolution)) is None
