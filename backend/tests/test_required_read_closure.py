"""Replay observed omissions; synthetic model outputs, actual audited execution."""
import json
import os
from datetime import date
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import PrivateAttr

from app.services import agent_runtime as runtime
from app.services.agent_controller import complete_required_reads, execute_planned_agent
from app.services.agent_evidence_contract import build_evidence_contract, guard_read_completion
from app.services.agent_required_read_arguments import validate_read_arguments
from app.services.agent_intent import IntentResolution
from app.services.agent_tools import NoArguments, WorkoutHistoryArguments, TOOL_ID_BY_LANGCHAIN_NAME
from app.services.agent_trace import build_initial_execution_trace, complete_execution_trace
from app.schemas.agent_planning import ExecutorDecision, FinalResponse, MicroPlan

CASES = json.loads((Path(__file__).parent/'fixtures/required_read_omissions.json').read_text(encoding='utf-8'))['cases']
ALLOWLIST = ['workout.get_next','workout.list_history','plan.get_active']
HISTORY = {'count':1,'sessions':[{'trained_at':'2026-11-09','status':'completed','duration_min':30}]}
ANSWER = '已核对计划和历史。休息秒数是计划目标；本次按实际呼吸和动作控制准备。'


class CapturingModel(FakeMessagesListChatModel):
    _inputs: list = PrivateAttr(default_factory=list)
    _bindings: list = PrivateAttr(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        self._bindings.append(([t.name for t in tools], kwargs))
        return self

    def _generate(self, messages, *args, **kwargs):
        self._inputs.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


def intent_for(case):
    return IntentResolution(primary_intent='next_workout_query', intent_domain='workout_session',
        request_kind='query', requested_effect='read', confidence=.9,
        resolved_query=case['resolved_query'], evidence_requirements=case['evidence_requirements'])


def initial(intent, allowlist=ALLOWLIST):
    return build_initial_execution_trace(intent, allowlist).model_copy(update={
        'evidence_contract':build_evidence_contract(intent,allowlist,budget=runtime.settings.AGENT_MAX_TOOL_CALLS)})


def reads(calls, *, history=HISTORY, fail_history=False):
    @tool('plan_get_active',args_schema=NoArguments,description='Read active plan.')
    async def plan():
        calls.append(('plan.get_active',{}))
        return {'found':True,'plan':{'id':'own-plan','name':'合成计划'}}

    @tool('workout_get_next',args_schema=NoArguments,description='Read next workout.')
    async def next_workout():
        calls.append(('workout.get_next',{}))
        return {'found':True,'plan_id':'own-plan','day_of_week':1,'exercises':[
            {'exercise_name':'卧推','rest_seconds':45},{'exercise_name':'深蹲','rest_seconds':150}]}

    @tool('workout_list_history',args_schema=WorkoutHistoryArguments,description='Read recent or calendar history.')
    async def history_read(limit=5,start_date='',end_date='',completed_only=False,statuses=None):
        calls.append(('workout.list_history',dict(limit=limit,start_date=start_date,end_date=end_date,
                                                completed_only=completed_only,statuses=statuses or [])))
        if fail_history:
            raise TimeoutError('synthetic history timeout')
        return history
    return [next_workout,history_read,plan]


def omitted_call(case):
    return AIMessage(content='',tool_calls=[{'name':t.replace('.','_'),'args':{},'id':f'observed-{i}'}
        for i,t in enumerate(case['observed_tools'])])


def binding_response(arguments=None, name='workout_list_history'):
    return AIMessage(content='',tool_calls=[{'name':name,'args':{'limit':5} if arguments is None else arguments,'id':'binding'}])


def extract(intent,result):
    trace=complete_execution_trace(initial(intent),result,summarize_observation=runtime._audit_result_summary)
    return guard_read_completion(result['messages'][-1].content,trace)


@pytest.mark.asyncio
@pytest.mark.parametrize('case',CASES,ids=lambda x:x['case_id'])
async def test_recorded_omission_delivers_after_actual_reads(monkeypatch,case):
    calls=[]; tools=reads(calls); intent=intent_for(case)
    model=CapturingModel(responses=[omitted_call(case),AIMessage(content=case['undelivered_draft']),
        binding_response(),AIMessage(content=ANSWER)])
    monkeypatch.setattr(runtime,'_build_model',lambda **kwargs:model)
    monkeypatch.setattr(runtime,'build_read_tools',lambda *a,**kw:tools)
    result=await runtime.invoke_langchain_agent(None,user_id='synthetic',history=[],
        user_message=case['message'],tool_allowlist=ALLOWLIST,resolution=intent,resolved_query=intent.resolved_query)
    reply,trace=extract(intent,result)
    if folder:=os.getenv('READ_CLOSURE_EVIDENCE_DIR'):
        (Path(folder)/f"{case['case_id']}-replay.json").write_text(json.dumps({
            'kind':'captured_omission_with_synthetic_binding_and_answer_no_live_model',
            'case':case['case_id'],'calls':calls,'reply':reply,'trace':trace.model_dump(mode='json'),
            'messages':[m.model_dump(mode='json') for m in result['messages']]},ensure_ascii=False,indent=2),encoding='utf-8')
    assert reply==ANSWER and trace.evidence_contract.status=='complete'
    assert {name for name,_ in calls}==set(ALLOWLIST)
    assert len(calls)==3
    assert trace.budget_usage.tool_calls==3 and trace.budget_usage.model_calls==4
    assert model._bindings[-1]==(['workout_list_history'],{'tool_choice':'workout_list_history'})
    audits=runtime._tool_call_records(result,'synthetic')
    history_audit=next(a for a in audits if a.tool_name=='workout.list_history')
    assert history_audit.arguments_data['limit']==5
    assert all(a.status=='completed' for a in audits)
    final_tools=[m for m in model._inputs[-1] if isinstance(m,ToolMessage)]
    assert {m.name for m in final_tools}=={t.replace('.','_') for t in ALLOWLIST}
    assert json.loads(next(m.content for m in final_tools if m.name=='workout_list_history'))==HISTORY
    binding_tools = [m.name for m in model._inputs[-2] if isinstance(m, ToolMessage)]
    assert 'plan_get_active' in binding_tools


class OmitPolicy:
    def __init__(self): self.final=[]
    async def create_plan(self,**kwargs):
        return MicroPlan.model_validate({'goal':kwargs['goal'],'steps':[{
            'objective':'读取下一练','candidate_tools':['workout.get_next'],'execution_strategy':'direct',
            'completion_policy':'after_successful_observation','planned_actions':[],'success_signal':'取得下一练'}]})
    async def decide_step(self,**kwargs):
        return ExecutorDecision(decision='call_tool',tool_id='workout.get_next',arguments={},reason='下一练')
    async def finalize(self,**kwargs):
        self.final.append(kwargs)
        return FinalResponse(terminal_action='answer',reply=ANSWER)


@pytest.mark.asyncio
async def test_planned_completion_has_same_arguments_audits_and_budget():
    calls=[]; audits=[]; policy=OmitPolicy(); intent=intent_for(CASES[1])
    async def sink(trace,audit):
        if audit: audits.append(audit)
    result=await execute_planned_agent(db=None,user_id='synthetic',run_id='synthetic',
        model=CapturingModel(responses=[binding_response()]),goal=intent.resolved_query,subtasks=[],
        tool_allowlist=ALLOWLIST,initial_trace=initial(intent),tools=reads(calls),policy=policy,event_sink=sink,
        summarize_observation=runtime._audit_result_summary)
    reply,trace=guard_read_completion(result.reply,result.execution_trace)
    assert reply==ANSWER and trace.evidence_contract.status=='complete'
    assert [n for n,_ in calls]==['workout.get_next','plan.get_active','workout.list_history']
    assert trace.budget_usage.model_calls==4 and trace.budget_usage.tool_calls==3
    assert len(audits)==3 and audits[-1].arguments['limit']==5
    assert policy.final[-1]['observations'][-1]['arguments']['limit']==5
    assert policy.final[-1]['observations'][-1]['result']==HISTORY


@pytest.mark.asyncio
@pytest.mark.parametrize('history_state',['unbound','failed','unavailable'])
async def test_unrecoverable_history_does_not_suppress_plan(history_state):
    calls=[]; intent=intent_for(CASES[1]); tools=reads(calls)
    result={'messages':[AIMessage(content='draft')]}
    if history_state=='failed':
        result['messages']=[binding_response(),ToolMessage(content='failed',status='error',
            name='workout_list_history',tool_call_id='binding'),AIMessage(content='draft')]
    trace=complete_execution_trace(initial(intent),result,summarize_observation=runtime._audit_result_summary)
    mapping={TOOL_ID_BY_LANGCHAIN_NAME[t.name]:t for t in tools}
    if history_state=='unavailable': mapping.pop('workout.list_history')
    completed=await complete_required_reads(trace,tool_allowlist=ALLOWLIST,tools_by_id=mapping,
        summarize_observation=runtime._audit_result_summary)
    _,guarded=guard_read_completion('draft',completed.trace)
    assert [n for n,_ in calls]==['workout.get_next','plan.get_active']
    assert guarded.evidence_contract.missing_tools==['workout.list_history']


@pytest.mark.asyncio
@pytest.mark.parametrize('remaining',[0,1,2])
async def test_binding_reserves_final_answer_turn(monkeypatch,remaining):
    calls=[]; intent=intent_for(CASES[1]); tools=reads(calls)
    monkeypatch.setattr(runtime.settings,'AGENT_MAX_MODEL_CALLS',2+remaining)
    model=CapturingModel(responses=[omitted_call(CASES[1]),AIMessage(content='draft'),binding_response(),AIMessage(content=ANSWER)])
    monkeypatch.setattr(runtime,'_build_model',lambda **kw:model)
    monkeypatch.setattr(runtime,'build_read_tools',lambda *a,**kw:tools)
    result=await runtime.invoke_langchain_agent(None,user_id='synthetic',history=[],user_message=CASES[1]['message'],
        tool_allowlist=ALLOWLIST,resolution=intent)
    _,trace=extract(intent,result)
    assert trace.budget_usage.model_calls<=2+remaining
    assert len(model._inputs)==(4 if remaining==2 else 2)
    assert ('workout.list_history' in [n for n,_ in calls]) is (remaining==2)
    assert (trace.evidence_contract.status=='complete') is (remaining==2)


@pytest.mark.asyncio
@pytest.mark.parametrize('case', CASES, ids=lambda x:x['case_id'])
@pytest.mark.parametrize('reply',[AIMessage(content='skip'),binding_response({}),binding_response({'limit':21}),
    binding_response({'limit':5,'user_id':'other'}),binding_response(name='plan_get_active')],
    ids=['no-call','implicit-default','invalid-limit','foreign-user','wrong-tool'])
async def test_invalid_binding_never_invokes_history_or_answers(monkeypatch,reply,case):
    calls=[]; intent=intent_for(case); model=CapturingModel(responses=[omitted_call(case),AIMessage(content='draft'),reply])
    monkeypatch.setattr(runtime,'_build_model',lambda **kw:model)
    monkeypatch.setattr(runtime,'build_read_tools',lambda *a,**kw:reads(calls))
    result=await runtime.invoke_langchain_agent(None,user_id='synthetic',history=[],user_message=case['message'],
        tool_allowlist=ALLOWLIST,resolution=intent)
    answer,trace=extract(intent,result)
    assert [n for n,_ in calls]==['workout.get_next','plan.get_active']
    assert len(model._inputs)==3 and trace.budget_usage.model_calls==3
    assert trace.evidence_contract.missing_tools==['workout.list_history']
    assert '还没有完整取得训练历史' in answer
    assert trace.stage_timings[-1].status=='error'


@pytest.mark.parametrize('query,args,valid',[
    ('查询2026-11-01至2026-11-07已完成训练并给准备建议',{'limit':20,'start_date':'2026-11-01','end_date':'2026-11-07','completed_only':True},True),
    ('查询2026-11-01至2026-11-07已完成训练并给准备建议',{'limit':5},False),
    ('查询2026-11-01至2026-11-07已完成训练并给准备建议',{'limit':20,'start_date':'2026-11-01','end_date':'2026-11-08','completed_only':True},False),
    ('查询2026-11-01至2026-11-07已完成训练并给准备建议',{'limit':20,'start_date':'2026-11-01','end_date':'2026-11-07'},False),
    ('查询2026-11-01至2026-11-07的训练并给准备建议',{'limit':20,'start_date':'2026-11-01','end_date':'2026-11-07','statuses':['completed']},False),
    ('上次深蹲没缓过来，下次怎么准备',{'limit':5,'start_date':'2026-11-01','end_date':'2026-11-07'},False),
    ('列出最近3次训练',{'limit':3},True),('列出最近3次训练',{'limit':5},False),
    ('结合最近3次训练给下次准备建议',{'limit':5},False),
    ('结合最近21次训练给下次准备建议',{'limit':20},False),
    ('参考最近两周的训练给准备建议',{'limit':5},False),
    ('比较上周和本周的训练',{'limit':5},False),
    ('参考上个月的训练给建议',{'limit':5},False),
    ('参考去年训练给建议',{'limit':5},False),
    ('参考上周一训练给建议',{'limit':20,'start_date':'2026-11-09','end_date':'2026-11-15'},False),
    ('参考2026/11/01训练给建议',{'limit':5},False),
    ('查询2026-11-01和2026-11-07的训练',{'limit':20,'start_date':'2026-11-01','end_date':'2026-11-07'},False),
    ('查询前天已完成训练',{'limit':20,'start_date':'2026-11-14','end_date':'2026-11-14','completed_only':True},True),
    ('查询前天已完成训练',{'limit':20,'start_date':'2026-11-15','end_date':'2026-11-15','completed_only':True},False),
])
def test_binding_preserves_calendar_count_and_status(monkeypatch,query,args,valid):
    import app.services.agent_required_read_arguments as arguments_module
    monkeypatch.setattr(arguments_module,'business_today',lambda:date(2026,11,16))
    history=reads([])[1]
    if valid:
        result=validate_read_arguments('workout.list_history',history,args,[query])
        assert all(result[k]==v for k,v in args.items())
    else:
        with pytest.raises(ValueError): validate_read_arguments('workout.list_history',history,args,[query])


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['empty','timeout'])
async def test_empty_history_is_evidence_but_failed_query_is_not(monkeypatch,kind):
    calls=[]; intent=intent_for(CASES[1]); model=CapturingModel(responses=[omitted_call(CASES[1]),AIMessage(content='draft'),
        binding_response(),AIMessage(content='没有查到历史，用户自述不等于数据库记录。')])
    monkeypatch.setattr(runtime,'_build_model',lambda **kw:model)
    monkeypatch.setattr(runtime,'build_read_tools',lambda *a,**kw:reads(calls,history={'count':0,'sessions':[]},fail_history=kind=='timeout'))
    result=await runtime.invoke_langchain_agent(None,user_id='synthetic',history=[],user_message=CASES[1]['message'],
        tool_allowlist=ALLOWLIST,resolution=intent)
    reply,trace=extract(intent,result)
    assert [n for n,_ in calls].count('workout.list_history')==1
    assert (trace.evidence_contract.status=='complete') is (kind=='empty')
    assert len(model._inputs)==(4 if kind=='empty' else 3)
    if kind=='timeout':
        assert '还没有完整取得训练历史' in reply
        assert runtime._tool_call_records(result,'synthetic')[-1].status=='failed'


@pytest.mark.asyncio
async def test_binding_timeout_keeps_independent_read_and_does_not_retry(monkeypatch):
    import asyncio
    import app.services.agent_required_read_arguments as arguments_module
    invoked = []
    cancelled = []
    async def slow_binding(*args, **kwargs):
        invoked.append(kwargs['tool_id'])
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)
    monkeypatch.setattr(arguments_module, 'bind_read_arguments', slow_binding)
    monkeypatch.setattr(runtime.settings, 'AGENT_EXECUTOR_TIMEOUT_SECONDS', .01)
    case = CASES[1]
    calls = []
    model = CapturingModel(responses=[omitted_call(case), AIMessage(content='draft')])
    monkeypatch.setattr(runtime, '_build_model', lambda **kw: model)
    monkeypatch.setattr(runtime, 'build_read_tools', lambda *a, **kw: reads(calls))
    intent = intent_for(case)
    result = await runtime.invoke_langchain_agent(None, user_id='synthetic', history=[],
        user_message=case['message'], tool_allowlist=ALLOWLIST, resolution=intent)
    reply, trace = extract(intent, result)
    assert invoked == ['workout.list_history'] and cancelled == [True]
    assert [n for n, _ in calls] == ['workout.get_next', 'plan.get_active']
    assert len(model._inputs) == 2 and trace.budget_usage.model_calls == 3
    assert trace.stage_timings[-1].error_category == 'required_read_arguments_deadline_exceeded'
    assert '还没有完整取得训练历史' in reply


@pytest.mark.asyncio
async def test_planned_binding_failure_skips_finalizer():
    calls = []
    policy = OmitPolicy()
    model = CapturingModel(responses=[binding_response({})])
    result = await execute_planned_agent(db=None, user_id='synthetic', run_id='synthetic',
        model=model, goal=CASES[1]['message'], subtasks=[], tool_allowlist=ALLOWLIST,
        initial_trace=initial(intent_for(CASES[1])), tools=reads(calls), policy=policy,
        summarize_observation=runtime._audit_result_summary)
    reply, trace = guard_read_completion(result.reply, result.execution_trace)
    assert [n for n, _ in calls] == ['workout.get_next', 'plan.get_active']
    assert policy.final == [] and len(model._inputs) == 1
    assert trace.budget_usage.model_calls == 3
    assert trace.finalization_contract.selected_outcome == 'insufficient_evidence'
    assert '还没有完整取得训练历史' in reply


@pytest.mark.asyncio
@pytest.mark.parametrize('constraint', ['authority', 'tool-budget', 'already-failed', 'unsupported-tool'])
async def test_completion_cannot_expand_execution_scope(monkeypatch, constraint):
    calls = []
    invoked = []
    tools = reads(calls)
    allowlist = list(ALLOWLIST)
    trace = initial(intent_for(CASES[1]))
    if constraint == 'authority':
        allowlist.remove('workout.list_history')
    elif constraint == 'tool-budget':
        monkeypatch.setattr(runtime.settings, 'AGENT_MAX_TOOL_CALLS', 2)
    elif constraint == 'already-failed':
        trace = complete_execution_trace(trace, {'messages': [binding_response(),
            ToolMessage(content='query failed', status='error', name='workout_list_history',
                        tool_call_id='binding'), AIMessage(content='draft')]},
            summarize_observation=runtime._audit_result_summary)
    else:
        # A different parameterized read must not acquire history's new binding authority.
        tools[1] = tools[1].model_copy(update={'name':'nutrition_list_history'})
        allowlist[1] = 'nutrition.list_history'
        trace = trace.model_copy(update={'evidence_contract': trace.evidence_contract.model_copy(
            update={'required_tools':allowlist})})
    async def resolver(tool_id, tool, observations):
        invoked.append(tool_id)
        return {'limit':5}
    completed = await complete_required_reads(trace, tool_allowlist=allowlist,
        tools_by_id={TOOL_ID_BY_LANGCHAIN_NAME[t.name]:t for t in tools},
        summarize_observation=runtime._audit_result_summary, argument_resolver=resolver)
    assert invoked == []
    assert [n for n, _ in calls] == ([] if constraint in {'authority','tool-budget'}
                                   else ['workout.get_next','plan.get_active'])
    assert guard_read_completion('draft', completed.trace)[1].evidence_contract.status != 'complete'


@pytest.mark.asyncio
async def test_api_history_completion_is_audited_user_scoped_and_read_only(client, db_session, monkeypatch):
    from unittest.mock import AsyncMock
    from uuid import uuid4
    from sqlalchemy import select
    from app.models.user import User
    from app.models.exercise import Exercise
    from app.models.workout import WorkoutPlan, PlannedExercise, WorkoutSession, SessionExercise
    from app.models.agent import AgentRun, AgentToolCall, AgentProposal
    from app.services.agent_intent import IntentResolverOutcome

    email = f'closure-{uuid4()}@example.com'
    account = await client.seed_existing_account(json={'email':email,'password':'test-password'})
    headers = {'Authorization':f"Bearer {account.json()['access_token']}"}
    owner = await db_session.scalar(select(User).where(User.email == email))
    other = User(email=f'closure-other-{uuid4()}@example.com', password_hash='synthetic')
    exercise = Exercise(name_zh='合成闭环动作',name_en='Closure synthetic',category='strength',difficulty='beginner')
    db_session.add_all([other, exercise])
    await db_session.flush()
    plan = WorkoutPlan(user_id=owner.id, name='合成闭环计划')
    other_plan = WorkoutPlan(user_id=other.id, name='其他用户的计划')
    db_session.add_all([plan, other_plan])
    await db_session.flush()
    own_history = WorkoutSession(user_id=owner.id, plan_id=plan.id, plan_name=plan.name,
        trained_at=date(2026,11,9), status='completed', duration_min=30, notes='owner-only-note')
    foreign_history = WorkoutSession(user_id=other.id, plan_id=other_plan.id, plan_name=other_plan.name,
        trained_at=date(2026,11,10), status='completed', duration_min=99, notes='foreign-only-note')
    db_session.add_all([own_history, foreign_history,
        PlannedExercise(plan_id=plan.id,exercise_id=exercise.id,day_of_week=3,sets=3,reps='8-10')])
    await db_session.commit()
    async def snapshot():
        return {model.__tablename__: [dict(row) for row in
            (await db_session.execute(select(model.__table__).order_by(model.id))).mappings()]
            for model in (WorkoutPlan, PlannedExercise, WorkoutSession, SessionExercise, AgentProposal)}
    before = await snapshot()
    case = CASES[1]
    intent = intent_for(case)
    monkeypatch.setattr(runtime, 'resolve_intent_with_fallback', AsyncMock(return_value=
        IntentResolverOutcome(resolution=intent,source='model',attempt_count=1)))
    model = CapturingModel(responses=[omitted_call(case),AIMessage(content='draft'),
                                     binding_response(),AIMessage(content=ANSWER)])
    monkeypatch.setattr(runtime,'_build_model',lambda **kwargs:model)
    response = await client.post('/api/v1/agent/chat',headers=headers,json={'message':case['message']})
    assert response.status_code == 200, response.text
    body = response.json()
    run = await db_session.get(AgentRun, body['run_id'])
    audits = (await db_session.scalars(select(AgentToolCall).where(AgentToolCall.run_id == run.id))).all()
    history = next(json.loads(m.content) for m in model._inputs[-1]
                   if isinstance(m,ToolMessage) and m.name == 'workout_list_history')
    after = await snapshot()
    if folder := os.getenv('READ_CLOSURE_EVIDENCE_DIR'):
        (Path(folder)/'history-api-sql.json').write_text(json.dumps({
            'response':body,'trace':run.execution_trace,'history':history,
            'audits':[{'tool':a.tool_name,'arguments':a.arguments_data,'status':a.status} for a in audits],
            'before':before,'after':after},ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    assert run.execution_trace['evidence_contract']['status'] == 'complete'
    assert len(audits) == 3 and {a.tool_name for a in audits} == set(ALLOWLIST)
    assert all(a.status == 'completed' for a in audits)
    assert next(a for a in audits if a.tool_name == 'workout.list_history').arguments_data['limit'] == 5
    assert [s['id'] for s in history['sessions']] == [own_history.id]
    assert 'foreign-only-note' not in json.dumps(body,ensure_ascii=False)
    assert 'foreign-only-note' not in str(model._inputs)
    assert before == after


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['success', 'provider-400', 'budget-exhausted'])
async def test_binding_wire_protocol_and_billing(db_session, session_factory, monkeypatch, mode):
    """Real SDK and durable billing around synthetic HTTP; no provider network."""
    import httpx
    from uuid import uuid4
    from sqlalchemy import select
    from langchain_core.messages import HumanMessage
    from app.models.user import User
    from app.models.model_budget import ModelBudget, ModelCharge
    from app.services import model_budget as budget

    monkeypatch.setattr(budget,'AsyncSessionLocal',session_factory)
    monkeypatch.setattr(runtime.settings,'AGENT_ENABLED',True)
    monkeypatch.setattr(runtime.settings,'DEEPSEEK_API_KEY','synthetic-wire-test')
    monkeypatch.setattr(runtime.settings,'DEEPSEEK_BASE_URL','https://api.deepseek.com')
    owner = User(email=f'closure-wire-{uuid4()}@example.com',password_hash='synthetic')
    db_session.add(owner)
    await db_session.flush()
    if mode == 'budget-exhausted':
        db_session.add(ModelBudget(user_id=owner.id, spent_nano=budget.LIMIT_NANO))
    await db_session.commit()
    requests, responses = [], []
    def provider(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if mode == 'provider-400' or payload.get('thinking') != {'type':'disabled'}:
            response = {'error':{'type':'invalid_request_error','message':'synthetic provider rejection'}}
            responses.append(response)
            return httpx.Response(400,json=response)
        binding = len(requests) == 1
        message = {'role':'assistant','content':None if binding else ANSWER}
        if binding:
            message['tool_calls'] = [{'id':'wire-binding','type':'function','function':{
                'name':'workout_list_history','arguments':json.dumps({'limit':5})}}]
        response = {'id':f'synthetic-{len(requests)}','object':'chat.completion','created':1,
            'model':'deepseek-flash','choices':[{'index':0,'message':message,
                'finish_reason':'tool_calls' if binding else 'stop'}],
            'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120}}
        responses.append(response)
        return httpx.Response(200,json=response)
    case = CASES[1]
    original = {'messages':[HumanMessage(content=case['message']),omitted_call(case),
        ToolMessage(name='workout_get_next',tool_call_id='observed-0',
                    content=json.dumps({'found':True,'plan_id':'synthetic'})),AIMessage(content='draft')]}
    calls = []
    async with httpx.AsyncClient(transport=budget.BudgetTransport(httpx.MockTransport(provider))) as http:
        monkeypatch.setattr(runtime,'model_http_client',lambda:http)
        with budget.billing_user(owner.id):
            result = await runtime._complete_direct_required_reads(original, db=None,
                model=runtime._build_model(max_tokens=128),tools=reads(calls),resolution=intent_for(case),
                tool_allowlist=ALLOWLIST,user_message=case['message'])
    reply, trace = extract(intent_for(case),result)
    async with session_factory() as db:
        charges = (await db.scalars(select(ModelCharge).where(ModelCharge.user_id == owner.id))).all()
    state = await budget.budget_status(owner.id)
    if folder := os.getenv('READ_CLOSURE_EVIDENCE_DIR'):
        (Path(folder)/f'binding-wire-{mode}.json').write_text(json.dumps({
            'requests':requests,'responses':responses,'reply':reply,'trace':trace.model_dump(mode='json'),
            'charges':[{'status':c.status,'actual_nano':c.actual_nano,'reserved_nano':c.reserved_nano} for c in charges],
            'budget':state},ensure_ascii=False,indent=2),encoding='utf-8')
    assert len(requests) == len(charges) == {'success':2,'provider-400':1,'budget-exhausted':0}[mode]
    assert trace.budget_usage.model_calls == (4 if mode == 'success' else 3)
    assert [n for n, _ in calls] == (['plan.get_active','workout.list_history'] if mode == 'success' else ['plan.get_active'])
    if requests:
        assert [t['function']['name'] for t in requests[0]['tools']] == ['workout_list_history']
        assert requests[0]['tool_choice'] == {'type':'function','function':{'name':'workout_list_history'}}
        assert requests[0]['thinking'] == {'type':'disabled'}
        # The SDK omits tool-message names on the wire; pairing is by call ID.
        names = {call['id']:call['function']['name'] for m in requests[0]['messages']
                 for call in m.get('tool_calls', [])}
        assert [names[m['tool_call_id']] for m in requests[0]['messages'] if m['role']=='tool'] == ['workout_get_next','plan_get_active']
    if mode == 'success':
        assert trace.evidence_contract.status == 'complete' and reply == ANSWER
        assert 'tools' not in requests[1] and 'tool_choice' not in requests[1]
        assert all(c.status == 'settled' for c in charges)
        assert state['spent_yuan'] == '0.000720000'
    else:
        assert trace.evidence_contract.status == 'partial'
        assert '还没有完整取得训练历史' in reply
        if mode == 'provider-400':
            assert charges[0].status == 'http_error_estimate' and charges[0].actual_nano is None
