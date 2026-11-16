import pytest

from evals.postgres_test_fixtures import query_eval_database_url


def test_missing_eval_url_never_falls_back_to_application_database(monkeypatch):
    monkeypatch.delenv('AGENT_QUERY_EVAL_DATABASE_URL', raising=False)
    monkeypatch.setenv('DATABASE_URL', 'postgresql+asyncpg://user:secret@localhost/app')
    with pytest.raises(RuntimeError, match='Set AGENT_QUERY_EVAL_DATABASE_URL'):
        query_eval_database_url()


@pytest.mark.parametrize('value', [
    'postgresql+asyncpg://user:secret@localhost/production',
    'postgresql+asyncpg://user:secret@example.com/app_test',
    'postgresql+asyncpg://user:secret@localhost/app_test?options=unsafe',
    'sqlite+aiosqlite:///:memory:',
    'not a database URL',
])
def test_unsafe_or_ambiguous_configuration_is_rejected_without_disclosing_it(monkeypatch, value):
    monkeypatch.setenv('AGENT_QUERY_EVAL_DATABASE_URL', value)
    with pytest.raises(RuntimeError) as error:
        query_eval_database_url()
    assert 'secret' not in str(error.value) and value not in str(error.value)


def test_explicit_local_test_database_is_accepted(monkeypatch):
    value='postgresql+asyncpg://eval:unused@127.0.0.1:5432/fitness_test'
    monkeypatch.setenv('AGENT_QUERY_EVAL_DATABASE_URL', value)
    assert query_eval_database_url() == value
