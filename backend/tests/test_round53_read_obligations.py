"""Archived production-path decisions and counterexamples, no external models."""
import copy,json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from langchain_core.messages import ToolMessage
from app.config import settings
from app.services import agent_intent_model,agent_runtime as runtime
from app.services.agent_intent import resolve_intent,route_tools
from app.services.agent_intent_model import IntentRouteDecision,resolve_intent_with_fallback
from app.services.agent_evidence_contract import guard_read_completion
from app.services.agent_trace import observation_fingerprint
from app.schemas.agent_trace import AgentExecutionTrace
from app.services.business_clock import BUSINESS_DATE

ROWS=json.loads((Path(__file__).parent/'fixtures/round53/archived-paths.json').read_text(encoding='utf8'))['rows']
def row(id):return copy.deepcopy(next(r for r in ROWS if r['id']==id))

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    token=BUSINESS_DATE.set(date(2026,10,9))
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','offline-r53')
    monkeypatch.setattr(agent_intent_model,'structured_chat_completion',AsyncMock(side_effect=AssertionError('No model network')))
    yield
    BUSINESS_DATE.reset(token)

@pytest.mark.asyncio
@pytest.mark.parametrize('id',['DX01-r1','DX01-r2','CW02-r1','CW02-r2'])
async def test_baseline_archived_read_routes(id,monkeypatch):
    r=row(id)
    for payload in r['routes']:
        monkeypatch.setattr(agent_intent_model,'_invoke_model_route',AsyncMock(return_value=IntentRouteDecision.model_validate(payload)))
        result=await resolve_intent_with_fallback(r['message'],use_model=True)
        assert not result.understanding_failed,result.error_category
        assert result.attempt_count==1 and result.resolution.requested_effect=='read'
        assert not result.resolution.change_requests

@pytest.mark.parametrize('message',[
    '请核对昨天全部记录与三个营养目标的差额。',
    '请对照今天所有记录和给定蛋白质目标。',
    '请比较本周已有训练记录与当前计划。',
    '请分别核对计划和实际记录。',
    '查看当前计划显示的完成训练日为0是否正确。',
    '请说明完成训练日数为2和训练场次数为3的差别。',
    '核对2026-10-05至2026-10-09全部训练记录。',
])
def test_baseline_nominal_count_objects(message):
    assert resolve_intent(message).requested_effect=='read'

@pytest.mark.parametrize('message',[
    '请分别记录早餐米饭100克和午餐鸡胸100克。',
    '请分别修改计划训练频率和重量。',
    '请核对全部饮食记录，然后记录晚餐米饭100克。',
    '核对计划后请完成当前训练。',
    '把当前计划的完成训练日设为2。',
    '请将全部训练记录下来。',
    '请记录全部训练场次。',
    '请把全部饮食记录为今天。',
    '请分别保存这两份方案。',
    '不要查询，直接记录晚餐米饭100克。',
    '不得不修改当前计划的训练频率。',
    '请只核对全部记录，但随后删除昨天的午餐。',
])
@pytest.mark.asyncio
async def test_positive_writes_cannot_be_laundered_as_read(message,monkeypatch):
    raw=IntentRouteDecision(intent_domain='nutrition',request_kind='query',requested_effect='read',requested_output='answer',read_targets=['nutrition_history'],decision_action=None,artifact_action=None,normalized_request=message,risk_level='low',confidence=.99)
    monkeypatch.setattr(agent_intent_model,'_invoke_model_route',AsyncMock(return_value=raw))
    result=await resolve_intent_with_fallback(message,use_model=True)
    assert result.understanding_failed and not route_tools(result.resolution)

@pytest.mark.parametrize('prefix',['别','请别','先别','不要','不用','暂不'])
def test_real_negative_commands_remain_read(prefix):
    assert resolve_intent(prefix+'修改计划，请查看训练记录。').requested_effect=='read'

def evidence(id='CW01-r1'):
    r=row(id);trace=AgentExecutionTrace.model_validate(r['trace']);messages=[]
    for t in r['tools']:
        action=next(a for a in trace.actions if a.tool_id==t['tool'])
        messages.append(ToolMessage(content=t['raw_content'],tool_call_id=action.call_id,name=t['tool'].replace('.','_'),status='success'))
    return r,trace,{'messages':messages}

def history_data(result):
    message=next(m for m in result['messages'] if m.name=='workout_list_history')
    return message,json.loads(message.content)

def replace_history(trace,result,update,*,rebind=True):
    message,data=history_data(result);update(data);message.content=json.dumps(data,ensure_ascii=False)
    if rebind:
        for obs in trace.observations:
            if obs.call_id==message.tool_call_id:obs.result_fingerprint=observation_fingerprint(message.content)

@pytest.mark.parametrize('id',['CW01-r1','CW01-r2'])
def test_baseline_complete_history_can_prove_count_without_progress(id):
    r,trace,result=evidence(id);before=copy.deepcopy(result)
    reply,checked=guard_read_completion('本周已完成2次训练，对应1个计划完成日。',trace,result=result,query=r['message'])
    assert reply.startswith('本周已完成2次')
    assert checked.evidence_contract.status=='complete'
    assert 'workout.get_progress' in checked.evidence_contract.required_tools
    assert all(o.tool_id!='workout.get_progress' for o in checked.observations)
    proof=checked.evidence_contract.substitutions[0]
    assert proof.required_tool=='workout.get_progress' and proof.session_count==2
    assert proof.range_start=='2026-10-05' and proof.range_end=='2026-10-09'
    assert proof.statuses==['completed']
    assert result==before

@pytest.mark.parametrize('change',[
    lambda d:d.update(truncated=True),lambda d:d.update(total_count=3),
    lambda d:d.update(total_count=True),lambda d:d.update(count=1),
    lambda d:d.update(range_start='2026-10-06'),lambda d:d.update(range_end='2026-10-08'),
    lambda d:d.update(as_of='2026-10-10'),lambda d:d.update(timezone='UTC'),
    lambda d:d.update(status_filter=['skipped']),lambda d:d.pop('truncated'),
    lambda d:d['sessions'][0].update(trained_at='2026-10-11'),
    lambda d:d['sessions'][0].update(id=d['sessions'][1]['id']),
    lambda d:d['sessions'][0].update(status='invented'),
])
def test_partial_wrong_scope_or_invalid_history_never_satisfies_count(change):
    r,trace,result=evidence();replace_history(trace,result,change)
    reply,checked=guard_read_completion('声称完成',trace,result=result,query=r['message'])
    assert checked.evidence_contract.status=='partial' and 'workout.get_progress' in checked.evidence_contract.missing_tools
    assert reply!='声称完成'

@pytest.mark.parametrize('suffix',['另外给出容量趋势。','比较每周平均次数。','判断训练强度。','计算计划完成率。','按动作统计卧推组数。','列出个人最好成绩。'])
def test_other_progress_obligations_are_not_replaced_by_session_count(suffix):
    r,trace,result=evidence()
    _,checked=guard_read_completion('不完整答案',trace,result=result,query=r['message']+suffix)
    assert 'workout.get_progress' in checked.evidence_contract.missing_tools

@pytest.mark.parametrize('kind',['fingerprint','call_id','status','args','missing_plan'])
def test_substitution_requires_actual_successful_matching_observation(kind):
    r,trace,result=evidence();message,data=history_data(result)
    if kind=='fingerprint':replace_history(trace,result,lambda d:d.update(total_count=99),rebind=False)
    elif kind=='call_id':message.tool_call_id='foreign-call'
    elif kind=='status':message.status='error'
    elif kind=='args':next(a for a in trace.actions if a.tool_id=='workout.list_history').arguments['start_date']='2026-10-06'
    else:trace.observations=[o for o in trace.observations if o.tool_id!='plan.get_active']
    reply,checked=guard_read_completion('声称完成',trace,result=result,query=r['message'])
    assert checked.evidence_contract.status=='partial' and reply!='声称完成'

@pytest.mark.asyncio
@pytest.mark.parametrize('risk',['low','medium'])
async def test_baseline_http_query_contract_is_independent_of_prefetch(monkeypatch,risk):
    from test_round50_provider_evidence import install_http
    from test_round49_required_evidence import resolution
    calls,requests=install_http(monkeypatch)
    r=resolution(request_kind='query',risk_level=risk,intent_domain='workout_plan',evidence_requirements=['active_plan','health_screening','nutrition_history'])
    result=await runtime.invoke_langchain_agent(None,user_id='synthetic-owner',history=[],user_message='已有伤病记录，请结合资料给一周临时训练建议，最多15分钟，只要建议。',tool_allowlist=['plan.get_active','health.get_screening_summary','nutrition.list_history'],resolution=r)
    assert len(requests)==2
    assert calls==[('nutrition.list_history',{'start_date':'2026-10-08','end_date':'2026-10-08'})], 'Projection must not add prefetch authority'
    assert '按当日余力选择负重' in requests[0]['messages'][0]['content']
    sent=[json.loads(m['content']) for m in requests[-1]['messages'] if m['role']=='tool']
    assert all(d.get('assessment_evidence_version')==1 for d in sent)
    assert all('assessment_evidence_version' not in json.loads(m.content) for m in result['messages'] if isinstance(m,ToolMessage))

@pytest.mark.asyncio
async def test_real_graph_trace_accepts_equivalent_history_without_fake_progress(monkeypatch):
    from langchain_core.messages import AIMessage
    from langchain_core.tools import StructuredTool
    from app.services.agent_tools import NoArguments,WorkoutHistoryArguments
    from app.services.agent_intent import IntentResolution
    from app.services.agent_trace import complete_execution_trace
    from test_round49_required_evidence import Model
    r,trace,_=evidence();calls=[]
    data={t['tool']:json.loads(t['raw_content']) for t in r['tools']}
    async def history(**args):calls.append(('history',args));return data['workout.list_history']
    async def plan():calls.append(('plan',{}));return data['plan.get_active']
    tools=[StructuredTool.from_function(coroutine=history,name='workout_list_history',description='Synthetic history',args_schema=WorkoutHistoryArguments),StructuredTool.from_function(coroutine=plan,name='plan_get_active',description='Synthetic plan',args_schema=NoArguments)]
    monkeypatch.setattr(runtime,'build_read_tools',lambda *args,**kwargs:tools)
    actions=[{'id':'real-history','name':'workout_list_history','args':trace.actions[0].arguments},{'id':'real-plan','name':'plan_get_active','args':{}}]
    monkeypatch.setattr(runtime,'_build_model',lambda:Model(responses=[AIMessage(content='',tool_calls=actions),AIMessage(content='截至10月9日本周完成2次，对应计划1日。')]))
    resolution=IntentResolution(primary_intent='workout_progress_query',intent_domain='workout_progress',request_kind='query',requested_effect='read',risk_level='low',confidence=.99,resolved_query=r['message'],evidence_requirements=['workout_history','workout_progress','active_plan'])
    result=await runtime.invoke_langchain_agent(None,user_id='synthetic-owner',history=[],user_message=r['message'],tool_allowlist=trace.evidence_contract.required_tools,resolution=resolution)
    trace=trace.model_copy(update={'actions':[],'observations':[],'termination_reason':None})
    trace=complete_execution_trace(trace,result,summarize_observation=runtime._audit_result_summary)
    reply,checked=guard_read_completion(result['messages'][-1].content,trace,result=result,query=r['message'])
    assert checked.evidence_contract.status=='complete' and reply.startswith('截至10月9日')
    assert len(calls)==len(checked.actions)==len(checked.observations)==2
    assert checked.evidence_contract.substitutions[0].session_count==2
