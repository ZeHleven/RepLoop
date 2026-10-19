"""Current-run evidence reaches every generation path; external HTTP is scripted."""
import copy,hashlib,json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest,pytest_asyncio
from langchain_core.messages import ToolMessage,AIMessage,SystemMessage
from app.config import settings
from app.services import agent_runtime as runtime
from app.services.agent_intent import IntentResolution,route_tools
from app.services.agent_required_evidence import ReadEvidenceJournal
from app.services.agent_read_context import ReadResponseContext
from test_round57_read_delivery import install_tools,scripted_http

ROWS=json.loads((Path(__file__).parent/'fixtures/round76/archived.json').read_text(encoding='utf8'))
GOOD='离线执行链验证，不是业务建议。'

@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    yield

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','offline-scripted')
    monkeypatch.setattr(settings,'DEEPSEEK_BASE_URL','https://api.deepseek.com')

def resolution(body):
    return IntentResolution.model_validate({**{k:v for k,v in body.items() if k in IntentResolution.model_fields},'confidence':body['intent_confidence']})

def tool_calls(*names,prefix='r76-'):
    return {'role':'assistant','content':'','tool_calls':[{'id':prefix+name,'type':'function','function':{'name':name,'arguments':'{}'}} for name in names]}

def system(req):return '\n'.join(m['content'] for m in req['messages'] if m['role']=='system')

@pytest.mark.asyncio
@pytest.mark.parametrize('id',[f'{q}-r{n}' for q in ('SV01','SV03','SV06','SV07') for n in (1,2)])
@pytest.mark.parametrize('mode',['normal','protocol','assessment','budget'])
async def test_dynamic_read_and_recovery_share_actual_sources(monkeypatch,id,mode):
    row=copy.deepcopy(ROWS[id]);body=row['turns'][0]['body'];query=row['turns'][0]['message'];r=resolution(body)
    calls=install_tools(monkeypatch,{'tools':row['tool_attempts']})
    if mode=='budget':monkeypatch.setattr(settings,'AGENT_MAX_TOOL_CALLS',2)
    first=tool_calls('plan_get_active','health_get_screening_summary')
    sequence=[first,tool_calls('plan_get_active',prefix='r76-budget-') if mode=='budget' else {'role':'assistant','content':
        '<｜DSML｜function_calls>bad</｜DSML｜function_calls>' if mode=='protocol' else '少练这一周不会有什么大损失。' if mode=='assessment' else GOOD}]
    if mode!='normal':sequence.append({'role':'assistant','content':GOOD})
    requests=scripted_http(monkeypatch,sequence)
    result=await runtime.invoke_langchain_agent(None,user_id='synthetic',history=[],user_message=query,resolved_query=r.resolved_query,tool_allowlist=route_tools(r),resolution=r)
    assert result['messages'][-1].content==GOOD,result
    assert len(requests)==len(sequence) and len(calls)==2
    assert 'current_symptom_report' not in system(requests[0])
    for req in requests[1:]:
        s=system(req)
        assert hashlib.sha256(query.encode()).hexdigest() in s and query in s
        assert 'r76-plan_get_active' in s and s.count('当前新症状与训练建议契约：')==1
        assert 'training_adjustment_envelope' in s
    assert runtime._usage_totals(result)==(12*len(sequence),5*len(sequence))
    if mode!='normal':assert not requests[-1].get('tools')

def context_inputs():
    row=ROWS['SV01-r1'];r=resolution(row['turns'][0]['body'])
    messages=[ToolMessage(content=json.dumps(t['result']),name=t['tool'].replace('.','_'),tool_call_id=t['attempt_id']) for t in row['tool_attempts']]
    return row['turns'][0]['message'],r,messages

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['error','not_executed','payload_not_executed','not_found','business_error','invalid','history','assistant'])
async def test_non_observation_cannot_activate_plan_contract(mode):
    query,r,messages=context_inputs();m=messages[0]
    assert m.name=='plan_get_active'
    if mode=='error':m=m.model_copy(update={'status':'error'})
    elif mode=='not_executed':m=m.model_copy(update={'additional_kwargs':{'executed':False}})
    elif mode=='payload_not_executed':m=m.model_copy(update={'content':json.dumps({**json.loads(m.content),'executed':False})})
    elif mode=='not_found':m=m.model_copy(update={'content':'{"found":false}'})
    elif mode=='business_error':m=m.model_copy(update={'content':json.dumps({**json.loads(m.content),'error_code':'timeout'})})
    elif mode=='invalid':m=m.model_copy(update={'content':'invalid'})
    journal=ReadEvidenceJournal([] if mode in {'history','assistant'} else [m,*messages[1:]])
    journal.messages=messages+[AIMessage(content='计划已经查询成功')]
    ctx=ReadResponseContext(query=query,resolution=r,journal=journal)
    assert ctx.context()==''

@pytest.mark.asyncio
async def test_journal_freezes_prefetch_and_only_wrapped_tool_observations():
    query,r,messages=context_inputs();journal=ReadEvidenceJournal(messages)
    ctx=ReadResponseContext(query=query,resolution=r,journal=journal)
    before=ctx.context();messages[0].content='{}';assert ctx.context()==before
    bad=SimpleNamespace(tool_call={'id':'failed','name':'plan_get_active'})
    with pytest.raises(RuntimeError):await journal.awrap_tool_call(bad,AsyncMock(side_effect=RuntimeError('offline')))
    assert ctx.context()==before and len(journal.evidence_messages)==2
    handler=AsyncMock(return_value=ToolMessage(content='{"found":false}',name='plan_get_active',tool_call_id='empty'))
    await journal.awrap_tool_call(bad,handler)
    assert len(journal.evidence_messages)==3

@pytest.mark.asyncio
@pytest.mark.parametrize('blocks',[False,True])
async def test_transient_system_is_not_accumulated_or_persisted(blocks):
    query,r,messages=context_inputs();ctx=ReadResponseContext(query=query,resolution=r,journal=ReadEvidenceJournal(messages))
    system_message=SystemMessage(content=[{'type':'text','text':'base'}] if blocks else 'base')
    class Request:
        def __init__(self,system):self.system_message=system;self.messages=[]
        def override(self,**changes):return Request(changes['system_message'])
    request=Request(system_message);handler=AsyncMock(return_value='ok')
    for _ in range(2):assert await ctx.awrap_model_call(request,handler)=='ok'
    assert system_message.content==([{'type':'text','text':'base'}] if blocks else 'base')
    assert handler.call_args_list[0].args[0].system_message.content==handler.call_args_list[1].args[0].system_message.content

def continuation(id):
    row=ROWS[id];query=row['turns'][1]['message'];state=row['turns'][0]['body']['execution_trace']['task_state']
    context=[{'role':'task_state','content':json.dumps(state,ensure_ascii=False)}, {'role':'user','content':row['turns'][0]['message']},{'role':'assistant','content':row['turns'][0]['body']['reply']}]
    routes=[json.loads(x['response']['choices'][0]['message']['tool_calls'][0]['function']['arguments']) for x in row['provider'] if x['turn']==1]
    return query,context,routes

@pytest.mark.asyncio
@pytest.mark.parametrize('id',['SV05-r1','SV05-r2'])
async def test_archived_readonly_continuation_preserves_units(monkeypatch,id):
    from app.services import agent_intent_model as model
    query,context,routes=continuation(id)
    for raw in routes:
        route=model.IntentRouteDecision.model_validate(raw)
        monkeypatch.setattr(model,'_invoke_model_route',AsyncMock(return_value=route))
        out=await model.resolve_intent_with_fallback(query,context_messages=context,use_model=True)
        assert not out.understanding_failed,out.error_category
        assert out.attempt_count==1 and out.resolution.requested_effect=='read'
        assert '2分钟' not in out.resolution.resolved_query and '10分钟' in out.resolution.resolved_query
        assert all('duration' not in e.key for e in out.resolution.task_update.requirements if e.quote=='约2分')
        assert route.model_dump(mode='json')['normalized_request']==raw['normalized_request']

@pytest.mark.asyncio
@pytest.mark.parametrize('suffix',['然后保存这份方案','并记录今天体重72公斤','再删除旧计划','不得不保存方案','不要不保存方案','请确认并执行这份方案'])
async def test_continuation_does_not_override_positive_writes(monkeypatch,suffix):
    from app.services import agent_intent_model as model
    query,context,routes=continuation('SV05-r1');query+='；'+suffix
    route=model.IntentRouteDecision.model_validate(routes[0]);route.task_update.trigger=query
    monkeypatch.setattr(model,'_invoke_model_route',AsyncMock(return_value=route))
    out=await model.resolve_intent_with_fallback(query,context_messages=context,use_model=True)
    assert out.understanding_failed and not route_tools(out.resolution),out

@pytest.mark.parametrize('message,normalized,changed',[
    ('今天手腕疼，约2分，无肿胀。','手腕疼，持续约2分钟。',True),
    ('今天手腕疼了2分钟，没有肿胀。','手腕疼，持续约2分钟。',False),
    ('今天手腕疼，约2分，走路2分钟没事。','手腕疼，走路2分钟没事。',False),
    ('今天手腕疼，约2分，每次12分钟。','手腕疼，每次12分钟。',False),
    ('运动了2分，今天手腕疼。','运动了2分钟，手腕疼。',False),
])
def test_ambiguous_number_does_not_become_duration(message,normalized,changed):
    from app.services.agent_intent_model import IntentRouteDecision
    from app.services.agent_source_fidelity import preserve_symptom_measurements
    raw=continuation('SV05-r1')[2][0];route=IntentRouteDecision.model_validate(raw).model_copy(update={'normalized_request':normalized,'task_update':None})
    fixed=preserve_symptom_measurements(route,message)
    assert (fixed.normalized_request!=normalized)==changed
