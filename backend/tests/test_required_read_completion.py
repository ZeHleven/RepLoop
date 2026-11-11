"""Synthetic omission replay; the production phone tool trace was not supplied."""
import json
import os
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import PrivateAttr

from app.schemas.agent_planning import ExecutorDecision, FinalResponse, MicroPlan
from app.services import agent_runtime as runtime
from app.services.agent_controller import execute_planned_agent
from app.services.agent_evidence_contract import build_evidence_contract, guard_read_completion
from app.services.agent_intent import IntentResolution
from app.services.agent_tools import NoArguments
from app.services.agent_trace import build_initial_execution_trace, complete_execution_trace

MESSAGES = [
    '给我下一次训练的准备建议',
    '结合我已有的计划，给下一次训练的准备建议，先不要修改。',
]
ALLOWLIST = ['plan.get_active', 'workout.get_next']
PLAN = {'found': True, 'plan': {'id':'synthetic-plan', 'name':'合成测试计划',
    'exercises':[{'exercise_name':'合成动作甲','sets':3,'reps':'8-10','day_of_week':3}]}}
NEXT = {'found': True, 'plan_id':'synthetic-plan','plan_name':'合成测试计划',
    'day_of_week':3,'exercises':PLAN['plan']['exercises']}


def resolution(message):
    return IntentResolution(primary_intent='next_workout_query', request_kind='query',
        confidence=1, resolved_query=message, evidence_requirements=['active_plan','next_workout'],
        subtasks=['读取现有计划和下一练','给出准备建议'])


def trace_for(message, allowlist=ALLOWLIST, budget=4):
    intent = resolution(message)
    return build_initial_execution_trace(intent, allowlist).model_copy(update={
        'evidence_contract':build_evidence_contract(intent, allowlist, budget=budget)})


def read_tools(calls, *, plan_result=PLAN, plan_error=None):
    @tool('plan_get_active', args_schema=NoArguments, description='Read the active plan.')
    async def plan():
        calls.append('plan.get_active')
        if plan_error:
            raise plan_error
        return plan_result

    @tool('workout_get_next', args_schema=NoArguments, description='Read the next workout.')
    async def next_workout():
        calls.append('workout.get_next')
        return NEXT
    return [plan, next_workout]


class CapturingModel(FakeMessagesListChatModel):
    _inputs: list = PrivateAttr(default_factory=list)
    def bind_tools(self, tools, **kwargs):
        return self
    def _generate(self, messages, *args, **kwargs):
        self._inputs.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


def omitted_model():
    return CapturingModel(responses=[
        AIMessage(content='', tool_calls=[{'name':'workout_get_next','args':{},'id':'synthetic-next'}]),
        AIMessage(content='仅凭下一练准备回答。'),
        AIMessage(content='根据合成测试计划，提前确认动作安排和所需器械。'),
    ])


def retain(name, payload):
    folder=os.getenv('PREP_EVIDENCE_DIR')
    if folder:
        target=Path(folder)/f'{name}.json'
        assert not target.exists(), 'Keep previous replay evidence.'
        target.write_text(json.dumps({'evidence_kind':'synthetic_model_replay_not_phone_trace',
            **payload},ensure_ascii=False,indent=2,default=str),encoding='utf-8')


@pytest.mark.asyncio
@pytest.mark.parametrize('message', MESSAGES, ids=['phone-short','planned-long'])
async def test_direct_omitted_plan_is_read_before_answer(monkeypatch, message):
    calls=[]; model=omitted_model(); tools=read_tools(calls)
    monkeypatch.setattr(runtime,'_build_model',lambda **kwargs:model)
    monkeypatch.setattr(runtime,'build_read_tools',lambda *args,**kwargs:tools)
    result=await runtime.invoke_langchain_agent(None,user_id='synthetic',history=[],
        user_message=message,tool_allowlist=ALLOWLIST,resolution=resolution(message))
    trace=complete_execution_trace(trace_for(message),result,summarize_observation=runtime._audit_result_summary)
    reply,guarded=guard_read_completion(result['messages'][-1].content,trace)
    retain('direct-'+str(MESSAGES.index(message)),{'message':message,'calls':calls,
        'messages':[m.model_dump(mode='json') for m in result['messages']],
        'trace':guarded.model_dump(mode='json'),'reply':reply})
    assert calls == ['workout.get_next','plan.get_active']
    assert guarded.evidence_contract.status == 'complete'
    assert reply == '根据合成测试计划，提前确认动作安排和所需器械。'
    assert trace.budget_usage.model_calls == 3
    assert trace.budget_usage.tool_calls == 2
    assert any(isinstance(m,ToolMessage) and '合成测试计划' in str(m.content)
               and m.name=='plan_get_active' for m in model._inputs[-1])
    assert len(runtime._tool_call_records(result,'synthetic-run')) == 2


class OmittedPlanPolicy:
    def __init__(self):
        self.final_inputs=[]
    async def create_plan(self, **kwargs):
        return MicroPlan.model_validate({'goal':kwargs['goal'],'steps':[{
            'objective':'读取下一练','candidate_tools':['workout.get_next'],
            'execution_strategy':'direct','completion_policy':'after_successful_observation',
            'planned_actions':[],'success_signal':'下一练已返回'}]})
    async def decide_step(self, **kwargs):
        return ExecutorDecision(decision='call_tool',tool_id='workout.get_next',arguments={},reason='读取下一练')
    async def finalize(self, **kwargs):
        self.final_inputs.append(kwargs)
        return FinalResponse(terminal_action='answer',reply='根据合成测试计划准备器械。')


@pytest.mark.asyncio
@pytest.mark.parametrize('message', MESSAGES, ids=['phone-short','planned-long'])
async def test_planned_omitted_plan_reaches_finalizer_and_audit(message):
    calls=[]; audits=[]; policy=OmittedPlanPolicy()
    async def sink(trace,audit):
        if audit: audits.append(audit)
    result=await execute_planned_agent(db=None,user_id='synthetic',run_id='synthetic-run',
        model=None,goal=message,subtasks=resolution(message).subtasks,
        tool_allowlist=ALLOWLIST,initial_trace=trace_for(message),tools=read_tools(calls),
        policy=policy,event_sink=sink,summarize_observation=runtime._audit_result_summary)
    reply,guarded=guard_read_completion(result.reply,result.execution_trace)
    retain('planned-'+str(MESSAGES.index(message)),{'message':message,'calls':calls,
        'finalizer_inputs':policy.final_inputs,'trace':guarded.model_dump(mode='json'),
        'reply':reply,'audits':[a.__dict__ for a in audits]})
    assert calls == ['workout.get_next','plan.get_active']
    assert guarded.evidence_contract.status == 'complete'
    assert [o['tool_id'] for o in policy.final_inputs[-1]['observations']] == calls
    assert [a.tool_id for a in audits] == calls
    assert guarded.budget_usage.model_calls == 3  # planner, executor, finalizer
    assert guarded.budget_usage.tool_calls == 2


def partial_result(*, plan_status=None):
    messages=[AIMessage(content='',tool_calls=[
        {'name':'workout_get_next','args':{},'id':'next'}]),
        ToolMessage(name='workout_get_next',tool_call_id='next',content=json.dumps(NEXT))]
    if plan_status:
        messages.extend([AIMessage(content='',tool_calls=[
            {'name':'plan_get_active','args':{},'id':'plan'}]),
            ToolMessage(name='plan_get_active',tool_call_id='plan',status=plan_status,
                content=json.dumps(PLAN if plan_status=='success' else {'error':'synthetic failure'}))])
    messages.append(AIMessage(content='原答复'))
    return {'messages':messages}


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['complete','failed','model-budget','tool-budget',
    'authority','missing-tool','clarification','high-risk','mutation','parameterized'])
async def test_direct_completion_does_not_expand_scope_or_retry(monkeypatch,case):
    calls=[]; tools=read_tools(calls); model=CapturingModel(responses=[AIMessage(content='不应调用')])
    intent=resolution(MESSAGES[0]); allowlist=list(ALLOWLIST)
    result=partial_result(plan_status={'complete':'success','failed':'error'}.get(case))
    if case=='model-budget': monkeypatch.setattr(runtime.settings,'AGENT_MAX_MODEL_CALLS',2)
    if case=='tool-budget': monkeypatch.setattr(runtime.settings,'AGENT_MAX_TOOL_CALLS',1)
    if case=='authority': allowlist=['workout.get_next']
    if case=='missing-tool': tools=tools[1:]
    if case=='clarification': intent=intent.model_copy(update={'clarification_required':True})
    if case=='high-risk': intent=intent.model_copy(update={'risk_level':'high'})
    if case=='mutation': intent=intent.model_copy(update={'request_kind':'mutation'})
    if case=='parameterized':
        intent=intent.model_copy(update={'evidence_requirements':['active_plan','workout_history']})
        allowlist=['plan.get_active','workout.list_history']
        tools=runtime.build_read_tools(None,user_id='synthetic',allowlist=allowlist)
    actual=await runtime._complete_direct_required_reads(result,db=None,model=model,tools=tools,
        resolution=intent,tool_allowlist=allowlist)
    assert actual is result
    assert calls == [] and model._inputs == []


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [TimeoutError('synthetic timeout'),ValueError('synthetic failure')],
    ids=['timeout','tool-error'])
async def test_failed_completion_is_audited_not_retried_or_shown_as_data(error):
    calls=[]; model=CapturingModel(responses=[AIMessage(content='不应调用')])
    result=await runtime._complete_direct_required_reads(partial_result(),db=None,model=model,
        tools=read_tools(calls,plan_error=error),resolution=resolution(MESSAGES[0]),tool_allowlist=ALLOWLIST)
    trace=complete_execution_trace(trace_for(MESSAGES[0]),result,summarize_observation=runtime._audit_result_summary)
    reply,trace=guard_read_completion(result['messages'][-1].content,trace)
    assert calls == ['plan.get_active'] and model._inputs == []
    assert trace.evidence_contract.missing_tools == ['plan.get_active']
    assert trace.observations[-1].status == 'error'
    assert trace.budget_usage.model_calls == 2 and trace.budget_usage.tool_calls == 2
    assert '还没有完整取得当前计划' in reply
    assert runtime._tool_call_records(result,'synthetic')[-1].status == 'failed'
    assert [c['type'] for c in runtime._extract_agent_output(result)[1]] == ['workout.get_next']


@pytest.mark.asyncio
async def test_no_active_plan_is_a_valid_negative_observation():
    calls=[]; model=CapturingModel(responses=[AIMessage(content='没有启用的训练计划，请先选择计划。')])
    result=await runtime._complete_direct_required_reads(partial_result(),db=None,model=model,
        tools=read_tools(calls,plan_result={'found':False}),resolution=resolution(MESSAGES[0]),tool_allowlist=ALLOWLIST)
    trace=complete_execution_trace(trace_for(MESSAGES[0]),result,summarize_observation=runtime._audit_result_summary)
    reply,trace=guard_read_completion(result['messages'][-1].content,trace)
    assert trace.evidence_contract.status == 'complete'
    assert reply == '没有启用的训练计划，请先选择计划。'
    assert json.loads([m for m in model._inputs[-1] if isinstance(m,ToolMessage)][-1].content)=={'found':False}
    assert len(model._inputs)==1 and calls==['plan.get_active']


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [AIMessage(content=''),AIMessage(content='',tool_calls=[
    {'name':'plan_get_active','args':{},'id':'unexpected'}])],ids=['empty','attempted-tool'])
async def test_answer_only_rewrite_is_bounded_on_protocol_error(response):
    calls=[]; model=CapturingModel(responses=[response])
    result=await runtime._complete_direct_required_reads(partial_result(),db=None,model=model,
        tools=read_tools(calls),resolution=resolution(MESSAGES[0]),tool_allowlist=ALLOWLIST)
    trace=complete_execution_trace(trace_for(MESSAGES[0]),result,summarize_observation=runtime._audit_result_summary)
    assert calls==['plan.get_active'] and len(model._inputs)==1
    assert trace.termination_reason=='required_read_answer_unavailable'
    assert trace.budget_usage.model_calls==3
    assert '未能完成回答' in result['messages'][-1].content
    assert not getattr(result['messages'][-1],'tool_calls',None)


@pytest.mark.asyncio
async def test_completion_failure_rolls_back_only_its_savepoint(db_session):
    from sqlalchemy import text
    @tool('plan_get_active',args_schema=NoArguments,description='Synthetic failed SELECT.')
    async def broken_plan():
        await db_session.execute(text('SELECT 1 / 0'))
    result=await runtime._complete_direct_required_reads(partial_result(),db=db_session,
        model=CapturingModel(responses=[AIMessage(content='不应调用')]),tools=[broken_plan],
        resolution=resolution(MESSAGES[0]),tool_allowlist=ALLOWLIST)
    assert await db_session.scalar(text('SELECT 1')) == 1
    assert runtime._tool_call_records(result,'synthetic')[-1].status=='failed'
    assert '还没有完整取得' in result['messages'][-1].content


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal',['clarify','safe_stop'])
async def test_planned_stop_never_completes_reads(terminal):
    class StopPolicy(OmittedPlanPolicy):
        async def decide_step(self,**kwargs):
            return ExecutorDecision(decision=terminal,reason='需要停止',message='请先确认情况。',
                missing_slot='所需信息' if terminal=='clarify' else None)
    calls=[]; policy=StopPolicy()
    result=await execute_planned_agent(db=None,user_id='synthetic',run_id='synthetic-stop',
        model=None,goal=MESSAGES[0],subtasks=resolution(MESSAGES[0]).subtasks,
        tool_allowlist=ALLOWLIST,initial_trace=trace_for(MESSAGES[0]),tools=read_tools(calls),
        policy=policy,summarize_observation=runtime._audit_result_summary)
    assert calls == [] and policy.final_inputs==[]
    assert result.execution_trace.terminal_action==terminal


@pytest.mark.asyncio
async def test_api_persists_both_reads_without_mutating_either_users_plan(client,db_session,monkeypatch):
    from unittest.mock import AsyncMock
    from sqlalchemy import select,func
    from app.models.user import User
    from app.models.exercise import Exercise
    from app.models.workout import WorkoutPlan,PlannedExercise,WorkoutSession
    from app.models.agent import AgentToolCall,AgentProposal,AgentRun
    from app.services.agent_intent import IntentResolverOutcome
    account=await client.seed_existing_account(json={'email':'prep-owner@example.com','password':'test-password'})
    headers={'Authorization':f"Bearer {account.json()['access_token']}"}
    owner=await db_session.scalar(select(User).where(User.email=='prep-owner@example.com'))
    other=User(id='prep-other',email='prep-other@example.com',password_hash='not-a-login')
    exercise=Exercise(id='prep-exercise',name_zh='合成动作甲',name_en='Synthetic A',category='strength',difficulty='beginner')
    db_session.add_all([other,exercise]); await db_session.flush()
    own_plan=WorkoutPlan(id='prep-own-plan',user_id=owner.id,name='合成测试计划')
    other_plan=WorkoutPlan(id='prep-other-plan',user_id=other.id,name='其他用户的合成计划')
    db_session.add_all([own_plan,other_plan]); await db_session.flush()
    db_session.add(PlannedExercise(id='prep-own-exercise',plan_id=own_plan.id,
        exercise_id=exercise.id,day_of_week=3,sets=3,reps='8-10'))
    await db_session.commit()
    async def business_snapshot():
        return {'plans':[tuple(row) for row in (await db_session.execute(select(
            WorkoutPlan.id,WorkoutPlan.user_id,WorkoutPlan.name,WorkoutPlan.is_active).order_by(WorkoutPlan.id))).all()],
            'exercises':[tuple(row) for row in (await db_session.execute(select(
                PlannedExercise.id,PlannedExercise.sets,PlannedExercise.reps).order_by(PlannedExercise.id))).all()],
            'proposals':await db_session.scalar(select(func.count()).select_from(AgentProposal)),
            'sessions':await db_session.scalar(select(func.count()).select_from(WorkoutSession))}
    before=await business_snapshot()
    intent=resolution(MESSAGES[0]).model_copy(update={'subtasks':[]})  # exercise the direct runtime path
    monkeypatch.setattr(runtime,'resolve_intent_with_fallback',AsyncMock(return_value=
        IntentResolverOutcome(resolution=intent,source='model',attempt_count=1)))
    model=omitted_model(); monkeypatch.setattr(runtime,'_build_model',lambda **kwargs:model)
    response=await client.post('/api/v1/agent/chat',headers=headers,json={'message':MESSAGES[0]})
    assert response.status_code==200,response.text
    body=response.json(); run=await db_session.get(AgentRun,body['run_id'])
    calls=(await db_session.scalars(select(AgentToolCall).where(AgentToolCall.run_id==run.id))).all()
    retain('api-sql',{'response':body,'trace':run.execution_trace,
        'tools':[{'name':c.tool_name,'status':c.status,'args':c.arguments_data,'summary':c.result_data} for c in calls],
        'before':before,'after':await business_snapshot()})
    assert run.execution_trace['evidence_contract']['status']=='complete'
    assert {c.tool_name for c in calls}==set(ALLOWLIST) and len(calls)==2
    plan_card=next(c for c in body['cards'] if c['type']=='plan.get_active')
    assert plan_card['data']['plan']['id']==own_plan.id
    assert '其他用户' not in json.dumps(body,ensure_ascii=False)
    assert before == await business_snapshot()
    assert len(model._inputs)==3


@pytest.mark.asyncio
@pytest.mark.parametrize('provider_failure', [False, True], ids=['thinking-contract', 'provider-400'])
async def test_rewrite_http_contract_and_billing(db_session, session_factory, monkeypatch, provider_failure):
    """Real SDK serialization and budget transport; synthetic HTTP, never a live model."""
    import httpx
    from uuid import uuid4
    from sqlalchemy import select
    from langchain_core.messages import HumanMessage
    from app.models.user import User
    from app.models.model_budget import ModelCharge
    from app.services import model_budget as budget

    monkeypatch.setattr(budget, 'AsyncSessionLocal', session_factory)
    monkeypatch.setattr(runtime.settings, 'DEEPSEEK_API_KEY', 'synthetic-wire-test')
    monkeypatch.setattr(runtime.settings, 'DEEPSEEK_BASE_URL', 'https://api.deepseek.com')
    monkeypatch.setattr(runtime.settings, 'AGENT_ENABLED', True)
    user = User(email=f'prep-wire-{uuid4()}@example.com', password_hash='synthetic')
    db_session.add(user)
    await db_session.commit()
    requests = []
    statuses = []

    def provider(request):
        payload = json.loads(request.content)
        requests.append(payload)
        # The controller's synthetic assistant envelope has no model reasoning
        # to replay. Reproduce the actual provider rejection at the wire edge.
        if provider_failure or payload.get('thinking', {}).get('type') != 'disabled':
            statuses.append(400)
            return httpx.Response(400, json={'error': {
                'type': 'invalid_request_error',
                'message': 'The `reasoning_content` in the thinking mode must be passed back to the API.'}})
        statuses.append(200)
        return httpx.Response(200, json={
            'id': 'synthetic-rewrite', 'object': 'chat.completion', 'created': 1,
            'model': 'deepseek-flash', 'choices': [{'index': 0,
                'message': {'role': 'assistant', 'content': '依据完整计划准备器械。'},
                'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}})

    calls = []
    original = partial_result()
    original['messages'].insert(0, HumanMessage(content=MESSAGES[1]))
    transport = budget.BudgetTransport(httpx.MockTransport(provider))
    async with httpx.AsyncClient(transport=transport) as client:
        monkeypatch.setattr(runtime, 'model_http_client', lambda: client)
        with budget.billing_user(user.id):
            result = await runtime._complete_direct_required_reads(original,
                db=None, model=runtime._build_model(max_tokens=128), tools=read_tools(calls),
                resolution=resolution(MESSAGES[1]), tool_allowlist=ALLOWLIST)
    trace = complete_execution_trace(trace_for(MESSAGES[1]), result,
        summarize_observation=runtime._audit_result_summary)
    _, trace = guard_read_completion(result['messages'][-1].content, trace)
    state = await budget.budget_status(user.id)
    async with session_factory() as db:
        charges = (await db.scalars(select(ModelCharge).where(ModelCharge.user_id == user.id))).all()
    assert len(charges) == 1
    retain('rewrite-wire-' + str(provider_failure), {'requests': requests, 'statuses': statuses,
        'reply': result['messages'][-1].content, 'trace': trace.model_dump(mode='json'),
        'budget': state})
    assert calls == ['plan.get_active'] and len(requests) == 1
    assert 'tools' not in requests[0] and 'tool_choice' not in requests[0]
    assert trace.evidence_contract.status == 'complete'
    assert trace.budget_usage.model_calls == 3 and trace.budget_usage.tool_calls == 2
    assert len(runtime._tool_call_records(result, 'synthetic')) == 2
    observations = [json.loads(m['content']) for m in requests[0]['messages'] if m['role'] == 'tool']
    assert observations == [NEXT, PLAN]
    assert result['messages'][:len(original['messages'])] == original['messages']
    if provider_failure:
        assert statuses == [400]
        assert trace.termination_reason == 'required_read_answer_unavailable'
        assert '未能完成回答' in result['messages'][-1].content
        assert charges[0].status == 'http_error_estimate' and charges[0].actual_nano is None
        assert float(state['spent_yuan']) == charges[0].reserved_nano / 1e9
    else:
        assert statuses == [200]
        assert requests[0]['thinking'] == {'type': 'disabled'}
        assert result['messages'][-1].content == '依据完整计划准备器械。'
        assert state['spent_yuan'] == '0.000360000' and not state['blocked']
