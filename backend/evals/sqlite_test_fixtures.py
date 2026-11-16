"""Explicit opt-in SQLite fixtures; never connect to configured PostgreSQL.

Use --noconftest -p evals.sqlite_test_fixtures for portable SQL tests.
This does not validate PostgreSQL locks, migrations, or production concurrency.
"""
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import MetaData, text, CheckConstraint, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.schema import DefaultClause

from app.database import Base, get_db
from app.main import app
from app import models  # register schemas


@compiles(JSONB, 'sqlite')
def compile_json(_type, _compiler, **_kw):
    return 'JSON'


@pytest_asyncio.fixture
async def engine():
    db_engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    metadata = MetaData()
    names = ['users', 'user_profiles', 'exercises', 'workout_plans', 'planned_exercises',
             'workout_sessions', 'session_exercises', 'agent_conversations', 'agent_runs', 'agent_messages', 'agent_tool_calls', 'agent_artifacts', 'agent_proposals',
             'weight_logs', 'foods', 'food_aliases', 'custom_foods', 'meal_logs', 'meal_items']
    for name in names:
        table = Base.metadata.tables[name].to_metadata(metadata)
        for constraint in list(table.constraints):
            if isinstance(constraint, CheckConstraint) and any(
                    marker in str(constraint.sqltext) for marker in ('jsonb_', ' ~ ', 'INTERVAL', '#>>')):
                table.constraints.remove(constraint)
        for column in table.columns:
            if column.server_default and '::jsonb' in str(column.server_default.arg):
                column.server_default = DefaultClause(text(str(column.server_default.arg).replace('::jsonb', '')))
        for index in table.indexes:
            where = index.dialect_options['postgresql'].get('where')
            if where is not None:
                index.dialect_options['sqlite']['where'] = where
    try:
        async with db_engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        yield db_engine
    finally:
        await db_engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(session_factory):
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(session_factory):
    async def override_db():
        async with session_factory() as session:
            yield session
    app.dependency_overrides[get_db] = override_db
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url='http://test') as client:
            yield client
    finally:
        app.dependency_overrides.clear()
