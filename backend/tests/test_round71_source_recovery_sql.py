"""Actual ASGI/worker/SQL and HTTP wire; provider answers are scripted."""
import json
import pytest
from evals.postgres_test_fixtures import engine,session_factory,db_session
from test_round54_narrative_sql import setup_db
import test_round54_narrative_sql as nutrition_sql
import test_round65_assessment_delivery_sql as training_sql


@pytest.mark.asyncio
@pytest.mark.parametrize('scenario,count',[('normal',1),('sql_timeout_once',2),('provider_failure',1),('sql_error',1)])
async def test_real_sql_contributions_keep_user_date_and_meal_isolation(db_session,session_factory,monkeypatch,scenario,count):
    original=nutrition_sql.provider;sent=[]
    def capture(monkeypatch,failure):
        requests=original(monkeypatch,failure);sent.append(requests);return requests
    monkeypatch.setattr(nutrition_sql,'provider',capture)
    await nutrition_sql.test_scoped_food_evidence_survives_handoff_and_failure(db_session,session_factory,monkeypatch,scenario,'narrative',count)
    for request in sent[0]:
        tools=[m for m in request['messages'] if m['role']=='tool']
        view=json.loads(tools[-1]['content'])
        days=view['boundaries']['food_contributions_by_date']
        assert len(days)==1 and days[0]['date']=='2026-10-08'
        assert days[0]['basis']=='selected_meal_records'
        assert days[0]['selected_meal_types']==['午餐']
        values={f['identity']['food_name']:f['values']['protein_g'] for f in days[0]['foods']}
        assert values=={'米饭':2.4,'鸡胸肉':37.2}
        assert days[0]['reconciliation']['protein_g']['matches'] is True
        assert '隔离' not in json.dumps(days,ensure_ascii=False)
        assert 'food_contributions_by_date' not in view['business_data']


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['corrected','still_bad','protocol','budget','write_call'])
async def test_return_bounds_reach_first_and_repair_provider_without_writes(db_session,session_factory,monkeypatch,mode):
    original=training_sql.scripted_http;sent=[]
    def capture(monkeypatch,responses):
        requests=original(monkeypatch,responses);sent.append(requests);return requests
    monkeypatch.setattr(training_sql,'scripted_http',capture)
    await training_sql.test_assessment_check_runs_in_actual_worker_without_writes(db_session,session_factory,monkeypatch,mode)
    contexts=[]
    for request in sent[0]:
        system=next(m['content'] for m in request['messages'] if m['role']=='system')
        start=system.index('{"training_adjustment_envelope":')
        context=json.JSONDecoder().raw_decode(system[start:])[0]
        spec=context['training_adjustment_envelope'];bounds=spec['return_progression']
        assert context['plan_source_call_id'] and len(context['plan_result_sha256'])==64
        assert bounds['baseline_source']=='active_plan_not_profile_preference'
        assert bounds['days_per_week_ceiling']==spec['baseline_days']
        assert bounds['weekly_set_ceiling']==spec['baseline_weekly_sets']
        assert not bounds['saved_weight_is_mandatory_target'] and not bounds['medical_clearance']
        assert bounds['each_step_requires_actual_recovery_feedback']
        contexts.append(context)
    assert len(contexts)==(1 if mode=='budget' else 2)
    assert all(x==contexts[0] for x in contexts)
