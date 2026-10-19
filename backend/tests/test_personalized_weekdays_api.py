import pytest

from app.models.exercise import Exercise


async def account(client, name):
    response = await client.post('/api/v1/auth/register', json={
        'email': f'weekday-{name}@example.com', 'password': 'pass1234',
    })
    headers = {'Authorization': f"Bearer {response.json()['access_token']}"}
    response = await client.put('/api/v1/profile', headers=headers, json={
        'age': 30, 'gender': 'prefer_not_to_say', 'height_cm': 170, 'weight_kg': 65,
        'experience_level': 'beginner', 'primary_goal': 'general_fitness',
        'training_days_per_week': 3, 'session_duration_min': 40, 'training_location': 'home',
        'diet_restriction': 'none', 'injuries': [], 'chronic_conditions': [], 'onboarding_completed': True,
    })
    assert response.status_code == 200
    return headers


@pytest.mark.asyncio
@pytest.mark.parametrize('days', [[2, 4, 7], [6, 7], [7], [1, 2, 3, 4, 5, 6, 7]])
async def test_selected_weekdays_roundtrip_without_preview_writes(client, db_session, days):
    variant = ''.join(map(str, days))
    db_session.add(Exercise(id=f'weekday-{variant}', name_zh='测试徒手划臂', name_en=f'Test arm row {variant}', category='力量',
        muscle_primary=['back'], difficulty='初级', movement_pattern='pull', equipment=['bodyweight'], is_active=True))
    await db_session.commit()
    headers = await account(client, variant)
    response = await client.post('/api/v1/workouts/plans/personalized/preview',
        headers=headers, json={'training_days': list(reversed(days))})
    assert response.status_code == 200
    preview = response.json()
    assert preview['days_per_week'] == len(days)
    assert {row['day_of_week'] for row in preview['exercises']} == set(days)
    assert (await client.get('/api/v1/workouts/plans', headers=headers)).json() == []
    candidate = {**preview, 'training_days': days}
    # The same day count must not mask a different selected weekday.
    if len(days) < 7:
        wrong_days = sorted(set(days[1:]) | {next(day for day in range(1, 8) if day not in days)})
        invalid = await client.post('/api/v1/workouts/plans/personalized/confirm',
            headers=headers, json={**candidate, 'training_days': wrong_days})
        assert invalid.status_code == 422
        assert (await client.get('/api/v1/workouts/plans', headers=headers)).json() == []
    response = await client.post('/api/v1/workouts/plans/personalized/confirm', headers=headers, json=candidate)
    assert response.status_code == 201
    plan = response.json()
    assert {row['day_of_week'] for row in plan['exercises']} == set(days)
    assert plan['days_per_week'] == len(days)
    assert (await client.post('/api/v1/workouts/plans/personalized/confirm', headers=headers, json=candidate)).status_code == 409
    assert len((await client.get('/api/v1/workouts/plans', headers=headers)).json()) == 1
    start = await client.post('/api/v1/workouts/sessions/start', headers=headers,
        json={'plan_id': plan['id'], 'day_of_week': days[-1]})
    assert start.status_code == 201
    assert start.json()['status'] == 'in_progress'
    assert start.json()['exercises']


@pytest.mark.asyncio
async def test_invalid_selected_days_are_rejected_before_writes(client):
    headers = await account(client, 'invalid')
    item = {'exercise_id': 'not-in-db', 'exercise_name': '动作', 'category': '力量',
            'day_of_week': 1, 'sets': 3, 'reps': '8', 'rest_seconds': 90, 'order_index': 0}
    candidate = {'name': '新计划', 'goal': 'strength', 'duration_weeks': 4, 'days_per_week': 1,
                 'session_duration_min': 40, 'exercises': [item]}
    for days in ([], [0], [8], [1, 1], [True], [1.5], ['2']):
        response = await client.post('/api/v1/workouts/plans/personalized/preview',
            headers=headers, json={'training_days': days})
        assert response.status_code == 422
        response = await client.post('/api/v1/workouts/plans/personalized/confirm',
            headers=headers, json={**candidate, 'training_days': days})
        assert response.status_code == 422
    response = await client.post('/api/v1/workouts/plans/personalized/preview',
        headers=headers, json={'training_days': [2, 7], 'days_per_week': 3})
    assert response.status_code == 422
    assert (await client.get('/api/v1/workouts/plans', headers=headers)).json() == []
