"""Archived failures and counterexamples; no network or medical certification."""
import copy,hashlib,json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest,pytest_asyncio
from langchain_core.messages import AIMessage,ToolMessage
from app.services.agent_assessment_delivery import assessment_findings,recover_assessment_answer
from app.services.agent_read_delivery import ReadDeliveryBudget
from app.services.agent_symptom_context import symptom_response_context
from app.services.agent_movement_reference import movement_reference
from app.schemas.agent_trace import AgentAssessmentDeliveryTrace

ROWS=json.loads((Path(__file__).parent/'fixtures/round75/archived.json').read_text(encoding='utf8'))
Q=ROWS['WX35-r2']['turns'][1]['message']

@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    yield

def resolution(**changes):
    return SimpleNamespace(**({'request_kind':'assessment','requested_effect':'read','change_requests':[],'risk_level':'medium'}|changes))

def evidence():
    return [ToolMessage(content=json.dumps({'found':True,'plan':{'is_active':True,'exercises':[{'exercise_name':'卧推'},{'exercise_name':'深蹲'}]}}),name='plan_get_active',tool_call_id='actual-plan'),ToolMessage(content='{"found":true,"injuries":[]}',name='health_get_screening_summary',tool_call_id='actual-health')]

def findings(text,query=Q):
    return assessment_findings(text,{'messages':evidence()+[AIMessage(content=text)]},query)

def context(query=Q,messages=None,r=None):
    return symptom_response_context(query,r or resolution(),evidence() if messages is None else messages)

def archived_result(id,turn):
    r=ROWS[id];return {'messages':[ToolMessage(content=json.dumps(x['result'],ensure_ascii=False),name=x['tool'].replace('.','_'),tool_call_id=x['attempt_id'],status=x['status']) for x in r['tool_attempts'] if x.get('turn',0)==turn]}

def test_actual_false_pause_removed_with_true_current_contradiction_kept():
    turn=ROWS['WX35-r2']['turns'][1];draft=turn['body']['execution_trace']['assessment_delivery']['draft']
    assert not assessment_findings(draft,archived_result('WX35-r2',1),Q)
    assert len(findings('深蹲先停。当前临时安排深蹲2组。'))==2

@pytest.mark.parametrize('pause',[
    '卧推暂停，深蹲保留。','暂停卧推，深蹲保留。','暂停卧推而深蹲保留。',
    '深蹲保留而卧推暂停。','卧推暂停；深蹲保留。','卧推暂停,深蹲保留。',
    '卧推先停。深蹲不用暂停。','“暂停深蹲”是错误示例，卧推暂停。',
])
def test_pause_cannot_bind_another_retained_subject(pause):
    assert not findings(pause+'\n当前临时安排深蹲2组。')

@pytest.mark.parametrize('pause',[
    '暂停卧推和深蹲。','卧推和深蹲先停。','暂停卧推、深蹲。',
    '深蹲是可能诱发症状的动作，现在最该做的是先停掉它。','先暂停当前方案里的深蹲。',
    '卧推保留，深蹲暂停。','卧推暂停，深蹲也先停。',
])
def test_actual_shared_list_and_exact_subject_pauses_remain(pause):
    result=findings(pause+'\n当前临时安排深蹲2组。')
    assert len(result)==2,result

@pytest.mark.parametrize('text',[
    '少练这一周不会有什么大损失。','这周休息不会有明显损失。','少练不会有任何实质性损失。',
])
def test_effect_guarantees(text):
    assert any(x['code']=='training_effect_guarantee' for x in findings(text))

@pytest.mark.parametrize('text',[
    '不能保证少练这一周不会有什么大损失。','“不会有什么大损失”这个说法没有依据。',
    '目标是不会有明显损失，不是保证。','是否不会有明显损失，无法根据这些资料判断。',
    '少练这段时间会不会有损失，我不能保证没有。',
    '少练这段时间会不会有损失，我没法给你保证。',
])
def test_nonassertive_effect_language(text):
    assert not findings(text)

@pytest.mark.parametrize('text',[
    '“不会有什么大损失”这个说法没有依据，但你这周少练不会有明显损失。',
    '“不会有什么大损失”是对你的保证。',
    '以前减量会不会有损失还不知道，但这周少练不会有损失。',
])
def test_quote_denial_does_not_exempt_later_or_affirmed_guarantee(text):
    assert any(x['code']=='training_effect_guarantee' for x in findings(text))

def test_actual_effect_now_located_without_changing_raw_reply():
    turn=ROWS['WX24-r1']['turns'][0];text=turn['body']['reply']
    result=assessment_findings(text,archived_result('WX24-r1',0),turn['message'])
    assert any(x['code']=='training_effect_guarantee' and x['quote']=='不会有什么大损失' for x in result)
    assert all(text[x['start']:x['end']]==x['quote'] for x in result)

def test_actual_followup_retains_full_qualifiers_and_source_hash():
    assert not any(w in Q for w in ['训练','健身','练','运动'])
    data=json.loads(context().split('\n')[-1])
    assert data['current_symptom_report']['quote']==Q
    assert data['current_symptom_report']['sha256']==hashlib.sha256(Q.encode()).hexdigest()
    assert all(q in data['current_symptom_report']['quote'] for q in ['没有肿胀和麻木','拿杯子不疼','撑地和握重物才疼'])
    assert data['activation_plan_source_call_ids']==['actual-plan']
    assert data['diagnosis']=='unknown'
    assert data['movement_reference']['source_kind']=='curated_general_movement_reference_not_user_data'

@pytest.mark.parametrize('mode',['none','error','not_executed','not_found','malformed','business_error','wrong_tool'])
def test_continuation_needs_actual_plan_not_label(mode):
    messages=evidence()
    if mode=='none':messages=[]
    elif mode=='error':messages[0].status='error'
    elif mode=='not_executed':messages[0].additional_kwargs['executed']=False
    elif mode=='not_found':messages[0].content='{"found":false}'
    elif mode=='malformed':messages[0].content='not-json'
    elif mode=='business_error':messages[0].content='{"found":true,"plan":{},"error_code":"failed"}'
    else:messages[0].name='profile_get_summary'
    assert not context(messages=messages)

@pytest.mark.parametrize('changes',[
    {'request_kind':'mutation'},{'requested_effect':'update'},{'change_requests':[{}]}, {'risk_level':'high'},
])
def test_advice_context_does_not_expand_execution_permission(changes):
    assert not context(r=resolution(**changes))

@pytest.mark.parametrize('query',[
    '今天手腕不疼了，按计划给我建议。','如果今天手腕疼，按计划给建议。',
    '朋友今天手腕疼，我要看计划。','解释“今天手腕疼”，以及我的计划。',
    '以前手腕疼，今天已经恢复，按计划给建议。','我工作很累，按计划减量。',
])
def test_no_inferred_current_user_symptom(query):
    assert not context(query=query)

def test_reference_is_bounded_attributed_and_not_mutable():
    ref=movement_reference()
    assert len(ref['facts'])==2 and all(x['source_url'].startswith('https://www.acefitness.org/') for x in ref['facts'])
    assert ref['other_variants']=='not_covered_do_not_infer_from_names'
    assert ref['personal_tolerance'].startswith('unknown')
    ref['facts'].clear();assert len(movement_reference()['facts'])==2

@pytest.mark.asyncio
async def test_false_positive_does_not_spend_repair_budget():
    turn=ROWS['WX35-r2']['turns'][1];draft=turn['body']['execution_trace']['assessment_delivery']['draft']
    result=archived_result('WX35-r2',1);result['messages'].append(AIMessage(content=draft))
    model=AsyncMock();budget=ReadDeliveryBudget(1)
    got=await recover_assessment_answer(result,model=model,budget=budget,query=Q,history=[],system_prompt=context())
    assert got['messages'][-1].content==draft and 'assessment_delivery' not in got
    assert budget.calls==0;model.ainvoke.assert_not_awaited()

@pytest.mark.asyncio
async def test_repair_keeps_current_report_and_references_in_prompt():
    bad='这一周不会有什么大损失。';good='不能判断减量的效果；先避开你说的诱发环节，12分钟是上限。'
    result={'messages':evidence()+[AIMessage(content=bad)]};model=AsyncMock();model.ainvoke.return_value=AIMessage(content=good)
    out=await recover_assessment_answer(result,model=model,budget=ReadDeliveryBudget(1),query=Q,history=[],system_prompt=context())
    receipt=out['assessment_delivery'];assert receipt['checks']=='assertion_scope_v7' and receipt['status']=='corrected'
    system=model.ainvoke.call_args.args[0][0].content
    assert Q in system and 'movement_reference' in system and '撑地和握重物才疼' in system
    AgentAssessmentDeliveryTrace.model_validate(receipt)
    old=copy.deepcopy(receipt);old['checks']='assertion_scope_v5';AgentAssessmentDeliveryTrace.model_validate(old)
