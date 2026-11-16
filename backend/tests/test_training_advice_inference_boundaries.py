"""Real SQL counterexamples and model-input contracts, not generated-answer scores."""
from contextvars import ContextVar
from datetime import date
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.exercise import Exercise
from app.models.user import User
from app.models.workout import PlannedExercise, WorkoutPlan, WorkoutSession
from app.services import business_clock, training_lifecycle
from app.services.agent_tools import build_read_tools


@pytest.fixture(autouse=True)
def fixed_business_date(monkeypatch):
    # Reproduce the original Wednesday observation, independently of wall time.
    monkeypatch.setattr(business_clock, 'BUSINESS_DATE',
                        ContextVar('inference_business_date', default=date(2026, 11, 11)))
    # Plan-week queries use the training calendar, separately from Agent requests.
    monkeypatch.setattr(training_lifecycle, 'training_today', lambda: date(2026, 11, 11))


@pytest.mark.asyncio
@pytest.mark.parametrize('case,session_dates,arrangement_week,expected_days', [
    ('same-plan-unassigned', [date(2026, 11, 9), date(2026, 11, 10)], None, 0),
    ('previous-week', [date(2026, 11, 2)], date(2026, 11, 2), 0),
    ('two-sessions-one-arranged-day', [date(2026, 11, 9)] * 2, date(2026, 11, 9), 1),
    ('no-records', [], None, 0),
])
async def test_week_summary_does_not_establish_training_onset(
    db_session, case, session_dates, arrangement_week, expected_days,
):
    tag = uuid4().hex
    owner = User(id=tag, email=tag + '@example.invalid', password_hash='synthetic')
    exercise = Exercise(id=tag + '-ex', name_zh='合成动作', name_en='Synthetic',
                        category='strength', difficulty='beginner')
    db_session.add_all([owner, exercise])
    await db_session.flush()
    plan = WorkoutPlan(user_id=owner.id, name='推断边界合成计划')
    db_session.add(plan)
    await db_session.flush()
    db_session.add(PlannedExercise(plan_id=plan.id, exercise_id=exercise.id,
                                  day_of_week=1, sets=3, reps='10', rest_seconds=150))
    for trained_at in session_dates:
        db_session.add(WorkoutSession(
            user_id=owner.id, plan_id=plan.id, plan_family_id=plan.family_id,
            trained_at=trained_at, status='completed', duration_min=30,
            week_start=arrangement_week, day_of_week=1 if arrangement_week else None,
        ))
    await db_session.commit()
    statement = select(WorkoutSession.__table__).where(WorkoutSession.user_id == owner.id)
    before = (await db_session.execute(statement)).mappings().all()
    tools = build_read_tools(db_session, user_id=owner.id,
                             allowlist=['plan.get_active', 'workout.list_history'])
    summary = await tools[0].ainvoke({})
    history = await tools[1].ainvoke({
        'start_date': '2026-11-01', 'end_date': '2026-11-11', 'completed_only': True,
    })
    after = (await db_session.execute(statement)).mappings().all()
    if directory := os.environ.get('PREP_EVIDENCE_DIR'):
        (Path(directory) / f'inference-{case}.json').write_text(json.dumps({
            'summary': summary, 'history': history,
            'before': [dict(row) for row in before], 'after': [dict(row) for row in after],
        }, ensure_ascii=False, indent=2, default=str), encoding='utf-8')

    # A zero weekly count is compatible with completed sessions in this same plan.
    # Conversely, two correctly assigned sessions still count as one training day.
    assert summary['plan']['weekly_completed_days'] == expected_days
    assert history['total_count'] == len(session_dates) == history['count']
    assert history['truncated'] is False
    assert sorted(item['trained_at'] for item in history['sessions']) == sorted(
        item.isoformat() for item in session_dates)
    assert all(item['plan_id'] == plan.id for item in history['sessions'])
    assert before == after and not db_session.dirty
    scope = summary['weekly_completion_scope']
    assert scope['week_start'] == '2026-11-09'
    assert scope['includes_prior_weeks'] is False
    # This is a limit on the observation, NOT a claim that the user has trained.
    assert scope['establishes_first_training'] is False
    assert scope['includes_unlinked_sessions'] is False
