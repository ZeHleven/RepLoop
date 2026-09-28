"""Default query integration tests use explicitly configured PostgreSQL.

Portable SQLite checks remain opt-in via --noconftest -p evals.sqlite_test_fixtures.
"""
from evals.postgres_test_fixtures import db_session, engine, session_factory  # noqa: F401
