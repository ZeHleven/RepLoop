"""Frozen R65 drafts/routes and contrasting assertions; synthetic offline only."""
import copy,json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from langchain_core.messages import ToolMessage
from app.config import settings
from app.schemas.agent_task import TaskSnapshot
from app.services import agent_runtime as runtime,agent_intent_model as routing
from app.services.agent_intent import IntentResolution,route_tools
from app.services.agent_assessment_delivery import assessment_findings
from app.services.agent_required_evidence import prepare_required_evidence
from app.services.business_clock import BUSINESS_DATE
from test_round57_read_delivery import install_tools,scripted_http
from test_round65_assessment_delivery import evidence

DATA=json.loads((Path(__file__).parent/'fixtures/round66/archived.json').read_text(encoding='utf8'))
def followup():return copy.deepcopy(DATA['followup'])
def resolution(body):return IntentResolution.model_validate({**{k:v for k,v in body.items() if k in IntentResolution.model_fields},'confidence':body['intent_confidence']})
def state(r):return TaskSnapshot.model_validate(r['turns'][1]['body']['execution_trace']['task_state'])

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    token=BUSINESS_DATE.set(date(2026,10,9))
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','synthetic-offline-r66')
    monkeypatch.setattr(settings,'DEEPSEEK_BASE_URL','https://api.deepseek.com')
    monkeypatch.setattr(routing,'structured_chat_completion',AsyncMock(side_effect=AssertionError('No external route')))
    yield
    BUSINESS_DATE.reset(token)

@pytest.mark.parametrize('r',DATA['rows'],ids=lambda r:r['id'])
def test_exact_archived_triggers_and_remaining_repair(r):
    from app.schemas.agent_trace import AgentAssessmentDeliveryTrace
    assert AgentAssessmentDeliveryTrace.model_validate(r['receipt']).checks=='assertion_patterns_v1'
    result={'messages':[ToolMessage(content=json.dumps(t['result']),name=t['tool'].replace('.','_'),tool_call_id=t['attempt_id']) for t in r['tools']]}
    findings=assessment_findings(r['receipt']['draft'],result)
    if r['id']=='WX34-r2':
        # Legacy label concerns the quoted category trigger, not every claim.
        # V3 newly detects a different, affirmative load exemption in this draft.
        assert not r['valid_trigger']
        assert [f['quote'] for f in findings]==['不直接压脚踝']
    else:
        assert bool(findings)==r['valid_trigger']
    for f in findings:assert r['receipt']['draft'][f['start']:f['end']]==f['quote']
    if not r['valid_trigger']:
        repaired=assessment_findings(r['receipt']['repair_draft'],result)
        if r['id']=='WX34-r2':
            assert [f['quote'] for f in repaired]==['不直接压脚踝']
        else:
            assert repaired==[]

@pytest.mark.parametrize('text',[
    '可以做一些不涉及手腕支撑的轻活动。',
    '先试不涉及脚踝承重的部分。',
    '比如不涉及手腕承重、不握杠的轻量活动。',
    '不等于深蹲对膝盖零负荷。',
    '只做不涉及肩部的动作，观察次日反应。',
    '你可以选择不涉及手腕的活动。',
    '建议仅尝试不涉及脚踝的动作。',
])
def test_category_or_negated_assertion(text):assert assessment_findings(text,evidence(text,'plan.get_active'))==[]

@pytest.mark.parametrize('text',[
    '先试卧推，不涉及膝盖。',
    '可以做一些卧推，不涉及膝部负荷。',
    '建议选择卧推这种不涉及膝盖的动作。',
    '比如卧推不涉及手腕的动作。',
    '深蹲是不涉及膝盖的动作。',
    '有些动作不涉及手腕，例如卧推。',
    '不等于卧推安全，深蹲对膝盖零负荷。',
    '不等于深蹲对膝盖零负荷，卧推不涉及手腕。',
    '先试不涉及脚踝承重的部分，深蹲对膝盖没有压力。',
    '卧推不涉及手腕，但不能保证姿势安全。',
])
def test_category_exception_does_not_hide_named_or_separate_assertion(text):assert assessment_findings(text,evidence(text,'plan.get_active'))

@pytest.mark.asyncio
async def test_archived_followup_prefetches_without_model_selecting_tools(monkeypatch):
    r=followup();t=r['turns'][1];res=resolution(t['body'])
    calls=install_tools(monkeypatch,r);requests=scripted_http(monkeypatch,[{'role':'assistant','content':'离线续接执行检查，不计业务正确率。'}])
    out=await runtime.invoke_langchain_agent(None,user_id='synthetic',history=[{'role':'assistant','content':'旧答复不得当作现在的工具证据。'}],user_message=t['message'],tool_allowlist=route_tools(res),resolution=res,task_state=state(r))
    assert {c[0] for c in calls}=={'health.get_screening_summary','plan.get_active'} and len(calls)==2
    assert len(runtime._tool_call_records(out,'r66'))==2
    assert not requests[0].get('tools')
    assert sum(m['role']=='tool' for m in requests[0]['messages'])==2
    assert any('training_adjustment_envelope' in m['content'] for m in requests[0]['messages'] if m['role']=='system')

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['none','new','cancelled','different_task','unbacked','quoted_plan','negative_plan','generic_plan','high','write','clarify','forbidden','budget','wrong_quote'])
async def test_continuation_preserves_source_and_execution_gates(monkeypatch,change):
    r=followup();res=resolution(r['turns'][1]['body']);s=state(r);allowed=route_tools(res)
    if change=='none':s=None
    elif change=='new':s.transition='new'
    elif change=='cancelled':s.tasks[0].phase='cancelled'
    elif change=='different_task':s.active_task_id='other'
    elif change=='unbacked':s.tasks[0].requirements=[]
    elif change in {'quoted_plan','negative_plan','generic_plan'}:
        requirement=next(v for v in s.tasks[0].requirements if v.key=='plan_scope')
        replacement={'quoted_plan':'解释“根据已存计划给具体取舍及回归条件”的意思','negative_plan':'不要根据已存计划给取舍建议','generic_plan':'一般训练知识，不涉及我的计划'}[change]
        # Keep the counterfactual user source consistent with its task quote.
        # Otherwise the original affirmative request still authorizes a read.
        assert requirement.quote in r['turns'][1]['message']
        r['turns'][1]['message']=r['turns'][1]['message'].replace(requirement.quote,replacement)
        requirement.quote=replacement
    elif change=='high':res.risk_level='high'
    elif change=='write':res.request_kind='mutation';res.requested_effect='update'
    elif change=='clarify':res.clarification_required=True
    elif change=='forbidden':allowed=['health.get_screening_summary']
    elif change=='budget':monkeypatch.setattr(settings,'AGENT_MAX_TOOL_CALLS',1)
    else:res.read_scope_quotes=['invented quote']
    calls=install_tools(monkeypatch,r);tools=runtime.build_read_tools(None,user_id='s',allowlist=allowed)
    prepared=await prepare_required_evidence(None,tools=tools,resolution=res,allowlist=allowed,query=r['turns'][1]['message'],task_state=s)
    assert not calls
    if change in {'forbidden','budget'}:assert prepared.error_code==('read_tool_permission_denied' if change=='forbidden' else 'read_tool_budget_exhausted')
    else:assert not prepared.active
