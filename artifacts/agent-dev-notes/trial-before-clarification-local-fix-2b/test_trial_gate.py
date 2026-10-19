"""Independent development fixtures; no holdout text or assertions."""
import json
from unittest.mock import AsyncMock
import pytest,pytest_asyncio
from langchain_core.messages import AIMessage,ToolMessage
from app.services.agent_assessment_delivery import assessment_findings,recover_assessment_answer
from app.services.agent_read_delivery import ReadDeliveryBudget
from app.services.agent_assertion_scope import CURRENT_CARE_REPLY

FATIGUE='我只是精神疲倦，没有疼痛。按保存的计划给建议，动作实际做法尚未确认，不保存。'
CARE='今天跌倒后左膝明显肿胀和疼痛，平地走动也疼。卧推和深蹲的实际做法待确认，只建议，不保存。'
GOOD='请说明卧推和深蹲的实际做法与耐受情况？先暂停这两项，确认后再细化安排，不安排试练，不保存。'
CASES=[
 ('fatigue_sets',FATIGUE,'当前卧推2组，深蹲2组，先做再补充实际做法。'),
 ('fatigue_trial',FATIGUE,'卧推可以先用轻负荷试两次，不舒服就停。'),
 ('care_qualitative',CARE,CURRENT_CARE_REPLY+'可以只做上肢练习，比如靠背卧推，采用轻负荷，感觉稳定再继续。'),
 ('care_trial',CARE,CURRENT_CARE_REPLY+'卧推先用轻重量试几次，疼了就停。'),
]
@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

def evidence(text=''):
    plan={'found':True,'plan':{'days_per_week':1,'exercises':[{'exercise_name':n,'sets':3,'reps':'8','rest_seconds':75} for n in ['卧推','深蹲']]}}
    return {'messages':[ToolMessage(content=json.dumps(plan,ensure_ascii=False),name='plan_get_active',tool_call_id='p'),ToolMessage(content='{"found":true,"injuries":[]}',name='health_get_screening_summary',tool_call_id='h'),AIMessage(content=text)]}

@pytest.mark.parametrize('ident,query,draft',CASES,ids=[x[0] for x in CASES])
def test_independent_missing_confirmation(ident,query,draft):
    findings=[f for f in assessment_findings(draft,evidence(draft),query) if f['code']=='training_action_conflict']
    assert findings,(ident,draft)
    assert all(draft[f['start']:f['end']]==f['quote'] for f in findings)

@pytest.mark.parametrize('query,draft',[
 (FATIGUE,GOOD),
 (FATIGUE,'先确认实际做法和耐受后，再安排卧推1组。'),
 (FATIGUE,'保存的计划是卧推3组、深蹲3组，仅为来源记录。先确认做法。'),
 (FATIGUE,'“卧推先用轻负荷试两次”是错误的建议。'),
 (FATIGUE,'不要先做卧推，也不允许先用轻重量试几次。'),
 (FATIGUE,'不建议先做卧推；不允许先练深蹲。'),
 (FATIGUE,'先确认实际做法和耐受后，可以只做卧推。'),
 (FATIGUE,'卧推可以先试两次吗？先确认实际做法再说。'),
 ('我最近没睡好但没有疼痛，按保存计划建议。','当前卧推1组，深蹲1组。'),
 ('之前膝盖疼，现在已经完全恢复，日常功能正常。','当前卧推1组，深蹲1组。'),
 ('朋友的动作做法尚未确认。我没有疼痛。','当前卧推1组。'),
 (CARE,CURRENT_CARE_REPLY+GOOD),
],ids=['question','confirmed_first','source','quote','negative','negative_qualitative','confirmed_qualitative','permission_question','fatigue_no_gap','recovered','other_person','care_first'])
def test_allowed_scopes_remain_allowed(query,draft):
    assert not [f for f in assessment_findings(draft,evidence(draft),query) if f['code']=='training_action_conflict']

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['corrected','still_wrong','budget'])
async def test_existing_single_repair_and_budget(mode):
    draft=CASES[0][2];model=AsyncMock();model.ainvoke.return_value=AIMessage(content=draft if mode=='still_wrong' else GOOD)
    budget=ReadDeliveryBudget(1);budget.calls=int(mode=='budget')
    result=await recover_assessment_answer(evidence(draft),model=model,budget=budget,query=FATIGUE,source_query=FATIGUE,history=[],system_prompt='offline')
    receipt=result['assessment_delivery']
    assert model.ainvoke.await_count==(0 if mode=='budget' else 1)
    if mode=='corrected':assert receipt['status']=='corrected' and result['messages'][-1].content==GOOD
    else:assert receipt['status']=='blocked' and draft not in result['messages'][-1].content

@pytest.mark.parametrize('draft',[
 '卧推实际做法尚未确认，可以先用轻负荷试两次。',
 '可以只做上肢练习，比如靠背卧推，采用轻负荷，感觉稳定再继续。',
])
def test_empty_query_extractors_do_not_skip_unconfirmed_permission(draft):
    from app.services.agent_symptom_context import current_symptom_quotes
    from app.services.agent_action_consistency import _pending_clarifications
    query='最近忙于工作。请参考保存的动作名字给活动建议。'
    assert not current_symptom_quotes(query)
    assert not _pending_clarifications(query,{'卧推','深蹲','动作'},{'卧推','深蹲','动作'})
    assert any(f['code']=='training_action_conflict' for f in assessment_findings(draft,evidence(draft),query))

def test_confirmed_source_does_not_gain_new_trial_requirement():
    draft='可以只做卧推，采用轻负荷。';result=evidence(draft)
    plan=json.loads(result['messages'][0].content)
    plan['plan']['notes']='卧推是杠铃卧推。深蹲是徒手深蹲。'
    result['messages'][0]=result['messages'][0].model_copy(update={'content':json.dumps(plan,ensure_ascii=False)})
    assert not [f for f in assessment_findings(draft,result,'最近工作忙，没有疼痛。') if f['code']=='training_action_conflict']
