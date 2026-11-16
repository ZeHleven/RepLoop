"""Isolated PostgreSQL schemas for query evals; never use application DB config."""
import os
import uuid

import pytest_asyncio
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from app import models  # noqa: F401: register the full, unmodified model schema
from app.database import Base


def query_eval_database_url() -> str:
    value = os.environ.get('AGENT_QUERY_EVAL_DATABASE_URL', '')
    if not value:
        raise RuntimeError('Set AGENT_QUERY_EVAL_DATABASE_URL to an isolated local *_test database.')
    try:
        url = make_url(value)
    except Exception:
        raise RuntimeError('Invalid query evaluation database URL.') from None
    if (url.drivername != 'postgresql+asyncpg' or url.host not in {'localhost', '127.0.0.1', '::1'}
            or not (url.database or '').endswith('_test') or url.query):
        raise RuntimeError('Query evaluation requires a local PostgreSQL *_test database without URL options.')
    return value


@pytest_asyncio.fixture
async def engine():
    url = query_eval_database_url()
    schema = 'agent_query_eval_' + uuid.uuid4().hex
    control = create_async_engine(url, poolclass=NullPool)
    isolated = create_async_engine(url, poolclass=NullPool, connect_args={
        'server_settings': {'search_path': schema + ',public', 'timezone': 'UTC'},
    })
    created = False
    try:
        async with control.begin() as connection:
            await connection.execute(CreateSchema(schema))
        created = True
        async with isolated.begin() as connection:
            # Public tables already exist after migrations. checkfirst=False
            # ensures search_path cannot make us silently reuse those tables.
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, checkfirst=False))
        yield isolated
    finally:
        await isolated.dispose()
        try:
            if created:
                async with control.begin() as connection:
                    await connection.execute(DropSchema(schema, cascade=True))
        finally:
            await control.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(session_factory):
    async with session_factory() as session:
        yield session
