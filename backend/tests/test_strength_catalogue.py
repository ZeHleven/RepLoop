"""Catalogue contracts, isolated migration rollback, and filtering without broadening authority."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models.exercise import Exercise
from app.services.exercise_catalog_v1 import catalog_entries, catalog_id, sync_catalog
from app.services.exercise_search import selection_metadata, matches_exercise
from app.services.exercise_energy import classification_snapshot
from app.services.nutrition_energy import estimate_session
from app.services.personalized_planner import is_exercise_compatible, is_exercise_safe_for_areas


def new_entries():
    return [entry for entry in catalog_entries() if entry['new']]


def test_catalogue_count_identity_aliases_and_scope():
    entries = catalog_entries()
    assert len(entries) == 65 and len(new_entries()) == 48
    assert len({e['key'] for e in entries}) == len(entries)
    assert len({e['fields']['name_en'].casefold() for e in entries}) == len(entries)
    assert len({e['fields']['name_zh'] for e in entries}) == len(entries)
    assert all(e['fields']['category'] in {'力量', '核心'} for e in new_entries())
    assert all(e['body_parts'] for e in entries)
    assert all(e['aliases'] for e in new_entries())
    # Legacy 仰卧起坐/Crunch naming mismatch must not introduce a misleading alias.
    assert not any('辅助引体' in e['fields']['name_zh'] or '跑步机' in e['fields']['name_zh'] for e in new_entries())
    assert any('钟摆' in e['fields']['name_zh'] for e in new_entries())
    assert any('犀牛' in e['fields']['name_zh'] for e in new_entries())
    assert any('反向飞鸟' in e['fields']['name_zh'] for e in new_entries())


@pytest.mark.parametrize('entry', new_entries(), ids=lambda e: e['key'])
def test_new_options_search_health_and_energy_contract(entry):
    exercise = Exercise(id=catalog_id(entry['key']), owner_id=None, **entry['fields'])
    assert selection_metadata(exercise)['body_parts'] == entry['body_parts']
    assert matches_exercise(exercise, query=entry['aliases'][0], body_part=entry['body_parts'][0])
    assert not matches_exercise(exercise, query='绝不存在的动作')
    for area in exercise.contraindications:
        assert not is_exercise_safe_for_areas(exercise, {area})
    profile = NS(user_id='test', training_location='home', injuries=[], chronic_conditions=[], experience_level='advanced', age=30)
    if set(exercise.equipment) - {'bodyweight'}:
        assert not is_exercise_compatible(profile, exercise)
    snapshot = classification_snapshot(exercise)
    assert snapshot['energy_category'] in {'resistance_training', 'bodyweight_resistance'}
    now = datetime.now(timezone.utc)
    session = NS(id='s', status='completed', started_at=now, completed_at=now+timedelta(hours=1), plan_name='test')
    record = NS(id='r', exercise_id=exercise.id, exercise_name=exercise.name_zh, sets_data=[{'reps':8}], **snapshot)
    estimate = estimate_session(session, [(record, exercise)], weight=80, daily_baseline=2136)
    assert estimate['reason'] is None and estimate['net_kcal'] > 0


def test_private_name_cannot_acquire_standard_metadata():
    entry = new_entries()[0]
    exercise = Exercise(id='private', owner_id='other', **entry['fields'])
    assert selection_metadata(exercise)['search_aliases'] == []
    profile = NS(user_id='self', training_location='gym', injuries=[], chronic_conditions=[], experience_level='advanced', age=30)
    assert not is_exercise_compatible(profile, exercise)


def migration_fixture(conn):
    schema = 'catalogue_' + uuid4().hex
    conn.execute(sa.text(f'CREATE SCHEMA {schema}'))
    conn.execute(sa.text(f'SET LOCAL search_path TO {schema}, public'))
    # Use actual model columns/defaults but keep this test transaction isolated from all other tests.
    conn.execute(sa.text('CREATE TABLE exercises (LIKE public.exercises INCLUDING ALL)'))
    for table in ('planned_exercises', 'session_exercises'):
        conn.execute(sa.text(f'CREATE TABLE {table} (id varchar PRIMARY KEY, exercise_id varchar)'))
    for table, column in [('agent_proposals', 'payload_data'), ('agent_artifacts', 'payload_data'), ('agent_messages', 'content_data')]:
        conn.execute(sa.text(f'CREATE TABLE {table} (id varchar PRIMARY KEY, {column} jsonb)'))
    spec = importlib.util.spec_from_file_location('catalogue_migration', Path(__file__).parents[1] / 'alembic/versions/0030_expand_strength_exercises.py')
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    migration.op = Operations(MigrationContext.configure(conn))
    return migration


@pytest.mark.asyncio
async def test_migration_roundtrip_idempotency_and_preexisting_reuse(db_session):
    def verify(conn):
        migration = migration_fixture(conn)
        table = sa.Table('exercises', sa.MetaData(), autoload_with=conn)
        first = new_entries()[0]
        conn.execute(table.insert().values(id='preexisting', owner_id=None, **first['fields']))
        before = dict(conn.execute(sa.select(table)).mappings().one())
        migration.upgrade()
        assert conn.scalar(sa.text('SELECT count(*) FROM exercises')) == 48
        assert sync_catalog(conn) == 0
        assert dict(conn.execute(sa.select(table).where(table.c.id == 'preexisting')).mappings().one()) == before
        assert conn.scalar(sa.text("SELECT created_by_import FROM exercise_catalog_imports WHERE exercise_id='preexisting'")) is False
        migration.downgrade()
        assert list(conn.execute(sa.select(table)).mappings()) == [before]
        migration.upgrade()
        assert conn.scalar(sa.text('SELECT count(*) FROM exercises')) == 48
        migration.downgrade()
    await (await db_session.connection()).run_sync(verify)


@pytest.mark.asyncio
async def test_public_search_metadata_and_alias_cannot_bypass_health(client, db_session):
    from app.models.user import User
    from app.models.profile import UserProfile
    entry = next(e for e in new_entries() if e['key'] == 'reverse-pec-deck')
    public_id, hidden_id, private_id = [str(uuid4()) for _ in range(3)]
    email = f'catalogue-{uuid4()}@example.com'
    response = await client.post('/api/v1/auth/register', json={'email':email, 'password':'pass1234'})
    assert response.status_code == 201
    headers = {'Authorization':'Bearer ' + response.json()['access_token']}
    user = await db_session.scalar(sa.select(User).where(User.email == email))
    profile = await db_session.scalar(sa.select(UserProfile).where(UserProfile.user_id == user.id))
    if profile is None:
        profile = UserProfile(user_id=user.id)
        db_session.add(profile)
    profile.training_location = 'gym'
    profile.experience_level = 'advanced'
    profile.injuries = ['肩关节']
    db_session.add_all([
        Exercise(id=public_id, owner_id=None, **entry['fields']),
        Exercise(id=hidden_id, owner_id=None, **{**entry['fields'], 'name_en':'inactive-test', 'is_active':False}),
        Exercise(id=private_id, owner_id=user.id, **entry['fields']),
    ])
    await db_session.commit()
    try:
        result = await client.get('/api/v1/exercises', params={'q':entry['aliases'][0], 'body_part':'肩部'})
        assert result.status_code == 200
        assert {row['id'] for row in result.json()} == {public_id}
        assert result.json()[0]['body_parts'] == ['肩部']
        assert (await client.get('/api/v1/exercises', params={'body_part':'无效'})).status_code == 422
        response = await client.post('/api/v1/exercises/custom', headers=headers, json={
            'name':entry['aliases'][0], 'description':'常规分组动作',
        })
        assert response.status_code == 409
    finally:
        await db_session.execute(sa.delete(Exercise).where(Exercise.id.in_([public_id, hidden_id, private_id])))
        await db_session.commit()



@pytest.mark.asyncio
async def test_conflicting_definition_rolls_back_entire_upgrade(db_session):
    def verify(conn):
        migration = migration_fixture(conn)
        last = new_entries()[-1]
        fields = {**last['fields'], 'equipment':['conflicting-machine']}
        table = sa.Table('exercises', sa.MetaData(), autoload_with=conn)
        conn.execute(table.insert().values(id='conflict', owner_id=None, **fields))
        with pytest.raises(RuntimeError, match='catalogue conflict'):
            with conn.begin_nested():
                migration.upgrade()
        assert conn.scalar(sa.text('SELECT count(*) FROM exercises')) == 1
        assert not sa.inspect(conn).has_table('exercise_catalog_imports', schema=conn.scalar(sa.text('SELECT current_schema()')))
    await (await db_session.connection()).run_sync(verify)


@pytest.mark.asyncio
@pytest.mark.parametrize('reference', ['edit', 'planned_exercises', 'session_exercises', 'agent_proposals', 'agent_artifacts', 'agent_messages'])
async def test_downgrade_refuses_changed_or_referenced_exercises(db_session, reference):
    def verify(conn):
        migration = migration_fixture(conn)
        migration.upgrade()
        exercise_id = catalog_id(new_entries()[0]['key'])
        if reference == 'edit':
            conn.execute(sa.text('UPDATE exercises SET name_zh=:name WHERE id=:id'), dict(name='用户更正', id=exercise_id))
        elif reference in {'planned_exercises', 'session_exercises'}:
            conn.execute(sa.text(f"INSERT INTO {reference} VALUES ('ref', :id)"), dict(id=exercise_id))
        else:
            column = 'content_data' if reference == 'agent_messages' else 'payload_data'
            conn.execute(sa.text(f"INSERT INTO {reference} VALUES ('ref', jsonb_build_object('exercise_id', CAST(:id AS text)))"), dict(id=exercise_id))
        with pytest.raises(RuntimeError, match='downgrade'):
            with conn.begin_nested():
                migration.downgrade()
        assert conn.scalar(sa.text('SELECT count(*) FROM exercises')) == 48
        assert conn.scalar(sa.text('SELECT count(*) FROM exercise_catalog_imports')) == 48
    await (await db_session.connection()).run_sync(verify)
