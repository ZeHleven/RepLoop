"""Real worker, SQL isolation, persisted audit and provider wire assertions."""
import pytest
import test_round54_narrative_sql as existing
from test_round54_narrative_sql import setup_db
from evals.postgres_test_fixtures import engine,session_factory,db_session
from test_round67_nutrition_obligations import block

@pytest.mark.asyncio
@pytest.mark.parametrize('scenario,count',[('normal',1),('sql_timeout_once',2),('provider_failure',1),('sql_error',1)])
async def test_current_scope_and_response_limits_reach_actual_provider(db_session,session_factory,monkeypatch,scenario,count):
    factory=existing.provider;sent=[]
    def capture(monkeypatch,failure):
        rows=factory(monkeypatch,failure);sent.append(rows);return rows
    monkeypatch.setattr(existing,'provider',capture)
    # Existing integration checks include foreign user/date/dinner exclusion,
    # unchanged business SQL, exact original audits, and successful/failed Run.
    await existing.test_scoped_food_evidence_survives_handoff_and_failure(db_session,session_factory,monkeypatch,scenario,'narrative',count)
    requests=sent[0]
    assert len(requests)==(0 if scenario=='sql_error' else 1)
    for request in requests:
        ctx=block(next(m['content'] for m in request['messages'] if m['role']=='system'))
        assert ctx['advice_limit']['max_actions']==1
        assert ctx['profile_evidence']['status']=='not_observed'
        assert len(ctx['source_scopes'])==1
        scope=ctx['source_scopes'][0]
        assert scope['scope']['meal_types']==['午餐'] and scope['day_totals_basis']=='selected_meals_only'
        assert scope['scope']['start_date']==scope['scope']['end_date']=='2026-10-08'
