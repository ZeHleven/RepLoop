"""Guard fallback delivery via real API/worker/SQL, with HTTP scripted offline."""
import copy,json
from pathlib import Path
from unittest.mock import AsyncMock
import httpx,pytest
from app.config import settings
from app.services import agent_intent_model
from app.services.agent_intent import IntentResolution
from app.services.agent_assertion_scope import CURRENT_CARE_REPLY
from test_recent_and_emergency_sql import execute,runner,ROWS

def assert_clarification(body,care=False):
    trace=body['execution_trace'];receipt=trace['assessment_delivery']
    assert receipt['status']=='blocked' and receipt['remaining_findings']
    assert body['error_code']=='read_assessment_boundary_invalid'  # Guard failure stays auditable.
    assert body['clarification_required'] and body['clarification_question']
    assert trace['terminal_action']=='clarify'
    task=next(t for t in trace['task_state']['tasks'] if t['id']==trace['task_state']['active_task_id'])
    assert task['phase']=='awaiting_input'
    assert body['clarification_question'] in body['reply']
    assert '尚未完成你的请求' not in body['reply'] and '请稍后重试' not in body['reply']
    assert '没有安排试练' in body['reply'] and '没有修改计划' in body['reply']
    assert not any(x in body['reply'] for x in ['杠铃','哑铃','器械推','不受力','不承重','先做2组'])
    if care:assert body['reply'].startswith(CURRENT_CARE_REPLY) and receipt['current_care_fallback_preserved']

async def replay(monkeypatch,raw,case):
    indices={t['index']:0 for t in raw['turns']}
    replies={turn:[copy.deepcopy(p['response']) for p in raw['provider'] if p['turn']==turn] for turn in indices}
    async def transport(client,request,**kwargs):
        assert request.url.host=='api.deepseek.com'
        turn=runner.CURRENT['current_turn'];index=indices[turn];indices[turn]+=1
        assert index<len(replies[turn]),'No extra model request allowed'
        response=copy.deepcopy(replies[turn][index]);response['usage']={'prompt_tokens':12,'completion_tokens':5,'total_tokens':17}
        return httpx.Response(200,json=response,request=request)
    rec,root=await execute(monkeypatch,case,raw['repeat'],transport,expected_error='read_assessment_boundary_invalid')
    assert indices=={k:len(v) for k,v in replies.items()}
    proof={'source_instance':raw['instance'],'reply_before':[t['body']['reply'] for t in raw['turns']],
           'reply_after':[t['body']['reply'] for t in rec['turns']], 'provider_requests':len(rec['provider']),
           'tool_attempts':len(rec['tool_attempts']),'read_only':rec['baseline']==rec['final'], 'business_score':'not rescored; offline replay only'}
    (root/'guard-replay-proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2))
    return rec

@pytest.mark.asyncio
@pytest.mark.parametrize('ident',['SV01-r2','SV07-r2'])
async def test_two_actual_development_guard_fallbacks(monkeypatch,ident):
    raw=json.loads((Path('/guardprior/runs')/(ident+'.json')).read_text());case=next(c for c in runner.BENCH['cases'] if c['id']==raw['case_id'])
    rec=await replay(monkeypatch,raw,case)
    for t in rec['turns']:assert_clarification(t['body'])

@pytest.mark.asyncio
async def test_independent_current_care_stays_before_clarification(monkeypatch):
    query='今天摔倒碰伤左膝，膝盖肿胀且明显疼痛。我工作累，能否继续训练？只要建议，不保存。'
    intent=IntentResolution(primary_intent='health_query',intent_domain='health',request_kind='assessment',requested_effect='read',resolved_query=query,risk_level='medium',confidence=1,evidence_requirements=['health_screening','active_plan'],task_update={'action':'new','trigger':query,'requirements':[{'key':'advice','quote':'只要建议，不保存'}]})
    monkeypatch.setattr(agent_intent_model,'_invoke_model_intent',AsyncMock(return_value=intent))
    sent=[];bad=CURRENT_CARE_REPLY+'卧推不会影响膝盖。'
    async def transport(client,request,**kwargs):
        sent.append(json.loads(request.content));assert len(sent)<=3
        msg={'role':'assistant','content':bad};reason='stop'
        if len(sent)==1:
            msg={'role':'assistant','content':None,'tool_calls':[{'id':'gf-'+n,'type':'function','function':{'name':n,'arguments':'{}'}} for n in ['health_get_screening_summary','plan_get_active']]};reason='tool_calls'
        return httpx.Response(200,json={'id':'offline','object':'chat.completion','model':'synthetic','choices':[{'index':0,'finish_reason':reason,'message':msg}],'usage':{'prompt_tokens':12,'completion_tokens':5,'total_tokens':17}},request=request)
    case=copy.deepcopy(runner.BENCH['cases'][0]);case.update(id='GF01',group='independent_guard_delivery',turns=[query],expected_tools=[])
    rec,root=await execute(monkeypatch,case,1,transport,expected_error='read_assessment_boundary_invalid')
    assert len(sent)==3
    assert_clarification(rec['turns'][0]['body'],care=True)
