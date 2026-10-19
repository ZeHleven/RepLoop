from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from app.services.training_lifecycle import training_today


async def auth(client):
    response = await client.post('/api/v1/auth/register', json={
        'email': f'nutrition-{uuid4()}@example.com', 'password': 'pass1234',
    })
    assert response.status_code == 201, response.text
    return {'Authorization': f'Bearer {response.json()["access_token"]}'}


def custom(**changes):
    return dict(name='自制酸奶', amount_g=200, calories=160, protein_g=12,
                carbs_g=20, fat_g=4, client_request_id=str(uuid4()), **changes)


@pytest.mark.asyncio
async def test_private_library_crud_idempotency_and_meal_snapshot(client):
    headers = await auth(client)
    body = custom()
    created = await client.post('/api/v1/foods/custom', headers=headers, json=body)
    assert created.status_code == 201, created.text
    food = created.json()
    assert food['source'] == 'custom' and food['calories_per_100g'] == 80
    again = await client.post('/api/v1/foods/custom', headers=headers, json=body)
    assert again.json()['id'] == food['id']
    conflict = await client.post('/api/v1/foods/custom', headers=headers, json={**body, 'calories': 200})
    assert conflict.status_code == 409
    library = await client.get('/api/v1/foods/library?scope=mine', headers=headers)
    assert [row['id'] for row in library.json()] == [food['id']]
    public = await client.get('/api/v1/foods')
    assert food['id'] not in [row['id'] for row in public.json()]
    assert (await client.get('/api/v1/meals/today', headers=headers)).json()['meals'] == []
    item = dict(food_name='客户端伪造名称', amount_g=150, calories=0, protein_g=0,
                carbs_g=0, fat_g=0, custom_food_id=food['id'], custom_food_version=1)
    meal_body = dict(logged_at=str(training_today()), meal_type='午餐', items=[item])
    meal = await client.post('/api/v1/meals', headers=headers, json=meal_body)
    assert meal.status_code == 201, meal.text
    snapshot = meal.json()['items'][0]
    assert snapshot['calories'] == 120 and snapshot['food_name'] == body['name']
    updated = await client.put(f'/api/v1/foods/custom/{food["id"]}', headers=headers,
                               json={k: v for k, v in {**body, 'calories': 200, 'version': 1}.items() if k != 'client_request_id'})
    assert updated.status_code == 200 and updated.json()['version'] == 2
    assert (await client.post('/api/v1/meals', headers=headers, json=meal_body)).status_code == 409
    assert (await client.delete(f'/api/v1/foods/custom/{food["id"]}?version=2', headers=headers)).status_code == 204
    assert (await client.get('/api/v1/foods/library?scope=mine', headers=headers)).json() == []
    today = (await client.get('/api/v1/meals/today', headers=headers)).json()
    assert len(today['meals']) == 1 and today['total_calories'] == 120
    # Editing an old meal explicitly uses its own immutable snapshot, not the library.
    edited = await client.put(f'/api/v1/meals/{meal.json()["id"]}', headers=headers,
                              json={**meal_body, 'items': [{k: v for k, v in snapshot.items() if k not in {'id', 'custom_food_id', 'custom_food_version'}}]})
    assert edited.status_code == 200 and edited.json()['items'][0]['calories'] == 120


@pytest.mark.asyncio
async def test_private_food_ownership_and_atomic_rejection(client):
    owner, other = await auth(client), await auth(client)
    food = (await client.post('/api/v1/foods/custom', headers=owner, json=custom())).json()
    assert (await client.get('/api/v1/foods/library')).status_code == 403  # existing HTTPBearer contract
    assert (await client.get('/api/v1/foods/library?scope=mine', headers=other)).json() == []
    assert (await client.delete(f'/api/v1/foods/custom/{food["id"]}?version=1', headers=other)).status_code == 404
    item = dict(food_name='test', amount_g=100, calories=10)
    bad = {**item, 'custom_food_id': food['id'], 'custom_food_version': 1}
    body = dict(logged_at=str(training_today()), meal_type='午餐', items=[item, bad])
    assert (await client.post('/api/v1/meals', headers=other, json=body)).status_code == 409
    assert (await client.get('/api/v1/meals/today', headers=other)).json()['meals'] == []
    assert (await client.post('/api/v1/meals', headers=owner, json={**body, 'items': [{**bad, 'food_id': 'other'}]})).status_code == 422


@pytest.mark.asyncio
async def test_energy_profile_activity_and_missing_data(client):
    headers = await auth(client)
    initial = (await client.get('/api/v1/meals/today', headers=headers)).json()
    assert initial['energy_estimate']['status'] == 'unavailable'
    await client.put('/api/v1/profile', headers=headers, json={
        'age': 30, 'gender': 'male', 'height_cm': 180, 'weight_kg': 80,
        'injuries': [], 'chronic_conditions': [], 'daily_activity_level': 'walking',
    })
    today = (await client.get('/api/v1/meals/today', headers=headers)).json()
    energy = today['energy_estimate']
    assert energy['bmr_kcal'] == 1780
    assert energy['total_kcal'] == pytest.approx(2492)
    assert energy['balance_kcal'] is None
    assert energy['activity_defaulted'] is False


def test_workout_energy_duration_rests_met_and_no_double_count():
    from app.services.nutrition_energy import estimate_session
    start = datetime(2026, 9, 8, 2, tzinfo=timezone.utc)
    session = NS(id='s', status='completed', started_at=start, completed_at=start + timedelta(hours=1))
    exercise = NS(owner_id=None, category='力量', equipment=['barbell'])
    rows = [(NS(sets_data=[{'reps': 10, 'weight_kg': 50, 'actual_rest_seconds': 900}]), exercise)]
    value = estimate_session(session, rows, weight=80, daily_baseline=2400)
    assert value['effective_minutes'] == 55
    assert value['net_kcal'] == pytest.approx((3.5 * 80 - 100) * 55 / 60)
    assert rows[0][0].sets_data[0]['actual_rest_seconds'] == 900
    exercise.owner_id = 'private'
    assert estimate_session(session, rows, weight=80, daily_baseline=2400)['reason']
    exercise.owner_id = None
    session.completed_at = start + timedelta(hours=5)
    assert estimate_session(session, rows, weight=80, daily_baseline=2400)['reason']


@pytest.mark.parametrize('gender,expected', [('male', 1780), ('female', 1614)])
def test_mifflin_formula(gender, expected):
    from app.services.nutrition_energy import resting_energy
    assert resting_energy(age=30, height=180, weight=80, gender=gender) == expected


@pytest.mark.parametrize('change,reason', [
    ('pending', '尚未结束'), ('empty', '没有有效'), ('aerobic', '耗能参数'),
    ('private', '自定义'), ('reverse', '时长异常'), ('long', '时长异常'),
    ('rest_exceeds_session', '不一致'), ('negative_rest', '无效'), ('no_end', '缺少'),
])
def test_unreliable_session_is_explicit_not_zero(change, reason):
    from app.services.nutrition_energy import estimate_session
    start = datetime(2026, 9, 8, 2, tzinfo=timezone.utc)
    session = NS(id='s', status='completed', started_at=start, completed_at=start + timedelta(hours=1))
    exercise = NS(owner_id=None, category='力量', equipment=['bodyweight'])
    record = NS(sets_data=[{'reps': 10, 'weight_kg': 0, 'actual_rest_seconds': 90}])
    if change == 'pending': session.status = 'in_progress'
    elif change == 'empty': record.sets_data = []
    elif change == 'aerobic': exercise.category = '有氧'
    elif change == 'private': exercise.owner_id = 'u'
    elif change == 'reverse': session.completed_at = start - timedelta(seconds=1)
    elif change == 'long': session.completed_at = start + timedelta(hours=5)
    elif change == 'rest_exceeds_session': record.sets_data[0]['actual_rest_seconds'] = 4000
    elif change == 'negative_rest': record.sets_data[0]['actual_rest_seconds'] = -1
    elif change == 'no_end': session.completed_at = None
    value = estimate_session(session, [(record, exercise)], weight=80, daily_baseline=2400)
    assert reason in value['reason'] and value['net_kcal'] is None


def test_auto_timing_ignores_client_duration_and_includes_normal_rest():
    from app.services.nutrition_energy import estimate_session
    start = datetime(2026, 9, 8, 2, tzinfo=timezone.utc)
    session = NS(id='s', status='ended_early', duration_min=999, started_at=start, completed_at=start + timedelta(minutes=30))
    record = NS(sets_data=[{'reps': 10, 'actual_rest_seconds': 90}])
    exercise = NS(owner_id=None, category='核心', equipment=['bodyweight'])
    value = estimate_session(session, [(record, exercise)], weight=80, daily_baseline=2400)
    assert value['effective_minutes'] == 30 and value['met'] == 3
    assert value['net_kcal'] == 70
    assert value == estimate_session(session, [(record, exercise)], weight=80, daily_baseline=2400)


@pytest.mark.asyncio
async def test_library_creation_concurrent_retry_and_numeric_validation(client):
    import asyncio
    headers = await auth(client)
    body = custom()
    responses = await asyncio.gather(*[client.post('/api/v1/foods/custom', headers=headers, json=body) for _ in range(3)])
    assert all(response.status_code == 201 for response in responses)
    assert len({response.json()['id'] for response in responses}) == 1
    for changes in ({'name': '   '}, {'amount_g': 0}, {'calories': ''}, {'fat_g': -1}, {'amount_g': 0.00001}):
        response = await client.post('/api/v1/foods/custom', headers=headers, json={**custom(), **changes})
        assert response.status_code == 422, response.text
    tiny_zero = {**custom(), 'amount_g': 1e-320, 'calories': 0, 'protein_g': 0, 'carbs_g': 0, 'fat_g': 0}
    response = await client.post('/api/v1/foods/custom', headers=headers, json=tiny_zero)
    assert response.status_code == 201 and response.json()['calories_per_100g'] == 0


@pytest.mark.asyncio
async def test_private_update_atomicity_and_standard_food_recalculation(client, db_session):
    from app.models.food import Food
    headers = await auth(client)
    food = (await client.post('/api/v1/foods/custom', headers=headers, json=custom())).json()
    body = dict(logged_at=str(training_today()), meal_type='午餐', items=[dict(food_name='旧餐', amount_g=100, calories=100)])
    meal = (await client.post('/api/v1/meals', headers=headers, json=body)).json()
    bad_item = dict(food_name='x', amount_g=100, calories=1, custom_food_id=food['id'], custom_food_version=99)
    response = await client.put(f'/api/v1/meals/{meal["id"]}', headers=headers, json={**body, 'items': body['items'] + [bad_item]})
    assert response.status_code == 409
    assert (await client.get('/api/v1/meals/today', headers=headers)).json()['total_calories'] == 100
    standard = Food(id=str(uuid4()), name_zh='标准米饭', category='主食', calories_per_100g=130, protein_g=3, carbs_g=28, fat_g=1)
    db_session.add(standard)
    await db_session.commit()
    response = await client.post('/api/v1/meals', headers=headers, json={**body, 'items': [{**body['items'][0], 'food_id': standard.id, 'amount_g': 200}]})
    assert response.json()['items'][0]['calories'] == 260


@pytest.mark.asyncio
async def test_today_partial_energy_does_not_hide_meals_and_finished_training_updates_balance(client, db_session):
    from app.models.workout import WorkoutSession, SessionExercise
    from app.models.exercise import Exercise
    from app.models.agent import AgentArtifact, AgentProposal
    from sqlalchemy import select, func
    headers = await auth(client)
    profile = (await client.put('/api/v1/profile', headers=headers, json={
        'age': 30, 'gender': 'male', 'height_cm': 180, 'weight_kg': 80,
        'injuries': [], 'chronic_conditions': [],
    })).json()
    await client.post('/api/v1/meals', headers=headers, json=dict(logged_at=str(training_today()), meal_type='早餐', items=[dict(food_name='早餐', amount_g=100, calories=1000)]))
    before = (await client.get('/api/v1/meals/today', headers=headers)).json()['energy_estimate']
    assert before['activity_defaulted'] and before['balance_kcal'] == pytest.approx(1000 - 1780 * 1.2)
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    session = WorkoutSession(user_id=profile['user_id'], trained_at=training_today(), status='in_progress', started_at=start)
    exercise = Exercise(name_zh='test', name_en='test', category='力量', difficulty='初级', equipment=['bodyweight'])
    db_session.add_all([session, exercise]); await db_session.flush()
    db_session.add(SessionExercise(session_id=session.id, exercise_id=exercise.id, sets_data=[{'reps': 10, 'actual_rest_seconds': 90}]))
    await db_session.commit()
    assert (await client.get('/api/v1/meals/today', headers=headers)).json()['energy_estimate']['training_kcal'] == 0
    session.status = 'ended_early'; session.completed_at = start + timedelta(hours=1)
    await db_session.commit()
    completed = (await client.get('/api/v1/meals/today', headers=headers)).json()['energy_estimate']
    assert completed['status'] == 'estimated' and completed['training_kcal'] > 0
    exercise.owner_id = profile['user_id']; await db_session.commit()
    partial = (await client.get('/api/v1/meals/today', headers=headers)).json()
    assert partial['total_calories'] == 1000 and partial['energy_estimate']['status'] == 'partial'
    assert partial['energy_estimate']['balance_kcal'] is None
    assert await db_session.scalar(select(func.count()).select_from(AgentArtifact).where(AgentArtifact.user_id == profile['user_id'])) == 0
    assert await db_session.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id == profile['user_id'])) == 0


@pytest.mark.asyncio
async def test_energy_failure_isolated_from_daily_meals(client, monkeypatch):
    headers = await auth(client)
    async def fail(*args): raise RuntimeError('private-sensitive-example-not-for-logs')
    monkeypatch.setattr('app.services.nutrition_energy.build_energy_estimate', fail)
    response = await client.get('/api/v1/meals/today', headers=headers)
    assert response.status_code == 200 and response.json()['meals'] == []
    assert response.json()['energy_estimate']['status'] == 'unavailable'
    assert 'private-sensitive' not in response.text


@pytest.mark.asyncio
async def test_activity_uses_current_profile_weight_and_medical_boundary(client):
    headers = await auth(client)
    await client.put('/api/v1/profile', headers=headers, json={'age': 30, 'gender': 'female', 'height_cm': 170, 'weight_kg': 60, 'daily_activity_level': 'physical_work'})
    first = (await client.get('/api/v1/meals/today', headers=headers)).json()['energy_estimate']
    await client.post('/api/v1/profile/weight', headers=headers, json={'weight_kg': 65})
    second = (await client.get('/api/v1/meals/today', headers=headers)).json()['energy_estimate']
    assert second['total_kcal'] - first['total_kcal'] == pytest.approx(80)
    await client.put('/api/v1/profile', headers=headers, json={'weight_kg': 70})
    third = (await client.get('/api/v1/meals/today', headers=headers)).json()['energy_estimate']
    assert third['profile_inputs']['weight_kg'] == 70  # newer direct profile edit beats older log
    for fields in ({'gender': 'prefer_not_to_say'}, {'gender': 'female', 'age': 16}, {'age': 30, 'chronic_conditions': ['孕期']}):
        await client.put('/api/v1/profile', headers=headers, json=fields)
        energy = (await client.get('/api/v1/meals/today', headers=headers)).json()['energy_estimate']
        assert energy['status'] == 'unavailable' and energy['balance_kcal'] is None


@pytest.mark.asyncio
async def test_missing_profile_weight_falls_back_only_to_owned_valid_log(client, db_session):
    from sqlalchemy import select
    from app.models.profile import UserProfile, WeightLog
    owner, other = await auth(client), await auth(client)
    for headers, weight in ((owner, 80), (other, 60)):
        await client.put('/api/v1/profile', headers=headers, json={
            'age': 30, 'gender': 'male', 'height_cm': 180, 'weight_kg': weight,
        })
    first = (await client.get('/api/v1/meals/today', headers=owner)).json()['energy_estimate']
    second = (await client.get('/api/v1/meals/today', headers=other)).json()['energy_estimate']
    assert first['activity_defaulted'] and second['activity_defaulted']
    assert first['total_kcal'] - second['total_kcal'] == pytest.approx(240)
    user_id = (await client.get('/api/v1/profile', headers=owner)).json()['user_id']
    profile = await db_session.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    profile.weight_kg = None
    db_session.add_all([
        WeightLog(user_id=user_id, weight_kg=75, recorded_at=datetime(2026, 9, 7, tzinfo=timezone.utc)),
        WeightLog(user_id=user_id, weight_kg=-1, recorded_at=datetime(2026, 9, 8, tzinfo=timezone.utc)),
    ])
    await db_session.commit()
    fallback = (await client.get('/api/v1/meals/today', headers=owner)).json()['energy_estimate']
    assert fallback['profile_inputs']['weight_kg'] == 75
    assert fallback['profile_inputs']['weight_source'] == 'weight_log'


@pytest.mark.asyncio
async def test_today_and_meal_validation_change_at_beijing_midnight(client, monkeypatch):
    from app.services import training_lifecycle
    class Clock(datetime):
        instant = datetime(2026, 9, 8, 15, 59, tzinfo=timezone.utc)
        @classmethod
        def now(cls, tz=None):
            return cls.instant.astimezone(tz) if tz else cls.instant.replace(tzinfo=None)
    monkeypatch.setattr(training_lifecycle, 'datetime', Clock)
    headers = await auth(client)
    await client.put('/api/v1/profile', headers=headers, json={
        'age': 30, 'gender': 'male', 'height_cm': 180, 'weight_kg': 80,
    })
    body = dict(logged_at='2026-09-08', meal_type='晚餐', items=[dict(food_name='记录', amount_g=100, calories=2200)])
    assert (await client.post('/api/v1/meals', headers=headers, json=body)).status_code == 201
    before = (await client.get('/api/v1/meals/today', headers=headers)).json()
    assert before['date'] == '2026-09-08' and before['energy_estimate']['balance_kcal'] > 0
    assert (await client.post('/api/v1/meals', headers=headers, json={**body, 'logged_at': '2026-09-09'})).status_code == 422
    Clock.instant = datetime(2026, 9, 8, 16, 1, tzinfo=timezone.utc)
    after = (await client.get('/api/v1/meals/today', headers=headers)).json()
    assert after['date'] == '2026-09-09' and after['meals'] == []
    assert after['energy_estimate']['balance_kcal'] is None
    zero = {**body, 'logged_at': '2026-09-09', 'items': [dict(food_name='无热量记录', amount_g=100, calories=0)]}
    assert (await client.post('/api/v1/meals', headers=headers, json=zero)).status_code == 201
    recorded = (await client.get('/api/v1/meals/today', headers=headers)).json()
    assert recorded['total_calories'] == 0
    assert recorded['energy_estimate']['balance_kcal'] == pytest.approx(-2136)


@pytest.mark.asyncio
async def test_today_query_budget_and_latency(client, db_session, engine, monkeypatch):
    import json
    import time
    from sqlalchemy import event
    from app.models.workout import WorkoutSession, SessionExercise
    from app.models.exercise import Exercise
    from app.services import nutrition_energy
    calculation_durations = []
    calculate = nutrition_energy.build_energy_estimate
    async def measured_calculate(*args):
        started = time.perf_counter()
        value = await calculate(*args)
        calculation_durations.append(round((time.perf_counter() - started) * 1000, 2))
        return value
    monkeypatch.setattr(nutrition_energy, 'build_energy_estimate', measured_calculate)
    headers = await auth(client)
    profile = (await client.put('/api/v1/profile', headers=headers, json={
        'age': 30, 'gender': 'male', 'height_cm': 180, 'weight_kg': 80,
    })).json()
    start = datetime.now(timezone.utc) - timedelta(hours=2)
    session = WorkoutSession(user_id=profile['user_id'], trained_at=training_today(), status='completed',
                             started_at=start, completed_at=start + timedelta(hours=1))
    exercise = Exercise(name_zh='基准测试动作', name_en='benchmark', category='力量', difficulty='初级', equipment=['barbell'])
    db_session.add_all([session, exercise]); await db_session.flush()
    db_session.add_all([SessionExercise(session_id=session.id, exercise_id=exercise.id, sets_data=[{'reps': 8, 'actual_rest_seconds': 30}]) for _ in range(30)])
    await db_session.commit()
    statements, durations, counts = [], [], []
    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.split()[0])  # no values or parameters retained
    event.listen(engine.sync_engine, 'before_cursor_execute', record)
    try:
        for _ in range(10):
            statements.clear(); started = time.perf_counter()
            response = await client.get('/api/v1/meals/today', headers=headers)
            durations.append(round((time.perf_counter() - started) * 1000, 2)); counts.append(len(statements))
            assert response.status_code == 200 and response.json()['energy_estimate']['status'] == 'estimated'
        assert max(counts) <= 9  # no per-exercise SQL loop
    finally:
        event.remove(engine.sync_engine, 'before_cursor_execute', record)
    print('NUTRITION_BENCHMARK ' + json.dumps({'requests': 10, 'exercise_rows': 30,
          'max_sql_statements': max(counts), 'median_ms': sorted(durations)[5], 'max_ms': max(durations),
          'energy_with_queries_median_ms': sorted(calculation_durations)[5],
          'energy_with_queries_max_ms': max(calculation_durations)}))
