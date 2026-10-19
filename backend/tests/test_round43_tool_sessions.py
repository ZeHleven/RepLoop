"""R43 real PostgreSQL archived ReAct replay and per-session safety contracts."""
import asyncio,copy,json
from datetime import date
from pathlib import Path
from uuid import uuid4
from unittest.mock import AsyncMock
from types import SimpleNamespace
import pytest,pytest_asyncio
from sqlalchemy import select,func
from sqlalchemy.ext.asyncio import AsyncSession
from langchain_core.messages import AIMessage,ToolMessage
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from httpx import AsyncClient,ASGITransport
from evals.postgres_test_fixtures import engine,session_factory,db_session
from app.services import agent_tools,agent_runtime,agent_intent_model
from app.services.agent_tools import build_read_tools
from app.services.agent_intent_model import IntentRouteDecision
from app.services.business_clock import BUSINESS_DATE
from app.models.agent import AgentRun,AgentToolCall,AgentProposal
from app.models.meal import MealLog,MealItem
from app.services.agent_jobs import claim_next_agent_run,process_agent_run
from app.services.auth import create_access_token
from app.main import app
from app.database import get_db
from app.config import settings
from test_agent_domain_proposals import _context

HERE=Path(__file__).parent
FIX=HERE/'archived-route.json'
if not FIX.exists():FIX=HERE/'fixtures/round43/archived-route.json'
ARCHIVE=json.loads(FIX.read_text(encoding='utf8'))

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

@pytest.fixture(autouse=True)
def scene(monkeypatch):
 token=BUSINESS_DATE.set(date(2026,10,9))
 monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','offline')
 async def blocked(*args,**kwargs):raise AssertionError('External HTTP forbidden in regression')
 import httpx,httpx2
 original=httpx.AsyncClient.send
 async def local_only(client,request,**kwargs):
  assert request.url.host=='test','External HTTP forbidden in regression'
  return await original(client,request,**kwargs)
 monkeypatch.setattr(httpx.AsyncClient,'send',local_only)
 monkeypatch.setattr(httpx2.AsyncClient,'send',blocked)
 yield
 BUSINESS_DATE.reset(token)

class ArchivedModel(FakeMessagesListChatModel):
 def bind_tools(self,tools,**kwargs):return self

def model():
 return ArchivedModel(responses=[AIMessage(content='',tool_calls=copy.deepcopy(ARCHIVE['calls'])),AIMessage(content='已核对历史晚餐和近期记录；没有记录不表示零摄入。')])

@pytest.mark.asyncio
@pytest.mark.parametrize('split_build',[False,True])
async def test_tools_serialize_same_session_across_builds(monkeypatch,split_build):
 active=0;peak=0;owners=[]
 async def observed(db,**kwargs):
  nonlocal active,peak
  active+=1;peak=max(peak,active);owners.append(kwargs['user_id'])
  try:await asyncio.sleep(.02);return []
  finally:active-=1
 monkeypatch.setattr(agent_tools,'list_nutrition_history',observed)
 async with AsyncSession() as db:
  a=build_read_tools(db,user_id='one',allowlist=['nutrition.list_history'])[0]
  b=build_read_tools(db,user_id='two',allowlist=['nutrition.list_history'])[0] if split_build else a
  await asyncio.gather(a.ainvoke({}),b.ainvoke({}))
 assert peak==1
 assert sorted(owners)==(['one','two'] if split_build else ['one','one'])

@pytest.mark.asyncio
async def test_distinct_sessions_can_overlap(monkeypatch):
 both=asyncio.Event();active=0;seen=[]
 async def observed(db,**kwargs):
  nonlocal active
  active+=1;seen.append(kwargs['user_id'])
  if active==2:both.set()
  await asyncio.wait_for(both.wait(),1)
  return []
 monkeypatch.setattr(agent_tools,'list_nutrition_history',observed)
 async with AsyncSession() as a,AsyncSession() as b:
  tools=[build_read_tools(db,user_id=uid,allowlist=['nutrition.list_history'])[0] for db,uid in [(a,'a'),(b,'b')]]
  await asyncio.gather(*(t.ainvoke({}) for t in tools))
 assert sorted(seen)==['a','b']

@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['exception','cancelled_holder','cancelled_waiter'])
async def test_lock_released_without_entering_cancelled_waiter(monkeypatch,failure):
 entered=asyncio.Event();release=asyncio.Event();calls=0
 async def observed(db,**kwargs):
  nonlocal calls
  calls+=1;n=calls
  if n==1:
   entered.set();await release.wait()
   if failure=='exception':raise ValueError('synthetic read failure')
  return []
 monkeypatch.setattr(agent_tools,'list_nutrition_history',observed)
 async with AsyncSession() as db:
  tool=build_read_tools(db,user_id='one',allowlist=['nutrition.list_history'])[0]
  first=asyncio.create_task(tool.ainvoke({}));await entered.wait()
  second=asyncio.create_task(tool.ainvoke({}));await asyncio.sleep(.01)
  if failure=='cancelled_waiter':
   second.cancel()
   with pytest.raises(asyncio.CancelledError):await second
   assert calls==1
   release.set();await first
  elif failure=='cancelled_holder':
   first.cancel()
   with pytest.raises(asyncio.CancelledError):await first
   await asyncio.wait_for(second,1)
  else:
   release.set()
   with pytest.raises(ValueError):await first
   await asyncio.wait_for(second,1)
  assert (await tool.ainvoke({}))['days']==[]

@pytest.mark.asyncio
async def test_call_id_arguments_and_identity_contract(monkeypatch):
 observed=AsyncMock(return_value=[])
 monkeypatch.setattr(agent_tools,'list_nutrition_history',observed)
 async with AsyncSession() as db:
  tool=build_read_tools(db,user_id='server-user',allowlist=['nutrition.list_history'])[0]
  message=await tool.ainvoke({'type':'tool_call','name':tool.name,'id':'retained-call-id','args':{'start_date':'2026-10-07','end_date':'2026-10-11','meal_types':['晚餐']}})
  assert isinstance(message,ToolMessage) and message.tool_call_id=='retained-call-id'
  assert json.loads(message.content)['days']==[]
  assert observed.call_args.kwargs['user_id']=='server-user'
  with pytest.raises(ValueError):await tool.ainvoke({'user_id':'other-user'})
  assert observed.await_count==1
  with pytest.raises(ValueError):build_read_tools(db,user_id='server-user',allowlist=['nutrition.delete'])

@pytest.mark.asyncio
@pytest.mark.parametrize('repeat',range(3))
async def test_archived_double_call_actual_agent_fresh_session(session_factory,db_session,monkeypatch,repeat):
 user,_,_,_=await _context(db_session,'r43-replay-'+uuid4().hex)
 monkeypatch.setattr(agent_runtime,'_build_model',model)
 async with session_factory() as db:
  result=await agent_runtime.invoke_langchain_agent(db,user_id=user.id,history=[],user_message=ARCHIVE['message'],tool_allowlist=['nutrition.list_history','nutrition.get_recent_context'])
 messages=[m for m in result['messages'] if isinstance(m,ToolMessage)]
 assert {m.tool_call_id for m in messages}=={c['id'] for c in ARCHIVE['calls']}
 assert len(messages)==2 and all(m.status=='success' for m in messages)

@pytest.mark.asyncio
async def test_archived_calls_for_explicit_two_goals_persist_both_results(db_session,session_factory,monkeypatch):
 user,_,conv,_=await _context(db_session,'r43-api-'+uuid4().hex)
 meal=MealLog(user_id=user.id,logged_at=date(2026,10,8),meal_type='晚餐');db_session.add(meal);await db_session.flush()
 db_session.add(MealItem(meal_id=meal.id,food_name='合成食品',amount_g=100,calories=199,protein_g=33.8,carbs_g=6.6,fat_g=4));await db_session.commit()
 # R45 removes redundant recent evidence from the archived single goal.
 # Keep this session test on a genuine two-goal request; the archived raw
 # route and direct double-call replays above remain unchanged.
 message=ARCHIVE['message']+'另查最近7天的饮食记录。'
 decision=copy.deepcopy(ARCHIVE['route'])
 decision['normalized_request']=message
 monkeypatch.setattr(agent_intent_model,'_invoke_model_route',AsyncMock(return_value=IntentRouteDecision.model_validate(decision)))
 monkeypatch.setattr(agent_runtime,'_build_model',model)
 async def override():
  async with session_factory() as db:yield db
 app.dependency_overrides[get_db]=override
 try:
  async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
   response=await client.post('/api/v1/agent/runs',json=dict(conversation_id=conv.id,message=message,client_request_id=uuid4().hex));assert response.status_code==202
   rid=response.json()['run_id']
   async with session_factory() as db:assert await claim_next_agent_run(db)==rid
   await process_agent_run(session_factory,rid)
   body=(await client.get('/api/v1/agent/runs/'+rid)).json()
   assert body['status']=='completed' and body['error_code'] is None
   assert {c['type'] for c in body['cards']}=={'nutrition.list_history','nutrition.get_recent_context'}
   async with session_factory() as db:
    audits=list((await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==rid))).all())
    assert {a.call_id for a in audits}=={c['id'] for c in ARCHIVE['calls']}
    assert all(a.status=='completed' for a in audits)
    assert await db.scalar(select(func.count()).select_from(AgentProposal))==0
    assert await db.scalar(select(func.count()).select_from(MealLog))==1
    assert (await db.get(AgentRun,rid)).attempt_count==1
   reopened=(await client.get('/api/v1/agent/conversations/'+conv.id+'/messages')).json()
   reply=next(m for m in reopened if m.get('run_id')==rid and m['role']=='assistant')
   assert reply['content']==body['reply'] and reply['content_data']['cards']==body['cards']
 finally:app.dependency_overrides.clear()
