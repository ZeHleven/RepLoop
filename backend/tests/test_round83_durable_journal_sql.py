"""SIGKILL + fresh worker + loopback HTTP + actual PostgreSQL, no provider calls."""
import asyncio,importlib.util,json,os,sys
from pathlib import Path
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import httpx,openai
import pytest,pytest_asyncio
from sqlalchemy import select,text,func
from alembic.migration import MigrationContext
from alembic.operations import Operations
from evals.postgres_test_fixtures import engine,session_factory,db_session
from app.models.agent import AgentRun,AgentMessage,AgentToolCall,AgentAttemptJournal,AgentProposal
from app.services.agent_attempt_journal import observe_attempt,read_attempt_history,AttemptAlreadyStarted,AttemptOwnershipLost,AttemptJournalUnavailable
from app.services.agent_model_usage import observe_model_usage,ObservedAsyncClient
from app.services.agent_jobs import claim_next_agent_run,process_agent_run
from app.services.agent_tools import build_read_tools
from app.services.auth import create_access_token
from app.main import app
from test_round56_evidence_sql import seed
from test_round76_live_evidence import ROWS,GOOD
from test_round76_source_scope_sql import install

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

async def context(db):
    user,conv=await seed(db,4)
    run=AgentRun(user_id=user.id,conversation_id=conv.id,status='running',attempt_count=1,idempotency_key=uuid4().hex,
        lease_expires_at=datetime.now(timezone.utc)+timedelta(seconds=120))
    db.add(run);await db.flush()
    db.add(AgentMessage(run_id=run.id,conversation_id=conv.id,role='user',content=ROWS['SV01-r1']['turns'][0]['message'],content_data={}))
    await db.commit()
    return user,conv,run

async def child(schema,rid,mode,d):
    d.mkdir(parents=True)
    proc=await asyncio.create_subprocess_exec(sys.executable,str(Path(__file__).with_name('round83_process_driver.py')),schema,rid,mode,str(d),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    try:
        if mode!='finish':
            for _ in range(400):
                if (d/'barrier').exists() or proc.returncode is not None:break
                await asyncio.sleep(.05)
            hit=(d/'barrier').exists()
            if proc.returncode is None:proc.kill()
            stdout,stderr=await proc.communicate()
            (d/'stderr.txt').write_bytes(stderr)
            assert hit,stderr.decode()[-2500:]
            assert proc.returncode==-9,proc.returncode
        else:
            stdout,stderr=await asyncio.wait_for(proc.communicate(),25)
            (d/'stderr.txt').write_bytes(stderr)
            assert proc.returncode==0,stderr.decode()[-2500:]
        return json.loads((d/'events.json').read_text())
    finally:
        if proc.returncode is None:proc.kill();await proc.wait()

MODES=['http_pending','later_http_pending','http_headers','model_done','tool_started','tool_uncheckpointed','tool_done','before_commit']
@pytest.mark.asyncio
@pytest.mark.parametrize('mode',MODES)
@pytest.mark.parametrize('repeat',[1,2,3])
async def test_kill_and_fresh_worker_preserve_old_attempt(db_session,session_factory,monkeypatch,tmp_path,mode,repeat):
    user,conv,run=await context(db_session);rid=run.id
    other,otherconv=await seed(db_session,1)
    schema=await db_session.scalar(text('select current_schema()'))
    root=Path(os.getenv('R83_EVIDENCE_DIR',str(tmp_path)))/('crash-'+mode+'-'+str(repeat)+'-'+uuid4().hex)
    before=await child(schema,rid,mode,root/'killed')
    async with session_factory() as db:
        old=await db.get(AgentRun,rid)
        first=await read_attempt_history(db,old)
        assert old.status=='running' and len(first['attempts'])==1
        assert first['input_tokens'] is None and not first['usage_complete']
        assert await db.scalar(select(func.count()).select_from(AgentToolCall).where(AgentToolCall.run_id==rid))==0
        usage=first['attempts'][0]['model_usage'];tools=first['attempts'][0]['tool_calls']
        assert usage['http_requests']>=1
        if mode in {'http_pending','later_http_pending','http_headers'}:
            assert usage['pending_or_cancelled']==1
            assert usage['http_responses']==(0 if mode=='http_pending' else 1)
        else:assert usage['known_input_tokens']>=12 and usage['known_output_tokens']>=5
        if mode in {'tool_started','tool_uncheckpointed'}:assert any(x['state']=='pending' for x in tools)
        if mode in {'tool_done','before_commit'}:
            assert any(x['state']=='completed' and json.loads(x['result_json']) for x in tools)
        old.lease_expires_at=datetime.now(timezone.utc)-timedelta(seconds=1);await db.commit()
        assert await claim_next_agent_run(db)==rid
    after=await child(schema,rid,'finish',root/'recovery')
    install(monkeypatch,session_factory)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
            body=(await client.get('/api/v1/agent/runs/'+rid)).json()
            response=await client.get('/api/v1/agent/runs/'+rid+'/attempts');assert response.status_code==200
            history=response.json()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(other.id)}) as stranger:
            assert (await stranger.get('/api/v1/agent/runs/'+rid+'/attempts')).status_code==404
    finally:app.dependency_overrides.clear()
    (root/'result.json').write_text(json.dumps({'mode':mode,'repeat':repeat,'first':first,'body':body,'history':history,'source':'actual SIGKILL, loopback scripted HTTP, SQL; not business quality'},default=str,ensure_ascii=False,indent=2))
    assert body['status']=='completed' and body['reply']==GOOD,body
    assert body['model_usage']['worker_attempt']==2 and body['model_usage']['input_tokens'] is None
    assert history['missing_attempts']==[] and len(history['attempts'])==2
    assert history['attempts'][0]['state']=='interrupted' and history['attempts'][1]['state']=='closed'
    assert history['attempts'][0]['tool_calls']==first['attempts'][0]['tool_calls']
    assert history['attempts'][0]['model_usage']==usage
    expected_in=sum(x['body']['usage']['prompt_tokens'] for x in before+after if x['kind']=='response') - (12 if mode=='http_headers' else 0)
    assert history['known_input_tokens']==expected_in
    unknown=mode in {'http_pending','later_http_pending','http_headers'}
    assert history['usage_complete']==(not unknown)
    assert history['input_tokens']==(expected_in if not unknown else None)
    assert len(history['attempts'][1]['tool_calls'])==2
    async with session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(AgentMessage).where(AgentMessage.run_id==rid,AgentMessage.role=='assistant'))==1
        assert await db.scalar(select(func.count()).select_from(AgentToolCall).where(AgentToolCall.run_id==rid))==2
        assert await db.scalar(select(func.count()).select_from(AgentProposal))==0
    # Finished run replay cannot create another attempt/request or duplicate a reply.
    await process_agent_run(session_factory,rid)
    async with session_factory() as db:
        saved=await db.get(AgentRun,rid);assert await read_attempt_history(db,saved)=={
            **history,'attempts':[{**a,'started_at':datetime.fromisoformat(a['started_at']),'finished_at':datetime.fromisoformat(a['finished_at']) if a['finished_at'] else None} for a in history['attempts']]}

@pytest.mark.asyncio
async def test_duplicate_attempt_and_late_owner_do_not_overwrite_history(db_session,session_factory):
    user,conv,run=await context(db_session);rid=run.id
    async with observe_attempt(session_factory,rid,1) as journal:
        async with observe_model_usage(1):
            with pytest.raises(AttemptAlreadyStarted):
                async with observe_attempt(session_factory,rid,1):assert False
            async with session_factory() as db:
                r=await db.get(AgentRun,rid);r.attempt_count=2;await db.commit()
            with pytest.raises(AttemptOwnershipLost):await journal.save(new_action=True)
            # Late facts belong to old row only; they do not change Run ownership.
            await journal.save()
            async with observe_attempt(session_factory,rid,2):
                async with observe_model_usage(2):pass
    async with session_factory() as db:
        r=await db.get(AgentRun,rid);assert r.attempt_count==2
        assert len((await read_attempt_history(db,r))['attempts'])==2

@pytest.mark.asyncio
async def test_concurrent_registered_reads_persist_all_results_and_rollback_is_independent(db_session,session_factory):
    user,conv,run=await context(db_session);rid=run.id
    async with observe_attempt(session_factory,rid,1) as journal:
        async with observe_model_usage(1):
            tools=build_read_tools(db_session,user_id=user.id,allowlist=['profile.get_summary','health.get_screening_summary','plan.get_active'])
            results=await asyncio.gather(*(t.ainvoke({}) for t in tools))
            run.error_code='uncommitted-synthetic';await db_session.flush();await db_session.rollback()
    async with session_factory() as db:
        saved=await db.get(AgentRun,rid);history=await read_attempt_history(db,saved)
        assert saved.error_code is None
        entries=history['attempts'][0]['tool_calls'];assert len(entries)==3
        assert {e['sequence'] for e in entries}=={1,2,3}
        assert [json.loads(e['result_json']) for e in entries]==results

@pytest.mark.asyncio
@pytest.mark.parametrize('stage',['initial','response'])
@pytest.mark.parametrize('repeat',[1,2,3])
async def test_sql_journal_failure_stops_provider_without_retry(db_session,session_factory,monkeypatch,stage,repeat):
    from app.services import agent_runtime as runtime
    from app.config import settings
    from test_round76_model_usage import transport,response
    user,conv,run=await context(db_session);rid=run.id
    condition='model_usage IS NULL' if stage=='initial' else "COALESCE((model_usage->>'known_input_tokens')::int,0)=0"
    await db_session.execute(text('ALTER TABLE agent_attempt_journals ADD CONSTRAINT synthetic_failure CHECK ('+condition+')'));await db_session.commit()
    requests=transport(monkeypatch,[(200,response({'prompt_tokens':12,'completion_tokens':5}))])
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','synthetic-offline')
    # SDK may translate the error or retry client entry; poisoned journal must
    # prevent any additional actual HTTP. Do not require SDK exception identity.
    with pytest.raises((AttemptJournalUnavailable,openai.APIConnectionError)):
        async with observe_attempt(session_factory,rid,1):
            async with observe_model_usage(1):await runtime._build_model().ainvoke('synthetic')
    assert len(requests)==(0 if stage=='initial' else 1)
    async with session_factory() as db:
        r=await db.get(AgentRun,rid);history=await read_attempt_history(db,r)
        assert history['input_tokens'] is None
        usage=history['attempts'][0]['model_usage']
        if stage=='response':assert usage['pending_or_cancelled']==1 and usage['http_requests']==1 and usage['http_responses']==0

@pytest.mark.asyncio
async def test_sql_tool_intent_failure_prevents_actual_read(db_session,session_factory):
    user,conv,run=await context(db_session);executed=[]
    await db_session.execute(text('ALTER TABLE agent_attempt_journals ADD CONSTRAINT synthetic_failure CHECK (jsonb_array_length(tool_calls)=0)'));await db_session.commit()
    async def call():executed.append(True);return {}
    with pytest.raises(AttemptJournalUnavailable):
        async with observe_attempt(session_factory,run.id,1) as journal:await journal.invoke('synthetic',call,(),{})
    assert executed==[]

@pytest.mark.asyncio
async def test_legacy_gap_is_not_zero_cost_and_tool_errors_survive(db_session,session_factory):
    user,conv,run=await context(db_session);rid=run.id
    run.attempt_count=2;await db_session.commit()
    async def fail():raise TimeoutError('secret exception text must not persist')
    async with observe_attempt(session_factory,rid,2) as journal:
        async with observe_model_usage(2):
            with pytest.raises(TimeoutError):await journal.invoke('synthetic',fail,(),{})
    async with session_factory() as db:
        r=await db.get(AgentRun,rid);r.status='failed';await db.commit()
        history=await read_attempt_history(db,r)
        assert history['missing_attempts']==[1] and history['input_tokens'] is None
        assert history['attempts'][0]['tool_calls'][0]['error_type']=='TimeoutError'
        assert 'secret exception' not in json.dumps(history,default=str)

@pytest.mark.asyncio
async def test_migration_preserves_old_run_and_unknown_history(engine,db_session,session_factory,monkeypatch):
    user,conv,run=await context(db_session);rid=run.id
    spec=importlib.util.spec_from_file_location('r83_migration',Path(__file__).resolve().parents[1]/'alembic/versions/0034_agent_attempt_journal.py')
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    async with engine.begin() as connection:
        def migrate(sync):
            monkeypatch.setattr(migration,'op',Operations(MigrationContext.configure(sync)))
            migration.downgrade();migration.upgrade()
            assert sync.execute(text('select count(*) from agent_attempt_journals')).scalar_one()==0
            assert sync.execute(text('select status from agent_runs where id=:id'),{'id':rid}).scalar_one()=='running'
        await connection.run_sync(migrate)
    async with session_factory() as db:
        r=await db.get(AgentRun,rid);assert (await read_attempt_history(db,r))['missing_attempts']==[1]

@pytest.mark.asyncio
async def test_missing_journal_row_refuses_next_action(db_session,session_factory):
    from sqlalchemy import delete
    user,conv,run=await context(db_session);executed=[]
    async def call():executed.append(True);return {}
    with pytest.raises(AttemptJournalUnavailable):
        async with observe_attempt(session_factory,run.id,1) as journal:
            async with session_factory() as db:
                await db.execute(delete(AgentAttemptJournal).where(AgentAttemptJournal.id==journal.id));await db.commit()
            await journal.invoke('synthetic',call,(),{})
    assert executed==[]

@pytest.mark.asyncio
@pytest.mark.parametrize('stage',['initial','response'])
async def test_worker_marks_failure_after_durable_log_write_rejected(db_session,session_factory,monkeypatch,stage):
    from app.services import agent_runtime as runtime
    from test_round76_model_usage import transport,response
    user,conv,run=await context(db_session);rid=run.id;called=[]
    condition='model_usage IS NULL' if stage=='initial' else "COALESCE((model_usage->>'known_input_tokens')::int,0)=0"
    await db_session.execute(text('ALTER TABLE agent_attempt_journals ADD CONSTRAINT synthetic_failure CHECK ('+condition+')'));await db_session.commit()
    requests=transport(monkeypatch,[(200,response({'prompt_tokens':12,'completion_tokens':5}))])
    async def history(*a,**kw):
        called.append(True)
        async with ObservedAsyncClient(source='agent') as client:await client.post('https://api.deepseek.com/chat/completions')
        return []
    monkeypatch.setattr(runtime,'_load_history',history)
    await process_agent_run(session_factory,rid)
    async with session_factory() as db:
        r=await db.get(AgentRun,rid)
        assert r.status=='failed' and r.error_code=='worker_runtime_error'
        assert len(called)==len(requests)==(0 if stage=='initial' else 1)
        assert (await read_attempt_history(db,r))['input_tokens'] is None
