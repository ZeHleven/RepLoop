"""Default query integration tests use explicitly configured PostgreSQL.

Portable SQLite checks remain opt-in via --noconftest -p evals.sqlite_test_fixtures.
"""
from evals.postgres_test_fixtures import db_session, engine, session_factory  # noqa: F401


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
