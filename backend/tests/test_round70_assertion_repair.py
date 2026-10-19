"""Actual receipts, paired scope cases and the exact bounded repair request."""
import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from langchain_core.messages import AIMessage,ToolMessage
from app.schemas.agent_trace import AgentAssessmentDeliveryTrace
from app.services.agent_assessment_delivery import assessment_findings,recover_assessment_answer
from app.services.agent_assessment_repair import build_repair_input,repair_input_hash,MARKER
from app.services.agent_read_delivery import ReadDeliveryBudget
from test_round65_assessment_delivery import evidence

DATA=json.loads((Path(__file__).parent/'fixtures/round70/archived.json').read_text(encoding='utf8'))


def archived(row):
    run=json.loads(row['run_json'])
    return {'messages':[ToolMessage(content=json.dumps(t['result'],ensure_ascii=False),name=t['tool'].replace('.','_'),tool_call_id=t['attempt_id'],status=t['status']) for t in run['tool_attempts']]}


@pytest.mark.parametrize('row',DATA['receipt_rows'],ids=lambda r:r['id'])
def test_actual_known_false_matches_removed_and_true_matches_retained(row):
    run=json.loads(row['run_json']);receipt=run['turns'][row['turn']]['body']['execution_trace']['assessment_delivery']
    AgentAssessmentDeliveryTrace.model_validate(receipt)  # old schemas remain readable
    result=archived(row)
    for field,judgments in [('draft',row['findings']),('repair_draft',row['remaining_findings'])]:
        text=receipt[field];now=assessment_findings(text,result)
        for old in judgments:
            overlaps=[x for x in now if x['code']==old['code'] and max(x['start'],old['start'])<min(x['end'],old['end'])]
            assert bool(overlaps)==old['valid'],(field,old,now)
        assert all(text[f['start']:f['end']]==f['quote'] for f in now)


@pytest.mark.parametrize('row',DATA['missed_rows'],ids=lambda r:r['id'])
def test_known_missed_finals_now_trigger(row):
    run=json.loads(row['run_json']);text=run['turns'][0]['body']['reply']
    findings=assessment_findings(text,archived(row))
    assert findings
    assert any(any(q in text[max(0,f['start']-15):f['end']+15] for q in row['quotes']) for f in findings)


@pytest.mark.parametrize('text',[
    '蛋白质102克，我不替你下“已经够用”的结论。',
    '蛋白质102克，我没法替你保证“足够”。',
    '不能认定蛋白质已经很足。',
    '“蛋白质足够”这个结论不成立。',
    '蛋白质是否足够需要确认。',
    '蛋白质够不够用不是当前能判断的。',
    '蛋白质充不充足尚不清楚。',
    '蛋白质102克，这个量对多数保持体重的人来说是够的。',
])
def test_nutrition_predicate_is_not_an_affirmed_personal_claim(text):
    assert not assessment_findings(text,evidence(text))


@pytest.mark.parametrize('text',[
    '蛋白质102克，我替你下“已经够用”的结论。',
    '蛋白质102克，我能替你保证“足够”。',
    '蛋白质是否足够要确认，但已经够用。',
    '蛋白质够不够用需要确认，但对你已经够用。',
    '我没法判断全天，但这餐蛋白质已经很足。',
    '蛋白质对你这个保持体重的人来说是够的。',
    '蛋白质对多数人来说够用，对你也已经够用。',
    '蛋白质够用，但这不等于所有营养达标。',
])
def test_negation_does_not_leak_to_the_next_assertion(text):
    assert assessment_findings(text,evidence(text))


@pytest.mark.parametrize('body',['手腕','膝盖','肩部','脚踝'])
@pytest.mark.parametrize('prefix',['这个动作不直接用','这个动作不会直接用','这个动作不用'])
def test_load_predicates_across_subjects(body,prefix):
    text=prefix+body+'承重，但仍不能保证整体安全。'
    assert assessment_findings(text,evidence(text,'plan.get_active'))


@pytest.mark.parametrize('text,expected',[
    ('具体动作我没法替你保证“不涉及肩或膝”。',False),
    ('具体动作我能替你保证“不涉及肩或膝”。',True),
    ('- 手腕完全不负重、不支撑、不扭转。具体动作我不替你指定。',False),
    ('- 该动作手腕完全不负重、不支撑、不扭转。具体动作我不替你指定。',True),
    ('- 手腕已经完全不负重。具体动作我不替你指定。',True),
    ('- 手腕完全不负重，而且深蹲不涉及手腕。具体动作我不替你指定。',True),
    ('- 只做不引发或加重脚踝不适的轻活动，比如坐姿或卧姿下、脚踝完全不受力的上肢活动。',False),
    ('- 只做不引发或加重脚踝不适的轻活动，比如卧推脚踝完全不受力的动作。',True),
])
def test_restriction_and_actual_property_are_distinct(text,expected):
    assert bool(assessment_findings(text,evidence(text,'plan.get_active')))==expected


@pytest.mark.asyncio
async def test_repair_provider_sees_working_input_and_facts_but_original_stays_in_receipt():
    bad='已记录蛋白质102克。蛋白质已经够用，不用再加。用户要求一条建议。'
    result=evidence(bad);result['messages'][1].content=json.dumps({'date':'2026-10-08','total_protein_g':102})
    second='已记录蛋白质102克，不能据此判定个人是否够用；一条搭配建议是下餐加入一份蔬菜。'
    model=AsyncMock();model.ainvoke.return_value=AIMessage(content=second)
    out=await recover_assessment_answer(result,model=model,budget=ReadDeliveryBudget(1),query='给一条建议，不保存',history=[],system_prompt='system')
    receipt=out['assessment_delivery'];AgentAssessmentDeliveryTrace.model_validate(receipt)
    assert receipt['draft']==bad and receipt['repair_draft']==second and receipt['status']=='corrected'
    request=model.ainvoke.call_args.args[0];payload=json.loads(request[-1].content)
    assert payload['revision']==receipt['repair_input']
    assert '蛋白质已经够用，不用再加' not in json.dumps(payload,ensure_ascii=False)
    assert '已记录蛋白质102克' in payload['revision']['working_draft']
    assert '用户要求一条建议' in payload['revision']['working_draft']
    assert '给一条建议，不保存'==request[-2].content
    assert payload['observations'][0]['data']['business_data']['total_protein_g']==102
    assert receipt['repair_input_sha256']==repair_input_hash(payload['revision'])
    assert MARKER in payload['revision']['working_draft'] and MARKER not in out['messages'][-1].content
    assert len(payload['revision']['excluded_spans'])==1
    assert model.ainvoke.await_count==1 and not model.ainvoke.call_args.kwargs


@pytest.mark.parametrize('mutation',['hash','span','working','missing'])
@pytest.mark.asyncio
async def test_persisted_repair_input_tampering_is_detected(mutation):
    result=evidence('蛋白质已经够用。');model=AsyncMock();model.ainvoke.return_value=AIMessage(content='已记录蛋白质102克。')
    out=await recover_assessment_answer(result,model=model,budget=ReadDeliveryBudget(1),query='查询',history=[],system_prompt='test')
    receipt=copy.deepcopy(out['assessment_delivery'])
    if mutation=='hash':receipt['repair_input_sha256']='0'*64
    elif mutation=='working':receipt['repair_input']['working_draft']='tampered'
    elif mutation=='missing':receipt.pop('repair_input')
    else:
        receipt['repair_input']['excluded_spans'][0]['end']=10000
        receipt['repair_input_sha256']=repair_input_hash(receipt['repair_input'])
    with pytest.raises(ValueError):AgentAssessmentDeliveryTrace.model_validate(receipt)


def test_repair_input_rejects_unbound_finding():
    with pytest.raises(ValueError):build_repair_input('蛋白质够用。',[{'code':'nutrition_personal_adequacy','start':0,'end':2,'quote':'fake'}])


@pytest.mark.asyncio
async def test_working_marker_is_never_delivered_as_a_valid_answer():
    result=evidence('蛋白质已经够用。');model=AsyncMock();model.ainvoke.return_value=AIMessage(content=MARKER)
    out=await recover_assessment_answer(result,model=model,budget=ReadDeliveryBudget(1),query='查询',history=[],system_prompt='test')
    assert out['read_error_code']=='read_response_protocol_invalid'
    assert out['assessment_delivery']['repair_draft']==MARKER
    assert MARKER not in out['messages'][-1].content
    assert model.ainvoke.await_count==1
