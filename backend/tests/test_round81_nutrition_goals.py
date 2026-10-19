import copy,json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from pathlib import Path
from langchain_core.messages import AIMessage
from app.services.agent_nutrition_goals import nutrition_goal_contract,nutrition_goal_context,nutrition_goal_findings,recover_nutrition_goals
from app.services.agent_nutrition_scope import nutrition_read_evidence,recent_assessment_request
from app.services.agent_read_delivery import ReadDeliveryBudget
from app.services.business_clock import BUSINESS_DATE
from app.schemas.agent_trace import AgentNutritionGoalDeliveryTrace
from round81_support import ROWS,resolution,messages,corrected

ARCHIVE=json.loads((Path(__file__).parent/'fixtures/round81/archive-regressions.json').read_text(encoding='utf8'))

@pytest.fixture(autouse=True)
def clock():
    token=BUSINESS_DATE.set(date(2026,10,9));yield;BUSINESS_DATE.reset(token)

@pytest.mark.parametrize('id',['U01-r1','U01-r2','U03-r1','U03-r2'])
def test_original_unfulfilled_responses_now_have_auditable_findings(id):
    row=ROWS[id];text=row['model_answers'][-1] if id=='U01-r2' else row['body']['reply']
    contract=nutrition_goal_contract(row['message'],resolution(id))
    found=nutrition_goal_findings(text,contract,messages(id));codes={f['code'] for f in found}
    assert ('diet_action_missing' if id.startswith('U01') else 'outing_action_missing') in codes
    if id=='U01-r1':assert {'record_meal_count_mismatch','intake_completeness_not_established'}<=codes
    if id=='U03-r1':assert 'unsupported_relative_restriction' in codes
    if id=='U03-r2':assert 'outing_scope_contracted' in codes
    for finding in found:
        if finding['kind']=='omission':assert finding['quote'] is finding['start'] is finding['end'] is None
        else:assert text[finding['start']:finding['end']]==finding['quote']
    assert not nutrition_goal_findings(corrected(id),contract,messages(id))

@pytest.mark.parametrize('query',[ROWS['U01-r1']['message'],'评估我的近期饮食，给两条饮食调整建议。','我最近吃得如何，三大营养素是否达标。'])
def test_vague_recent_request_uses_one_actual_scope_but_preserves_profile(query):
    required,optional,quotes=nutrition_read_evidence(query,domain='nutrition',kind='assessment',effect='read',risk='low',evidence=['nutrition_history','nutrition_recent_context','profile_summary'])
    assert required==['nutrition_recent_context','profile_summary','nutrition_history']
    assert optional==['nutrition_history'] and quotes==[query]

@pytest.mark.parametrize('query',[
    '我这几天吃得怎么样，再比较9月27日与10月2日。','我这几天吃得怎么样，只看午餐。',
    '我这几天吃得怎么样，给我上个月历史。','我这几天吃得怎么样，明天的也要。',
    '我这几天吃得怎么样，看看体重变化。','朋友说“我这几天吃得怎么样”。',
    '不要评估我的最近饮食。','最近三天的饮食怎么样？','评估我的近期饮食和训练。',
])
def test_other_scope_or_goal_cannot_be_dropped(query):
    assert recent_assessment_request(query) is None
    assert nutrition_read_evidence(query,domain='nutrition',kind='assessment',effect='read',risk='low',evidence=['nutrition_history','nutrition_recent_context','profile_summary'])==(['nutrition_history','nutrition_recent_context','profile_summary'],[],[])

@pytest.mark.parametrize('changes',[{'intent_domain':'workout_history'},{'request_kind':'mutation','requested_effect':'create'},{'risk_level':'high'},{'clarification_required':True,'clarification_question':'请补充'}])
def test_other_runtime_authority_never_gets_food_goals(changes):
    assert nutrition_goal_contract(ROWS['U03-r1']['message'],resolution('U03-r1',**changes)) is None

@pytest.mark.parametrize('query',[
    '看看饮食记录，不要给饮食建议。','朋友说“出去玩一天，给些饮食建议”，请解释这句话。',
    '如果我出去玩一天，解释饮食这个词。',
])
def test_negation_quote_and_hypothesis_do_not_invent_action_goals(query):
    contract=nutrition_goal_contract(query,resolution('U03-r1'))
    assert contract is None or contract['goals']==[]

def test_no_tool_advice_gets_original_day_and_week_quotes():
    query=ROWS['U03-r1']['message'];contract=nutrition_goal_contract(query,resolution('U03-r1'))
    assert {g['id'] for g in contract['goals']}=={'food_action','outing_action'}
    assert all(g['source_quote'] in query for g in contract['goals'])
    assert 'nutrition_response_goals' in nutrition_goal_context(query,resolution('U03-r1'))
    assert '一天' in contract['goals'][1]['source_quote']

@pytest.mark.parametrize('id',['U02-r1','U02-r2','U04-r1','U04-r2'])
def test_previously_passed_training_cases_keep_original_domain_and_reply(id):
    row=ROWS[id];r=resolution(id,intent_domain=row['body']['intent_domain'])
    assert nutrition_goal_contract(row['message'],r) is None
    assert nutrition_goal_context(row['message'],r)==''

def test_no_tool_nutrition_still_cannot_claim_personal_adequacy():
    from app.services.agent_assessment_delivery import assessment_findings
    assert assessment_findings('你的蛋白质已经足够。',{'messages':[]},nutrition_requested=True)
    assert not assessment_findings('无法判断你的蛋白质是否足够。',{'messages':[]},nutrition_requested=True)

@pytest.mark.parametrize('query',[
    '本周吃得健康点，不要把主食减到平时七八成，给建议。',
    '本周想吃得健康点，朋友说“主食减到平时七八成”，给我建议。',
])
def test_negative_or_quoted_ratio_is_not_personal_restriction_support(query):
    c=nutrition_goal_contract(query,resolution('U03-r1'))
    assert 'unsupported_relative_restriction' in {f['code'] for f in nutrition_goal_findings('主食量按你平时的七八成就好。',c,[])}

@pytest.mark.parametrize('row',ARCHIVE,ids=[r['id'] for r in ARCHIVE])
def test_archived_passes_do_not_gain_invented_food_goals_or_lose_example_actions(row):
    from app.services.agent_intent import IntentResolution
    b=row['body'];args={k:b[k] for k in IntentResolution.model_fields if k in b};args['confidence']=b.get('intent_confidence') or 0
    contract=nutrition_goal_contract(row['message'],IntentResolution.model_validate(args))
    assert not nutrition_goal_findings(row['reply'],contract,[])

@pytest.mark.parametrize('text',[
    '下次正餐可搭配一份符合饮食限制且平时能耐受的蔬菜，例如青菜。',
    '每餐加一份蔬菜，比如西兰花、青菜、番茄。',
])
def test_example_after_actual_advice_does_not_cancel_it(text):
    c=nutrition_goal_contract(ROWS['U01-r1']['message'],resolution())
    assert not nutrition_goal_findings(text,c,[])

@pytest.mark.parametrize('text',[
    '把目标发给我，补记三餐。','本周多注意营养，外出当天正常吃、开心就好。',
    '外出当天不要提前饿肚子，之后不补偿。','“外出当天带一瓶水”，这是错误例句。',
])
def test_logging_slogans_and_quoted_actions_do_not_satisfy_food_goals(text):
    c=nutrition_goal_contract(ROWS['U03-r1']['message'],resolution('U03-r1'))
    assert 'outing_action_missing' in {f['code'] for f in nutrition_goal_findings(text,c,[])}

@pytest.mark.parametrize('text,limit',[
    ('本周与外出当天都可搭配一份平时能耐受的蔬菜，这是唯一一项一般建议。',1),
    ('本周保留主食搭配蔬菜。\n\n外出当天可先选定吃午餐的地方。',None),
])
def test_real_food_actions_cover_periods_and_preserve_requested_limit(text,limit):
    q=ROWS['U03-r1']['message']+('只给一条建议。' if limit else '');c=nutrition_goal_contract(q,resolution('U03-r1'))
    assert c['advice_limit']['max_actions']==limit
    assert not nutrition_goal_findings(text,c,[])

@pytest.mark.parametrize('mode',['failed','not_executed','bad_totals','conflicting'])
def test_bad_or_failed_sources_do_not_support_count_findings(mode):
    ms=messages('U01-r1');recent=next(m for m in ms if m.name=='nutrition_get_recent_context')
    if mode=='failed':recent.status='error'
    elif mode=='not_executed':recent.additional_kwargs['executed']=False
    elif mode=='bad_totals':
        data=json.loads(recent.content);data['recent_days'][1]['total_protein_g']=999;recent.content=json.dumps(data)
    else:
        other=copy.deepcopy(recent);other.tool_call_id='other';data=json.loads(other.content);data['recent_days']=[]
        data['scope'].update(returned_dates=0,first_logged_date=None,last_logged_date=None);data['today']['meals']=[]
        for key in ('total_calories','total_protein_g','total_carbs_g','total_fat_g'):data['today'][key]=0
        other.content=json.dumps(data);ms.append(other)
    c=nutrition_goal_contract(ROWS['U01-r1']['message'],resolution())
    assert not any(f['code']=='record_meal_count_mismatch' for f in nutrition_goal_findings('9天里有5天只记了一餐。',c,ms))

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['corrected','still_missing','budget','timeout','protocol'])
async def test_repair_is_one_bounded_generation_with_distinct_omission_receipt(mode):
    row=ROWS['U03-r1'];query=row['message'];original=row['body']['reply'];budget=ReadDeliveryBudget(1 if mode=='budget' else 2);budget.consume()
    model=SimpleNamespace(ainvoke=AsyncMock(side_effect=TimeoutError('synthetic') if mode=='timeout' else None,
        return_value=AIMessage(content=original if mode=='still_missing' else '<tool_call>fake</tool_call>' if mode=='protocol' else corrected('U03-r1'))))
    result={'messages':[AIMessage(content=original)]}
    async def invoke():return await recover_nutrition_goals(result,model=model,budget=budget,query=query,source_query=query,history=[],system_prompt='synthetic',resolution=resolution('U03-r1'))
    if mode=='timeout':
        with pytest.raises(TimeoutError):await invoke()
        assert result['nutrition_goal_delivery']['status']=='interrupted';return
    output=await invoke();receipt=AgentNutritionGoalDeliveryTrace.model_validate(output['nutrition_goal_delivery'])
    assert receipt.draft==original and receipt.findings and budget.calls<=budget.limit
    assert model.ainvoke.await_count==(0 if mode=='budget' else 1)
    assert receipt.status==('corrected' if mode=='corrected' else 'blocked')
    if mode=='corrected':assert output['messages'][-1].content==corrected('U03-r1')
    else:assert output['read_error_code']
    if mode!='budget':
        request=model.ainvoke.await_args.args[0];assert all(not getattr(m,'tool_calls',None) for m in request)
        data=json.loads(request[-1].content);assert data['nutrition_revision']['contract']['source_query']==query
        assert 'nutrition_revision' in data and data['observations']==[]
