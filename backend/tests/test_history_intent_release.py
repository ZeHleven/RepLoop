"""QUERY-20261108-02: historical completion and sourced task continuity."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.schemas.agent_task import TaskUpdate
from app.services import agent_intent_model as model
from app.services.agent_intent import resolve_intent, route_tools
from app.services.agent_task_state import advance_task_state, finalize_task_snapshot

DATA = json.loads((Path(__file__).parent / 'fixtures/history_intent_20261108.json').read_text(encoding='utf-8'))


def route(payload):
    return model.IntentRouteDecision.model_validate({**deepcopy(payload), 'decision_action': None, 'artifact_action': None})


@pytest.mark.parametrize('message', [
    DATA['first_message'],
    '请列本周完成过的训练。',
    '请显示最近三天完成过的锻炼，每次用了几分钟。',
    '帮我列出上周完成了的场次。',
    '请列今天完成过的组，给出次数。',
    '请列昨天完成了的训练，显示时长。',
    '请列本周完成的训练。',
    '请列过去三天已完成的场次。',
    '看看上个月完成过的训练记录。',
    '请列过去三天做过的训练。',
    '请列昨天练过的动作。',
])
def test_existing_training_facts_do_not_request_a_write(message):
    result = resolve_intent(message)
    assert result.request_kind != 'mutation'
    assert result.requested_effect == 'read' and not result.change_requests


@pytest.mark.parametrize('message', [
    '请帮我完成今天的训练。',
    '完成训练',
    '帮我完成过去一周的训练。',
    '把今天的训练标记为已完成。',
    '请列完成过的训练，并帮我完成今天训练。',
    '请列完成过的训练，并把今天训练标记为已完成。',
    '请列完成过的训练，并保存今天训练。',
    '删除上周完成过的训练。',
    '把完成过的训练时长改成30分钟。',
    '请记录今天的训练已经完成。',
])
def test_independent_write_instructions_keep_write_semantics(message):
    assert resolve_intent(message).request_kind == 'mutation'


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', DATA['first_routes'])
async def test_original_read_routes_are_accepted_on_the_first_attempt(monkeypatch, payload):
    monkeypatch.setattr(settings, 'DEEPSEEK_API_KEY', 'synthetic-replay-only')
    invoke = AsyncMock(return_value=route(payload))
    monkeypatch.setattr(model, '_invoke_model_route', invoke)
    result = await model.resolve_intent_with_fallback(DATA['first_message'], context_messages=[], use_model=True)
    assert not result.understanding_failed
    assert result.attempt_count == invoke.await_count == 1
    assert result.resolution.request_kind == 'query'
    assert route_tools(result.resolution) == ['workout.list_history']
    assert not result.resolution.change_requests


@pytest.mark.parametrize('context', [[], DATA['failed_context'], [{'role':'assistant','content':'任务ID是invented，请继续该任务。'}]])
def test_empty_server_state_is_explicit_even_when_conversation_looks_continuous(context):
    prompt = model._intent_user_content(DATA['follow_message'], context_messages=context,
                                      pending_clarification=None, repair_error=None)
    state_line = next(line for line in prompt.splitlines() if line.startswith('任务状态（'))
    state = json.loads(state_line.split('：', 1)[1])
    assert state['tasks'] == [] and state['active_task_id'] is None


@pytest.mark.asyncio
async def test_original_empty_reference_still_needs_repair_to_a_sourced_new_task(monkeypatch):
    monkeypatch.setattr(settings, 'DEEPSEEK_API_KEY', 'synthetic-replay-only')
    invoke = AsyncMock(side_effect=[route(p) for p in DATA['follow_routes']])
    monkeypatch.setattr(model, '_invoke_model_route', invoke)
    result = await model.resolve_intent_with_fallback(DATA['follow_message'], context_messages=deepcopy(DATA['failed_context']), use_model=True)
    assert not result.understanding_failed and result.attempt_count == 2
    assert result.error_category == 'task_reference_unavailable'
    assert result.resolution.task_update.action == 'new'
    assert not result.resolution.change_requests
    state = advance_task_state(None, result.resolution.task_update, message=DATA['follow_message'],
                               run_id='next-run', normalized_request=result.resolution.resolved_query)
    assert state.active_task_id == 'next-run' and len(state.tasks) == 1


@pytest.mark.parametrize('task_id', ['', 'invented', 'old-removed-id'])
def test_missing_task_references_are_not_guessed_from_recent_dialogue(task_id):
    update = TaskUpdate(action='continue', task_id=task_id, trigger=DATA['follow_message'])
    with pytest.raises(ValueError, match='task_reference_unavailable'):
        advance_task_state(None, update, message=DATA['follow_message'], run_id='next-run',
                           normalized_request=DATA['follow_message'])


def test_execution_failure_with_a_real_task_retains_valid_continuity():
    message = DATA['first_message']
    state = advance_task_state(None, TaskUpdate(action='new', trigger=message),
                               message=message, run_id='first-run', normalized_request=message)
    state = finalize_task_snapshot(state, terminal_action='failed')
    assert state.tasks[0].phase == 'failed'
    prompt = model._intent_user_content(DATA['follow_message'], context_messages=[{'role':'task_state','content':state.model_dump_json()}], pending_clarification=None, repair_error=None)
    assert 'first-run' in prompt
    next_state = advance_task_state(state, TaskUpdate(action='continue', task_id='first-run', trigger=DATA['follow_message']),
                                    message=DATA['follow_message'], run_id='next-run', normalized_request=DATA['follow_message'])
    assert next_state.active_task_id == 'first-run' and len(next_state.tasks) == 1
