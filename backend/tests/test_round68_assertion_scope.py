"""Archived failures plus polarity/subject permutations, not live eval scores."""
import copy,json
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from langchain_core.messages import AIMessage,ToolMessage
from app.schemas.agent_trace import AgentAssessmentDeliveryTrace
from app.services.agent_assessment_delivery import assessment_findings,recover_assessment_answer
from app.services.agent_read_delivery import ReadDeliveryBudget
from test_round65_assessment_delivery import evidence

DATA=json.loads((Path(__file__).parent/'fixtures/round68/archived.json').read_text(encoding='utf8'))

def archived(row):
    return {'messages':[ToolMessage(content=json.dumps(t['result']),name=t['tool'].replace('.','_'),tool_call_id=t['attempt_id'],status=t['status']) for t in row['tools']]}

@pytest.mark.parametrize('row',DATA['receipt_rows'],ids=lambda r:r['id']+':'+str(r['turn']))
def test_archived_drafts_and_repairs_preserve_true_findings_only(row):
    receipt=row['receipt'];AgentAssessmentDeliveryTrace.model_validate(receipt)
    expected=0 if row['id']=='WX35-r2' else 1
    initial=assessment_findings(receipt['draft'],archived(row))
    assert len(initial)==expected,initial
    repaired=assessment_findings(receipt['repair_draft'],archived(row))
    assert len(repaired)==(1 if row['id'] in {'WX25-r2','WX27-r1','WX34-r2'} else 0),repaired
    for text,findings in [(receipt['draft'],initial),(receipt['repair_draft'],repaired)]:
        assert all(text[f['start']:f['end']]==f['quote'] for f in findings)

@pytest.mark.parametrize('row',DATA['missed_rows'],ids=lambda r:r['id'])
def test_actual_missed_training_finals(row):
    findings=assessment_findings(row['reply'],archived(row))
    assert any(f['code']==('clinical_exclusion' if row['id']=='WX31-r1' else 'exercise_load_exemption') for f in findings),findings

@pytest.mark.parametrize('body',['手腕','膝盖','肩部','脚踝'])
@pytest.mark.parametrize('predicate',['{body}不受力','{body}不主动发力','不靠{body}承重','不压{body}'])
@pytest.mark.parametrize('frame',['该替代动作{predicate}。','先选择这个动作：{predicate}，但我不能保证其他方面安全。'])
def test_load_predicate_orders_do_not_depend_on_exercise_dictionary(body,predicate,frame):
    text=frame.format(predicate=predicate.format(body=body))
    assert assessment_findings(text,evidence(text,'plan.get_active'))

@pytest.mark.parametrize('text',[
    '我不把任何替代动作说成“无负荷”或“绝对不涉及脚踝”。',
    '我不会将动作称为“手腕不受力”。',
    '也不保证少练这段时间对效果没有影响。',
    '我无法保证减量后的效果不变。',
    '这周目标是维持下肢活动、不刺激手腕，不是练满原计划。',
    '训练原则是不刺激手腕。',
    '只做完全不引发、不加重手腕症状的轻活动，比如不涉及手腕的走一走、活动髋膝。',
    '剩下的时间做不涉及手腕支撑的轻活动。',
    '不能根据这些信息说你不属于需要立刻就医的红旗表现。',
    '目前无法排除急症。',
    '是否属于急症尚不清楚。',
    '“卧推膝盖不受力”这个说法不成立。',
    '先询问是否需要手腕承重，不能保证无负荷。',
    '尚未排除急症。',
    '没有排除急症。',
    '待医生评估排除急症后，再考虑恢复。',
    '若经专业评估可以排除急症，才逐步恢复。',
    '我不认为“卧推不涉及膝盖”成立。',
    '我没法保证效果不变。',
])
def test_goals_negated_quotes_questions_and_selection(text):
    assert not assessment_findings(text,evidence(text,'plan.get_active'))

@pytest.mark.parametrize('text',[
    '这周目标是减量，卧推不涉及脚踝。',
    '这周目标是维持活动，但深蹲不靠手腕承重。',
    '我不保证一切安全，深蹲手腕不受力。',
    '我不把深蹲说成“无负荷”，但是卧推膝盖不受力。',
    '我认为“深蹲不靠手腕承重”是对的。',
    '目前不属于需要立刻就医的红旗表现。',
    '根据没有肿胀，可以排除急症。',
    '本周目标是确保效果不变。',
    '只做轻活动，比如卧推不涉及手腕的动作。',
    '保证少练这段时间对效果没有影响。',
    '如果没有肿胀，就可以排除急症。',
    '医生已经排除急症了，可以练。',
    '本周目标是保证效果不变。',
    '我不保证姿势安全但卧推膝盖不受力。',
    '这周目标是减量但卧推不涉及脚踝。',
])
def test_nonassertive_frame_cannot_mask_a_distinct_assertion(text):
    assert assessment_findings(text,evidence(text,'plan.get_active'))

@pytest.mark.asyncio
@pytest.mark.parametrize('first,second,blocked',[
    ('卧推脚不主动发力。','仍然是脚不受力，所以可以。',True),
    ('目前可以排除急症。','现有资料不足以排除急症；暂休并确认尚缺的功能信息。',False),
    ('深蹲不靠手腕承重。','现有动作名称不能证明安全；疼痛诱发动作先暂停，12分钟是上限。',False),
])
async def test_new_findings_share_one_tool_free_repair_and_keep_original(first,second,blocked):
    result=evidence(first,'plan.get_active');model=AsyncMock();model.ainvoke.return_value=AIMessage(content=second)
    budget=ReadDeliveryBudget(2);budget.consume()
    out=await recover_assessment_answer(result,model=model,budget=budget,query='临时训练建议',history=[],system_prompt='offline')
    receipt=out['assessment_delivery'];AgentAssessmentDeliveryTrace.model_validate(receipt)
    assert receipt['checks']=='assertion_scope_v7' and receipt['draft']==first and receipt['repair_draft']==second
    assert budget.calls==2 and model.ainvoke.await_count==1
    assert bool(out.get('read_error_code'))==blocked
    assert not model.ainvoke.call_args.kwargs

@pytest.mark.asyncio
async def test_nonassertion_does_not_spend_model_budget_or_create_receipt():
    text='这周目标是不刺激手腕；也不保证效果不变。'
    result=evidence(text,'plan.get_active');model=AsyncMock();budget=ReadDeliveryBudget(1)
    out=await recover_assessment_answer(result,model=model,budget=budget,query='临时建议',history=[],system_prompt='offline')
    assert out is result and not out.get('assessment_delivery') and budget.calls==0
    model.ainvoke.assert_not_called()
