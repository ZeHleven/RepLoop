"""Actual R64 drafts plus semantic counterexamples; no live model calls."""
import copy,json,hashlib
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from langchain_core.messages import AIMessage,ToolMessage
from app.services.agent_assessment_delivery import assessment_findings,recover_assessment_answer
from app.services.agent_read_delivery import ReadDeliveryBudget
from app.services.agent_runtime import _usage_totals,_tool_call_records
from app.schemas.agent_trace import AgentAssessmentDeliveryTrace

ROWS=json.loads((Path(__file__).parent/'fixtures/round65/archived-drafts.json').read_text(encoding='utf8'))['rows']

def evidence(reply,tool='nutrition.list_history'):
    return {'messages':[AIMessage(content='',tool_calls=[{'name':tool.replace('.','_'),'id':'source-1','args':{}}],additional_kwargs={'controller_generated':True}),
        ToolMessage(content=json.dumps({'found':True,'records':[],'plan':{'exercises':[{'exercise_name':'卧推'},{'exercise_name':'深蹲'}]}}),name=tool.replace('.','_'),tool_call_id='source-1'),AIMessage(content=reply,usage_metadata={'input_tokens':12,'output_tokens':5,'total_tokens':17})]}

def archived(r):
    result={'messages':[]}
    for t in r['tools']:
        result['messages'].append(ToolMessage(content=json.dumps(t['result']),name=t['tool'].replace('.','_'),tool_call_id=t['attempt_id']))
    result['messages'].append(AIMessage(content=r['reply']))
    return result

@pytest.mark.parametrize('r',ROWS,ids=lambda r:r['id'])
def test_all_seven_real_drafts_now_have_localized_findings(r):
    findings=assessment_findings(r['reply'],archived(r))
    assert findings
    assert all(r['reply'][f['start']:f['end']]==f['quote'] for f in findings)

@pytest.mark.parametrize('reply',[
    '用户确认全天记全，蛋白质102克，比你给出的100克目标多2克；仅按你的数值目标达标，不证明目标适合你。',
    '这几天蛋白质记录比昨天偏少，差2克；这不代表个人需要量不足。',
    '只比较记录：蛋白102克、碳水84克、脂肪13.8克；无法判断个人是否达标。',
    '不能断言蛋白质很充足。',
    '这些数据不代表蛋白质已经够。',
    '蛋白质是否充足还不能判断。',
    '蛋白质不能判断为足够。',
    '记录中未见蔬菜，不等于你没有吃。',
    '一般参考不应当被视为个人目标；先保留已有数值比较。',
    '记录中蛋白质相对充足的部分主要来自鸡胸，不能推断个人营养需要。',
    '建议搭配一种蛋白来源，让一餐的蛋白质更充足。',
])
def test_numeric_comparisons_and_nonassertions_are_not_blocked(reply):
    assert assessment_findings(reply,evidence(reply))==[]

@pytest.mark.parametrize('reply',[
    '不能保证卧推不涉及膝部负荷。',
    '我不替你保证某个动作一定不涉及脚踝。',
    '我不替你打包票说卧推一定不涉及手腕。',
    '注意：深蹲本身是否完全不影响你的右肩，我无法保证。',
    '恢复不涉及脚踝冲击的动作，观察练后和次日反应。',
    '不要认定上肢动作不会影响脚踝。',
    '“卧推不涉及膝部负荷”这个说法不成立。',
    '休息是可选项，但不能保证效果不变。',
    '没有保存修改，不会影响已保存计划。',
    '旧档案没有伤病并不说明今天的训练安全。',
    '如果不适加重就停止。恢复取决于症状和日常功能，练后及次日再观察。',
])
def test_conditional_and_negated_training_statements_are_not_blocked(reply):
    assert assessment_findings(reply,evidence(reply,'plan.get_active'))==[]

@pytest.mark.parametrize('reply',[
    '卧推本身不涉及膝部负荷，但我不能保证姿势安全。',
    '上肢动作不会影响脚踝。',
    '卧推对膝盖没有压力。',
    '坐姿划船不涉及膝部负荷。',
    '某个新替代动作不会影响脚踝。',
    '这一周少练一次不会影响什么。',
    '减量以后效果完全不变。',
])
def test_disclaimer_cannot_cancel_an_earlier_unfounded_assertion(reply):
    assert assessment_findings(reply,evidence(reply,'plan.get_active'))

@pytest.mark.parametrize('state',['error','unexecuted','invalid_json','not_object','unrelated'])
def test_no_synthetic_evidence_from_failed_or_unrelated_tools(state):
    result=evidence('蛋白质已经够。');m=result['messages'][1]
    if state=='error':m.status='error'
    elif state=='unexecuted':m.additional_kwargs['executed']=False
    elif state=='invalid_json':m.content='not json'
    elif state=='not_object':m.content='[]'
    else:m.name='weather_lookup'
    assert not assessment_findings(result['messages'][-1].content,result)

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['corrected','still_bad','protocol','empty','budget','provider_error','nontext'])
async def test_one_tool_free_repair_shares_budget_and_preserves_draft(mode):
    result=evidence('蛋白质很充足，但记录可能不完整。');draft=result['messages'][-1].content
    text={'corrected':'已记录蛋白质102克；现有证据不足以判断个人需要量。',
          'still_bad':'蛋白质不用再加了。','protocol':'<tool_call>fake</tool_call>','empty':'','nontext':[{'type':'text','text':'nested'}]}.get(mode,'unused')
    model=AsyncMock();model.ainvoke.return_value=AIMessage(content=text,usage_metadata={'input_tokens':20,'output_tokens':7,'total_tokens':27})
    budget=ReadDeliveryBudget(1 if mode=='budget' else 2);budget.consume()
    kwargs=dict(model=model,budget=budget,query='看看最近吃得如何',history=[],system_prompt='offline')
    if mode=='provider_error':
        model.ainvoke.side_effect=RuntimeError('offline provider interruption')
        with pytest.raises(RuntimeError):await recover_assessment_answer(result,**kwargs)
        assert result['assessment_delivery']['status']=='interrupted'
    else:
        result=await recover_assessment_answer(result,**kwargs)
        expected={'corrected':None,'still_bad':'read_assessment_boundary_invalid','protocol':'read_response_protocol_invalid','empty':'read_response_protocol_invalid','budget':'read_model_budget_exhausted','nontext':'read_response_protocol_invalid'}[mode]
        assert result.get('read_error_code')==expected
    receipt=result['assessment_delivery'];assert receipt['draft']==draft
    assert receipt['draft_sha256']==hashlib.sha256(draft.encode()).hexdigest()
    assert receipt['repair_attempts']==(0 if mode=='budget' else 1)
    AgentAssessmentDeliveryTrace.model_validate(receipt)
    assert budget.calls==(1 if mode=='budget' else 2)
    assert len(_tool_call_records(result,'offline'))==1
    if mode not in ('budget','provider_error'):
        assert _usage_totals(result)==(32,12)
        assert 'observations' in model.ainvoke.call_args.args[0][-1].content
        assert not model.ainvoke.call_args.kwargs,'No tool schemas or execution options added'

@pytest.mark.asyncio
async def test_ordinary_answer_does_not_spend_an_extra_call():
    result=evidence('已记录蛋白质102克。');model=AsyncMock();budget=ReadDeliveryBudget(2)
    returned=await recover_assessment_answer(result,model=model,budget=budget,query='查询记录',history=[],system_prompt='offline')
    assert returned is result and budget.calls==0 and not result.get('assessment_delivery')
    model.ainvoke.assert_not_called()

@pytest.mark.asyncio
async def test_a_failed_protocol_or_missing_evidence_answer_is_not_repaired_as_success():
    result=evidence('蛋白质很充足。');result['read_error_code']='read_response_protocol_invalid'
    model=AsyncMock()
    returned=await recover_assessment_answer(result,model=model,budget=ReadDeliveryBudget(2),query='查询记录',history=[],system_prompt='offline')
    assert returned['read_error_code']=='read_response_protocol_invalid';model.ainvoke.assert_not_called()
