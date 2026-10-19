"""R38 acceptance: attempt accounting, read-only bounds, real SQL/API persistence."""
import asyncio,copy,json,time
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock
import pytest
import pytest_asyncio
from langchain_core.tools import StructuredTool
from pydantic import BaseModel
from sqlalchemy import select,func,text
from httpx import AsyncClient,ASGITransport
from evals.postgres_test_fixtures import engine,session_factory,db_session
from app.config import settings
from app.main import app
from app.database import get_db
from app.models.agent import AgentRun,AgentToolCall,AgentMessage,AgentProposal
from app.models.meal import MealLog
from app.services import agent_runtime as runtime,agent_query_reports as reports,nutrition_history_reporting as nutrition
from app.services.agent_intent import IntentResolution,IntentResolverOutcome
from app.services.agent_jobs import claim_next_agent_run,process_agent_run
from app.services.auth import create_access_token
from test_agent_domain_proposals import _context
from test_round35_query_contracts import VALID

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

class Args(BaseModel):
    days:int=2

@pytest.mark.asyncio
@pytest.mark.parametrize('tool_id,kind',[('nutrition.list_history','nutrition_history'),('workout.list_history','history'),('workout.get_progress','progress')])
@pytest.mark.parametrize('scenario,expected,code',[
    ('normal',1,None),('timeout_once',2,None),('delayed_once',2,None),
    ('timeout',2,'read_tool_timeout'),('delayed',2,'read_tool_timeout'),
    ('schema',1,'read_tool_argument_invalid'),('permission',1,'read_tool_permission_denied'),
    ('execution',1,'read_tool_execution_failed'),('timeout_then_schema',2,'read_tool_argument_invalid'),
])
async def test_attempts(monkeypatch,tool_id,kind,scenario,expected,code):
    calls=[];cancelled=[]
    monkeypatch.setitem(settings.__dict__,'AGENT_QUERY_READ_TIMEOUT_SECONDS',.03)
    async def invoke(days):
        calls.append(days)
        if scenario=='timeout' or scenario in ('timeout_once','timeout_then_schema') and len(calls)==1:raise TimeoutError('private detail')
        if scenario=='delayed' or scenario=='delayed_once' and len(calls)==1:
            try:await asyncio.sleep(.1)
            finally:cancelled.append(True)
        if scenario=='permission':raise PermissionError('secret permission')
        if scenario=='execution':raise RuntimeError('secret sql')
        if scenario=='timeout_then_schema':Args(days='invalid')
        return {'count':0,'days':[]}
    tool=StructuredTool.from_function(coroutine=invoke,name=tool_id.replace('.','_'),description='test read',args_schema=Args)
    monkeypatch.setattr(nutrition,'render_history',lambda *a,**kw:'没有饮食记录')
    monkeypatch.setattr(reports,'render_history',lambda *a:'没有训练记录')
    monkeypatch.setattr(reports,'render_progress',lambda *a:'没有训练记录')
    monkeypatch.setattr(reports,'scope_progress',lambda data,scope:data)
    args={'days':'bad' if scenario=='schema' else 2};original=copy.deepcopy(args)
    report=reports.QueryReport(kind,tool_id,args,'test')
    result=await reports.execute_query_report(report,[tool])
    records=runtime._tool_call_records(result,'test-run')
    reply,cards=runtime._extract_agent_output(result)
    assert len(records)==expected and len({r.call_id for r in records})==expected
    assert all(r.arguments_data==original and r.duration_ms is not None for r in records)
    assert args==original and reply and 'secret' not in reply and 'private' not in reply
    assert result.get('read_error_code')==code
    if code:
        assert cards==[] and all(r.status=='failed' for r in records)
        assert records[-1].error_code==code and records[-1].result_data.get('error_code')==code
    else:
        assert len(cards)==1 and cards[0]['data']=={'count':0,'days':[]}
        assert records[-1].status=='completed'
        if expected==2:assert records[0].status=='failed' and records[0].error_code=='read_tool_timeout'
    if scenario.startswith('delayed'):assert len(cancelled)==(2 if scenario=='delayed' else 1)

@pytest.mark.asyncio
@pytest.mark.parametrize('budget',[0,1,2,4])
async def test_global_budget(monkeypatch,budget):
    monkeypatch.setattr(settings,'AGENT_MAX_TOOL_CALLS',budget)
    invoke=AsyncMock(side_effect=TimeoutError())
    tool=SimpleNamespace(name='nutrition_list_history',ainvoke=invoke)
    result=await reports.execute_query_report(reports.QueryReport('nutrition_history','nutrition.list_history',{'days':2},'q'),[tool])
    assert invoke.await_count==min(budget,2)
    assert result['read_error_code']==('read_tool_budget_exhausted' if budget==0 else 'read_tool_timeout')
    assert result['report_cards']==[]

@pytest.mark.asyncio
@pytest.mark.parametrize('tool_id,kind',[('nutrition.log_meal','nutrition_history'),('workout.list_history','nutrition_history')])
async def test_write_or_mismatched_kind_never_invoked(tool_id,kind):
    invoke=AsyncMock(return_value={'count':0,'days':[]})
    tool=SimpleNamespace(name=tool_id.replace('.','_'),ainvoke=invoke)
    with pytest.raises(ValueError):await reports.execute_query_report(reports.QueryReport(kind,tool_id,{},'q'),[tool])
    assert invoke.await_count==0

@pytest.mark.asyncio
async def test_external_cancellation_never_retried(monkeypatch):
    began=asyncio.Event();calls=[]
    async def invoke(args):calls.append(args);began.set();await asyncio.sleep(10)
    tool=SimpleNamespace(name='nutrition_list_history',ainvoke=invoke)
    task=asyncio.create_task(reports.execute_query_report(reports.QueryReport('nutrition_history','nutrition.list_history',{'days':2},'q'),[tool]))
    await began.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert len(calls)==1

@pytest.mark.asyncio
@pytest.mark.parametrize('scenario,code,count',[
    ('timeout_once',None,2),('sql_timeout_once',None,2),('delayed_once',None,2),
    ('timeout','read_tool_timeout',2),('delayed','read_tool_timeout',2),
    ('schema','read_tool_argument_invalid',1),('sql_error','read_tool_execution_failed',1),
    ('permission','read_tool_permission_denied',1),('execution','read_tool_execution_failed',1),
])
@pytest.mark.parametrize('execution',['worker','sync'])
async def test_worker_persists_visible_reply_and_all_attempts(db_session,session_factory,monkeypatch,scenario,code,count,execution):
    user,_,conv,_=await _context(db_session,'r38-'+uuid4().hex)
    async def override():
        async with session_factory() as db:yield db
    app.dependency_overrides[get_db]=override
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','offline')
    monkeypatch.setitem(settings.__dict__,'AGENT_QUERY_READ_TIMEOUT_SECONDS',.05)
    resolution=IntentResolution(primary_intent='nutrition_history_query',intent_domain='nutrition',request_kind='query',requested_effect='read',evidence_requirements=['nutrition_history'],resolved_query='查询最近2个有饮食记录的日期，逐日列热量。',confidence=.99)
    monkeypatch.setattr(runtime,'resolve_intent_with_fallback',AsyncMock(return_value=IntentResolverOutcome(resolution=resolution,source='model',attempt_count=1)))
    monkeypatch.setattr(nutrition,'structured_chat_completion',AsyncMock(return_value=SimpleNamespace(payload={**VALID,'days':2,'operations':['list']})))
    original=runtime.build_read_tools;calls=[]
    def builder(db,*args,**kwargs):
        ts=original(db,*args,**kwargs)
        for t in ts:
            orig=t.coroutine
            async def injected(_orig=orig,**kw):
                calls.append(copy.deepcopy(kw))
                if scenario=='timeout' or scenario=='timeout_once' and len(calls)==1:raise TimeoutError()
                if scenario=='sql_timeout_once' and len(calls)==1:
                    try:await db.execute(text('SELECT 1/0'))
                    except Exception as exc:raise TimeoutError() from exc
                if scenario=='sql_error':await db.execute(text('SELECT 1/0'))
                if scenario=='delayed' or scenario=='delayed_once' and len(calls)==1:await db.execute(text('SELECT pg_sleep(2)'))
                if scenario=='permission':raise PermissionError('secret permission')
                if scenario=='execution':raise RuntimeError('secret sql')
                return await _orig(**kw)
            t.coroutine=injected
        return ts
    monkeypatch.setattr(runtime,'build_read_tools',builder)
    if scenario=='schema':monkeypatch.setattr(nutrition,'history_arguments',lambda plan:{'days':'bad'})
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
            if execution=='sync':
                r=await client.post('/api/v1/agent/chat',json={'conversation_id':conv.id,'message':resolution.resolved_query})
                assert r.status_code==200,r.text
                rid=r.json()['run_id']
            else:
                r=await client.post('/api/v1/agent/runs',json={'conversation_id':conv.id,'message':resolution.resolved_query,'client_request_id':uuid4().hex})
                assert r.status_code==202,r.text
                rid=r.json()['run_id']
                async with session_factory() as db:assert await claim_next_agent_run(db)==rid
                await asyncio.wait_for(process_agent_run(session_factory,rid),10)
            r=await client.get('/api/v1/agent/runs/'+rid);body=r.json()
            assert body['status']=='completed' and body['reply'],body
            assert body.get('error_code')==code
            trace=body['execution_trace']
            assert trace['budget_usage']['tool_calls']==count
            assert trace['terminal_action']==('safe_stop' if code else 'answer')
            assert trace['evidence_contract']['status']==('partial' if code else 'complete')
            assert [o['status'] for o in trace['observations']]==(['error']*count if code else ['error','success'])
            assert all(s['status']==('failed' if code else 'completed') for s in trace['plan']['steps'])
            if code:assert not body['cards']
            again=await client.get('/api/v1/agent/runs/'+rid);assert again.json()['reply']==body['reply']
        async with session_factory() as db:
            audits=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==rid).order_by(AgentToolCall.created_at,AgentToolCall.id))).all()
            assert len(audits)==count and all(a.duration_ms is not None for a in audits)
            assert sum(a.status=='failed' for a in audits)==(count if code else 1)
            message=await db.scalar(select(AgentMessage).where(AgentMessage.run_id==rid,AgentMessage.role=='assistant'))
            assert message.content==body['reply']
            assert (await db.scalar(select(func.count()).select_from(MealLog).where(MealLog.user_id==user.id)))==0
            assert (await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id==user.id)))==0
    finally:app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_lost_ownership_signal_is_not_tool_failure():
    invoke=AsyncMock(side_effect=runtime.AgentRunOwnershipLost('stale attempt'))
    with pytest.raises(runtime.AgentRunOwnershipLost):
        await reports.execute_query_report(reports.QueryReport('nutrition_history','nutrition.list_history',{'days':2},'q'),[SimpleNamespace(name='nutrition_list_history',ainvoke=invoke)])
    assert invoke.await_count==1

@pytest.mark.asyncio
async def test_retry_uses_pristine_arguments(monkeypatch):
    seen=[]
    async def invoke(args):
        seen.append(copy.deepcopy(args));args['days']=99
        if len(seen)==1:raise TimeoutError()
        return {'count':0,'days':[]}
    monkeypatch.setattr(nutrition,'render_history',lambda *a,**kw:'没有记录')
    report=reports.QueryReport('nutrition_history','nutrition.list_history',{'days':2},'q')
    result=await reports.execute_query_report(report,[SimpleNamespace(name='nutrition_list_history',ainvoke=invoke)])
    assert seen==[{'days':2},{'days':2}] and report.arguments=={'days':2}
    assert all(r.arguments_data=={'days':2} for r in runtime._tool_call_records(result,'run'))

def test_arbitrary_error_payload_does_not_crash_auditor():
    assert runtime._audit_result_summary('nutrition.list_history',{'error_code':{'untrusted':'data'}})=={}
