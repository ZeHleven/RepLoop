"""R36 archived routes plus source-bound nutrition filter revision contracts."""
import copy,json
from pathlib import Path
from unittest.mock import AsyncMock,patch
import pytest
from app.config import settings
from app.schemas.agent_task import TaskSnapshot,ConversationTask,TaskUpdate
from app.services.agent_intent import IntentResolution,resolve_intent,route_tools
from app.services.agent_intent_model import IntentRouteDecision,resolve_intent_with_fallback
from app.services.agent_task_state import is_calendar_query_revision
from app.services.agent_query_reports import narrow_report_evidence

HERE=Path(__file__).parent
DATA=json.loads((HERE/'archived-routes.json' if (HERE/'archived-routes.json').exists() else HERE/'fixtures/round37/archived-routes.json').read_text(encoding='utf-8'))

def resolution(message,**kwargs):
    return IntentResolution(primary_intent='nutrition_history_query',intent_domain='nutrition',request_kind='query',requested_effect='read',evidence_requirements=['nutrition_history'],resolved_query=message,confidence=.95,**kwargs)

def context(task_id='query-task'):
    state=TaskSnapshot(active_task_id=task_id,tasks=[ConversationTask(id=task_id,last_run_id='verified-run',request='查询最近2个记录日期的早餐热量',phase='responded')])
    return [{'role':'task_state','content':state.model_dump_json()},{'role':'assistant','content':'查询结果','query_context':{'source_run_id':'verified-run','successful_query':True,'primary_intent':'nutrition_history_query','request_kind':'query','requested_effect':'read','risk_level':'low'}}]

@pytest.mark.asyncio
@pytest.mark.parametrize('row',[r for r in DATA if r['turn']==0],ids=lambda r:r['instance'])
async def test_archived_first_turn_routes(row):
    route=IntentRouteDecision.model_validate(row['route'])
    if route.request_kind=='mutation':
        v=row['resolution'];result=IntentResolution.model_validate({**{k:v[k] for k in IntentResolution.model_fields if k in v},'confidence':v['intent_confidence']});seam='app.services.agent_intent_model._invoke_model_intent'
    else:result=route;seam='app.services.agent_intent_model._invoke_model_route'
    with patch.object(settings,'DEEPSEEK_API_KEY','offline'),patch(seam,new=AsyncMock(return_value=result)):
        out=await resolve_intent_with_fallback(row['message'],use_model=True)
    assert not out.understanding_failed,(row['instance'],out.error_category)
    assert out.resolution.request_kind==route.request_kind

POSITIVE=[
 '仍然只看晚餐，日期改为9月30日和10月8日，列每日热量并合计。',
 '改成最近3个有记录日期，仍只要早餐。',
 '把日期改成10月6日到8日，还是只看晚餐。',
 '改为本周到今天，列每日热量，再算合计。',
 '查询日期改成2026-10-01至2026-10-03，只查午餐和晚餐。',
 '把查询餐次改成午餐，日期范围不变。',
 '取消早餐筛选，仍查最近3个有饮食记录的日期，列每日总热量。',
]
@pytest.mark.parametrize('message',POSITIVE)
def test_verified_nutrition_filter_revision(message):
    r=resolution(message,task_update=TaskUpdate(action='continue',task_id='query-task',trigger=message))
    assert is_calendar_query_revision(context(),r,message)

@pytest.mark.asyncio
@pytest.mark.parametrize('message',POSITIVE)
async def test_verified_revision_passes_semantic_boundary(message):
    r=resolution(message,task_update=TaskUpdate(action='continue',task_id='query-task',trigger=message))
    with patch.object(settings,'DEEPSEEK_API_KEY','offline'),patch('app.services.agent_intent_model._invoke_model_intent',new=AsyncMock(return_value=r)):
        out=await resolve_intent_with_fallback(message,context_messages=context(),use_model=True)
    assert not out.understanding_failed and out.resolution.requested_effect=='read'

@pytest.mark.parametrize('invalid',['missing','failed','wrong_run','wrong_task','cancelled','draft','high_risk','wrong_domain','wrong_primary','wrong_effect','empty_trigger','foreign_trigger'])
def test_revision_requires_matching_successful_read(invalid):
    message=POSITIVE[2];ctx=context();r=resolution(message,task_update=TaskUpdate(action='continue',task_id='query-task',trigger=message))
    if invalid=='missing':ctx=[]
    elif invalid=='failed':ctx[-1]['query_context']['successful_query']=False
    elif invalid=='wrong_run':ctx[-1]['query_context']['source_run_id']='other'
    elif invalid=='wrong_task':r.task_update.task_id='other'
    elif invalid in ('cancelled','draft'):
        state=TaskSnapshot.model_validate_json(ctx[0]['content'])
        if invalid=='cancelled':state.tasks[0].phase='cancelled'
        else:state.tasks[0].proposal_pending=True;state.tasks[0].proposal_id='proposal'
        ctx[0]['content']=state.model_dump_json()
    elif invalid=='high_risk':r.risk_level='high'
    elif invalid=='wrong_domain':r.intent_domain='profile'
    elif invalid=='wrong_primary':ctx[-1]['query_context']['primary_intent']='workout_history_query'
    elif invalid=='wrong_effect':ctx[-1]['query_context']['requested_effect']='update'
    elif invalid=='empty_trigger':r.task_update.trigger=''
    else:r.task_update.trigger='not in current message'
    assert not is_calendar_query_revision(ctx,r,message)

NEGATIVE=[
 '把这条饮食记录的日期改成10月8日。',
 '把已保存晚餐的日期改为10月8日。',
 '把日期改成10月6日到8日，并保存。',
 '日期改为10月8日，再记录午餐米饭200克。',
 '日期改成10月8日，删除早餐记录。',
 '把餐次改成午餐。',
 '把每个记录日期都改为10月8日。',
 '记录日期为2026年10月9日的午餐。',
 '查每个记录日期的热量，然后记录晚餐米饭200克。',
 '如果把日期改成10月8日会怎样？',
 '不要把日期改成10月8日。',
 '日期改为10月8日，开始今天的训练。',
]
@pytest.mark.parametrize('message',NEGATIVE)
def test_business_objects_and_extra_clauses_are_not_filter_edits(message):
    r=resolution(message,task_update=TaskUpdate(action='continue',task_id='query-task',trigger=message))
    assert not is_calendar_query_revision(context(),r,message)

@pytest.mark.asyncio
@pytest.mark.parametrize('message',NEGATIVE[:9]+NEGATIVE[11:])
async def test_actual_writes_cannot_borrow_query_context(message):
    r=resolution(message,task_update=TaskUpdate(action='continue',task_id='query-task',trigger=message))
    with patch.object(settings,'DEEPSEEK_API_KEY','offline'),patch('app.services.agent_intent_model._invoke_model_intent',new=AsyncMock(return_value=r)):
        out=await resolve_intent_with_fallback(message,context_messages=context(),use_model=True)
    assert out.understanding_failed and out.error_category=='semantic_mutation_structure_missing'

@pytest.mark.parametrize('message',[
 '查10月6日至8日的饮食，每个记录日期的热量都列出来。',
 '列出每一个记录日期的热量。',
 '查询这些记录日期的饮食热量。',
])
def test_record_date_noun_not_record_command(message):
    assert resolve_intent(message).request_kind=='query'

@pytest.mark.parametrize('message,expected',[
 ('最近4个自然日每天的饮食热量是多少？今天算在内。',True),
 ('查询过去7天每天的热量，包含今天。',True),
 ('列出本周到今天的每日饮食总热量。',True),
 ('最近4个自然日每天的饮食热量是多少？再列今天每餐吃了什么。',False),
 ('查询过去7天的热量合计，另外单独查询今天的饮食详情。',False),
 ('查最近4天饮食，再记录今天午餐。',False),
 ('查今天午餐每项食物，再看过去4天热量。',False),
 ('查本周和上周各自的每日热量。',False),
])
def test_today_inside_history_is_not_an_extra_goal(message,expected):
    r=resolution(message);r.evidence_requirements=['nutrition_history','nutrition_today'];r.primary_intent='nutrition_today_query'
    out=narrow_report_evidence(r,message)
    assert (out.evidence_requirements==['nutrition_history']) is expected
    if expected:assert out.primary_intent=='nutrition_history_query' and route_tools(out)==['nutrition.list_history']
