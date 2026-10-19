from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
import asyncio
from uuid import uuid4
from sqlalchemy import select, func

from app.models.exercise import Exercise
from app.models.workout import WorkoutSession, WorkoutPlan
from app.models.agent import AgentProposal
from app.services.training_lifecycle import training_today

from app.services.nutrition_energy import estimate_session


def sample(category, *, owner='user', equipment=None, valid=True):
    return (NS(id='row', exercise_id='exercise', exercise_name='测试动作',
               energy_category=category, energy_category_source='user_snapshot',
               energy_rule_version='strength_met_v1',
               sets_data=[{'reps': 8, 'weight_kg': 20}] if valid else []),
            NS(owner_id=owner, category='力量', equipment=equipment or ['barbell']))


def session():
    now = datetime.now(timezone.utc)
    return NS(id='session', status='completed', started_at=now,
              completed_at=now + timedelta(hours=1), plan_name='测试计划')


@pytest.mark.parametrize('categories,met', [
    (['resistance_training'], 3.5),
    (['bodyweight_resistance'], 3.0),
    (['bodyweight_resistance', 'resistance_training'], 3.5),
])
def test_explicit_custom_snapshots_enable_whole_session_estimate(categories, met):
    result = estimate_session(session(), [sample(c) for c in categories], weight=80, daily_baseline=2136)
    assert result['reason'] is None
    assert result['met'] == met
    assert result['net_kcal'] == pytest.approx(met * 80 - 89)


def test_unused_unknown_does_not_block_but_performed_unknown_is_actionable():
    result = estimate_session(session(), [sample('resistance_training'), sample(None, valid=False)], weight=80, daily_baseline=2136)
    assert result['met'] == 3.5
    result = estimate_session(session(), [sample(None)], weight=80, daily_baseline=2136)
    assert result['reason_code'] == 'classification_missing'
    assert result['unestimated_exercises'][0]['session_exercise_id'] == 'row'


def test_snapshots_not_live_catalog_drive_estimate_and_timing_still_wins():
    rows = [sample('bodyweight_resistance', owner=None, equipment=['barbell'])]
    assert estimate_session(session(), rows, weight=80, daily_baseline=2136)['met'] == 3.0
    invalid = session()
    invalid.completed_at = invalid.started_at + timedelta(hours=5)
    result = estimate_session(invalid, rows, weight=80, daily_baseline=2136)
    assert result['net_kcal'] is None
    assert result['reason_code'] == 'invalid_timing'


@pytest.mark.parametrize('category,equipment', [('resistance_training',['barbell']), ('bodyweight_resistance',['bodyweight'])])
def test_standard_and_custom_same_category_have_same_estimate(category, equipment):
    standard = estimate_session(session(), [sample(category, owner=None, equipment=equipment)], weight=80, daily_baseline=2136)
    custom = estimate_session(session(), [sample(category)], weight=80, daily_baseline=2136)
    assert (standard['met'], standard['net_kcal']) == (custom['met'], custom['net_kcal'])


@pytest.mark.parametrize('condition', ['in_progress', 'empty', 'reversed', 'long', 'rest_conflict', 'invalid_rest'])
def test_classification_cannot_bypass_invalid_training(condition):
    target, rows = session(), [sample('resistance_training')]
    if condition == 'in_progress': target.status = 'in_progress'
    if condition == 'empty': rows[0][0].sets_data = []
    if condition == 'reversed': target.completed_at = target.started_at - timedelta(seconds=1)
    if condition == 'long': target.completed_at = target.started_at + timedelta(hours=5)
    if condition == 'rest_conflict': rows[0][0].sets_data[0]['actual_rest_seconds'] = 3601
    if condition == 'invalid_rest': rows[0][0].sets_data[0]['actual_rest_seconds'] = -1
    result = estimate_session(target, rows, weight=80, daily_baseline=2136)
    assert result['net_kcal'] is None and result['reason_code'] != 'classification_missing'


def test_custom_long_rest_uses_existing_net_estimate_formula():
    rows = [sample('resistance_training')]
    rows[0][0].sets_data[0]['actual_rest_seconds'] = 900
    result = estimate_session(session(), rows, weight=80, daily_baseline=2136)
    assert result['effective_minutes'] == 55
    assert result['net_kcal'] == pytest.approx((280 - 89) * 55 / 60)


async def auth(client):
    r = await client.post('/api/v1/auth/register', json={'email': f'energy-{uuid4()}@example.com', 'password': 'pass1234'})
    assert r.status_code == 201, r.text
    return {'Authorization': 'Bearer ' + r.json()['access_token']}


async def create(client, headers, category=None):
    r = await client.post('/api/v1/exercises/custom', headers=headers, json={
        'name': '自定义' + str(uuid4()), 'description': '常规分组动作', 'energy_category': category,
    })
    assert r.status_code == 201, r.text
    return r.json()


async def log(client, headers, exercise):
    r = await client.post('/api/v1/workouts/sessions', headers=headers, json={
        'trained_at': str(training_today()), 'exercises': [{'exercise_id': exercise['exercise_id'], 'sets_data': [{'reps': 8, 'weight_kg': 20}]}],
    })
    assert r.status_code == 201, r.text
    return r.json()


def update_body(record, category='resistance_training', future=False):
    return {'expected_version': record['energy_classification_version'], 'update_future': future, 'exercises': [{
        'session_exercise_id': record['exercises'][0]['id'], 'energy_category': category,
        'expected_exercise_version': record['exercises'][0]['library_energy_category_version'],
    }]}


@pytest.mark.asyncio
async def test_session_only_and_future_updates_snapshots_energy_and_no_proposal(client, db_session):
    h = await auth(client)
    e = await create(client, h)
    first, other = await log(client, h, e), await log(client, h, e)
    stored = await db_session.get(WorkoutSession, first['id'])
    stored.started_at = stored.completed_at - timedelta(hours=1)
    await db_session.commit()
    proposals_before = await db_session.scalar(select(func.count()).select_from(AgentProposal))
    await client.put('/api/v1/profile', headers=h, json={'age':30,'gender':'male','height_cm':180,'weight_kg':80})
    before_sets = first['exercises'][0]['sets_data']
    path = f'/api/v1/workouts/sessions/{first["id"]}/energy-classifications'
    r = await client.put(path, headers=h, json=update_body(first))
    assert r.status_code == 200, r.text
    updated = r.json()
    assert updated['exercises'][0]['energy_category'] == 'resistance_training'
    assert updated['exercises'][0]['sets_data'] == before_sets
    assert updated['exercises'][0]['library_energy_category'] is None
    assert (await client.get(f'/api/v1/workouts/sessions/{other["id"]}', headers=h)).json()['exercises'][0]['energy_category'] is None
    # Replay is a conflict, not a second mutation; GET reconciles a lost response.
    assert (await client.put(path, headers=h, json=update_body(first))).status_code == 409
    r = await client.put(path, headers=h, json=update_body(updated, 'bodyweight_resistance', True))
    assert r.status_code == 200, r.text
    latest = r.json()
    assert latest['exercises'][0]['library_energy_category'] == 'bodyweight_resistance'
    assert (await log(client, h, e))['exercises'][0]['energy_category'] == 'bodyweight_resistance'
    assert (await client.get(f'/api/v1/workouts/sessions/{other["id"]}', headers=h)).json()['exercises'][0]['energy_category'] is None
    day = (await client.get('/api/v1/meals/today', headers=h)).json()['energy_estimate']
    assert next(w for w in day['workouts'] if w['session_id'] == first['id'])['met'] == 3
    assert day['status'] == 'partial'  # other manual records have invalid zero elapsed time
    assert await db_session.scalar(select(func.count()).select_from(AgentProposal)) == proposals_before


@pytest.mark.asyncio
async def test_library_changes_do_not_touch_started_or_finished_records(client, db_session):
    h = await auth(client)
    e = await create(client, h, 'bodyweight_resistance')
    plan = (await client.post('/api/v1/workouts/plans', headers=h, json={
        'name':'snapshot', 'exercises':[{'exercise_id':e['exercise_id'], 'day_of_week':1}],
    })).json()
    r = await client.post('/api/v1/workouts/sessions/start', headers=h, json={'plan_id':plan['id'],'day_of_week':1})
    assert r.status_code == 201, r.text
    started = r.json()
    assert started['exercises'][0]['energy_category'] == 'bodyweight_resistance'
    path = f'/api/v1/exercises/custom/{e["exercise_id"]}/energy-category'
    change = {'energy_category':'resistance_training','expected_version':e['energy_category_version']}
    assert (await client.put(path, headers=h, json=change)).status_code == 200
    assert (await client.put(path, headers=h, json=change)).status_code == 409
    assert (await client.get(f'/api/v1/workouts/sessions/{started["id"]}', headers=h)).json()['exercises'][0]['energy_category'] == 'bodyweight_resistance'
    assert (await client.put(f'/api/v1/workouts/sessions/{started["id"]}/energy-classifications', headers=h, json=update_body(started))).status_code == 409
    recorded = await log(client, h, e)
    await client.post(f'/api/v1/workouts/sessions/{started["id"]}/finish-early', headers=h)
    exercise = await db_session.get(Exercise, e['exercise_id'])
    exercise.is_active = False
    plan_row = await db_session.get(WorkoutPlan, plan['id'])
    plan_row.is_active = False
    await db_session.commit()
    assert (await client.delete(f'/api/v1/workouts/plans/{plan["id"]}', headers=h)).status_code == 204
    detail = (await client.get(f'/api/v1/workouts/sessions/{recorded["id"]}', headers=h)).json()
    assert detail['exercises'][0]['energy_category'] == 'resistance_training'
    assert detail['exercises'][0]['energy_classification_editable'] is True
    assert detail['exercises'][0]['library_energy_editable'] is False
    path = f'/api/v1/workouts/sessions/{recorded["id"]}/energy-classifications'
    assert (await client.put(path, headers=h, json=update_body(detail, future=True))).status_code == 409
    assert (await client.put(path, headers=h, json=update_body(detail))).status_code == 200


@pytest.mark.asyncio
async def test_batch_atomic_ownership_forgery_version_and_concurrency(client, db_session):
    h, stranger = await auth(client), await auth(client)
    e = await create(client, h)
    record = await log(client, h, e)
    path = f'/api/v1/workouts/sessions/{record["id"]}/energy-classifications'
    body = update_body(record, future=True)
    assert (await client.put(path, headers=stranger, json=body)).status_code == 404
    assert (await client.put(f'/api/v1/exercises/custom/{e["exercise_id"]}/energy-category', headers=stranger, json={'energy_category':'resistance_training','expected_version':0})).status_code == 404
    forged = {**body, 'exercises': [*body['exercises'], {**body['exercises'][0], 'session_exercise_id':str(uuid4())}]}
    assert (await client.put(path, headers=h, json=forged)).status_code == 404
    assert (await client.get('/api/v1/exercises/custom', headers=h)).json()[0]['energy_category_version'] == 0
    assert (await client.get(f'/api/v1/workouts/sessions/{record["id"]}', headers=h)).json()['energy_classification_version'] == 0
    wrong_version = {**body, 'exercises': [{**body['exercises'][0], 'expected_exercise_version':9}]}
    assert (await client.put(path, headers=h, json=wrong_version)).status_code == 409
    results = await asyncio.gather(client.put(path, headers=h, json=body), client.put(path, headers=h, json=body))
    assert sorted(r.status_code for r in results) == [200, 409]
    assert (await client.get(f'/api/v1/workouts/sessions/{record["id"]}', headers=h)).json()['energy_classification_version'] == 1
    assert (await client.get('/api/v1/exercises/custom', headers=h)).json()[0]['energy_category_version'] == 1


@pytest.mark.asyncio
async def test_classification_schema_rejects_client_met_and_invalid_category(client):
    h = await auth(client)
    for extra in ({'energy_category':'HIIT'}, {'met':6}, {'calories':100}):
        r = await client.post('/api/v1/exercises/custom', headers=h, json={'name':'测试','description':'方法',**extra})
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_correcting_only_unknown_session_restores_today_balance_without_plan_membership(client, db_session):
    h = await auth(client)
    e = await create(client, h)
    record = await log(client, h, e)
    stored = await db_session.get(WorkoutSession, record['id'])
    stored.started_at = stored.completed_at - timedelta(hours=1)
    stored.status = 'ended_early'
    stored.plan_name = '已不在当前计划的训练'
    await db_session.commit()
    r = await client.put('/api/v1/profile', headers=h, json={'age':30,'gender':'male','height_cm':180,'weight_kg':80})
    assert r.status_code == 200
    r = await client.post('/api/v1/meals', headers=h, json={'logged_at':str(training_today()),'meal_type':'午餐',
        'items':[{'food_name':'测试餐','amount_g':100,'calories':2000,'protein_g':100,'carbs_g':300,'fat_g':0}]})
    assert r.status_code == 201, r.text
    async def today(): return (await client.get('/api/v1/meals/today', headers=h)).json()['energy_estimate']
    before = await today()
    assert before['status'] == 'partial' and before['balance_kcal'] is None
    r = await client.put(f'/api/v1/workouts/sessions/{record["id"]}/energy-classifications', headers=h, json=update_body(record))
    assert r.status_code == 200, r.text
    after = await today()
    assert after['status'] == 'estimated'
    assert after['total_kcal'] == pytest.approx(2327)
    assert after['balance_kcal'] == pytest.approx(-327)
    assert after['workouts'][0]['plan_name'] == stored.plan_name
    assert (await today())['total_kcal'] == after['total_kcal']  # repeat reads never add twice


@pytest.mark.asyncio
async def test_standard_and_other_session_and_empty_rows_cannot_be_classified(client, db_session):
    h = await auth(client)
    standard = Exercise(name_zh='标准测试',name_en='standard',category='力量',difficulty='初级',equipment=['barbell'])
    db_session.add(standard); await db_session.commit()
    standard_record = await log(client, h, {'exercise_id': standard.id})
    # Shared suite keeps committed fixtures: don't leave our public catalog row selectable.
    standard.is_active = False
    await db_session.commit()
    path = f'/api/v1/workouts/sessions/{standard_record["id"]}/energy-classifications'
    assert (await client.put(path, headers=h, json=update_body(standard_record))).status_code == 404
    custom = await create(client, h)
    first, other = await log(client, h, custom), await log(client, h, custom)
    assert (await client.put(f'/api/v1/workouts/sessions/{first["id"]}/energy-classifications', headers=h, json=update_body(other))).status_code == 404
    from app.models.workout import SessionExercise
    row = await db_session.get(SessionExercise, first['exercises'][0]['id'])
    row.sets_data = []
    await db_session.commit()
    assert (await client.put(f'/api/v1/workouts/sessions/{first["id"]}/energy-classifications', headers=h, json=update_body(first))).status_code == 422
