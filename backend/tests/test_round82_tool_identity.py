import asyncio,copy,hashlib,json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest,pytest_asyncio
from pydantic import ValidationError
from langchain_core.messages import AIMessage,ToolMessage
from langchain.agents.middleware.types import ModelResponse
from app.services.agent_tool_identity import ReadToolIdentity
from app.schemas.agent_trace import AgentToolIdentityTrace,AgentExecutionTrace
from app.services.agent_runtime import _tool_call_records

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

def call(id='same',name='profile_get_summary',args=None):return {'id':id,'name':name,'args':args or {}}
def response(*calls):return ModelResponse(result=[AIMessage(content='',tool_calls=list(calls),response_metadata={'original':'kept'},usage_metadata={'input_tokens':11,'output_tokens':7,'total_tokens':18})],structured_response={'unchanged':True})

@pytest.mark.parametrize('id',['one','call:1','a'*120,'合成标识'])
def test_normal_unique_labels_do_not_change(id):
    source=response(call(id));gate=ReadToolIdentity([]);out=gate.normalize(source)
    assert out.result[0] is source.result[0] and gate.receipt() is None
    assert out.structured_response==source.structured_response

@pytest.mark.parametrize('id,reason',[(None,'missing'),('','blank'),('  ','blank'),('x'*121,'too_long'),('x'*5000,'too_long')])
def test_invalid_storage_labels_get_bounded_receipts(id,reason):
    gate=ReadToolIdentity([]);out=gate.normalize(response(call(id)));receipt=AgentToolIdentityTrace.model_validate(gate.receipt())
    entry=receipt.changes[0];assert entry.reason==reason and len(out.result[0].tool_calls[0]['id'])<=120
    assert entry.execution_id!=id and entry.provider_id_truncated==(id is not None and len(id)>4096)
    if id is not None:assert entry.provider_id_sha256==hashlib.sha256(id.encode()).hexdigest() and entry.provider_id_length==len(id)

@pytest.mark.parametrize('kind',['same_name','different_name','different_args','sequential'])
def test_same_label_is_not_a_cache_key_and_original_is_immutable(kind):
    a=call();b=call(name='health_get_screening_summary' if kind=='different_name' else 'profile_get_summary',args={'synthetic':2} if kind=='different_args' else {})
    source=response(a,b);original=copy.deepcopy(source);gate=ReadToolIdentity([])
    if kind=='sequential':gate.normalize(response(a));out=gate.normalize(response(b));ids=['same',out.result[0].tool_calls[0]['id']]
    else:out=gate.normalize(source);ids=[x['id'] for x in out.result[0].tool_calls]
    assert len(set(ids))==2 and ids[0]=='same' and source==original
    change=AgentToolIdentityTrace.model_validate(gate.receipt()).changes[0]
    assert change.provider_id=='same' and change.reason=='duplicate'
    assert change.generation==(2 if kind=='sequential' else 1)
    assert out.result[0].response_metadata==original.result[0].response_metadata and out.result[0].usage_metadata==original.result[0].usage_metadata

@pytest.mark.parametrize('id',['bad\x00label','\ud800','x'*5000+'\x00'])
def test_non_postgres_unicode_labels_use_explicit_reversible_encoding(id):
    gate=ReadToolIdentity([]);out=gate.normalize(response(call(id)))
    entry=AgentToolIdentityTrace.model_validate(gate.receipt()).changes[0]
    assert entry.reason=='invalid_characters' and entry.provider_id_encoding=='json_string'
    assert json.loads(entry.provider_id)==id[:4096] and '\x00' not in entry.provider_id
    assert entry.provider_id_sha256==hashlib.sha256(id.encode('utf8',errors='surrogatepass')).hexdigest()
    assert out.result[0].tool_calls[0]['id'].startswith('read-')

@pytest.mark.parametrize('seed',['assistant','tool','both'])
def test_prefetched_evidence_labels_are_reserved(seed):
    ai=AIMessage(content='',tool_calls=[call()]);tool=ToolMessage(content='{}',name='profile_get_summary',tool_call_id='same')
    gate=ReadToolIdentity([ai] if seed=='assistant' else [tool] if seed=='tool' else [ai,tool])
    out=gate.normalize(response(call()));assert out.result[0].tool_calls[0]['id']!='same'
    assert gate.receipt()['changes'][0]['reason']=='duplicate'

def test_same_batch_later_original_label_is_reserved(monkeypatch):
    from app.services import agent_tool_identity as module
    values=iter(['collision','safe'])
    monkeypatch.setattr(module,'uuid4',lambda:SimpleNamespace(hex=next(values)))
    out=ReadToolIdentity([]).normalize(response(call(),call(),call('read-collision')))
    assert [c['id'] for c in out.result[0].tool_calls]==['same','read-safe','read-collision']

def test_allocator_collision_fails_after_bounded_attempts(monkeypatch):
    from app.services import agent_tool_identity as module
    values=[]
    def duplicate():values.append(1);return SimpleNamespace(hex='collision')
    monkeypatch.setattr(module,'uuid4',duplicate)
    with pytest.raises(RuntimeError,match='allocation_failed'):ReadToolIdentity([]).normalize(response(call('read-collision'),call('read-collision')))
    assert len(values)==8

def test_two_same_label_outputs_are_audited_under_their_own_arguments_and_result():
    gate=ReadToolIdentity([]);out=gate.normalize(response(call(name='workout_list_history',args={'limit':1}),call(name='workout_list_history',args={'limit':5})))
    ids=[c['id'] for c in out.result[0].tool_calls]
    messages=[out.result[0],*[ToolMessage(content=json.dumps({'count':count,'sessions':[]}),name='workout_list_history',tool_call_id=id) for id,count in zip(ids,[1,5])]]
    audits=_tool_call_records({'messages':messages},'synthetic')
    assert [a.arguments_data['limit'] for a in audits]==[1,5] and [a.result_data['count'] for a in audits]==[1,5]

@pytest.mark.asyncio
async def test_parallel_run_scopes_do_not_share_id_state():
    gates=[ReadToolIdentity([]),ReadToolIdentity([])]
    async def invoke(gate):return await gate.awrap_model_call(None,AsyncMock(return_value=response(call(),call())))
    results=await asyncio.gather(*(invoke(g) for g in gates))
    assert all(x.result[0].tool_calls[0]['id']=='same' for x in results)
    assert len({x.result[0].tool_calls[1]['id'] for x in results})==2
    receipt=gates[0].receipt();receipt['changes'].clear();assert gates[0].receipt()['changes']

@pytest.mark.parametrize('change',['hash','length','same_id','duplicate_execution'])
def test_invalid_receipts_are_not_silently_accepted(change):
    gate=ReadToolIdentity([]);gate.normalize(response(call(),call()));receipt=gate.receipt();entry=receipt['changes'][0]
    if change=='hash':entry['provider_id_sha256']='0'*64
    elif change=='length':entry['provider_id_length']=99
    elif change=='same_id':entry['execution_id']='same'
    else:receipt['changes'].append(copy.deepcopy(entry))
    with pytest.raises(ValidationError):AgentToolIdentityTrace.model_validate(receipt)
