import os

import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from app.main import app
from app.database import Base, get_db

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://fitness:fitness_pass@localhost:5432/fitness_test",
)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine():
    # NullPool prevents connections from being tied to a specific event loop
    _engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
    yield _engine
    await _engine.dispose()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def setup_db(engine):
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
        if tables:
            await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield
    # drop_all omitted: pytest-asyncio 0.24 with session loop leaves function-scoped
    # db_session connections open at teardown, causing DROP TABLE to deadlock.


@pytest_asyncio.fixture
async def db_session(session_factory):
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(session_factory):
    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


import os as _os

import pytest as _pytest


@_pytest.fixture(autouse=True)
def _symptom_scope_test_default(monkeypatch):
    """SCOPE-20261025-01 keeps the legacy symptom path testable.

    Production default is enabled. Legacy tests exercise the model-written
    symptom path, so the suite default is off unless
    AGENT_SYMPTOM_SCOPE_TEST_DEFAULT=1; tests of the new path enable it.
    """
    from app.config import settings

    monkeypatch.setattr(
        settings,
        "AGENT_SYMPTOM_SCOPE_ENABLED",
        _os.getenv("AGENT_SYMPTOM_SCOPE_TEST_DEFAULT", "0") == "1",
    )
