import copy,json
from pathlib import Path
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import pytest
from app.config import settings
from app.services.agent_intent import IntentResolution,resolve_intent
from app.services.agent_intent_model import resolve_intent_with_fallback,IntentRouteDecision,_route_to_resolution
from app.services import nutrition_history_reporting as reporting

HERE=Path(__file__).parent
DATA=json.loads((HERE/'archived-routes.json' if (HERE/'archived-routes.json').exists() else HERE/'fixtures/round35/archived-routes.json').read_text(encoding='utf-8'))

@pytest.mark.asyncio
@pytest.mark.parametrize('row',DATA,ids=lambda r:r['instance'])
async def test_archived_first_turn_semantic_boundary(row):
    # Replay the original route through semantic validation for reads/generation;
    # replay the persisted resolution for writes (extraction is not re-evaluated).
    route=IntentRouteDecision.model_validate(row['route'])
    if route.request_kind=='mutation':
        r=IntentResolution.model_validate({**{k:v for k,v in row['resolution'].items() if k in IntentResolution.model_fields},'confidence':row['resolution']['intent_confidence']})
        seam='app.services.agent_intent_model._invoke_model_intent'
    else:
        r=route;seam='app.services.agent_intent_model._invoke_model_route'
    with patch.object(settings,'DEEPSEEK_API_KEY','offline'),patch(seam,new=AsyncMock(return_value=r)):
        outcome=await resolve_intent_with_fallback(row['message'],use_model=True)
    assert not outcome.understanding_failed,(row['instance'],outcome.error_category)
    assert outcome.resolution.request_kind==route.request_kind
    assert outcome.resolution.requested_effect==route.requested_effect
    assert outcome.attempt_count==1

@pytest.mark.parametrize('message',[
 '查我记录过的晚餐热量。','查看已记录过的饮食日期。',
 '即使记录跨月，也只查最近2个有记录日期。','查询早餐记录。','查询加餐记录。',
 '列出午餐记录的热量。','查饮食记录；记录集中在上个月，只统计有记录日期。',
])
def test_nominal_and_past_aspect_reads(message):
    assert resolve_intent(message).request_kind=='query'

@pytest.mark.asyncio
@pytest.mark.parametrize('message',[
 '记录过去一周的饮食。','记录过去的晚餐：豆腐200克。',
 '把晚餐记录下来。','把早餐记录为燕麦50克。',
 '查我记录过的晚餐，再记录今天午餐米饭200克。',
 '记录跨月饮食：9月30日晚餐米饭200克。',
 '记录集中训练后的晚餐：米饭200克。','查早餐记录，删除今天午餐。',
 '记录日常午餐米饭200克。','记录日期为2026年10月9日的午餐。',
 '查最近记录，然后开始今天的训练。',
])
async def test_actual_record_commands_still_block_read_route(message):
    assert resolve_intent(message).request_kind=='mutation'
    read=IntentResolution(primary_intent='nutrition_history_query',intent_domain='nutrition',request_kind='query',requested_effect='read',confidence=.95,resolved_query=message)
    with patch.object(settings,'DEEPSEEK_API_KEY','offline'),patch('app.services.agent_intent_model._invoke_model_intent',new=AsyncMock(return_value=read)):
        out=await resolve_intent_with_fallback(message,use_model=True)
    assert out.understanding_failed

BAD=[r for r in DATA if r['instance'] in ['DQ05-r1','RQ01-r1','RQ01-r2']]
@pytest.mark.asyncio
@pytest.mark.parametrize('row',BAD,ids=lambda r:r['instance'])
async def test_archived_null_strings_get_bounded_repair(row):
    fixed={**row['planner'],'start_date':'','end_date':''}
    model=AsyncMock(side_effect=[SimpleNamespace(payload=row['planner']),SimpleNamespace(payload=fixed)])
    with patch.object(reporting,'structured_chat_completion',new=model):
        plan=await reporting.plan_nutrition_history(message=row['message'],resolved_query=row['resolution']['resolved_query'],today=date(2026,10,9))
    assert plan.model_dump()==dict(fixed,comparison_targets=[],comparison_scope='meal',comparison_scope_quote='',response_mode='structured',date_comparisons=[]) and model.await_count==2
    request=json.loads(model.await_args_list[1].args[0][1]['content'])
    assert request['original_message']==row['message']
    assert request['resolved_query']==row['resolution']['resolved_query']
    assert request['repair_context']['errors']
    assert 'start_date' in json.dumps(request['repair_context']['errors'])

@pytest.mark.asyncio
async def test_exhausted_plan_has_specific_category():
    model=AsyncMock(return_value=SimpleNamespace(payload=BAD[0]['planner']))
    with patch.object(reporting,'structured_chat_completion',new=model):
        with pytest.raises(Exception) as caught:
            await reporting.plan_nutrition_history(message='查最近有记录日期',resolved_query='查最近有记录日期',today=date(2026,10,9))
    assert model.await_count==2
    assert getattr(caught.value,'category',None)=='nutrition_query_plan_invalid'

VALID={'supported':True,'days':3,'start_date':'','end_date':'','logged_dates':[],'meal_types':[],'metrics':['calories'],'operations':['list','mean']}

@pytest.mark.asyncio
@pytest.mark.parametrize('invalid',[
 {'start_date':'2026-10-01','end_date':''},
 {'start_date':'2026-10-09','end_date':'2026-10-01'},
 {'start_date':'2025-01-01','end_date':'2026-10-01'},
 {'logged_dates':['2026-02-30']},
 {'start_date':'2026-10-01','end_date':'2026-10-02','logged_dates':['2026-10-01']},
 {'start_date':None,'end_date':None}, {'start_date':'null','end_date':'null'},
])
async def test_invalid_dates_never_silently_normalized(invalid):
    model=AsyncMock(return_value=SimpleNamespace(payload={**VALID,**invalid}))
    with patch.object(reporting,'structured_chat_completion',new=model):
        with pytest.raises(reporting.NutritionHistoryPlanError) as error:
            await reporting.plan_nutrition_history(message='查指定日期',resolved_query='查指定日期',today=date(2026,10,9))
    assert error.value.category=='nutrition_query_plan_invalid' and model.await_count==2

@pytest.mark.asyncio
@pytest.mark.parametrize('invalid',[
 {'start_date':'2026-10-01','end_date':'null'},
 {'logged_dates':['2026-02-30']},
 {'meal_types':['晚餐'],'start_date':'null','end_date':'null'},
])
async def test_repair_cannot_erase_scope_to_pass_validation(invalid):
    model=AsyncMock(side_effect=[SimpleNamespace(payload={**VALID,**invalid}),SimpleNamespace(payload=VALID)])
    with patch.object(reporting,'structured_chat_completion',new=model):
        with pytest.raises(reporting.NutritionHistoryPlanError):
            await reporting.plan_nutrition_history(message='查指定日期晚餐',resolved_query='查指定日期晚餐',today=date(2026,10,9))
    assert model.await_count==2

@pytest.mark.asyncio
async def test_date_repair_preserves_original_request_and_then_invokes_tool_once(monkeypatch):
    from app.services import agent_runtime
    original='只查2026年10月1日至2026年10月2日晚餐热量。'
    fixed={**VALID,'start_date':'2026-10-01','end_date':'2026-10-02','meal_types':['晚餐']}
    model=AsyncMock(side_effect=[SimpleNamespace(payload={**fixed,'end_date':'null'}),SimpleNamespace(payload=fixed)])
    tool=AsyncMock(return_value={'scope':{'selection':'calendar_range','as_of':'2026-10-09',**fixed},'days':[]})
    monkeypatch.setattr(reporting,'structured_chat_completion',model)
    monkeypatch.setattr(agent_runtime,'build_read_tools',lambda *a,**kw:[SimpleNamespace(name='nutrition_list_history',ainvoke=tool)])
    resolution=IntentResolution(primary_intent='nutrition_history_query',intent_domain='nutrition',request_kind='query',requested_effect='read',evidence_requirements=['nutrition_history'],resolved_query=original,confidence=.99)
    result=await agent_runtime.invoke_langchain_agent(None,user_id='synthetic',history=[],user_message=original,tool_allowlist=['nutrition.list_history'],resolution=resolution)
    assert model.await_count==2 and tool.await_count==1
    assert tool.call_args.args[0]['end_date']=='2026-10-02'
    assert '2026-10-01 至 2026-10-02' in result['messages'][-1].content
    assert json.loads(model.await_args_list[1].args[0][1]['content'])['original_message']==original

@pytest.mark.asyncio
async def test_transport_failure_is_not_retried_or_reclassified():
    from app.services.ai_client import StructuredAIServiceError
    failure=StructuredAIServiceError('synthetic network failure',category='request_timeout')
    model=AsyncMock(side_effect=failure)
    with patch.object(reporting,'structured_chat_completion',new=model):
        with pytest.raises(StructuredAIServiceError) as caught:
            await reporting.plan_nutrition_history(message='查记录',resolved_query='查记录',today=date(2026,10,9))
    assert caught.value is failure and model.await_count==1

@pytest.mark.asyncio
async def test_http_schema_and_repair_request_with_real_structured_client(monkeypatch):
    import httpx
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','synthetic')
    monkeypatch.setattr(settings,'DEEPSEEK_BASE_URL','https://api.deepseek.com')
    requests=[]
    async def post(client,url,**kwargs):
        request=kwargs['json'];requests.append(request)
        schema=request['tools'][0]['function']['parameters']
        assert set(schema['properties'])==set(schema['required']) and schema['additionalProperties'] is False
        assert '空字符串' in schema['properties']['start_date']['description']
        assert 'default' not in schema['properties']['start_date']
        payload={**VALID,'start_date':'null','end_date':'null'} if len(requests)==1 else VALID
        return httpx.Response(200,json={'choices':[{'finish_reason':'tool_calls','message':{'tool_calls':[{'type':'function','function':{'name':'submit_nutrition_history_query','arguments':json.dumps(payload)}}]}}]},request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    result=await reporting.plan_nutrition_history(message='查最近3个记录日均值',resolved_query='查最近3个记录日均值',today=date(2026,10,9))
    assert result.model_dump()==dict(VALID,comparison_targets=[],comparison_scope='meal',comparison_scope_quote='',response_mode='structured',date_comparisons=[]) and len(requests)==2
    repair=json.loads(requests[1]['messages'][1]['content'])['repair_context']
    assert 'start_date' in repair['errors'][0] and 'end_date' in repair['errors'][0]

@pytest.mark.parametrize('message',[
    '帮我记录集中在上周的饮食。',
    '记录集中在上个月的早餐：米饭200克。',
    '查询早餐记录，再记录集中在上周的饮食。',
])
def test_attributive_phrase_can_be_object_of_real_record_command(message):
    assert resolve_intent(message).request_kind=='mutation'
