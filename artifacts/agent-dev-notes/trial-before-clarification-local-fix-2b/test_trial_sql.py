"""Independent draft repair through the actual API, worker and SQL path."""
import copy,json
from unittest.mock import AsyncMock
import httpx,pytest
from app.services import agent_intent_model
from app.services.agent_intent import IntentResolution
from app.services.agent_assertion_scope import CURRENT_CARE_REPLY
from test_recent_and_emergency_sql import execute,runner
from test_trial_gate import CASES,GOOD

@pytest.mark.asyncio
@pytest.mark.parametrize('index',[0,2],ids=['fatigue','current_care'])
async def test_safe_correction_delivered_before_execution(monkeypatch,index):
    ident,query,bad=CASES[index];care=index==2
    intent=IntentResolution(primary_intent='health_query',intent_domain='health',request_kind='assessment',requested_effect='read',resolved_query=query,risk_level='medium' if care else 'low',confidence=1,evidence_requirements=['health_screening','active_plan'],task_update={'action':'new','trigger':query,'requirements':[{'key':'advice','quote':'不保存'}]})
    monkeypatch.setattr(agent_intent_model,'_invoke_model_intent',AsyncMock(return_value=intent))
    sent=[];good=(CURRENT_CARE_REPLY if care else '')+GOOD
    async def transport(client,request,**kwargs):
        sent.append(json.loads(request.content));assert len(sent)<=3
        msg={'role':'assistant','content':bad if len(sent)==2 else good};reason='stop'
        if len(sent)==1:
            msg={'role':'assistant','content':None,'tool_calls':[{'id':'trial-'+n,'type':'function','function':{'name':n,'arguments':'{}'}} for n in ['health_get_screening_summary','plan_get_active']]};reason='tool_calls'
        return httpx.Response(200,json={'id':'offline','object':'chat.completion','model':'synthetic','choices':[{'index':0,'finish_reason':reason,'message':msg}],'usage':{'prompt_tokens':12,'completion_tokens':5,'total_tokens':17}},request=request)
    case=copy.deepcopy(runner.BENCH['cases'][0]);case.update(id='TG2'+str(index),group='independent_trial_gate',turns=[query],expected_tools=[])
    rec,root=await execute(monkeypatch,case,1,transport)
    body=rec['turns'][0]['body'];receipt=body['execution_trace']['assessment_delivery']
    assert len(sent)==3 and body['reply']==good and receipt['status']=='corrected'
    assert any(f['code']=='training_action_conflict' for f in receipt['findings'])
    (root/'trial-proof.json').write_text(json.dumps(dict(source='independent development',reply=body['reply'],receipt=receipt,requests=len(sent),read_only=rec['baseline']==rec['final']),ensure_ascii=False,indent=2))
