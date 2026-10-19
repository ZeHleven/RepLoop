import copy,json
from pathlib import Path
from unittest.mock import AsyncMock
import pytest,pytest_asyncio
from langchain_core.messages import AIMessage,ToolMessage
from app.services.agent_assessment_delivery import assessment_findings,recover_assessment_answer
from app.services.agent_read_delivery import ReadDeliveryBudget
from app.services.agent_assessment_repair import build_repair_input,MARKER
from app.schemas.agent_trace import AgentAssessmentDeliveryTrace

ROWS={k:json.loads(v['run_json']) for k,v in json.loads((Path(__file__).parent/'fixtures/round74/archived.json').read_text(encoding='utf8')).items()}
QUERY='今天膝盖突然疼，旧档案没有。我工作累，给一周每次最多15分钟临时训练建议和回归条件，不保存。'
@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    yield  # Pure component suite; real SQL coverage is in the SQL module.

def evidence(text='',name='深蹲'):
    return {'messages':[
        ToolMessage(content=json.dumps({'found':True,'plan':{'exercises':[{'exercise_name':name},{'exercise_name':'卧推'}]}}),name='plan_get_active',tool_call_id='plan',status='success'),
        ToolMessage(content='{"found":true,"injuries":[]}',name='health_get_screening_summary',tool_call_id='health',status='success'),AIMessage(content=text)]}

def conflicts(text,query=QUERY,name='深蹲'):
    return [f for f in assessment_findings(text,evidence(text,name),query) if f['code']=='training_action_conflict']

def archive(id):
    run=ROWS[id];t=run['turns'][0]
    result={'messages':[ToolMessage(content=json.dumps(x['result'],ensure_ascii=False),name=x['tool'].replace('.','_'),tool_call_id=x['attempt_id'],status=x['status']) for x in run['tool_attempts'] if (x.get('turn') or 0)==0]}
    return run,t,result

def test_actual_current_conflict_binds_both_exact_spans():
    _,t,result=archive('WX24-r2');text=t['body']['reply']
    fs=assessment_findings(text,result,t['message']);pairs=[f for f in fs if f['code']=='training_action_conflict']
    # R96 also catches the original unknown posture/support + bench prescription.
    assert len(pairs)==4,pairs
    assert '先停掉它' in pairs[0]['quote']
    assert {f['quote'] for f in pairs[1:]}=={'深蹲 2 组','卧推 2 组','姿势和支撑方式未知'}
    assert all(text[f['start']:f['end']]==f['quote'] for f in fs)
    revision=build_repair_input(text,fs)
    assert len(revision['excluded_spans'])==3
    assert '现在最该做的是先停掉它' not in revision['working_draft']
    assert '深蹲 2 组' not in revision['working_draft']
    assert '卧推 2 组' not in revision['working_draft']
    assert '之后怎么回归' in revision['working_draft']

def test_actual_effect_guarantee_now_detected():
    _,t,result=archive('WX35-r1');fs=assessment_findings(t['body']['reply'],result,t['message'])
    assert any(f['code']=='training_effect_guarantee' and f['quote']=='不会掉什么' for f in fs)

@pytest.mark.parametrize('id',['WX30-r1','WX35-r2'])
def test_actual_category_false_positives_removed(id):
    _,t,result=archive(id);text=t['body']['execution_trace']['assessment_delivery']['draft']
    assert not assessment_findings(text,result,t['message'])

@pytest.mark.parametrize('id',['WX24-r1','WX32-r1'])
def test_real_pause_clarification_and_resolved_symptom_do_not_conflict(id):
    _,t,result=archive(id)
    assert not [f for f in assessment_findings(t['body']['reply'],result,t['message']) if f['code']=='training_action_conflict']

@pytest.mark.parametrize('name',['深蹲','卧推','坐姿划船','弹力带划船'])
@pytest.mark.parametrize('pause',['{n}先停。','先暂停{n}。','{n}是可能诱发症状的动作，现在最该做的是先停掉它。'])
@pytest.mark.parametrize('prescribe',['本周安排：{n}2组。','当前临时安排：{n}改为1–2组。','现在怎么调整\n- {n}：一组。'])
def test_multiple_observed_names_and_surface_forms(name,pause,prescribe):
    text=pause.format(n=name)+'\n'+prescribe.format(n=name)
    assert len(conflicts(text,name=name))==2

@pytest.mark.parametrize('text',[
    '深蹲先停。\n回归条件\n深蹲2组。',
    '深蹲先停。等症状改善后，再从深蹲2组开始。',
    '深蹲先停。深蹲只有在日常功能恢复正常后才做2组。',
    '深蹲先停。原计划是深蹲3组。',
    '深蹲先停。当前临时安排深蹲0组。',
    '深蹲先停。不要安排深蹲2组。',
    '错误示例：“深蹲先停”。本周深蹲2组。',
    '深蹲先停。错误示例：深蹲2组。',
    '深蹲不用先停。当前临时安排深蹲2组。',
    '深蹲先停。当前卧推2组。',
    '当前深蹲2组。深蹲先停。',
])
def test_nonconflicting_scopes(text):
    assert not conflicts(text)

@pytest.mark.parametrize('query',[
    '以前膝盖疼，但今天已经完全不疼，给临时建议。',
    '如果今天膝盖疼该怎么办？',
    '朋友今天膝盖疼，给建议。',
    '他说“今天膝盖疼”，我想知道含义。',
    '工作累，给一周减量建议。',
])
def test_no_inferred_current_symptom(query):
    assert not conflicts('深蹲先停。当前深蹲2组。',query)

def test_future_heading_does_not_waive_a_later_current_phase():
    assert conflicts('深蹲先停。\n回归条件\n深蹲2组。\n当前怎么调整\n深蹲2组。')
    assert conflicts('深蹲先停。\n恢复条件还没满足，现在深蹲2组。')

@pytest.mark.parametrize('change',['unknown_name','health_error','plan_error','not_executed','no_health','malformed'])
def test_requires_successful_actual_plan_and_health_observations(change):
    text='深蹲先停。当前深蹲2组。';r=evidence(text)
    if change=='unknown_name':r=evidence(text,'跳绳')
    elif change=='health_error':r['messages'][1].status='error'
    elif change=='plan_error':r['messages'][0].status='error'
    elif change=='not_executed':r['messages'][0].additional_kwargs['executed']=False
    elif change=='no_health':r['messages'][1].content='{"found":false}'
    else:r['messages'][0].content='invalid'
    assert not [f for f in assessment_findings(text,r,QUERY) if f['code']=='training_action_conflict']

@pytest.mark.parametrize('text',[
    '少练一次不会掉什么。','这周休息不会有什么损失。','这次减量不会损失什么。',
    '我无法判断你的情况，但这周少练不会掉什么。',
])
def test_effect_guarantee_forms(text):
    assert any(f['code']=='training_effect_guarantee' for f in assessment_findings(text,evidence(text),QUERY))

@pytest.mark.parametrize('text',[
    '不能说少练一次不会掉什么。','“不会掉什么”这个说法不成立。','目标是不会掉什么，不是保证。',
    '是否不会有什么损失，需要更多信息。','如果物品放平就不会掉什么。',
])
def test_negation_quote_goal_and_nontraining_meaning(text):
    # Nontraining object claim is deliberately not checked without training evidence.
    r={'messages':[]} if '物品' in text else evidence(text)
    assert not assessment_findings(text,r,QUERY)

@pytest.mark.parametrize('text',[
    '这次就只做不涉及手腕的轻活动。','也可以只做不涉及手腕的轻活动。',
    '手腕不疼的日子，也可以只做不涉及手腕的轻活动（比如走路），以不加重症状为准。',
])
def test_category_selection_not_a_named_exercise_property(text):
    assert not assessment_findings(text,evidence(text),QUERY)

@pytest.mark.parametrize('text',[
    '这次卧推不涉及手腕。','也可以保证卧推不涉及手腕。','这次就只做卧推，卧推不涉及手腕。',
])
def test_category_grammar_does_not_exempt_named_claims(text):
    assert assessment_findings(text,evidence(text),QUERY)

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['corrected','still_conflict','budget'])
async def test_bounded_complete_rewrite_and_durable_receipt(mode):
    bad='深蹲先停。\n当前安排深蹲2组。\n每次15分钟，只给建议。';r=evidence(bad)
    good='当前先暂停深蹲，每次15分钟是上限，允许休息；请补起因与功能。等症状改善后，再从深蹲1组逐步恢复。仅建议未保存。'
    model=AsyncMock();model.ainvoke.return_value=AIMessage(content=bad if mode=='still_conflict' else good)
    budget=ReadDeliveryBudget(1);budget.calls=budget.limit if mode=='budget' else budget.calls
    out=await recover_assessment_answer(r,model=model,budget=budget,query='internal task wrapper',source_query=QUERY,history=[],system_prompt='test')
    receipt=out['assessment_delivery'];AgentAssessmentDeliveryTrace.model_validate(receipt)
    assert receipt['draft']==bad and receipt['checks']=='assertion_scope_v7'
    assert receipt['status']==('corrected' if mode=='corrected' else 'blocked')
    assert model.ainvoke.await_count==(0 if mode=='budget' else 1)
    if mode=='corrected':
        assert out['messages'][-1].content==good
        payload=json.loads(model.ainvoke.call_args.args[0][-1].content)
        assert '深蹲2组' not in payload['revision']['working_draft']
        assert '每次15分钟，只给建议' in payload['revision']['working_draft']
        assert MARKER not in out['messages'][-1].content
    else:assert out['read_error_code'] in {'read_model_budget_exhausted','read_assessment_boundary_invalid'}
