"""Archived source/obligation failures and strict write/compound counterexamples."""
import copy,json
from datetime import date
from unittest.mock import AsyncMock
import pytest,pytest_asyncio
from langchain_core.messages import ToolMessage
from app.config import settings
from app.services import agent_intent_model as model
from app.services.agent_intent import required_read_tools,normalize_resolution
from app.services.agent_assessment_delivery import assessment_findings
from app.services.business_clock import BUSINESS_DATE
from app.services.nutrition_query_scope import is_single_daily_history_goal,explicit_requested_selection,is_nutrition_filter_revision
from test_round76_live_evidence import ROWS,resolution
from round76_core_support import CORE,archived_context

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    token=BUSINESS_DATE.set(date(2026,10,9))
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','offline-scripted')
    yield
    BUSINESS_DATE.reset(token)

def findings(text):
    row=ROWS['SV07-r1']
    result={'messages':[ToolMessage(content=json.dumps(t['result']),name=t['tool'].replace('.','_'),tool_call_id=t['attempt_id']) for t in row['tool_attempts']]}
    return [x for x in assessment_findings(text,result,row['turns'][0]['message']) if x['code']=='training_action_conflict']

def test_actual_source_paragraph_false_positive_and_legacy_receipt():
    from app.schemas.agent_trace import AgentAssessmentDeliveryTrace
    receipt=ROWS['SV07-r1']['turns'][0]['body']['execution_trace']['assessment_delivery']
    assert receipt['checks']=='assertion_scope_v6' and len(receipt['remaining_findings'])==4
    assert AgentAssessmentDeliveryTrace.model_validate(receipt).checks=='assertion_scope_v6'
    assert findings(receipt['repair_draft'])==[]

@pytest.mark.parametrize('header',['**已保存的资料**','# 已存计划','已保存训练计划：','**当前生效计划**'])
def test_saved_source_is_separate_from_current_execution(header):
    assert not findings('卧推先停。\n\n'+header+'\n- 训练日两个动作。卧推3组×10次（建议20kg）。\n- 深蹲3组。')

@pytest.mark.parametrize('current',[
    '**这周的取舍**\n卧推3组。',
    '**另外的安排**\n卧推3组。',
    '现在安排如下：\n卧推3组。',
    '请做卧推3组。',
    '建议做卧推3组。',
    '照做：卧推3组。',
    '本周安排卧推3组。',
    '\n卧推3组。',
])
def test_heading_does_not_waive_a_new_or_explicit_instruction(current):
    text='卧推先停。\n**已保存的资料**\n- 记录中深蹲3组。\n'+current
    found=findings(text);assert len(found)==2,found
    assert all(text[x['start']:x['end']]==x['quote'] for x in found)

def test_history_pause_does_not_become_current_pause_and_conditional_return_is_distinct():
    assert not findings('**已保存的资料**\n- 上次资料写着卧推暂停。\n**现在安排**\n卧推1组。')
    assert not findings('卧推暂停。\n**已存计划**\n- 卧推3组。\n**回归条件**\n日常功能改善后再从卧推1组开始。')
    assert findings('卧推暂停。\n**已存计划，请现在照做**\n卧推3组。')

@pytest.mark.parametrize('id',['SV08-r1','SV08-r2'])
def test_saved_plan_temporary_request_has_no_extra_next_workout_obligation(id):
    row=ROWS[id];q=row['turns'][0]['message'];r=normalize_resolution(q,resolution(row['turns'][0]['body']))
    assert set(required_read_tools(r))=={'plan.get_active','health.get_screening_summary'}
    assert 'next_workout' in r.optional_evidence_requirements
    assert any('已存计划' in s for s in r.read_scope_quotes)

@pytest.mark.parametrize('extra,tool',[
    ('并查下一练。','workout.get_next'),('还想看下次练什么。','workout.get_next'),
    ('也查今天该练什么。','workout.get_daily_context'),
    ('并核对最近训练记录。','workout.list_history'),('还有我的个人资料。','profile.get_summary'),
])
def test_joint_requested_goals_remain_required(extra,tool):
    row=ROWS['SV08-r1'];q=row['turns'][0]['message']+extra;r=normalize_resolution(q,resolution(row['turns'][0]['body']))
    assert tool in required_read_tools(r)

def test_unrelated_or_unknown_temporary_context_does_not_erase_original_obligations():
    base=resolution(ROWS['SV08-r1']['turns'][0]['body'])
    for q in ['我膝盖疼，给我临时工作安排。','我有健康问题，只谈临时安排。','不要结合已存计划给临时安排，请先查询下一练。']:
        assert 'workout.get_next' in required_read_tools(normalize_resolution(q,base))

@pytest.mark.asyncio
@pytest.mark.parametrize('id',list(CORE))
async def test_archived_core_routes_do_not_require_unasked_evidence_or_writes(monkeypatch,id):
    query,context,routes=archived_context(id)
    for raw in routes:
        monkeypatch.setattr(model,'_invoke_model_route',AsyncMock(return_value=model.IntentRouteDecision.model_validate(raw)))
        out=await model.resolve_intent_with_fallback(query,context_messages=context,use_model=True)
        assert not out.understanding_failed,out.error_category
        assert out.attempt_count==1 and required_read_tools(out.resolution)==['nutrition.list_history']
        assert not out.resolution.change_requests
    if id.startswith('QQ'):
        assert out.resolution.resolved_query==query
        selected=explicit_requested_selection(query,date(2026,10,9))
        assert len(selected['logged_dates'])==6 and '2026-10-11' in selected['logged_dates']

@pytest.mark.asyncio
@pytest.mark.parametrize('id',['HN06-r1','HN11-r1'])
@pytest.mark.parametrize('invalid',['missing','failed','wrong_run','pending','cancelled','wrong_task','not_assistant'])
async def test_read_revisions_need_actual_matching_success(monkeypatch,id,invalid):
    query,context,routes=archived_context(id);raw=copy.deepcopy(routes[0]);state=json.loads(context[0]['content'])
    if invalid=='missing':context[-1].pop('query_context')
    elif invalid=='failed':context[-1]['query_context']['successful_query']=False
    elif invalid=='wrong_run':context[-1]['query_context']['source_run_id']='other'
    elif invalid=='not_assistant':context[-1]['role']='user'
    elif invalid=='wrong_task':raw['task_update']['task_id']='other'
    else:
        if invalid=='pending':state['tasks'][0]['proposal_pending']=True
        else:state['tasks'][0]['phase']='cancelled'
        context[0]['content']=json.dumps(state)
    monkeypatch.setattr(model,'_invoke_model_route',AsyncMock(return_value=model.IntentRouteDecision.model_validate(raw)))
    out=await model.resolve_intent_with_fallback(query,context_messages=context,use_model=True)
    assert out.understanding_failed

@pytest.mark.parametrize('extra',['再查今天吃了多少。','同时评估最近三周饮食。','然后记录今天午餐。','把最后一个日期改成2026-10-12。'])
def test_unknown_or_joint_goals_cannot_borrow_standalone_report_proof(extra):
    q=archived_context('QQ01-r2')[0]
    assert not is_single_daily_history_goal(q+extra,as_of=date(2026,10,9))
    r=normalize_resolution(q+extra,resolution(CORE['QQ01-r2']['turns'][0]['body']))
    from app.services.agent_query_reports import narrow_report_evidence
    assert 'nutrition.get_today' in required_read_tools(narrow_report_evidence(r,q+extra))

@pytest.mark.parametrize('q',[
    '多看两个。','改成最近4个有记录的日期，然后记录今天早餐。',
    '回到最开始那次：最近3个有饮食记录的日期，不限餐次，逐天列总热量。再删除早餐。',
    '回到最开始那次。','不要回到最开始那次：最近3个有记录的日期，每天热量。',
])
def test_partial_or_mixed_filter_edits_not_proven(q):assert not is_nutrition_filter_revision(q)

def test_scope_day_count_mismatch_stays_unproven():
    q=archived_context('QQ01-r2')[0]
    for count in ['五','七']:
        assert not is_single_daily_history_goal(q.replace('六',count),as_of=date(2026,10,9))
