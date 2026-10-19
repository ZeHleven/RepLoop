"""R39 real archived routing and independent missing-record expectations."""
import copy,json,re
from datetime import date,timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from uuid import uuid4
import pytest,pytest_asyncio
from httpx import AsyncClient,ASGITransport
from sqlalchemy import select,func
from evals.postgres_test_fixtures import engine,session_factory,db_session
from app.config import settings
from app.services.agent_intent import resolve_intent
from app.services import agent_intent_model,nutrition_history_reporting as reporting
from app.services.agent_intent_model import IntentRouteDecision,resolve_intent_with_fallback
from app.main import app
from app.database import get_db
from app.models.agent import AgentToolCall,AgentProposal
from app.models.meal import MealLog,MealItem
from app.services.agent_jobs import claim_next_agent_run,process_agent_run
from app.services.auth import create_access_token
from test_agent_domain_proposals import _context

HERE=Path(__file__).parent
FIX=HERE/'prior-failures/analysis.json'
if not FIX.exists():FIX=HERE/'fixtures/round39/analysis.json'
ROWS=json.loads(FIX.read_text(encoding='utf-8'))['failures']
KQ04=next(r for r in ROWS if r['instance']=='KQ04-r1')

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

@pytest.mark.parametrize('message',[
 KQ04['message'],'查询早餐和晚餐的记录。','列出晚餐的汇总记录。',
 '查询早餐的筛选后记录。','只读已有饮食的全部记录。',
])
def test_nominal_records(message):
 assert resolve_intent(message).request_kind=='query'

@pytest.mark.asyncio
@pytest.mark.parametrize('message',[
 '把早餐和晚餐合并记录。','把早餐和晚餐合并后记录下来。',
 '记录今天早餐米饭200克。','把早餐的记录保存。',
 '查询早餐的合并记录，再记录今天晚餐米饭200克。',
 '查询早餐的合并记录并删除今天午餐。',
 '把早餐的记录改成晚餐。','把今天的饮食记录下来。',
 '把早餐和晚餐的合并记录写入饮食记录。',
 '查询早餐的记录，然后保存今天的训练。',
 '把今天的早餐记录为米饭200克。',
])
async def test_independent_write_commands_are_not_hidden(message):
 assert resolve_intent(message).request_kind=='mutation'
 route=IntentRouteDecision.model_validate(KQ04['routes'][0])
 with patch.object(settings,'DEEPSEEK_API_KEY','offline'),patch.object(agent_intent_model,'_invoke_model_route',new=AsyncMock(return_value=route)):
  outcome=await resolve_intent_with_fallback(message,use_model=True)
 assert outcome.understanding_failed

@pytest.mark.asyncio
@pytest.mark.parametrize('row',[r for r in ROWS if r['instance'].startswith('KQ04')],ids=lambda r:r['instance'])
async def test_original_routes(row):
 for payload in row['routes']:
  with patch.object(settings,'DEEPSEEK_API_KEY','offline'),patch.object(agent_intent_model,'_invoke_model_route',new=AsyncMock(return_value=IntentRouteDecision.model_validate(payload))):
   out=await resolve_intent_with_fallback(row['message'],use_model=True)
  assert not out.understanding_failed,out.error_category
  assert out.resolution.request_kind=='query' and out.resolution.requested_effect=='read'
  assert out.attempt_count==1

def data(days=('2026-10-04',),**scope):
 rows=[dict(date=d,total_calories=195,total_protein_g=4.5,total_carbs_g=42,total_fat_g=1.5) for d in days]
 return dict(count=len(rows),days=rows,scope=dict(selection='specific_dates',requested_limit=30,logged_dates=['2026-10-04','2026-10-05'],meal_types=['早餐'],as_of='2026-10-09',first_logged_date=min(days,default=None),last_logged_date=max(days,default=None),returned_dates=len(rows),**scope))

def test_missing_day_explicit_and_no_zero_denominator():
 d=data();before=copy.deepcopy(d);reply=reporting.render_history(d,metrics=['calories'],operations=['list','mean'])
 assert '2026-10-05：未找到早餐记录' in reply
 assert '均值 195 千卡（除以1个' in reply and '没有记录不表示未进食' in reply
 assert d==before and '2026-10-05：热量 0' not in reply

def test_empty_dates_named_and_warning_retained():
 reply=reporting.render_history(data(()))
 for day in ['2026-10-04','2026-10-05']:assert f'{day}：未找到早餐记录' in reply
 assert '没有记录不表示未进食' in reply

def test_future_dates_not_asserted_missing():
 d=data();d['scope']['logged_dates']=['2026-10-04','2026-10-10']
 reply=reporting.render_history(d)
 assert '2026-10-10：晚于查询截止日期，未纳入历史查询' in reply
 assert '2026-10-10：未找到' not in reply

def test_zero_calories_is_a_record():
 d=data();d['days'][0]['total_calories']=0
 reply=reporting.render_history(d)
 assert '2026-10-04：热量 0 千卡' in reply
 assert '2026-10-04：未找到' not in reply

def test_latest_record_dates_do_not_imply_calendar_gaps():
 d=data();d['scope'].update(selection='latest_logged_dates',requested_limit=30,logged_dates=[])
 reply=reporting.render_history(d)
 assert '2026-10-05' not in reply and '未找到' not in reply

@pytest.mark.parametrize('meal_types,expected',[([], '饮食'),(['早餐','晚餐'],'早餐、晚餐')])
def test_missing_meal_scope(meal_types,expected):
 d=data();d['scope']['meal_types']=meal_types
 assert f'2026-10-05：未找到{expected}记录' in reporting.render_history(d)

def test_calendar_gaps_compact_and_complete():
 d=data();d['scope'].update(selection='calendar_range',logged_dates=[],start_date='2026-10-01',end_date='2026-10-07')
 reply=reporting.render_history(d)
 assert '2026-10-01 至 2026-10-03（共3天）：未找到早餐记录' in reply
 assert '2026-10-05 至 2026-10-07（共3天）：未找到早餐记录' in reply

def test_calendar_future_portion_separated():
 d=data(());d['scope'].update(selection='calendar_range',logged_dates=[],start_date='2026-10-08',end_date='2026-10-11')
 reply=reporting.render_history(d)
 assert '2026-10-08 至 2026-10-09（共2天）：未找到早餐记录' in reply
 assert '2026-10-10 至 2026-10-11（共2天）：晚于查询截止日期，未纳入历史查询' in reply

def test_long_calendar_gap_bounded_not_truncated():
 d=data(());d['scope'].update(selection='calendar_range',logged_dates=[],start_date='2025-10-10',end_date='2026-10-09')
 reply=reporting.render_history(d)
 assert '2025-10-10 至 2026-10-09（共365天）：未找到早餐记录' in reply
 assert len(reply)<500

@pytest.mark.parametrize('mutation',[
 {'returned_dates':2},{'selection':'other'},
 {'selection':'calendar_range','start_date':'2020-01-01','end_date':'2026-10-09'},
 {'selection':'calendar_range','start_date':'bad','end_date':'bad'},
 {'logged_dates':['2026-10-04','2026-10-04']},
])
def test_incomplete_or_invalid_scope_cannot_certify_absence(mutation):
 d=data();d['scope'].update(mutation)
 assert '未找到' not in reporting.render_history(d)

@pytest.mark.asyncio
@pytest.mark.parametrize('case',['KQ02','KQ04'])
async def test_real_api_worker_sql_and_reopened_reply(case,db_session,session_factory,monkeypatch):
 row=next(r for r in ROWS if r['instance']==case+'-r1')
 user,_,conv,_=await _context(db_session,'r39-'+uuid4().hex)
 day='2026-10-04' if case=='KQ02' else '2026-10-08'
 meal=MealLog(user_id=user.id,logged_at=date.fromisoformat(day),meal_type='早餐')
 db_session.add(meal);await db_session.flush()
 db_session.add(MealItem(meal_id=meal.id,food_name='合成食品',amount_g=100,calories=195,protein_g=4.5,carbs_g=42,fat_g=1.5))
 await db_session.commit()
 monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','offline')
 monkeypatch.setattr(agent_intent_model,'_invoke_model_route',AsyncMock(return_value=IntentRouteDecision.model_validate(row['routes'][0])))
 plan=dict(supported=True,days=30,start_date='',end_date='',logged_dates=['2026-10-04','2026-10-05'] if case=='KQ02' else ['2026-10-08'],meal_types=['早餐'] if case=='KQ02' else ['早餐','晚餐'],metrics=['calories'],operations=['list'])
 monkeypatch.setattr(reporting,'structured_chat_completion',AsyncMock(return_value=SimpleNamespace(payload=plan)))
 async def get_session():
  async with session_factory() as db:yield db
 app.dependency_overrides[get_db]=get_session
 try:
  async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
   r=await client.post('/api/v1/agent/runs',json=dict(conversation_id=conv.id,message=row['message'],client_request_id=uuid4().hex));assert r.status_code==202
   rid=r.json()['run_id']
   async with session_factory() as db:assert await claim_next_agent_run(db)==rid
   await process_agent_run(session_factory,rid)
   r=await client.get('/api/v1/agent/runs/'+rid);assert r.status_code==200
   body=r.json();assert body['error_code'] is None and body['status']=='completed'
   assert '195 千卡' in body['reply']
   if case=='KQ02':assert '2026-10-05：未找到早餐记录' in body['reply']
   reopened=(await client.get('/api/v1/agent/conversations/'+conv.id+'/messages')).json()
   persisted=next(m for m in reopened if m.get('run_id')==rid and m['role']=='assistant')
   assert persisted['content']==body['reply']
   async with session_factory() as db:
    audits=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==rid))).all()
    assert len(audits)==1 and audits[0].status=='completed'
    assert await db.scalar(select(func.count()).select_from(MealLog))==1
    assert await db.scalar(select(func.count()).select_from(AgentProposal))==0
 finally:app.dependency_overrides.clear()
