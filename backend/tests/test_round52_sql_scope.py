"""Real SQL plan reads distinguish request date, lifecycle counters and ownership."""
from datetime import date,datetime,timezone
from uuid import uuid4
import pytest
import pytest_asyncio
from sqlalchemy import select
from evals.postgres_test_fixtures import engine,session_factory,db_session
from app.models.user import User
from app.models.exercise import Exercise
from app.models.workout import WorkoutPlan,PlannedExercise,WorkoutSession
from app.services.business_clock import BUSINESS_DATE,wall_today
from app.services import training_lifecycle
from app.services.agent_tools import build_read_tools
from app.services.workout_queries import build_plan_detail

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

async def seed(db):
    uid='r52-'+uuid4().hex;foreign=uid+'-other'
    db.add_all([User(id=u,email=u+'@example.invalid',password_hash='synthetic') for u in [uid,foreign]])
    ex=Exercise(id=uid+'-ex',name_zh='合成动作',name_en='Synthetic',category='strength',difficulty='beginner',muscle_primary=['chest'])
    db.add(ex);await db.flush()
    plan=WorkoutPlan(user_id=uid,name='Synthetic scope',days_per_week=2);other=WorkoutPlan(user_id=foreign,name='Other scope')
    db.add_all([plan,other]);await db.flush()
    for day in [1,7]:db.add(PlannedExercise(plan_id=plan.id,exercise_id=ex.id,day_of_week=day,sets=2,reps='8'))
    def session(day,*,linked=True,owner=uid,source=plan):
        d=date.fromisoformat(day);kw=dict(user_id=owner,plan_id=source.id,plan_name=source.name,trained_at=d,status='completed',started_at=datetime.fromisoformat(day+'T08:00:00+00:00'),completed_at=datetime.fromisoformat(day+'T09:00:00+00:00'))
        if linked:kw.update(plan_family_id=source.family_id,week_start=training_lifecycle.training_week(d),day_of_week=d.isoweekday())
        return WorkoutSession(**kw)
    rows=[session('2026-10-05'),session('2026-10-07',linked=False),session('2026-10-11'),session('2026-10-12'),session('2026-10-05',owner=foreign,source=other)]
    db.add_all(rows);await db.commit();return uid,plan,rows

@pytest.mark.asyncio
async def test_baseline_agent_plan_week_matches_request_not_wall(db_session,monkeypatch):
    uid,plan,rows=await seed(db_session)
    monkeypatch.setattr(training_lifecycle,'training_today',lambda:date(2026,10,13))
    token=BUSINESS_DATE.set(date(2026,10,9))
    try:
        tools=build_read_tools(db_session,user_id=uid,allowlist=['plan.get_active','workout.get_progress'])
        actual=await tools[0].ainvoke({});progress=await tools[1].ainvoke({'weeks':1})
        assert actual['plan']['week_start']=='2026-10-05'
        assert actual['scope']['as_of']==progress['as_of']=='2026-10-09'
        assert actual['plan']['weekly_completed_days']==1
        assert actual['plan']['weekly_sessions'][0]['session_id']==rows[0].id
        assert progress['total_sessions']==2  # One linked plan day plus a legacy history session.
        assert actual['scope']['completed_days_basis']=='current_plan_continuity_and_scheduled_weekday'
        assert actual['scope']['history_session_count_is_distinct'] is True
        assert len((await db_session.scalars(select(WorkoutSession))).all())==5
        assert wall_today()==datetime.now(timezone.utc).astimezone(training_lifecycle.TRAINING_TIMEZONE).date()
    finally:BUSINESS_DATE.reset(token)

@pytest.mark.asyncio
@pytest.mark.parametrize('supplied',[False,True])
async def test_baseline_explicit_asof_filters_future_in_sql_and_supplied_rows(db_session,monkeypatch,supplied):
    uid,plan,rows=await seed(db_session)
    monkeypatch.setattr(training_lifecycle,'training_today',lambda:date(2026,10,13))
    detail=await build_plan_detail(db_session,plan,as_of=date(2026,10,9),**({'weekly_sessions':rows} if supplied else {}))
    assert detail.week_start==date(2026,10,5) and detail.weekly_completed_days==1
    assert [s.session_id for s in detail.weekly_sessions]==[rows[0].id]
    current=await build_plan_detail(db_session,plan)
    assert current.week_start==date(2026,10,12) and current.weekly_completed_days==1
