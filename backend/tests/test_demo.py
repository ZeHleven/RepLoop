import asyncio
from datetime import date, datetime, timedelta, timezone
import secrets

import pytest
import pytest_asyncio
from sqlalchemy import select, func
from app.config import settings
from app.models.demo import DemoVisit, DemoIdentity
from app.models.workout import WorkoutSession, WorkoutPlan
from app.services import model_budget as budget
from app.services.auth import decode_token
from app.services.business_clock import BUSINESS_DATE
from evals.postgres_test_fixtures import engine, session_factory, db_session  # noqa: F401

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    # Each scenario owns a disposable schema. Demo catalogue rows must not leak
    # into ordinary product tests that intentionally start with an empty library.
    yield


@pytest.fixture
def enabled(monkeypatch, session_factory):
    monkeypatch.setattr(settings, 'DEMO_ENABLED', True)
    monkeypatch.setattr(settings, 'DEMO_MAX_VISITS', 10000)
    monkeypatch.setattr(settings, 'DEMO_VISITS_PER_HOUR', 1000)
    monkeypatch.setattr(settings, 'DEMO_TOTAL_BUDGET_NANO', 50_000_000_000)
    monkeypatch.setattr(budget, 'AsyncSessionLocal', session_factory)


async def start(client, key=None):
    response = await client.post('/api/v1/demo/sessions', json={'request_key': key or secrets.token_hex(32)})
    assert response.status_code == 201, response.text
    body = response.json()
    return body, {'Authorization': f"Bearer {body['access_token']}"}


async def test_disabled_is_not_an_authentication_backdoor(client):
    response = await client.post('/api/v1/demo/sessions', json={'request_key': secrets.token_hex(32)})
    assert response.status_code == 404


async def test_idempotent_creation_and_real_domain_reads(client, enabled, db_session):
    key = secrets.token_hex(32)
    first, headers = await start(client, key)
    again, _ = await start(client, key)
    user_id = decode_token(first['access_token'])
    assert decode_token(again['access_token']) == user_id
    assert (await client.get('/api/v1/profile', headers=headers)).json()['onboarding_completed'] is True
    history = await client.get('/api/v1/workouts/sessions', headers=headers)
    assert history.status_code == 200, history.text
    sessions = (await db_session.scalars(select(WorkoutSession).where(WorkoutSession.user_id == user_id))).all()
    assert 10 <= len(sessions) <= 12
    assert all(row.completed_at is not None and row.status == 'completed' for row in sessions)
    progress = await client.get('/api/v1/workouts/sessions/progress?weeks=4', headers=headers)
    assert progress.status_code == 200, progress.text
    detail = await client.get(f'/api/v1/workouts/sessions/{sessions[0].id}', headers=headers)
    assert detail.status_code == 200, detail.text
    session_info = await client.get('/api/v1/demo/session', headers=headers)
    assert session_info.headers['cache-control'] == 'no-store'
    assert 'access_token' not in session_info.json()


async def test_visitors_cannot_read_each_others_records(client, enabled, db_session):
    first, headers_a = await start(client)
    _, headers_b = await start(client)
    user_id = decode_token(first['access_token'])
    record = await db_session.scalar(select(WorkoutSession).where(WorkoutSession.user_id == user_id))
    response = await client.get(f'/api/v1/workouts/sessions/{record.id}', headers=headers_b)
    assert response.status_code == 404
    assert (await client.get('/api/v1/me', headers=headers_a)).json() != (await client.get('/api/v1/me', headers=headers_b)).json()


async def test_reset_retires_old_tokens_and_preserves_budget_and_expiry(client, enabled):
    first, old_headers = await start(client)
    old_user = decode_token(first['access_token'])
    charge = await budget.reserve(old_user, 'deepseek-flash', 100_000)
    result = await client.post('/api/v1/demo/reset', headers=old_headers)
    assert result.status_code == 200, result.text
    second = result.json()
    new_user = decode_token(second['access_token'])
    assert new_user != old_user
    assert second['expires_at'] == first['expires_at']
    assert (await client.get('/api/v1/me', headers=old_headers)).status_code == 401
    assert (await client.post('/api/v1/auth/refresh', json={'refresh_token': first['refresh_token']})).status_code == 401
    assert await budget.settle(charge, (50_000, 10, 5))
    assert (await budget.budget_status(new_user))['spent_yuan'] == '0.000050000'
    with pytest.raises(budget.ModelBudgetError):
        await budget.reserve(old_user, 'deepseek-flash', 1)


async def test_expired_visit_cannot_be_refreshed_or_replayed(client, enabled, db_session):
    key = secrets.token_hex(32)
    body, headers = await start(client, key)
    row = await db_session.scalar(select(DemoVisit).where(DemoVisit.current_user_id == decode_token(body['access_token'])))
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()
    assert (await client.get('/api/v1/me', headers=headers)).status_code == 401
    assert (await client.post('/api/v1/auth/refresh', json={'refresh_token': body['refresh_token']})).status_code == 401
    assert (await client.post('/api/v1/demo/sessions', json={'request_key': key})).status_code == 401


async def test_global_cap_is_atomic_across_visitors(client, enabled, monkeypatch):
    first, _ = await start(client)
    second, _ = await start(client)
    monkeypatch.setattr(settings, 'DEMO_TOTAL_BUDGET_NANO', 1_000_000_000)
    # Other tests may have spent a small amount; allow exactly one new 0.6-yuan reservation.
    users = [decode_token(first['access_token']), decode_token(second['access_token'])]
    results = await asyncio.gather(*[budget.reserve(user, 'deepseek-flash', 600_000_000) for user in users], return_exceptions=True)
    assert sum(isinstance(value, str) for value in results) == 1
    assert sum(isinstance(value, budget.ModelBudgetError) for value in results) == 1


async def test_creation_limits_and_normal_login_are_enforced(client, enabled, monkeypatch):
    await start(client)
    monkeypatch.setattr(settings, 'DEMO_MAX_VISITS', 1)
    assert (await client.post('/api/v1/demo/sessions', json={'request_key': secrets.token_hex(32)})).status_code == 429
    assert (await client.post('/api/v1/auth/wechat', json={'code': 'synthetic'})).status_code == 403
    assert (await client.post('/api/v1/auth/login', json={'email': 'demo@example.com', 'password': 'password'})).status_code == 403


@pytest.mark.parametrize('day', [date(2027, 1, 1), date(2026, 11, 16)])
async def test_seed_dates_follow_business_day_and_never_future(client, enabled, db_session, day):
    token = BUSINESS_DATE.set(day)
    try:
        body, _ = await start(client)
    finally:
        BUSINESS_DATE.reset(token)
    rows = (await db_session.scalars(select(WorkoutSession).where(WorkoutSession.user_id == decode_token(body['access_token'])))).all()
    assert all(day - timedelta(days=27) <= row.trained_at < day for row in rows)
    assert all(row.day_of_week == row.trained_at.isoweekday() for row in rows)
    assert all(row.week_start == row.trained_at - timedelta(days=row.trained_at.weekday()) for row in rows)


async def test_reset_limit_and_parallel_old_identity(client, enabled, monkeypatch):
    _, headers = await start(client)
    responses = await asyncio.gather(*[client.post('/api/v1/demo/reset', headers=headers) for _ in range(2)])
    assert sorted(r.status_code for r in responses) == [200, 401]
    body = next(r.json() for r in responses if r.status_code == 200)
    monkeypatch.setattr(settings, 'DEMO_MAX_RESETS', 1)
    assert (await client.post('/api/v1/demo/reset', headers={'Authorization': f"Bearer {body['access_token']}"})).status_code == 429
