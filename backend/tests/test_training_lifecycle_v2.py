import uuid
import asyncio
from datetime import date, timedelta

import pytest
from sqlalchemy import select
from unittest.mock import patch

from app.models.exercise import Exercise
from app.models.workout import SessionExercise, WorkoutPlan, WorkoutSession
from app.services.training_lifecycle import training_week, display_plan_name


async def setup_training(client, db):
    auth = await client.post('/api/v1/auth/register', json={
        'email': f'training-{uuid.uuid4()}@example.com', 'password': 'pass1234',
    })
    headers = {'Authorization': f'Bearer {auth.json()["access_token"]}'}
    exercise = Exercise(id=str(uuid.uuid4()), name_zh='测试划船', name_en='Test Row',
                        category='力量', difficulty='初级', equipment=['bodyweight'])
    db.add(exercise)
    await db.commit()
    plan = await client.post('/api/v1/workouts/plans', headers=headers, json={
        'name': '周训练计划', 'days_per_week': 1,
        'exercises': [{'exercise_id': exercise.id, 'day_of_week': 1}],
    })
    assert plan.status_code == 201, plan.text
    return headers, plan.json(), exercise


async def start_and_record(client, headers, plan):
    response = await client.post('/api/v1/workouts/sessions/start', headers=headers,
                                 json={'plan_id': plan['id'], 'day_of_week': 1})
    assert response.status_code == 201, response.text
    session = response.json()
    path = f'/api/v1/workouts/sessions/{session["id"]}/exercises/{session["exercises"][0]["id"]}/sets/1'
    response = await client.put(path, headers=headers, json={'reps': 8, 'weight_kg': 25})
    assert response.status_code == 200, response.text
    return response.json(), path


@pytest.mark.asyncio
async def test_completed_day_is_readable_and_cannot_restart(client, db_session):
    headers, plan, _ = await setup_training(client, db_session)
    session, _ = await start_and_record(client, headers, plan)
    result = await client.post(f'/api/v1/workouts/sessions/{session["id"]}/complete',
                               headers=headers, json={})
    assert result.status_code == 200, result.text
    again = await client.post('/api/v1/workouts/sessions/start', headers=headers,
                              json={'plan_id': plan['id'], 'day_of_week': 1})
    assert again.status_code == 409
    detail = (await client.get(f'/api/v1/workouts/plans/{plan["id"]}', headers=headers)).json()
    assert detail['weekly_completed_days'] == 1
    assert detail['weekly_sessions'][0]['session_id'] == session['id']


@pytest.mark.asyncio
async def test_archived_plan_removal_preserves_sessions(client, db_session):
    headers, plan, _ = await setup_training(client, db_session)
    session, _ = await start_and_record(client, headers, plan)
    await client.post(f'/api/v1/workouts/sessions/{session["id"]}/complete', headers=headers, json={})
    stored = await db_session.get(WorkoutPlan, plan['id'])
    stored.is_active = False
    await db_session.commit()
    removed = await client.delete(f'/api/v1/workouts/plans/{plan["id"]}', headers=headers)
    assert removed.status_code == 204
    assert (await client.get('/api/v1/workouts/plans', headers=headers)).json() == []
    detail = await client.get(f'/api/v1/workouts/sessions/{session["id"]}', headers=headers)
    assert detail.json()['total_sets'] == 1
    assert await db_session.get(WorkoutPlan, plan['id']) is not None


@pytest.mark.asyncio
async def test_end_orphan_preserves_sets_and_releases_active_slot(client, db_session):
    headers, plan, _ = await setup_training(client, db_session)
    session, _ = await start_and_record(client, headers, plan)
    row = await db_session.get(WorkoutSession, session['id'])
    row.plan_id = None
    await db_session.commit()
    result = await client.post(f'/api/v1/workouts/sessions/{session["id"]}/finish-early', headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()['status'] == 'ended_early'
    assert result.json()['total_sets'] == 1
    assert (await client.get('/api/v1/workouts/sessions/active', headers=headers)).json() is None
    retry = await client.post(f'/api/v1/workouts/sessions/{session["id"]}/finish-early', headers=headers)
    assert retry.json()['total_sets'] == 1


@pytest.mark.asyncio
async def test_actual_rest_is_idempotent_and_not_overwritten_by_set_edit(client, db_session):
    headers, plan, _ = await setup_training(client, db_session)
    session, path = await start_and_record(client, headers, plan)
    payload = {'actual_rest_seconds': 17, 'event_id': str(uuid.uuid4()), 'end_reason': 'next_set'}
    result = await client.put(path + '/rest', headers=headers, json=payload)
    assert result.status_code == 200, result.text
    assert (await client.put(path + '/rest', headers=headers, json=payload)).status_code == 200
    edited = await client.put(path, headers=headers, json={'reps': 9, 'weight_kg': 26})
    record = edited.json()['exercises'][0]['sets_data'][0]
    assert record['actual_rest_seconds'] == 17
    assert record['reps'] == 9
    assert (await client.put(path + '/rest', headers=headers, json={**payload, 'actual_rest_seconds': 50})).status_code == 409


@pytest.mark.asyncio
async def test_concurrent_starts_claim_only_one_session(client, db_session):
    headers, plan, _ = await setup_training(client, db_session)
    async def start():
        return await client.post('/api/v1/workouts/sessions/start', headers=headers,
                                 json={'plan_id': plan['id'], 'day_of_week': 1})
    results = await asyncio.gather(start(), start())
    assert sorted(row.status_code for row in results) == [201, 409]
    sessions = (await client.get('/api/v1/workouts/sessions', headers=headers)).json()
    assert len(sessions) == 1


@pytest.mark.asyncio
async def test_next_week_resets_completion_but_keeps_previous_record(client, db_session):
    headers, plan, _ = await setup_training(client, db_session)
    session, _ = await start_and_record(client, headers, plan)
    await client.post(f'/api/v1/workouts/sessions/{session["id"]}/complete', headers=headers, json={})
    next_week = training_week() + timedelta(days=7)
    with patch('app.services.workout_queries.training_week', return_value=next_week), patch('app.routers.workouts.training_week', return_value=next_week):
        detail = (await client.get(f'/api/v1/workouts/plans/{plan["id"]}', headers=headers)).json()
        assert detail['weekly_completed_days'] == 0
        new, _ = await start_and_record(client, headers, plan)
        assert new['id'] != session['id']
    assert len((await client.get('/api/v1/workouts/sessions', headers=headers)).json()) == 2


@pytest.mark.asyncio
async def test_daily_drilldown_is_bounded_and_counts_early_actual_work(client, db_session):
    headers, plan, _ = await setup_training(client, db_session)
    session, _ = await start_and_record(client, headers, plan)
    await client.post(f'/api/v1/workouts/sessions/{session["id"]}/finish-early', headers=headers)
    week = training_week()
    result = await client.get('/api/v1/workouts/sessions/progress', headers=headers, params={'week_start': str(week)})
    assert result.status_code == 200, result.text
    payload = result.json()
    assert len(payload['daily']) == 7
    assert payload['daily'][0]['date'] == str(week)
    assert payload['total_sets'] == 1
    assert sum(day['volume_kg'] for day in payload['daily']) == payload['total_volume_kg'] == 200
    outside = await client.get('/api/v1/workouts/sessions/progress', headers=headers, params={'week_start': str(week - timedelta(weeks=9))})
    assert outside.status_code == 422


@pytest.mark.asyncio
async def test_detail_snapshot_and_rest_are_immutable_after_completion(client, db_session):
    headers, plan, exercise = await setup_training(client, db_session)
    session, path = await start_and_record(client, headers, plan)
    await client.post(f'/api/v1/workouts/sessions/{session["id"]}/complete', headers=headers, json={})
    exercise.name_zh = '已改名动作'
    await db_session.commit()
    detail = (await client.get(f'/api/v1/workouts/sessions/{session["id"]}', headers=headers)).json()
    assert detail['exercises'][0]['exercise_name'] == '测试划船'
    assert 'actual_rest_seconds' not in detail['exercises'][0]['sets_data'][0]
    assert (await client.put(path + '/rest', headers=headers, json={'actual_rest_seconds': 90, 'event_id': 'late-rest', 'end_reason': 'next_set'})).status_code == 409


@pytest.mark.asyncio
async def test_private_exercises_are_allowed_with_notice_not_shared_or_forgeable(client, db_session):
    first_headers, _, _ = await setup_training(client, db_session)
    second_headers, _, _ = await setup_training(client, db_session)
    definition = {'name': '全新自定义组合', 'description': '用户自行描述的计次动作'}
    response = await client.post('/api/v1/exercises/custom', headers=first_headers, json=definition)
    assert response.status_code == 201, response.text
    option = response.json()
    assert '仅提供记录与计划管理' in option['safety_notice']
    retry = await client.post('/api/v1/exercises/custom', headers=first_headers, json=definition)
    assert retry.json()['exercise_id'] == option['exercise_id']
    assert len((await client.get('/api/v1/exercises/custom', headers=first_headers)).json()) == 1
    assert (await client.get('/api/v1/exercises/custom', headers=second_headers)).json() == []
    public = (await client.get('/api/v1/exercises', params={'limit': 50})).json()
    assert option['exercise_id'] not in [row['id'] for row in public]
    unauthorized = await client.post('/api/v1/workouts/sessions', headers=second_headers, json={
        'trained_at': str(date.today()), 'exercises': [{'exercise_id': option['exercise_id'], 'sets_data': [{'reps': 8}]}],
    })
    assert unauthorized.status_code == 422
    no_auth = await client.post('/api/v1/exercises/custom', json=definition)
    assert no_auth.status_code == 403


@pytest.mark.asyncio
async def test_custom_known_injury_conflict_is_blocked_without_insert(client, db_session):
    headers, plan, _ = await setup_training(client, db_session)
    response = await client.put('/api/v1/profile', headers=headers, json={'injuries': ['肩关节']})
    assert response.status_code == 200, response.text
    response = await client.post('/api/v1/exercises/custom', headers=headers,
                                 json={'name': '我的新动作', 'description': '在卧推基础上增加负重'})
    assert response.status_code == 409, response.text
    rows = (await db_session.execute(select(Exercise).where(Exercise.owner_id == plan['user_id']))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_cross_user_cannot_end_read_or_remove_training(client, db_session):
    headers, plan, _ = await setup_training(client, db_session)
    other_headers, _, _ = await setup_training(client, db_session)
    session, path = await start_and_record(client, headers, plan)
    assert (await client.get(f'/api/v1/workouts/sessions/{session["id"]}', headers=other_headers)).status_code == 404
    assert (await client.post(f'/api/v1/workouts/sessions/{session["id"]}/finish-early', headers=other_headers)).status_code == 404
    assert (await client.delete(f'/api/v1/workouts/plans/{plan["id"]}', headers=other_headers)).status_code == 404
    assert (await client.put(path + '/rest', headers=other_headers, json={'event_id': 'foreign-rest', 'actual_rest_seconds': 10, 'end_reason': 'next_set'})).status_code == 404


def test_week_calendar_and_generated_name_are_conservative():
    assert training_week(date(2026, 9, 6)) == date(2026, 8, 31)
    assert training_week(date(2026, 9, 7)) == date(2026, 9, 7)
    assert display_plan_name('增肌 · 6日入门计划', generated=True) == '增肌 · 6日训练计划'
    assert display_plan_name('增肌 · 6日入门计划', generated=False) == '增肌 · 6日入门计划'
    assert display_plan_name('我的入门挑战', generated=True) == '我的入门挑战'


@pytest.mark.asyncio
async def test_custom_plan_preview_selection_and_proposal_revalidate_health(client, db_session, monkeypatch):
    from test_workout_router import complete_onboarding
    from app.config import settings
    monkeypatch.setattr(settings, 'MANUAL_PLAN_PROPOSALS_ENABLED', True)
    headers, plan, _ = await setup_training(client, db_session)
    token = headers['Authorization'].split(' ', 1)[1]
    await complete_onboarding(client, token)
    custom = (await client.post('/api/v1/exercises/custom', headers=headers, json={
        'name': '用户自定义推拉', 'description': '我自行描述的方法', 'contraindications': ['肩关节'],
    })).json()
    context = (await client.get(f'/api/v1/workouts/plans/{plan["id"]}/edit-context', headers=headers)).json()
    assert any(row['exercise_id'] == custom['exercise_id'] and row['safety_notice'] for row in context['exercise_options'])
    original = context['base_plan']['exercises'][0]
    candidate = {
        'duration_weeks': 4, 'training_days': [1], 'exercises': [{
            **{key: value for key, value in original.items() if key not in {'category', 'exercise_name'}},
            'exercise_id': custom['exercise_id'],
        }],
    }
    proposal = await client.post(f'/api/v1/workouts/plans/{plan["id"]}/adjustment-proposals', headers=headers, json={
        'client_request_id': 'custom-replace-candidate', 'expected_base_fingerprint': context['base_plan_fingerprint'], 'candidate': candidate,
    })
    assert proposal.status_code == 201, proposal.text
    unchanged = (await client.get(f'/api/v1/workouts/plans/{plan["id"]}', headers=headers)).json()
    assert unchanged['exercises'][0]['exercise_id'] != custom['exercise_id']
    await client.put('/api/v1/profile', headers=headers, json={'injuries': ['肩关节']})
    confirmed = await client.post(f'/api/v1/proposals/{proposal.json()["id"]}/confirm', headers=headers,
                                  json={'client_request_id': 'custom-confirm-changed-health', 'expected_version': 1})
    assert confirmed.status_code == 409, confirmed.text
    still = (await client.get('/api/v1/workouts/plans', headers=headers)).json()
    assert len(still) == 1 and still[0]['exercises'][0]['exercise_id'] != custom['exercise_id']


@pytest.mark.asyncio
async def test_custom_exercise_can_be_manually_added_to_personalized_draft(client, db_session):
    from test_workout_router import complete_onboarding
    headers, plan, _ = await setup_training(client, db_session)
    await complete_onboarding(client, headers['Authorization'].split(' ', 1)[1])
    existing = await db_session.get(WorkoutPlan, plan['id'])
    existing.is_active = False
    await db_session.commit()
    custom = (await client.post('/api/v1/exercises/custom', headers=headers, json={
        'name': '新私有组合动作', 'description': '无额外可核验安全资料',
    })).json()
    preview = await client.post('/api/v1/workouts/plans/personalized/preview', headers=headers, json={'days_per_week': 1})
    assert preview.status_code == 200, preview.text
    payload = preview.json()
    assert all(item['exercise_id'] != custom['exercise_id'] for item in payload['exercises'])
    payload['exercises'].append({'exercise_id': custom['exercise_id'], 'exercise_name': '客户端伪造名称',
        'category': '力量', 'sets': 2, 'reps': '8', 'rest_seconds': 60,
        'day_of_week': 1, 'order_index': len(payload['exercises'])})
    confirmed = await client.post('/api/v1/workouts/plans/personalized/confirm', headers=headers, json=payload)
    assert confirmed.status_code == 201, confirmed.text
    real = next(row for row in confirmed.json()['exercises'] if row['exercise_id'] == custom['exercise_id'])
    assert real['exercise_name'] == '新私有组合动作'
    assert real['safety_notice']
    assert '入门' not in confirmed.json()['name']
