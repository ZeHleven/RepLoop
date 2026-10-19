import asyncio,copy,hashlib,importlib,json,os,sys,time
from datetime import datetime,timedelta,timezone,date
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4
import httpx
import pytest,pytest_asyncio
from sqlalchemy import text,select
from evals.postgres_test_fixtures import engine,session_factory,db_session
from evals.attempt_capture import collect_attempt_snapshot,record_attempt_snapshot
from evals.attempt_metrics import summarize_attempts
from app.models.agent import AgentRun,AgentAttemptJournal
from app.main import app
from app.config import settings
from app.services.auth import create_access_token
from app.services.agent_jobs import claim_next_agent_run
from app.services import agent_runtime as runtime
from app.services.agent_intent import IntentResolverOutcome
from test_round83_durable_journal_sql import context,child
from test_round76_source_scope_sql import install
from test_round76_live_evidence import ROWS,resolution,tool_calls,GOOD
from test_round57_read_delivery import scripted_http

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

def destination(tmp_path,name):
    d=Path(os.getenv('R84_EVIDENCE_DIR',str(tmp_path)))/(name+'-'+uuid4().hex);d.mkdir();return d

async def collect(client,factory,user,conv,rid):
    body=(await client.get('/api/v1/agent/runs/'+rid)).json()
    async def request(method,path):return await client.request(method,path)
    observation=await collect_attempt_snapshot(request,factory,run_id=rid,user_id=user.id,conversation_id=conv.id)
    rec=dict(attempt_observation_version=1,user_id=user.id,conversation_id=conv.id,runs=[body])
    record_attempt_snapshot(rec,observation)
    return rec

@pytest.mark.asyncio
@pytest.mark.parametrize('recover',[False,True])
@pytest.mark.parametrize('repeat',[1,2,3])
async def test_actual_worker_api_sql_and_new_process_recovery(db_session,session_factory,monkeypatch,tmp_path,recover,repeat):
    user,conv,run=await context(db_session);rid=run.id
    schema=await db_session.scalar(text('select current_schema()'))
    d=destination(tmp_path,'api-recovery' if recover else 'api-normal')
    if recover:
        await child(schema,rid,'http_pending',d/'killed')
        async with session_factory() as db:
            r=await db.get(AgentRun,rid);r.lease_expires_at=datetime.now(timezone.utc)-timedelta(seconds=1);await db.commit()
            assert await claim_next_agent_run(db)==rid
    await child(schema,rid,'finish',d/'completed')
    install(monkeypatch,session_factory)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
            rec=await collect(client,session_factory,user,conv,rid)
            out=summarize_attempts(rec)
            assert out['evidence_valid'] and out['coverage_complete'],out
            assert out['runs'][0]['source']=='api_and_sql'
            assert out['model']['observed_requests']==(3 if recover else 2)
            assert out['model']['known_observations']['known_input_tokens']==24
            assert out['model']['input_tokens']==(None if recover else 24)
            assert out['model']['full_request_count']==(None if recover else 2)
            assert out['registered_read_intents']['successes']==2
            assert out['tool_dispatch']['success_rate'] is None
            again=await collect(client,session_factory,user,conv,rid)
            record_attempt_snapshot(rec,again['attempt_observations'][0]);assert summarize_attempts(rec)==out
            (d/'collected.json').write_text(json.dumps({'record':rec,'metrics':out,'scripted_only':True,'repeat':repeat},ensure_ascii=False,indent=2))
        # Unauthorized API cannot be bypassed by supplying a valid SQL owner.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as unauth:
            async def request(method,path):return await unauth.request(method,path)
            bad=await collect_attempt_snapshot(request,session_factory,run_id=rid,user_id=user.id,conversation_id=conv.id)
            assert bad['history'] is bad['sql'] is None and bad['capture_errors']
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
            async def request(method,path):return await client.request(method,path)
            bad=await collect_attempt_snapshot(request,session_factory,run_id=rid,user_id='foreign',conversation_id=conv.id)
            assert bad['sql'] is None and 'sql_scope_mismatch' in bad['capture_errors']
    finally:app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_collection_race_is_reported_instead_of_mixing_attempts(db_session,session_factory,monkeypatch,tmp_path):
    user,conv,run=await context(db_session);rid=run.id;schema=await db_session.scalar(text('select current_schema()'))
    d=destination(tmp_path,'capture-race');await child(schema,rid,'finish',d/'completed');install(monkeypatch,session_factory)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
            body=(await client.get('/api/v1/agent/runs/'+rid)).json()
            async def request(method,path):
                result=await client.request(method,path)
                async with session_factory() as db:
                    r=await db.get(AgentRun,rid);r.attempt_count=2;await db.commit()
                return result
            observation=await collect_attempt_snapshot(request,session_factory,run_id=rid,user_id=user.id,conversation_id=conv.id)
            rec={'attempt_observation_version':1,'user_id':user.id,'conversation_id':conv.id,'runs':[body],'attempt_observations':[observation]}
            out=summarize_attempts(rec)
            assert not out['evidence_valid'] and out['model']['input_tokens'] is None
            (d/'result.json').write_text(json.dumps({'record':rec,'metrics':out},ensure_ascii=False,indent=2))
    finally:app.dependency_overrides.clear()

def load_runner(monkeypatch):
    root=Path(os.environ['R84_EVIDENCE_DIR'])/'runner'
    monkeypatch.syspath_prepend(str(root))
    for file in root.glob('*.py'):monkeypatch.delitem(sys.modules,file.stem,raising=False)
    return importlib.import_module('run')

@pytest.mark.asyncio
async def test_actual_frozen_runner_copy_collects_and_scores_attempts(monkeypatch,tmp_path):
    runner=load_runner(monkeypatch)
    from durable_budget import Budget
    from session_registry import Registry
    d=destination(tmp_path,'runner-integration');(d/'runs').mkdir();(d/'freeze.json').write_text('{"synthetic_only":true}')
    fingerprint=hashlib.sha256((d/'freeze.json').read_bytes()).hexdigest()
    monkeypatch.setattr(runner,'ROOT',d);monkeypatch.setattr(runner,'RUN_COUNT',0)
    monkeypatch.setattr(runner,'BUDGET',Budget(d/'synthetic-budget.json',fingerprint,seconds=90,runs=2,requests=12,max_estimated_cny=1))
    case=copy.deepcopy(next(c for c in runner.BENCH['cases'] if c['id']=='WX24'))
    case['turns']=[ROWS['SV01-r1']['turns'][0]['message']]
    case['expected_tools']=['plan.get_active','health.get_screening_summary'];case['requires_nutrition']=False
    monkeypatch.setattr(runner,'REGISTRY',Registry(d,freeze=fingerprint,limits=runner.BUDGET.state['limits'],prefix=runner.SCHEMA_PREFIX,expected_turns={'WX24-r1':1}))
    token=runner.BUSINESS_DATE.set(date.fromisoformat(runner.BENCH['business_date']))
    monkeypatch.setattr(settings,'AGENT_ENABLED',True);monkeypatch.setattr(settings,'AGENT_PLANNED_EXECUTION_ENABLED',False)
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','synthetic-offline');monkeypatch.setattr(settings,'DEEPSEEK_BASE_URL','https://api.deepseek.com')
    monkeypatch.setattr(runtime,'resolve_intent_with_fallback',AsyncMock(return_value=IntentResolverOutcome(resolution=resolution(ROWS['SV01-r1']['turns'][0]['body']),source='model',attempt_count=1)))
    originals={name:importlib.import_module(name).AsyncClient.send for name in ('httpx','httpx2')}
    requests=scripted_http(monkeypatch,[tool_calls('plan_get_active','health_get_screening_summary'),{'role':'assistant','content':GOOD}])
    for name,original_send in originals.items():
        module=importlib.import_module(name);scripted=module.AsyncClient.send
        async def send(client,request,_original=original_send,_scripted=scripted,**kw):
            if request.url.host=='synthetic-test':return await _original(client,request,**kw)
            return await _scripted(client,request,**kw)
        monkeypatch.setattr(module.AsyncClient,'send',send)
    # Provider is patched before the real capture wrapper; no external HTTP.
    monkeypatch.setenv('R31_MODE','live');runner.CAPTURE.budget=runner.BUDGET
    runner.TOOLS.install();runner.CAPTURE.install()
    original=runner.collect_attempt_snapshot;timing_seen=[]
    async def captured(*a,**kw):
        turn=runner.CURRENT['turns'][-1];assert turn['latency_scope']=='submission_to_terminal'
        timing_seen.append(turn['api_to_terminal_ms'])
        return await original(*a,**kw)
    monkeypatch.setattr(runner,'collect_attempt_snapshot',captured)
    try:
        record=await runner.run_case(case,1)
        assert len(requests)==2 and record['turns'][0]['body']['status']=='completed',record.get('error')
        metrics=record['score']['execution_metrics']
        assert metrics['evidence_valid'] and metrics['model']['input_tokens']==24,metrics
        assert metrics['registered_read_intents']['success_rate']==1
        assert metrics['tool_dispatch']['complete'] and metrics['tool_dispatch']['success_rate']==1,metrics['tool_dispatch']
        assert timing_seen and any(h['path'].endswith('/attempts') for h in record['http'])
        assert record['score']['semantic_pass'] is None
        summaries=list(d.glob('execution-summary-*.json'));assert len(summaries)==1
        summary=json.loads(summaries[0].read_text())
        assert summary['instances']==1 and summary['tokens']['input_total']==24 and summary['task_success_rate'] is None
        (d/'verified.json').write_text(json.dumps({'metrics':metrics,'raw_record':str(d/'runs/WX24-r1.json'),'external_requests':0,'business_score':None},ensure_ascii=False,indent=2))
    finally:
        runner.CAPTURE.uninstall();runner.TOOLS.uninstall();runner.BUSINESS_DATE.reset(token)
        app.dependency_overrides.clear()
    # The integrated copy cannot reopen a paid window through its CLI entry.
    with pytest.raises(RuntimeError,match='offline-only'):await runner.main()
    assert not (Path(os.environ['R84_EVIDENCE_DIR'])/'runner/live-budget.json').exists()
