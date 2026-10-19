"""Scripted provider IDs through real SDK/Agent/API/worker/PostgreSQL."""
import asyncio,copy,importlib,json,os
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest,pytest_asyncio
from httpx import AsyncClient,ASGITransport
from sqlalchemy import select,func
from evals.postgres_test_fixtures import engine,session_factory,db_session
from app.main import app
from app.config import settings
from app.models.agent import AgentRun,AgentToolCall,AgentProposal,AgentArtifact
from app.models.workout import WorkoutSession,WorkoutPlan
from app.models.meal import MealLog
from app.services import agent_runtime as runtime
from app.services.agent_intent import IntentResolverOutcome
from app.services.auth import create_access_token
from app.services.agent_jobs import claim_next_agent_run,process_agent_run
from test_round56_evidence_sql import seed
from test_round57_read_delivery import scripted_http
from test_round76_live_evidence import ROWS,resolution,tool_calls,GOOD
from test_round76_source_scope_sql import install,run

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

def save(name,data):
    if destination:=os.getenv('R82_EVIDENCE_DIR'):
        (Path(destination)/('api-'+name+'-'+uuid4().hex+'.json')).write_text(json.dumps({'model':'scripted HTTP, no external provider request',**data},ensure_ascii=False,indent=2),encoding='utf8')

async def snapshot(db):
    return {'workouts':[tuple(x) for x in (await db.execute(select(WorkoutSession.id,WorkoutSession.user_id,WorkoutSession.status).order_by(WorkoutSession.id))).all()],
            'meals':await db.scalar(select(func.count()).select_from(MealLog)),
            'proposals':await db.scalar(select(func.count()).select_from(AgentProposal)),
            'artifacts':await db.scalar(select(func.count()).select_from(AgentArtifact))}

MODES=[(m,n) for m in ('same_batch','sequential','budget','prefetch','tool_failure','model_failure','provider503','nul') for n in (1,2,3)]+[(m,1) for m in ('normal','parameters','missing','blank','long','huge')]

@pytest.mark.asyncio
@pytest.mark.parametrize('mode,repeat',MODES)
async def test_identity_survives_actual_worker_and_storage(db_session,session_factory,monkeypatch,mode,repeat):
    user,conv=await seed(db_session,4);other,otherconv=await seed(db_session,2)
    before=await snapshot(db_session);install(monkeypatch,session_factory)
    row=ROWS['SV01-r1'];query=row['turns'][0]['message'];r=resolution(row['turns'][0]['body'])
    if mode in {'parameters','prefetch'}:
        r.evidence_requirements.append('workout_history')
        if mode=='prefetch':
            r.optional_evidence_requirements=['workout_history'];r.request_kind='assessment';r.risk_level='low'
    monkeypatch.setattr(runtime,'resolve_intent_with_fallback',AsyncMock(return_value=IntentResolverOutcome(resolution=r,source='model',attempt_count=1)))
    first=tool_calls('plan_get_active','health_get_screening_summary');duplicate=first['tool_calls'][0]['id']
    if mode not in {'normal','sequential','budget','parameters','prefetch'}:first['tool_calls'][1]['id']=duplicate
    if mode in {'missing','blank','long','huge','nul'}:first['tool_calls'][1]['id']={'missing':None,'blank':'','long':'x'*121,'huge':'x'*5000,'nul':'bad\x00label'}[mode]
    if mode=='sequential':
        first=tool_calls('plan_get_active');second=tool_calls('health_get_screening_summary');second['tool_calls'][0]['id']=duplicate
        sequence=[first,second,{'role':'assistant','content':GOOD}]
    elif mode=='budget':
        monkeypatch.setattr(settings,'AGENT_MAX_TOOL_CALLS',2);sequence=[first,tool_calls('plan_get_active'),{'role':'assistant','content':GOOD}]
    elif mode=='parameters':
        for limit in (1,5):first['tool_calls'].append({'id':'history-dup','type':'function','function':{'name':'workout_list_history','arguments':json.dumps({'limit':limit})}})
        sequence=[first,{'role':'assistant','content':GOOD}]
    elif mode=='prefetch':
        first={'role':'assistant','content':'','tool_calls':[{'id':'pending-prefetch','type':'function','function':{'name':'workout_list_history','arguments':'{"limit":1}'}}]}
        sequence=[first,{'role':'assistant','content':GOOD}]
        from app.services import agent_required_evidence as required
        original_prepare=required.prepare_required_evidence
        async def prepare(*args,**kwargs):
            prepared=await original_prepare(*args,**kwargs)
            first['tool_calls'][0]['id']=next(m.tool_call_id for m in prepared.messages if getattr(m,'type',None)=='tool')
            return prepared
        monkeypatch.setattr(required,'prepare_required_evidence',prepare)
    else:sequence=[first,{'role':'assistant','content':GOOD}]
    if mode in {'tool_failure','model_failure','provider503'}:sequence=sequence[:1]
    actual=[];build=runtime.build_read_tools;plan_finished=asyncio.Event()
    def observed(*args,**kwargs):
        tools=build(*args,**kwargs)
        for tool in tools:
            original=tool.coroutine;name=tool.name
            async def invoke(*a,_original=original,_name=name,**kw):
                if mode=='tool_failure' and _name=='health_get_screening_summary':
                    await asyncio.wait_for(plan_finished.wait(),2);actual.append({'name':_name,'args':kw,'error':'TimeoutError'});raise TimeoutError('synthetic tool failure')
                result=await _original(*a,**kw);actual.append({'name':_name,'args':kw,'result':result})
                if _name=='plan_get_active':plan_finished.set()
                return result
            tool.coroutine=invoke
        return tools
    monkeypatch.setattr(runtime,'build_read_tools',observed)
    requests=scripted_http(monkeypatch,sequence);saved={'mode':mode,'repeat':repeat,'sequence':sequence,'actual':actual,'requests':requests}
    if mode=='provider503':
        statuses=[];saved['response_statuses']=statuses
        for module in ('httpx','httpx2'):
            mod=importlib.import_module(module);original=mod.AsyncClient.send
            async def unavailable(client,request,_mod=mod,_original=original,**kwargs):
                if request.url.host=='test':return await _original(client,request,**kwargs)
                if not requests:
                    response=await _original(client,request,**kwargs);statuses.append(response.status_code);return response
                assert request.url.host=='api.deepseek.com'
                requests.append(json.loads(request.content));statuses.append(503)
                return _mod.Response(503,json={'error':{'type':'synthetic_service_unavailable','message':'scripted fault after tools'}},request=request)
            monkeypatch.setattr(mod.AsyncClient,'send',unavailable)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
            body=await run(client,session_factory,conv,query);saved['body']=body;trace=body['execution_trace']
            if mode in {'tool_failure','model_failure','provider503'}:assert body['status']=='failed' and body['error_code']=='ai_service_error',body
            else:assert body['status']=='completed' and body['error_code'] is None and body['reply']==GOOD,body
            if mode=='normal':assert 'tool_call_identity' not in trace
            else:
                changes=trace['tool_call_identity']['changes'];assert len(changes)==1,changes
                assert changes[0]['reason']==({'missing':'missing','blank':'blank','long':'too_long','huge':'too_long','nul':'invalid_characters'}.get(mode,'duplicate'))
                if mode=='nul':assert changes[0]['provider_id_encoding']=='json_string' and json.loads(changes[0]['provider_id'])=='bad\x00label'
            assert (await client.get('/api/v1/agent/runs/'+body['id'])).json()==body
            async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(other.id)}) as stranger:
                assert (await stranger.get('/api/v1/agent/runs/'+body['id'])).status_code==404
        async with session_factory() as db:
            audits=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==body['id']).order_by(AgentToolCall.id))).all()
            saved['audits']=[{'call_id':a.call_id,'tool':a.tool_name,'args':a.arguments_data,'result':a.result_data,'status':a.status,'error_code':a.error_code} for a in audits]
            expected=4 if mode=='parameters' else 3 if mode in {'prefetch','budget'} else 2
            assert len(audits)==expected and len({a.call_id for a in audits})==expected,saved['audits']
            assert len(actual)==expected-(1 if mode=='budget' else 0)
            if mode=='budget':assert sum(a.error_code=='read_tool_budget_exhausted' for a in audits)==1
            elif mode=='tool_failure':assert sum(a.status=='failed' for a in audits)==1
            else:assert all(a.status=='completed' for a in audits)
            if mode=='parameters':
                histories=sorted((a for a in audits if a.tool_name=='workout.list_history'),key=lambda a:a.arguments_data['limit'])
                assert [a.arguments_data['limit'] for a in histories]==[1,5] and [a.result_data['count'] for a in histories]==[1,5]
            saved_run=await db.get(AgentRun,body['id']);assert saved_run.execution_trace==trace and saved_run.model_usage==body['model_usage']
            assert await snapshot(db)==before
        assert body['model_usage']['http_responses']==len(sequence)+(2 if mode=='provider503' else 0)
        assert body['model_usage']['client_attempts']==len(sequence)+(2 if mode in {'model_failure','provider503'} else 0)
        assert body['model_usage']['known_input_tokens']==12*len(sequence)
        if mode=='provider503':assert statuses==[200,503,503] and body['model_usage']['http_errors']==2 and body['model_usage']['input_tokens'] is None
        saved.update(passed=True,business_unchanged=True,other_user_denied=True)
    finally:app.dependency_overrides.clear();save(mode+'-r'+str(repeat),saved)


@pytest.mark.asyncio
async def test_concurrent_user_runs_keep_separate_identity_and_evidence(db_session,session_factory,monkeypatch):
    owners=[await seed(db_session,n) for n in (1,2)];before=await snapshot(db_session);install(monkeypatch,session_factory)
    row=ROWS['SV01-r1'];r=resolution(row['turns'][0]['body'])
    monkeypatch.setattr(runtime,'resolve_intent_with_fallback',AsyncMock(return_value=IntentResolverOutcome(resolution=r,source='model',attempt_count=1)))
    requests={key:[] for key in ('并发A','并发B')}
    for module in ('httpx','httpx2'):
        mod=importlib.import_module(module);original=mod.AsyncClient.send
        async def provider(client,request,_mod=mod,_original=original,**kwargs):
            if request.url.host=='test':return await _original(client,request,**kwargs)
            assert request.url.host=='api.deepseek.com'
            body=json.loads(request.content);keys=[k for k in requests if k in json.dumps(body,ensure_ascii=False)]
            assert len(keys)==1;key=keys[0];requests[key].append(body)
            assert len(requests[key])<=2
            message=tool_calls('plan_get_active','health_get_screening_summary') if len(requests[key])==1 else {'role':'assistant','content':GOOD}
            if message.get('tool_calls'):
                for call in message['tool_calls']:call['id']='same-across-runs'
            return _mod.Response(200,json={'id':'synthetic-'+key,'object':'chat.completion','model':'deepseek-flash','choices':[{'index':0,'finish_reason':'tool_calls' if message.get('tool_calls') else 'stop','message':message}],'usage':{'prompt_tokens':12,'completion_tokens':5,'total_tokens':17}},request=request)
        monkeypatch.setattr(mod.AsyncClient,'send',provider)
    saved={'requests':requests,'bodies':[]};ids=[]
    try:
        for key,(owner,conv) in zip(requests,owners):
            async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(owner.id)}) as client:
                posted=await client.post('/api/v1/agent/runs',json={'conversation_id':conv.id,'message':key+'。'+row['turns'][0]['message'],'client_request_id':uuid4().hex})
                assert posted.status_code==202;ids.append(posted.json()['run_id'])
        claimed=[]
        for _ in ids:
            async with session_factory() as db:claimed.append(await claim_next_agent_run(db))
        assert set(claimed)==set(ids)
        await asyncio.wait_for(asyncio.gather(*(process_agent_run(session_factory,id) for id in ids)),20)
        for i,(owner,conv) in enumerate(owners):
            async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(owner.id)}) as client:
                body=(await client.get('/api/v1/agent/runs/'+ids[i])).json();saved['bodies'].append(body)
                assert body['status']=='completed' and body['reply']==GOOD and body['model_usage']['http_responses']==2,body
                assert (await client.get('/api/v1/agent/runs/'+ids[1-i])).status_code==404
                plan=next(c['data']['plan'] for c in body['cards'] if c['type']=='plan.get_active')
                async with session_factory() as db:
                    assert (await db.get(WorkoutPlan,plan['id'])).user_id==owner.id
                    audits=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==ids[i]))).all()
                    assert len(audits)==2 and len({a.call_id for a in audits})==2
        changed=[b['execution_trace']['tool_call_identity']['changes'][0]['execution_id'] for b in saved['bodies']]
        assert len(set(changed))==2 and all(len(v)==2 for v in requests.values())
        async with session_factory() as db:assert await snapshot(db)==before
        saved.update(passed=True,business_unchanged=True,other_user_denied=True)
    finally:app.dependency_overrides.clear();save('parallel',saved)
