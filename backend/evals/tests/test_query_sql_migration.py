"""Real SQL and persisted conversation checks using synthetic local data only."""
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock

import pytest
from sqlalchemy import event, select

from app.models.agent import AgentConversation, AgentRun, AgentMessage, AgentToolCall
from app.models.user import User
from app.models.exercise import Exercise
from app.models.workout import WorkoutSession, SessionExercise
from app.services.agent_runtime import _load_history, execute_agent_run
from app.services.agent_intent import IntentResolution, IntentResolverOutcome
from app.services.agent_intent_model import resolve_intent_with_fallback
from app.services.workout_queries import get_workout_progress_summary
from app.services.workout_reporting import REPORT_DATE

TODAY = date(2026, 9, 22)
NOW = datetime(2026, 9, 22, 5, 0, tzinfo=timezone.utc)


async def seed(db):
    db.add_all([User(id='u', email='u@example.test', password_hash='unused'),
                User(id='v', email='v@example.test', password_hash='unused')])
    await db.commit()
    db.add_all([AgentConversation(id='c', user_id='u'), AgentConversation(id='d', user_id='v')])
    await db.commit()


def read_resolution(query='查询本周训练次数和组数'):
    return IntentResolution(primary_intent='workout_progress_query', intent_domain='workout_progress',
        resolved_query=query, evidence_requirements=['workout_progress'], confidence=.95)


@pytest.mark.asyncio
async def test_real_progress_preserves_ended_early_daily_and_excludes_future_and_other_user(db_session):
    db = db_session
    await seed(db)
    db.add(Exercise(id='ex', name_zh='深蹲', name_en='Squat', category='strength', difficulty='beginner'))
    await db.commit()
    for i, (user, when, status) in enumerate([
        ('u', date(2026, 9, 21), 'completed'), ('u', TODAY, 'ended_early'),
        ('u', date(2026, 9, 23), 'completed'), ('v', TODAY, 'completed'),
    ]):
        db.add(WorkoutSession(id=f's{i}', user_id=user, trained_at=when, status=status))
        await db.flush()
        db.add(SessionExercise(session_id=f's{i}', exercise_id='ex', exercise_name='深蹲',
            target_weight_kg=999, sets_data=[{'reps':10, 'weight_kg':50}]))
    await db.commit()
    result = await get_workout_progress_summary(db, user_id='u', weeks=4, today=TODAY)
    assert (result.total_sessions, result.total_sets, result.total_reps, result.total_volume_kg) == (2, 2, 20, 1000)
    assert result.average_denominator_weeks == 4
    assert result.averages_per_calendar_week['sets'] == .5
    assert result.weekly[-1].week_end == date(2026, 9, 27) and not result.weekly[-1].is_complete
    selected = await get_workout_progress_summary(db, user_id='u', weeks=4, today=TODAY, selected_week=TODAY)
    assert len(selected.daily) == 7 and [d.sessions for d in selected.daily] == [1,1,0,0,0,0,0]
    assert selected.selected_week == date(2026,9,21)


async def old_read(db, *, status='completed', audit=True, audit_value=1, user='u', conversation='c', effect='read'):
    run = AgentRun(id='old', user_id=user, conversation_id=conversation, status=status,
        queued_at=NOW-timedelta(minutes=1), primary_intent='workout_progress_query',
        intent_domain='workout_progress', resolved_query='查询本周训练次数和组数',
        request_kind='query', requested_effect=effect, tool_allowlist=['workout.get_progress'])
    db.add(run)
    await db.commit()
    # content_data deliberately contains no copied query context.
    db.add(AgentMessage(conversation_id=conversation, run_id='old', role='assistant', content='统计如下。', created_at=NOW-timedelta(seconds=30)))
    if audit:
        db.add(AgentToolCall(run_id='old', tool_name='workout.get_progress', arguments_data={'weeks':1}, result_data={'total_sessions':audit_value}, status='completed'))
    await db.commit()


@pytest.mark.asyncio
async def test_one_history_query_reuses_run_and_keeps_message_payload_small(db_session, engine):
    await seed(db_session)
    await old_read(db_session)
    current = AgentRun(id='current', user_id='u', conversation_id='c', queued_at=NOW)
    calls=[]
    def capture(*args): calls.append(args[2])
    event.listen(engine.sync_engine, 'before_cursor_execute', capture)
    try:
        history = await _load_history(db_session, conversation_id='c', before_run=current)
    finally:
        event.remove(engine.sync_engine, 'before_cursor_execute', capture)
    assert len(calls) == 1
    assert history[-1]['query_context']['source_run_id'] == 'old'
    outcome = await resolve_intent_with_fallback('那上周呢？', context_messages=history, use_model=False)
    assert not outcome.understanding_failed and outcome.resolution.evidence_requirements == ['workout_progress']
    assert '本周' not in outcome.resolution.resolved_query
    message = (await db_session.scalars(select(AgentMessage))).one()
    assert message.content_data == {}


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [
    {'status':'failed'}, {'audit':False}, {'audit_value':None},
    {'user':'v'}, {'conversation':'d'}, {'effect':'update'},
])
async def test_failed_unverified_and_cross_identity_runs_never_supply_context(db_session, changes):
    await seed(db_session)
    await old_read(db_session, **changes)
    current=AgentRun(id='current', user_id='u', conversation_id='c', queued_at=NOW)
    history = await _load_history(db_session, conversation_id='c', before_run=current)
    assert not any('query_context' in item for item in history)
    outcome = await resolve_intent_with_fallback('那上周呢？', context_messages=history, use_model=False)
    assert outcome.understanding_failed
    assert await _load_history(db_session, conversation_id='d', before_run=current) == []


@pytest.mark.asyncio
async def test_real_runtime_persists_audit_and_continues_followup_without_duplicate_context(db_session):
    db=db_session
    await seed(db)
    conversation=await db.get(AgentConversation,'c')
    first=AgentRun(id='first', user_id='u', conversation_id='c', queued_at=NOW)
    db.add(first)
    await db.commit()
    token=REPORT_DATE.set(TODAY)
    try:
        with patch('app.services.agent_runtime.resolve_intent_with_fallback', new=AsyncMock(return_value=
                IntentResolverOutcome(resolution=read_resolution(), source='model'))), patch(
                'app.services.agent_runtime._build_model', side_effect=AssertionError('report must not call free-form model')):
            await execute_agent_run(db, run=first, conversation=conversation, user_message='查本周训练次数和组数')
        assert first.status == 'completed', first.error_code
        assert first.execution_trace['budget_usage']['model_calls'] == 0
        assert first.execution_trace['budget_usage']['tool_calls'] == 1
        second=AgentRun(id='second', user_id='u', conversation_id='c', queued_at=NOW+timedelta(minutes=1))
        db.add(second)
        await db.commit()
        with patch('app.services.agent_runtime._build_model', side_effect=AssertionError('followup must reuse verified read')):
            await execute_agent_run(db, run=second, conversation=conversation, user_message='那上周呢？')
        assert second.status == 'completed', second.error_code
        assert second.intent_fallback_reason == 'verified_query_followup'
        audits=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id=='second'))).all()
        assert [a.arguments_data for a in audits] == [{'weeks':2}]
        messages=(await db.scalars(select(AgentMessage))).all()
        assert all('query_context' not in m.content_data for m in messages)
        assert all(m.created_at.microsecond for m in messages)
    finally:
        REPORT_DATE.reset(token)
