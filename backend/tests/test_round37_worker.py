"""Real API/worker and SQL audits, deterministic model payloads, no network."""
import copy
import json
from datetime import date
from types import SimpleNamespace
from uuid import uuid4
import pytest
import pytest_asyncio
from sqlalchemy import select,func
from httpx import AsyncClient,ASGITransport
from evals.postgres_test_fixtures import engine,session_factory,db_session
from app.main import app
from app.database import get_db
from app.config import settings
from app.models.agent import AgentRun,AgentToolCall,AgentProposal
from app.models.meal import MealLog
from app.services import agent_runtime,agent_intent_model,nutrition_history_reporting
from app.services.agent_intent import IntentResolution
from app.schemas.agent_task import TaskUpdate
from app.services.agent_task_state import state_from_context,active_task
from app.services.agent_jobs import claim_next_agent_run,process_agent_run
from app.services.auth import create_access_token
from test_agent_domain_proposals import _context
from test_round35_query_contracts import VALID

@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    yield

@pytest_asyncio.fixture
async def client(session_factory):
    async def get_session():
        async with session_factory() as db:yield db
    app.dependency_overrides[get_db]=get_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:yield client
    finally:app.dependency_overrides.clear()

@pytest.mark.asyncio
@pytest.mark.parametrize('invalid',[None,'partial','no_contract','failed_tool','missing_count','extra_tool','wrong_owner','wrong_conversation','failed_run','clarification','high_risk','mutation'])
async def test_nutrition_revision_requires_persisted_success(client,db_session,session_factory,monkeypatch,invalid):
    user,_,conv,_=await _context(db_session,'r37-'+uuid4().hex)
    headers={'Authorization':'Bearer '+create_access_token(user.id)}
    seen=[]
    async def invoke(message,*,context_messages,**kwargs):
        seen.append(copy.deepcopy(context_messages))
        task=active_task(state_from_context(context_messages))
        return IntentResolution(primary_intent='nutrition_today_query' if task is None else 'nutrition_history_query',
            intent_domain='nutrition',request_kind='query',requested_effect='read',confidence=.99,resolved_query=message,
            evidence_requirements=['nutrition_history','nutrition_today'] if task is None else ['nutrition_history'],
            task_update=TaskUpdate(action='continue' if task else 'new',task_id=task.id if task else '',trigger=message))
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','offline')
    monkeypatch.setattr(agent_intent_model,'_invoke_model_intent',invoke)
    first_message='最近4个自然日每天的饮食热量是多少？今天算在内。'
    second_message='把日期改成10月6日到8日，还是只看晚餐。'
    planner_messages=[]
    async def complete_plan(messages,**kwargs):
        original=json.loads(messages[-1]['content'])['original_message']
        assert original in {first_message,second_message}
        planner_messages.append(original)
        # Synthetic plans follow each user's actual dates and meal filter.
        return SimpleNamespace(payload={**VALID,'days':30,'start_date':'2026-10-06',
            'end_date':'2026-10-09' if original==first_message else '2026-10-08',
            'meal_types':[] if original==first_message else ['晚餐'],'operations':['list']})
    monkeypatch.setattr(nutrition_history_reporting,'structured_chat_completion',complete_plan)
    async def enqueue(message):
        r=await client.post('/api/v1/agent/runs',json={'conversation_id':conv.id,'message':message,'client_request_id':uuid4().hex},headers=headers)
        assert r.status_code==202,r.text
        return r.json()['run_id']
    async def process(rid):
        async with session_factory() as db:assert await claim_next_agent_run(db)==rid
        await process_agent_run(session_factory,rid)
        r=await client.get('/api/v1/agent/runs/'+rid,headers=headers)
        assert r.status_code==200
        return r.json()
    first=await enqueue(first_message)
    body=await process(first)
    assert body['execution_trace']['evidence_contract']['status']=='complete'
    assert body['primary_intent']=='nutrition_history_query' and body['tool_allowlist']==['nutrition.list_history']
    second=await enqueue(second_message)
    async with session_factory() as db:
        prior=await db.get(AgentRun,first)
        audit=await db.scalar(select(AgentToolCall).where(AgentToolCall.run_id==first))
        assert audit.result_data['count']==0  # Empty evidence is still a successful query.
        if invalid=='partial':prior.execution_trace={**prior.execution_trace,'evidence_contract':{'status':'partial'}}
        elif invalid=='no_contract':prior.execution_trace={}
        elif invalid=='failed_tool':audit.status='failed'
        elif invalid=='missing_count':audit.result_data={}
        elif invalid=='extra_tool':prior.tool_allowlist=['nutrition.list_history','nutrition.get_today']
        elif invalid=='wrong_owner':
            other,_,_,_=await _context(db,'other-'+uuid4().hex);prior.user_id=other.id
        elif invalid=='wrong_conversation':
            _,_,other,_=await _context(db,'other-'+uuid4().hex);prior.conversation_id=other.id
        elif invalid=='failed_run':prior.status='failed'
        elif invalid=='clarification':prior.clarification_required=True
        elif invalid=='high_risk':prior.risk_level='high'
        elif invalid=='mutation':prior.request_kind='mutation';prior.requested_effect='update'
        await db.commit()
        current=await db.get(AgentRun,second)
        history=await agent_runtime._load_history(db,conversation_id=conv.id,before_run=current)
        verified=[h['query_context'] for h in history if 'query_context' in h]
        assert bool(verified) is (invalid is None)
    if invalid is None:
        # A second real worker Run must receive this context at its semantic gate.
        body=await process(second)
        assert seen[-1][-1]['query_context']['source_run_id']==first
        assert body['execution_trace']['termination_reason']=='verified_nutrition_history_report'
        assert body['execution_trace']['evidence_contract']['status']=='complete'
    assert planner_messages==([first_message,second_message] if invalid is None else [first_message])
    async with session_factory() as db:
        if invalid is None:
            revised_audit=await db.scalar(select(AgentToolCall).where(AgentToolCall.run_id==second))
            assert revised_audit.tool_name=='nutrition.list_history'
            assert revised_audit.arguments_data['start_date']=='2026-10-06'
            assert revised_audit.arguments_data['end_date']=='2026-10-08'
            assert revised_audit.arguments_data['meal_types']==['晚餐']
        assert (await db.scalar(select(func.count()).select_from(MealLog).where(MealLog.user_id==user.id)))==0
        assert (await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id==user.id)))==0
