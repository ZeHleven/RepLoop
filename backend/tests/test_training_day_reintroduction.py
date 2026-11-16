"""A removed/reintroduced weekday is new work, not an old completed occurrence."""
import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.config import settings
from app.services.training_day_continuity import TrainingDayContinuity
from test_training_lifecycle_v2 import setup_training
from test_workout_router import complete_onboarding


def recorded_snapshot(detail):
    # Comparison baselines/best performances are live aggregates of all sessions,
    # not immutable fields of this particular workout's recorded snapshot.
    live = {'previous_sets_data', 'personal_best_weight_kg', 'personal_best_reps'}
    return {**detail, 'exercises': [
        {key: value for key, value in exercise.items() if key not in live}
        for exercise in detail['exercises']
    ]}


async def revise_days(client, headers, plan, exercise, days, *, sets=2):
    context = (await client.get(
        f'/api/v1/workouts/plans/{plan["id"]}/edit-context', headers=headers,
    )).json()
    existing = {item['day_of_week']: item for item in context['base_plan']['exercises']}
    candidate = {'duration_weeks': 4, 'training_days': days, 'exercises': []}
    for day in days:
        candidate['exercises'].append({
            'item_key': existing.get(day, {}).get('item_key', f'new:{uuid.uuid4()}'),
            'exercise_id': exercise.id, 'day_of_week': day,
            'sets': sets, 'reps': '8', 'rest_seconds': 60,
            'recommended_weight_kg': None, 'order_index': 0,
        })
    proposal = await client.post(
        f'/api/v1/workouts/plans/{plan["id"]}/adjustment-proposals', headers=headers,
        json={'client_request_id': str(uuid.uuid4()),
              'expected_base_fingerprint': context['base_plan_fingerprint'],
              'candidate': candidate},
    )
    assert proposal.status_code == 201, proposal.text
    confirmation = await client.post(
        f'/api/v1/proposals/{proposal.json()["id"]}/confirm', headers=headers,
        json={'client_request_id': str(uuid.uuid4()), 'expected_version': 1},
    )
    assert confirmation.status_code == 200, confirmation.text
    return (await client.get(
        f'/api/v1/workouts/plans/{confirmation.json()["result_plan_id"]}', headers=headers,
    )).json()


async def complete_day(client, headers, plan, day):
    started = await client.post('/api/v1/workouts/sessions/start', headers=headers,
                                json={'plan_id': plan['id'], 'day_of_week': day})
    assert started.status_code == 201, started.text
    session = started.json()
    path = f'/api/v1/workouts/sessions/{session["id"]}'
    recorded = await client.put(
        path + f'/exercises/{session["exercises"][0]["id"]}/sets/1',
        headers=headers, json={'reps': 8, 'weight_kg': 10},
    )
    assert recorded.status_code == 200, recorded.text
    completed = await client.post(path + '/complete', headers=headers, json={})
    assert completed.status_code == 200, completed.text
    proposal = completed.json().get('adaptive_adjustment_proposal')
    if proposal:
        rejected = await client.post(
            f'/api/v1/proposals/{proposal["id"]}/reject', headers=headers,
            json={'client_request_id': str(uuid.uuid4()), 'expected_version': proposal['version']},
        )
        assert rejected.status_code == 200, rejected.text
    return (await client.get(path, headers=headers)).json()


async def removed_then_reintroduced(client, db, monkeypatch):
    monkeypatch.setattr(settings, 'MANUAL_PLAN_PROPOSALS_ENABLED', True)
    headers, plan, exercise = await setup_training(client, db)
    await complete_onboarding(client, headers['Authorization'].split(' ', 1)[1])
    old_plan = await revise_days(client, headers, plan, exercise, [1, 3])
    monday = await complete_day(client, headers, old_plan, 1)
    wednesday = await complete_day(client, headers, old_plan, 3)
    reduced = await revise_days(client, headers, old_plan, exercise, [1])
    # Hiding archived cards must not erase the evidence of the removed weekday.
    hidden = await client.delete(f'/api/v1/workouts/plans/{reduced["id"]}', headers=headers)
    assert hidden.status_code == 409  # active plan still cannot be hidden
    restored = await revise_days(client, headers, reduced, exercise, [1, 3])
    assert (await client.delete(f'/api/v1/workouts/plans/{reduced["id"]}', headers=headers)).status_code == 204
    return headers, restored, monday, wednesday, exercise


@pytest.mark.asyncio
async def test_reintroduced_day_is_not_completed_in_detail_or_list(client, db_session, monkeypatch):
    headers, plan, monday, old_wednesday, _ = await removed_then_reintroduced(client, db_session, monkeypatch)
    detail = (await client.get(f'/api/v1/workouts/plans/{plan["id"]}', headers=headers)).json()
    listed = next(row for row in (await client.get('/api/v1/workouts/plans', headers=headers)).json()
                  if row['id'] == plan['id'])
    for current in [detail, listed]:
        assert current['weekly_completed_days'] == 1
        assert current['weekly_sessions'] == [
            {'day_of_week': 1, 'session_id': monday['id'], 'status': 'completed'},
        ]
    for original in [monday, old_wednesday]:
        history = (await client.get(f'/api/v1/workouts/sessions/{original["id"]}', headers=headers)).json()
        assert recorded_snapshot(history) == recorded_snapshot(original)


@pytest.mark.asyncio
async def test_reintroduced_day_can_start_once_but_retained_day_cannot(client, db_session, monkeypatch):
    headers, plan, monday, old_wednesday, _ = await removed_then_reintroduced(client, db_session, monkeypatch)
    denied = await client.post('/api/v1/workouts/sessions/start', headers=headers,
                               json={'plan_id': plan['id'], 'day_of_week': 1})
    assert denied.status_code == 409
    assert denied.json()['detail']['session_id'] == monday['id']

    async def start():
        return await client.post('/api/v1/workouts/sessions/start', headers=headers,
                                 json={'plan_id': plan['id'], 'day_of_week': 3})
    results = await asyncio.gather(start(), start())
    assert sorted(row.status_code for row in results) == [201, 409]
    new = next(row.json() for row in results if row.status_code == 201)
    assert new['id'] != old_wednesday['id']
    detail = (await client.get(f'/api/v1/workouts/plans/{plan["id"]}', headers=headers)).json()
    assert next(row for row in detail['weekly_sessions'] if row['day_of_week'] == 3) == {
        'day_of_week': 3, 'session_id': new['id'], 'status': 'in_progress',
    }


@pytest.mark.asyncio
async def test_completed_reintroduced_day_survives_parameter_edit_and_restarts_after_another_removal(
    client, db_session, monkeypatch,
):
    headers, plan, _, old_wednesday, exercise = await removed_then_reintroduced(client, db_session, monkeypatch)
    new_wednesday = await complete_day(client, headers, plan, 3)
    edited = await revise_days(client, headers, plan, exercise, [1, 3], sets=3)
    assert edited['weekly_completed_days'] == 2
    assert next(row for row in edited['weekly_sessions'] if row['day_of_week'] == 3)['session_id'] == new_wednesday['id']
    denied = await client.post('/api/v1/workouts/sessions/start', headers=headers,
                               json={'plan_id': edited['id'], 'day_of_week': 3})
    assert denied.status_code == 409
    reduced = await revise_days(client, headers, edited, exercise, [1], sets=3)
    restored = await revise_days(client, headers, reduced, exercise, [1, 3], sets=3)
    assert restored['weekly_completed_days'] == 1
    for original in [old_wednesday, new_wednesday]:
        history = (await client.get(f'/api/v1/workouts/sessions/{original["id"]}', headers=headers)).json()
        assert recorded_snapshot(history) == recorded_snapshot(original)


@pytest.mark.parametrize(('target', 'source', 'day', 'expected'), [
    ('restored', 'root', 3, False),
    ('restored', 'root', 1, True),
    ('edited', 'restored', 3, True),
    ('edited', 'root', 3, False),
    ('reduced', 'root', 3, False),
    ('restored', 'reduced', 3, False),
    ('edited', 'edited', 3, True),
    ('legacy', 'root', 3, True),
    ('restored', None, 3, True),
])
def test_day_continuity_only_resets_with_confirmed_evidence(target, source, day, expected):
    context = TrainingDayContinuity(
        dict.fromkeys(['root', 'reduced', 'restored', 'edited', 'legacy'], 'root'),
        {'root': {1, 3}, 'reduced': {1}, 'restored': {1, 3}, 'edited': {1, 3}, 'legacy': {1, 3}},
        {'reduced': 'root', 'restored': 'reduced', 'edited': 'restored'},
    )
    plan = SimpleNamespace(id=target, user_id='owner', family_id='root')
    session = SimpleNamespace(plan_id=source, user_id='owner', plan_family_id='root', day_of_week=day)
    assert context.matches(plan, session) is expected
    assert context.matches(plan, session) is expected  # memoized result is identical
    session.user_id = 'other'
    assert context.matches(plan, session) is False


def test_incomplete_or_cyclic_lineage_does_not_unlock_completed_day():
    context = TrainingDayContinuity(
        {'a': 'root', 'b': 'root', 'root': 'root'},
        {'a': {1}, 'b': {1}, 'root': {1}}, {'a': 'b', 'b': 'a'},
    )
    plan = SimpleNamespace(id='a', user_id='owner', family_id='root')
    session = SimpleNamespace(plan_id='root', user_id='owner', plan_family_id='root', day_of_week=1)
    assert context.matches(plan, session) is True
