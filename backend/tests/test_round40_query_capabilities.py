"""R40 archived refusals, bounded review, and paired mutation safeguards."""
import copy,json
from datetime import date
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
from app.services.ai_client import AIServiceError
from app.main import app
from app.database import get_db
from app.models.agent import AgentToolCall,AgentProposal
from app.models.meal import MealLog,MealItem
from app.services.agent_jobs import claim_next_agent_run,process_agent_run
from app.services.auth import create_access_token
from test_agent_domain_proposals import _context

HERE=Path(__file__).parent
FIX=HERE/'prior-failures/analysis.json'
if not FIX.exists():FIX=HERE/'fixtures/round40/analysis.json'
ROWS=json.loads(FIX.read_text(encoding='utf-8'))['failures']
def first(cid):return next(r for r in ROWS if r['instance']==cid)['turns'][0]
def payload(row,name):return next(o['payload'] for o in row['outputs'] if o['name']==name)
BASE=payload(first('LQ02-r1'),'submit_nutrition_history_query')
TODAY=date(2026,10,9)

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

@pytest.fixture(autouse=True)
def business_scene():
 from app.services.business_clock import BUSINESS_DATE
 token=BUSINESS_DATE.set(date(2026,10,9))
 try:yield
 finally:BUSINESS_DATE.reset(token)

@pytest.mark.asyncio
@pytest.mark.parametrize('cid',['LQ02-r1','LQ06-r1','LQ06-r2'])
async def test_original_false_gets_one_complete_review(cid,monkeypatch):
 row=first(cid);original=payload(row,'submit_nutrition_history_query');corrected=dict(original,supported=True)
 mock=AsyncMock(side_effect=[SimpleNamespace(payload=original),SimpleNamespace(payload=corrected)])
 monkeypatch.setattr(reporting,'structured_chat_completion',mock)
 plan=await reporting.plan_nutrition_history(message=row['message'],resolved_query=row['message'],today=TODAY)
 assert plan.model_dump()==dict(corrected,comparison_targets=[],comparison_scope='meal',comparison_scope_quote='',response_mode='structured',date_comparisons=[]) and mock.await_count==2
 request=json.loads(mock.call_args.args[0][1]['content'])
 assert request['original_message']==row['message'] and request['resolved_query']==row['message']
 assert request['repair_context']['errors']==['review_query_capability']
 assert request['repair_context']['previous_plan']==dict(original,comparison_targets=[],comparison_scope='meal',comparison_scope_quote='',response_mode='structured',date_comparisons=[])

@pytest.mark.asyncio
@pytest.mark.parametrize('message',[
 '查这两个日期的逐食物营养明细。','判断这些热量是否健康。','给我未来一周饮食建议。',
 '查询并保存今天午餐。','比较上周与本周的总热量。','10月4日热量减去10月6日是多少。',
])
async def test_persistent_unsupported_stays_visible_and_bounded(message,monkeypatch):
 mock=AsyncMock(return_value=SimpleNamespace(payload=BASE))
 monkeypatch.setattr(reporting,'structured_chat_completion',mock)
 with pytest.raises(reporting.NutritionHistoryPlanError) as error:
  await reporting.plan_nutrition_history(message=message,resolved_query=message,today=TODAY)
 assert error.value.category=='nutrition_query_unsupported' and mock.await_count==2

@pytest.mark.asyncio
@pytest.mark.parametrize('changes',[
 {'logged_dates':[]},{'logged_dates':['2026-10-04']},{'logged_dates':['2026-10-04','2026-10-07']},
 {'meal_types':[]},{'meal_types':['早餐']},{'meal_types':['午餐','晚餐']},
 {'metrics':['protein_g']},{'operations':['sum']},
])
async def test_capability_review_cannot_change_valid_requirements(changes,monkeypatch):
 changed=dict(BASE,supported=True,**changes)
 mock=AsyncMock(side_effect=[SimpleNamespace(payload=BASE),SimpleNamespace(payload=changed)])
 monkeypatch.setattr(reporting,'structured_chat_completion',mock)
 with pytest.raises(reporting.NutritionHistoryPlanError) as error:
  await reporting.plan_nutrition_history(message='完整查询',resolved_query='完整查询',today=TODAY)
 assert error.value.category=='nutrition_query_plan_invalid' and mock.await_count==2

@pytest.mark.asyncio
async def test_supported_first_pass_no_extra_call(monkeypatch):
 mock=AsyncMock(return_value=SimpleNamespace(payload=dict(BASE,supported=True)))
 monkeypatch.setattr(reporting,'structured_chat_completion',mock)
 assert (await reporting.plan_nutrition_history(message='查午餐',resolved_query='查午餐',today=TODAY)).supported
 assert mock.await_count==1
 system=mock.call_args.args[0][0]['content']
 assert '缺记录日期' in system and '未来日期' in system and '不支持' in system

@pytest.mark.asyncio
async def test_transport_error_is_not_a_capability_retry(monkeypatch):
 mock=AsyncMock(side_effect=AIServiceError('offline transport'))
 monkeypatch.setattr(reporting,'structured_chat_completion',mock)
 with pytest.raises(AIServiceError,match='offline transport'):
  await reporting.plan_nutrition_history(message='查询',resolved_query='查询',today=TODAY)
 assert mock.await_count==1

@pytest.mark.parametrize('message',[
 first('LQ05-r1')['message'],'列出已记录日期的早餐热量。','列出缺记录日期。',
 '查询未记录日期，不能当作零摄入。','查询早餐已记录日数和缺记录天数。',
])
def test_date_nouns_are_readonly(message):assert resolve_intent(message).request_kind=='query'

@pytest.mark.asyncio
@pytest.mark.parametrize('message',[
 '记录日期为2026年10月8日的早餐米饭200克。','把早餐记录日期改为昨天。',
 '查询已记录日期，再记录今天早餐米饭200克。','查询缺记录日期并删除昨天午餐。',
 '把已记录日期的早餐改成晚餐。','查询未记录日期，然后保存今天的训练。',
 '把2026年10月8日记录为早餐日期。','把缺记录日期的餐食补录进去。',
])
async def test_writes_not_hidden_by_nominal_masks(message):
 assert resolve_intent(message).request_kind=='mutation'
 route=IntentRouteDecision.model_validate(payload(first('LQ05-r1'),'submit_semantic_route'))
 with patch.object(settings,'DEEPSEEK_API_KEY','offline'),patch.object(agent_intent_model,'_invoke_model_route',AsyncMock(return_value=route)):
  result=await resolve_intent_with_fallback(message,use_model=True)
 assert result.understanding_failed

@pytest.mark.asyncio
@pytest.mark.parametrize('cid',['LQ05-r1','LQ05-r2'])
async def test_original_read_routes_not_rejected(cid):
 row=first(cid)
 for out in row['outputs']:
  if out['name']!='submit_semantic_route':continue
  route=IntentRouteDecision.model_validate(out['payload'])
  with patch.object(settings,'DEEPSEEK_API_KEY','offline'),patch.object(agent_intent_model,'_invoke_model_route',AsyncMock(return_value=route)):
   result=await resolve_intent_with_fallback(row['message'],use_model=True)
  assert not result.understanding_failed and result.resolution.request_kind=='query'

@pytest.mark.asyncio
@pytest.mark.parametrize('cid,deny',['LQ02-r1',False] if False else [('LQ02-r1',False),('LQ02-r1',True),('LQ05-r1',False)])
async def test_api_worker_review_and_sql_integrity(cid,deny,db_session,session_factory,monkeypatch):
 row=first(cid);user,_,conv,_=await _context(db_session,'r40-'+uuid4().hex)
 day='2026-10-06' if cid=='LQ02-r1' else '2026-10-08';mealtype='午餐' if cid=='LQ02-r1' else '早餐'
 meal=MealLog(user_id=user.id,logged_at=date.fromisoformat(day),meal_type=mealtype);db_session.add(meal);await db_session.flush()
 db_session.add(MealItem(meal_id=meal.id,food_name='合成食品',amount_g=100,calories=195,protein_g=4.5,carbs_g=42,fat_g=1.5));await db_session.commit()
 monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','offline')
 monkeypatch.setattr(agent_intent_model,'_invoke_model_route',AsyncMock(return_value=IntentRouteDecision.model_validate(payload(row,'submit_semantic_route'))))
 plan=copy.deepcopy(BASE) if cid=='LQ02-r1' else dict(supported=False,days=30,start_date='2026-10-08',end_date='2026-10-10',logged_dates=[],meal_types=['早餐'],metrics=['calories'],operations=['list'])
 planner=AsyncMock(side_effect=[SimpleNamespace(payload=plan),SimpleNamespace(payload=dict(plan,supported=not deny))]);monkeypatch.setattr(reporting,'structured_chat_completion',planner)
 async def get_session():
  async with session_factory() as db:yield db
 app.dependency_overrides[get_db]=get_session
 try:
  async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
   response=await client.post('/api/v1/agent/runs',json=dict(conversation_id=conv.id,message=row['message'],client_request_id=uuid4().hex));assert response.status_code==202
   rid=response.json()['run_id']
   async with session_factory() as db:assert await claim_next_agent_run(db)==rid
   await process_agent_run(session_factory,rid)
   body=(await client.get('/api/v1/agent/runs/'+rid)).json();assert body['status']=='completed'
   assert planner.await_count==2
   if deny:assert body['error_code']=='nutrition_query_unsupported' and '本次未查询' in body['reply']
   else:
    assert body['error_code'] is None and '195 千卡' in body['reply']
    if cid=='LQ05-r1':assert '2026-10-09：未找到早餐记录' in body['reply'] and '2026-10-10：晚于查询截止日期，未纳入历史查询' in body['reply']
   messages=(await client.get('/api/v1/agent/conversations/'+conv.id+'/messages')).json()
   assert next(m['content'] for m in messages if m.get('run_id')==rid and m['role']=='assistant')==body['reply']
   async with session_factory() as db:
    audits=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==rid))).all()
    assert len(audits)==(0 if deny else 1)
    assert await db.scalar(select(func.count()).select_from(MealLog))==1
    assert await db.scalar(select(func.count()).select_from(AgentProposal))==0
 finally:app.dependency_overrides.clear()
