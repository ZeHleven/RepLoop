"""Archived failures, full request ownership and raw observation integrity."""
import copy,json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
import pytest,pytest_asyncio
from langchain_core.messages import ToolMessage
from app.schemas.agent_trace import AgentExecutionTrace,AgentNutritionDeliveryTrace
from app.services.agent_trace import observation_fingerprint
from app.services.agent_evidence_contract import check_evidence
from app.services.agent_nutrition_scope import requested_nutrition_dates,nutrition_read_evidence
from app.services.agent_nutrition_delivery import detail_request,ground_nutrition_delivery
from app.services.business_clock import BUSINESS_DATE

ROWS={key:json.loads(value['run_json']) for key,value in json.loads((Path(__file__).parent/'fixtures/round73/archived.json').read_text(encoding='utf8')).items()}
@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    # Pure observation tests do not use the directory's default SQL fixture.
    # Real DB/HTTP integration is covered in the separate SQL module.
    yield

@pytest.fixture(autouse=True)
def clock():
    token=BUSINESS_DATE.set(date(2026,10,9));yield;BUSINESS_DATE.reset(token)

def archived(id):
    row=copy.deepcopy(ROWS[id]);trace=AgentExecutionTrace.model_validate(row['turns'][0]['body']['execution_trace']);messages=[]
    for action in trace.actions:
        if action.status!='completed':continue
        item=next(t for t in row['tool_attempts'] if t['tool']==action.tool_id and t['status']=='success' and t['arguments']==action.arguments)
        content=json.dumps(item['result'],ensure_ascii=False)
        assert observation_fingerprint(content)==next(o.result_fingerprint for o in trace.observations if o.call_id==action.call_id)
        messages.append(ToolMessage(content=content,name=action.tool_id.replace('.','_'),tool_call_id=action.call_id))
    return row,trace,{'messages':messages},row['turns'][0]['message']

RESOLUTION=SimpleNamespace(request_kind='assessment',requested_effect='read',change_requests=[],clarification_required=False)

@pytest.mark.parametrize('id',['NA01-r1','NA01-r2','DX04-r1','DX04-r2'])
def test_existing_complete_recent_days_are_equivalent_for_assessment(id):
    row,trace,result,query=archived(id)
    assert trace.evidence_contract.status=='partial'
    selected=requested_nutrition_dates(query,date(2026,10,9))
    assert selected and all(q in query for q in selected[1])
    updated=check_evidence(trace,result=result,query=query)
    assert updated.evidence_contract.status=='complete'
    assert len(updated.evidence_contract.substitutions)==1
    assert updated.evidence_contract.substitutions[0].requested_dates==(['2026-10-07','2026-10-08','2026-10-09'] if id.startswith('NA') else ['2026-10-08'])
    assert updated.evidence_contract.substitutions[0].actual_intake_completeness=='unknown'
    required,optional,quotes=nutrition_read_evidence(query,domain='nutrition',kind='assessment',effect='read',risk='low',evidence=['nutrition_history','nutrition_recent_context','profile_summary'])
    assert optional==['nutrition_recent_context'] and 'profile_summary' in required

@pytest.mark.parametrize('query',[
 '评估10月7日至9日我的饮食，三大营养素是否达到目标，给一条建议。',
 '评价2026-10-07至2026-10-08营养，给具体调整建议。',
 '昨天所有食物饮料已经记录完整。请核对已有饮食记录与我的每日目标：蛋白质100克、碳水80克、脂肪20克，只给差额。'.replace('、','，'),
])
def test_assessment_synonyms_keep_exact_scope(query):
    assert requested_nutrition_dates(query,date(2026,10,9))

@pytest.mark.parametrize('extra',[
 '，也查上周','，然后改成今天','，并对比前一天','，删除记录','，早餐不算','，只评估午餐',
 '，我朋友说的','，再看训练','，每日也分别查询','，其他日期也要','，我没说记齐','，保存目标',
])
def test_assessment_unknown_second_goals_do_not_shrink_scope(extra):
    query='昨天全部食物饮料都已记齐。请分别核对已有饮食记录与我的每日目标：蛋白质100克，碳水80克，脂肪20克'+extra
    assert requested_nutrition_dates(query,date(2026,10,9)) is None

@pytest.mark.parametrize('prefix',['不要','假设','朋友说','“','例如'])
def test_assessment_nonassertive_prefix_not_a_date_proof(prefix):
    assert requested_nutrition_dates(prefix+'评估10月7日至9日我的饮食，给一条建议。',date(2026,10,9)) is None

@pytest.mark.parametrize('id',[k for k in ROWS if k.startswith('NX')])
def test_food_details_deliver_every_selected_date_and_one_action(id):
    row,trace,result,query=archived(id)
    text,after=ground_nutrition_delivery('original model text',trace,result=result,query=query,resolution=RESOLUTION)
    receipt=after.nutrition_delivery
    assert receipt is not None and text!='original model text'
    assert receipt.max_actions==1 and len(receipt.actions)==1
    assert receipt.actions[0].kind=='add_one_vegetable'
    assert all(f.meal_type=='午餐' and f.date in receipt.requested_dates for f in receipt.item_facts)
    assert '不表示已判断你缺少' in text and '没有保存或修改记录' in text
    assert all(d in text for d in receipt.requested_dates)
    assert all(f'{f.protein_g:g}g' in text for f in receipt.item_facts)
    if id.startswith(('NX09','NX12')):assert receipt.empty_dates==['2026-10-07'] and '不等于当天没有进食' in text
    if id.startswith(('NX13','NX15')):assert receipt.requested_dates==['2026-10-06','2026-10-08'] and '2026-10-07' not in text
    restored=AgentExecutionTrace.model_validate(after.model_dump(mode='json'))
    assert restored.nutrition_delivery==receipt

@pytest.mark.parametrize('day',['昨天','今日','2026-10-08','10月6日至8日','2026-10-06和2026-10-08'])
@pytest.mark.parametrize('meal',['早餐','午餐','晚餐','加餐'])
def test_request_composes_days_and_meals(day,meal):
    q=f'请核对{day}{meal}各食物的蛋白质，给一条搭配建议。'
    request=detail_request(q,date(2026,10,9));assert request and request['meal']==meal and request['max_actions']==1

@pytest.mark.parametrize('extra',[
 '，给两条建议','，以后每天都这么吃','，只列米饭','，我对青菜过敏','，我是素食者','，还要比较昨日',
 '，判断是否适合糖尿病','，我的目标100克','，我的体重是多少','，保存午餐','，帮我减脂','，蛋白质占热量多少',
 '，不是这些日期','，给每餐一条建议','，再说训练怎么调','，顺便说热量','，做成计划',
])
def test_unknown_constraints_and_goals_keep_semantic_delivery(extra):
    q='核对2026-10-08午餐各食物的蛋白质，给一条建议'+extra
    assert detail_request(q,date(2026,10,9)) is None

@pytest.mark.parametrize('mode',['wrong_hash','wrong_call','failed','unexecuted','scope_date','scope_meal','scope_total','wrong_sum','missing_item','different_day','duplicate','missing_limit','stale','read_error','write','missing_contract','other_goal','missing_date'])
def test_no_delivery_from_unproved_evidence(mode):
    _,trace,result,query=archived('NX08-r1');resolution=copy.copy(RESOLUTION);m=result['messages'][0];data=json.loads(m.content)
    if mode=='wrong_hash':trace.observations[0].result_fingerprint='0'*64
    elif mode=='wrong_call':m.tool_call_id='other'
    elif mode=='failed':trace.observations[0].status='error'
    elif mode=='unexecuted':m.additional_kwargs['executed']=False
    elif mode=='stale':trace.request_context.as_of='2026-10-08'
    elif mode=='read_error':result['read_error_code']='timeout'
    elif mode=='write':resolution.requested_effect='write'
    elif mode=='missing_contract':trace.evidence_contract.status='partial'
    elif mode=='other_goal':query+='还要评估我的训练。'
    else:
        if mode=='scope_date':data['scope']['logged_dates']=['2026-10-07']
        elif mode=='scope_meal':data['scope']['meal_types']=['晚餐']
        elif mode=='scope_total':data['count']=2
        elif mode=='wrong_sum':data['days'][0]['total_protein_g']+=1
        elif mode=='missing_item':data['days'][0]['meals'][0]['items']=[]
        elif mode=='different_day':data['days'][0]['meals'][0]['logged_at']='2026-10-07'
        elif mode=='duplicate':data['days'][0]['meals'][0]['items'].append(data['days'][0]['meals'][0]['items'][0])
        elif mode=='missing_limit':data['evidence_limits']={}
        elif mode=='missing_date':data['scope']['logged_dates']=[]
        m.content=json.dumps(data,ensure_ascii=False);trace.observations[0].result_fingerprint=observation_fingerprint(m.content)
    text,after=ground_nutrition_delivery('unchanged',trace,result=result,query=query,resolution=resolution)
    assert text=='unchanged' and after.nutrition_delivery is None

def test_no_extra_actions_can_enter_receipt():
    _,trace,result,query=archived('NX08-r1');_,after=ground_nutrition_delivery('old',trace,result=result,query=query,resolution=RESOLUTION)
    data=after.nutrition_delivery.model_dump();data['actions']*=2
    with pytest.raises(ValueError):AgentNutritionDeliveryTrace.model_validate(data)

def test_zero_advice_has_no_action_but_keeps_contributions():
    _,trace,result,query=archived('NX08-r1');query=query.replace('给一条搭配建议','不要搭配建议')
    text,after=ground_nutrition_delivery('old',trace,result=result,query=query,resolution=RESOLUTION)
    assert after.nutrition_delivery.max_actions==0 and after.nutrition_delivery.actions==[]
    assert '21g' in text and '一条搭配建议' not in text
