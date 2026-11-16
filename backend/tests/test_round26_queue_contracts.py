"""Deterministic PostgreSQL regressions; synthetic data and no model calls."""
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from evals.postgres_test_fixtures import engine, session_factory  # noqa: F401
from app.models.agent import AgentConversation, AgentMessage, AgentRun
from app.models.user import User
from app.schemas.agent_task import ConversationTask, TaskSnapshot
from app.services.agent_jobs import AgentIdempotencyConflict, claim_next_agent_run, enqueue_agent_run
from app.services.agent_runtime import _load_history, run_agent_chat
from app.services.agent_task_state import load_task_snapshot
from app.services.ai_client import AIServiceError


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    # Each test gets an isolated schema in the explicitly supplied test database.
    yield


async def user_id(session_factory):
    async with session_factory() as db:
        row = User(id=uuid4().hex, email=uuid4().hex + '@example.invalid', password_hash='synthetic')
        db.add(row)
        await db.commit()
        return row.id


def trace(task_id, run_id):
    return {'task_state': TaskSnapshot(active_task_id=task_id, tasks=[
        ConversationTask(id=task_id, request=task_id, last_run_id=run_id),
    ]).model_dump(mode='json')}


@pytest.mark.parametrize('replay_message', ['same-message', 'changed-message'])
async def test_new_conversation_replay_after_first_lookup_returns_original_conversation(session_factory, replay_message):
    uid = await user_id(session_factory)
    missed = asyncio.Event()
    committed = asyncio.Event()
    request_id = uuid4().hex

    async def late_request():
        async with session_factory() as db:
            original_scalar = db.scalar
            first = True

            async def gated_scalar(*args, **kwargs):
                nonlocal first
                value = await original_scalar(*args, **kwargs)
                if first:
                    first = False
                    assert value is None
                    missed.set()
                    await asyncio.wait_for(committed.wait(), timeout=10)
                return value

            db.scalar = gated_scalar
            return await enqueue_agent_run(db, user_id=uid, user_message=replay_message,
                client_request_id=request_id, conversation=None)

    async def winning_request():
        await asyncio.wait_for(missed.wait(), timeout=10)
        async with session_factory() as db:
            result = await enqueue_agent_run(db, user_id=uid, user_message='same-message',
                client_request_id=request_id, conversation=None)
            committed.set()
            return result

    late, winner = await asyncio.gather(late_request(), winning_request(), return_exceptions=True)
    if replay_message != 'same-message':
        assert isinstance(late, AgentIdempotencyConflict), late
        async with session_factory() as db:
            assert await db.scalar(select(func.count()).select_from(AgentConversation).where(
                AgentConversation.user_id == uid)) == 1
            assert await db.scalar(select(func.count()).select_from(AgentRun).where(
                AgentRun.user_id == uid)) == 1
        return
    actual = {
        'same_run': late.run.id == winner.run.id,
        'same_response_conversation': late.conversation.id == winner.conversation.id,
        'response_matches_run': late.conversation.id == late.run.conversation_id,
    }
    async with session_factory() as db:
        actual['conversation_count'] = await db.scalar(select(func.count()).select_from(
            AgentConversation).where(AgentConversation.user_id == uid))
        actual['run_count'] = await db.scalar(select(func.count()).select_from(
            AgentRun).where(AgentRun.user_id == uid))
    assert actual == {'same_run': True, 'same_response_conversation': True,
        'response_matches_run': True, 'conversation_count': 1, 'run_count': 1}, actual


async def test_async_sync_async_restores_latest_task_and_history(session_factory, monkeypatch):
    uid = await user_id(session_factory)

    async def complete_synthetic_sync(db, *, run, conversation, **kwargs):
        run.status = 'completed'
        run.execution_trace = trace('latest-sync-task', run.id)
        db.add(AgentMessage(conversation_id=conversation.id, run_id=run.id,
            role='assistant', content='sync-answer'))
        await db.commit()
        return run

    monkeypatch.setattr('app.services.agent_runtime.execute_agent_run', complete_synthetic_sync)
    async with session_factory() as db:
        first = await enqueue_agent_run(db, user_id=uid, user_message='async-first',
            client_request_id=uuid4().hex, conversation=None)
        first.run.status = 'completed'
        first.run.execution_trace = trace('older-async-task', first.run.id)
        db.add(AgentMessage(conversation_id=first.conversation.id, run_id=first.run.id,
            role='assistant', content='async-answer'))
        await db.commit()
        sync_run = await run_agent_chat(db, user_id=uid, conversation=first.conversation,
            user_message='sync-latest')
        current = await enqueue_agent_run(db, user_id=uid, user_message='async-followup',
            client_request_id=uuid4().hex, conversation=first.conversation)
        snapshot = await load_task_snapshot(db, current.run)
        history = await _load_history(db, conversation_id=first.conversation.id, before_run=current.run)
        actual = {'active_task_id': snapshot.active_task_id,
            'positions': [first.run.queue_position, sync_run.queue_position, current.run.queue_position],
            'history': [item['content'] for item in history]}
        assert actual == {'active_task_id': 'latest-sync-task', 'positions': [1, 2, 3],
            'history': ['async-first', 'async-answer', 'sync-latest', 'sync-answer']}, actual


@pytest.mark.parametrize('pending_status', ['queued', 'running'])
async def test_sync_compatibility_cannot_overtake_pending_async_work(session_factory, monkeypatch, pending_status):
    uid = await user_id(session_factory)

    async def must_not_execute(*args, **kwargs):
        raise AssertionError('Synchronous execution must not overtake accepted work')

    monkeypatch.setattr('app.services.agent_runtime.execute_agent_run', must_not_execute)
    async with session_factory() as db:
        first = await enqueue_agent_run(db, user_id=uid, user_message='accepted-first',
            client_request_id=uuid4().hex, conversation=None)
        first.run.status = pending_status
        await db.commit()
        with pytest.raises(AIServiceError, match='当前会话仍有请求正在处理'):
            await run_agent_chat(db, user_id=uid, conversation=first.conversation, user_message='try-to-overtake')
        assert await db.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.user_id == uid)) == 1
        assert await db.scalar(select(func.count()).select_from(AgentMessage).where(
            AgentMessage.conversation_id == first.conversation.id)) == 1


async def test_legacy_null_group_and_numbered_runs_cannot_form_a_claim_cycle(session_factory):
    uid = await user_id(session_factory)
    async with session_factory() as db:
        conversation = AgentConversation(user_id=uid)
        db.add(conversation)
        await db.flush()
        now = datetime.now(timezone.utc)
        # The former hybrid relation formed A < B < legacy < A and claimed none.
        numbered_first = AgentRun(user_id=uid, conversation_id=conversation.id,
            status='queued', queue_position=1, queued_at=now + timedelta(seconds=10))
        numbered_second = AgentRun(user_id=uid, conversation_id=conversation.id,
            status='queued', queue_position=2, queued_at=now)
        legacy = AgentRun(user_id=uid, conversation_id=conversation.id,
            status='queued', queue_position=None, queued_at=now + timedelta(seconds=5))
        db.add_all([numbered_first, numbered_second, legacy])
        await db.commit()
        for expected in [legacy, numbered_first, numbered_second]:
            assert await claim_next_agent_run(db) == expected.id
            expected.status = 'completed'
            expected.lease_expires_at = None
            await db.commit()
        assert await claim_next_agent_run(db) is None


async def test_first_numbered_request_restores_legacy_history_despite_reversed_time(session_factory):
    uid = await user_id(session_factory)
    async with session_factory() as db:
        conversation = AgentConversation(user_id=uid)
        db.add(conversation)
        await db.flush()
        now = datetime.now(timezone.utc)
        legacy = AgentRun(user_id=uid, conversation_id=conversation.id, status='completed',
            queue_position=None, queued_at=now + timedelta(seconds=10))
        db.add(legacy)
        await db.flush()
        legacy.execution_trace = trace('legacy-task', legacy.id)
        db.add_all([
            AgentMessage(conversation_id=conversation.id, run_id=legacy.id, role='user',
                content='legacy-request', created_at=now + timedelta(seconds=10)),
            AgentMessage(conversation_id=conversation.id, run_id=legacy.id, role='assistant',
                content='legacy-answer', created_at=now + timedelta(seconds=11)),
        ])
        await db.commit()
        current = await enqueue_agent_run(db, user_id=uid, user_message='current',
            client_request_id=uuid4().hex, conversation=conversation)
        assert current.run.queue_position == 1
        assert (await load_task_snapshot(db, current.run)).active_task_id == 'legacy-task'
        assert [item['content'] for item in await _load_history(db,
            conversation_id=conversation.id, before_run=current.run)] == ['legacy-request', 'legacy-answer']
