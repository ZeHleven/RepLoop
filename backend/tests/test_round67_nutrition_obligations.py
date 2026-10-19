"""Source-scope and instruction-boundary regressions, not a semantic judge."""
import copy,json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from langchain_core.messages import AIMessage,SystemMessage,ToolMessage
from app.services.agent_nutrition_obligations import advice_limit,nutrition_obligations,nutrition_response_context
from app.services.assessment_grounding import project_evidence,AssessmentGroundingMiddleware
from app.services.agent_read_delivery import ReadDeliveryBudget,recover_protocol_answer,recover_budget_answer
from app.services.agent_assessment_delivery import recover_assessment_answer

FIX=json.loads((Path(__file__).parent/'fixtures/round67/nutrition-raw.json').read_text())['rows']
def tm(tool,data,*,status='success',cid='source',executed=True):
    return ToolMessage(name=tool,content=json.dumps(data,ensure_ascii=False),tool_call_id=cid,status=status,additional_kwargs={'executed':executed})
def messages(row):return [tm(t['tool'],t['result'],status='success' if t['status']=='success' else 'error',cid=f"{row['id']}-{i}") for i,t in enumerate(row['tools'])]
def block(content):
    start=content.index('{"nutrition_response_obligations":')
    return json.JSONDecoder().raw_decode(content[start:])[0]['nutrition_response_obligations']

@pytest.mark.parametrize('query,expected',[
('给一条搭配建议。',1),('核对各食物蛋白，给最多一条建议。',1),
('请给我一个下一步建议，不判断全天。',1),('给不超过两条搭配建议。',2),
('请提供至多3项具体建议。',3),('请给一个适合我的下一步，不替我设目标。',1),
('不给饮食建议。',0),('不要食物建议。',0),('不要给我任何建议。',0),
('只给差额，不给饮食建议。',0),('给0条建议。',0),
])
def test_explicit_action_limits(query,expected):
    result=advice_limit(query);assert result['status']=='explicit' and result['max_actions']==expected
    assert all(quote in query for quote in result['source_quotes'])

@pytest.mark.parametrize('query',[
('朋友说给一条建议，帮我解释这句话。'),('如果我说给一条建议你会怎样？'),
('不要只给一条建议。'),('“给一条搭配建议”是例子。'),('之前给一条建议，现在重新评估。'),
('每餐给一条建议。'),('给一条建议，再给两条建议。'),('分别给两个日期各一条建议。'),
('一天三餐、蛋白100克，帮我看看。'),('给一条记录加备注。'),
('不要不给建议。'),('他要给一条建议，不是我的要求。'),
('至少给两条建议。'),('不止给一条建议。'),('每餐分别评估，给一条建议。'),
])
def test_ambiguous_or_nonrequest_numbers_not_global_limits(query):assert advice_limit(query)['max_actions'] is None

@pytest.mark.parametrize('id,n',[('NX07-r1',2),('NX08-r1',1),('NX13-r1',1),('NX15-r1',1),('NA15-r1',1),('NX03D-r1',1)])
def test_real_failed_request_has_action_budget(id,n):
    row=next(x for x in FIX if x['id']==id);assert nutrition_obligations(row['query'],messages(row))['advice_limit']['max_actions']==n

@pytest.mark.parametrize('row',FIX,ids=lambda x:x['id'])
def test_all_archived_nutrition_projections_preserve_raw_source_and_scope(row):
    before=copy.deepcopy(row);msgs=messages(row);ctx=nutrition_obligations(row['query'],msgs)
    nutrition=[t for t in row['tools'] if t['tool'].startswith('nutrition.') and t['status']=='success']
    assert (ctx is not None)==bool(nutrition)
    for t in nutrition:
        raw=t['result'];out=project_evidence(t['tool'],raw);assert out['business_data']==raw
        selected=raw.get('scope',{}).get('meal_types',[])
        totals=[f for f in out['facts'] if f['kind'] in ('recorded_day','recorded_selection')]
        if selected:
            assert all(f['kind']=='recorded_selection' and '已记录日合计' not in f['text'] for f in totals)
            assert all(all(m in f['subject'] for m in selected) for f in totals)
            assert out['boundaries']['other_meals_existence']=='not_established'
        else:assert all(f['kind']=='recorded_day' for f in totals)
        assert out['boundaries']['whole_day_completeness']=='unknown'
    assert row==before

@pytest.mark.parametrize('profile,expected,targets',[
(None,'not_observed','unknown'),(tm('profile.get_summary',{'error':'timeout'},status='error'),'read_failed_or_unusable','unknown'),
(tm('profile.get_summary',{'found':False}),'not_found','unknown'),
(tm('profile.get_summary',{'found':True}),'observed','unknown'),
(tm('profile.get_summary',{'found':True,'nutrition_targets':None}),'observed','absent_in_observed_profile'),
(tm('profile.get_summary',{'found':True,'nutrition_targets':{'protein_g':75}}),'observed','observed_value'),
(tm('profile.get_summary',{'found':True,'nutrition_targets':None},executed=False),'read_failed_or_unusable','unknown'),
])
def test_profile_unknown_absent_and_failed_are_distinct(profile,expected,targets):
    ms=[tm('nutrition.get_today',{'meals':[]})]+([profile] if profile is not None else [])
    result=nutrition_obligations('核对饮食',ms)['profile_evidence'];assert result['status']==expected and result['numeric_targets']==targets

@pytest.mark.parametrize('declaration',[
'昨天所有吃喝记全了。','昨天漏记了零食。','我不确定是否记全。','如果记全了就算差额。','朋友说已经记全。',
])
def test_database_never_promoted_to_user_completeness(declaration):
    row=next(x for x in FIX if x['id']=='NX11-r1');ctx=nutrition_obligations(declaration,messages(row))
    assert all(s['complete_intake']=='not_certified_by_database' for s in ctx['source_scopes'])
    assert ctx['unrecorded_food_exists']=='unknown' and not ctx['meal_count_proves_missing_intake']
    # The declaration stays in the original user message; compiler does not
    # invent a matched date or overrule the user's valid scope confirmation.

@pytest.mark.asyncio
async def test_actual_model_middleware_view_uses_original_query_not_assistant_or_tool_text():
    row=next(x for x in FIX if x['id']=='NX15-r1');ms=[AIMessage(content='给五条建议'),*messages(row)]
    request=SimpleNamespace(messages=ms,system_message=SystemMessage(content='base'))
    request.override=lambda **kwargs:SimpleNamespace(**kwargs)
    handler=AsyncMock(return_value='sent');original=[m.model_dump() for m in ms]
    await AssessmentGroundingMiddleware(source_query=row['query']).awrap_model_call(request,handler)
    passed=handler.call_args.args[0];ctx=block(passed.system_message.content)
    assert ctx['advice_limit']['max_actions']==1 and ctx['profile_evidence']['status']=='not_observed'
    assert original==[m.model_dump() for m in ms]

@pytest.mark.asyncio
@pytest.mark.parametrize('cause',['protocol','tool_budget','assessment'])
async def test_all_repair_paths_keep_scope_and_budget_without_extra_tools(cause):
    row=next(x for x in FIX if x['id']=='NX15-r1');result={'messages':messages(row)}
    result['messages'].append(AIMessage(content='<tool_call>broken</tool_call>' if cause=='protocol' else '蛋白质很充足。'))
    model=SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(content='已记录午餐的蛋白质如上；只给一项一般搭配建议。')))
    budget=ReadDeliveryBudget(1);kwargs=dict(model=model,budget=budget,query='消解后给五条建议',source_query=row['query'],history=[],system_prompt='base')
    if cause=='protocol':out=await recover_protocol_answer(result,**kwargs)
    elif cause=='tool_budget':out=await recover_budget_answer(result,allowed=True,**kwargs)
    else:out=await recover_assessment_answer(result,**kwargs)
    assert budget.calls==1 and model.ainvoke.await_count==1
    ctx=block(model.ainvoke.call_args.args[0][0].content)
    assert ctx['advice_limit']['max_actions']==1 and ctx['profile_evidence']['status']=='not_observed'
    assert all(s['day_totals_basis']=='selected_meals_only' for s in ctx['source_scopes'])
    assert not out.get('read_error_code')

def test_other_domain_and_failed_nutrition_do_not_get_obligations():
    assert nutrition_response_context('训练',[tm('plan.get_active',{'found':True})])==''
    assert nutrition_response_context('饮食',[tm('nutrition.get_today',{'error':'bad'},status='error')])==''

def test_mixed_domain_count_not_forced_onto_entire_answer():
    ctx=nutrition_obligations('不给饮食建议，只给一条训练建议。',[tm('nutrition.get_today',{'meals':[]})])
    assert ctx['advice_limit']['max_actions'] is None and ctx['advice_limit']['status']=='requires_semantic_scope'
