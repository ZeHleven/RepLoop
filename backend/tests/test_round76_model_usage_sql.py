"""Atomic terminal telemetry, replay, ownership fencing and migration."""
import importlib.util
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select,text
from alembic.migration import MigrationContext
from alembic.operations import Operations

from evals.postgres_test_fixtures import engine,session_factory,db_session
from app.models.agent import AgentRun,AgentMessage
from app.services import agent_runtime as runtime
from app.services.ai_client import AIServiceError
from app.services.agent_model_usage import observe_model_usage,ObservedAsyncClient
from test_agent_domain_proposals import _context
from test_round76_model_usage import transport,response


@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield


async def context(db):
    user,_,conv,_=await _context(db,'r76-usage-'+uuid4().hex)
    run=AgentRun(user_id=user.id,conversation_id=conv.id,status='running',attempt_count=1)
    db.add(run);await db.commit()
    return conv,run


async def request():
    async with ObservedAsyncClient(source='structured') as client:
        return await client.post('https://api.deepseek.com/chat/completions')


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['known','missing'])
async def test_failure_before_trace_keeps_actual_usage_in_fenced_transaction(db_session,session_factory,monkeypatch,mode):
    conv,run=await context(db_session);rid=run.id
    transport(monkeypatch,[(200,response({'prompt_tokens':12,'completion_tokens':5}))] if mode=='known' else [(402,{'error':'synthetic'})])
    async def history(*a,**kw):
        await request()
        raise AIServiceError('synthetic terminal failure')
    monkeypatch.setattr(runtime,'_load_history',history)
    with pytest.raises(AIServiceError):
        await runtime.execute_agent_run(db_session,run=run,conversation=conv,user_message='synthetic',expected_attempt_count=1)
    async with session_factory() as db:
        saved=await db.get(AgentRun,rid)
        assert saved.status=='failed' and saved.execution_trace is None
        assert saved.model_usage['http_requests']==1
        assert (saved.input_tokens,saved.output_tokens)==((12,5) if mode=='known' else (None,None))
        assert saved.model_usage['http_errors']==(mode=='missing')


@pytest.mark.asyncio
async def test_stale_worker_cannot_replace_new_owner_metrics(db_session,session_factory,monkeypatch):
    conv,run=await context(db_session);rid=run.id
    async with observe_model_usage(2) as ledger:other=ledger.snapshot(complete=False).model_dump(mode='json')
    transport(monkeypatch,[(200,response({'prompt_tokens':12,'completion_tokens':5}))])
    async def history(*a,**kw):
        await request()
        async with session_factory() as db:
            replacement=await db.get(AgentRun,rid);replacement.attempt_count=2;replacement.model_usage=other
            await db.commit()
        raise AIServiceError('synthetic late failure')
    monkeypatch.setattr(runtime,'_load_history',history)
    with pytest.raises(runtime.AgentRunOwnershipLost):
        await runtime.execute_agent_run(db_session,run=run,conversation=conv,user_message='synthetic',expected_attempt_count=1)
    async with session_factory() as db:
        saved=await db.get(AgentRun,rid)
        assert saved.status=='running' and saved.attempt_count==2 and saved.model_usage==other
        assert saved.input_tokens is None and saved.output_tokens is None


@pytest.mark.asyncio
async def test_reply_replay_preserves_original_metric_and_makes_no_http(db_session,session_factory,monkeypatch):
    conv,run=await context(db_session);rid=run.id
    async with observe_model_usage(1) as ledger:original=ledger.snapshot(complete=True).model_dump(mode='json')
    run.model_usage=original;run.input_tokens=0;run.output_tokens=0
    db_session.add(AgentMessage(conversation_id=conv.id,run_id=run.id,role='assistant',content='previous reply',content_data={}))
    await db_session.commit()
    requests=transport(monkeypatch,[])
    result=await runtime.execute_agent_run(db_session,run=run,conversation=conv,user_message='synthetic',expected_attempt_count=1)
    assert result.reply=='previous reply' and not requests
    async with session_factory() as db:
        saved=await db.get(AgentRun,rid)
        assert saved.status=='completed' and saved.model_usage==original
        assert saved.input_tokens==saved.output_tokens==0


@pytest.mark.asyncio
async def test_usage_migration_keeps_legacy_unknown_and_downgrades(engine,db_session,monkeypatch):
    conv,run=await context(db_session);rid=run.id
    file=Path(__file__).resolve().parents[1]/'alembic/versions/0033_agent_model_usage.py'
    spec=importlib.util.spec_from_file_location('r76_usage_migration',file);migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    async with engine.begin() as connection:
        def migrate(sync):
            op=Operations(MigrationContext.configure(sync));monkeypatch.setattr(migration,'op',op)
            migration.downgrade();migration.upgrade()
            value=sync.execute(text('select model_usage, input_tokens, output_tokens from agent_runs where id=:id'),{'id':rid}).one()
            assert value==(None,None,None)
            migration.downgrade()
            assert sync.execute(text("select count(*) from information_schema.columns where table_schema=current_schema() and table_name='agent_runs' and column_name='model_usage'")).scalar_one()==0
            migration.upgrade()
        await connection.run_sync(migrate)
