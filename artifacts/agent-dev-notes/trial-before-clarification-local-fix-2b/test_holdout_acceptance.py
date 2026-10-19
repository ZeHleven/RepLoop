"""Frozen boundary replay, not a model rerun or a new business score.

Archived final text and successful tool results are unmodified. The response to
the newly triggered repair is explicitly scripted using the development fixture.
"""
import copy,hashlib,json,os
from pathlib import Path
from unittest.mock import AsyncMock
import pytest,pytest_asyncio
from langchain_core.messages import AIMessage,ToolMessage
from app.services.agent_assessment_delivery import assessment_findings,recover_assessment_answer
from app.services.agent_read_delivery import ReadDeliveryBudget
from app.services.agent_assertion_scope import CURRENT_CARE_REPLY
from test_trial_gate import GOOD

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

@pytest.mark.asyncio
@pytest.mark.parametrize('ident',['HO04-r1','HO06-r1'])
async def test_frozen_archived_delivery_requires_confirmation(ident):
    frozen=json.loads(Path('/evidence/frozen-candidate.json').read_text())
    for row in frozen['files']:
        assert hashlib.sha256((Path('/workspace')/row['file']).read_bytes()).hexdigest()==row['sha256']
    raw=json.loads((Path('/holdout/runs')/(ident+'.json')).read_text())
    turn=raw['turns'][0];query=turn['message'];draft=turn['body']['reply']
    messages=[ToolMessage(content=json.dumps(t['result'],ensure_ascii=False),name=t['tool'].replace('.','_'),tool_call_id=t['attempt_id'],status='success') for t in raw['tool_attempts'] if t['status']=='success']
    result={'messages':messages+[AIMessage(content=draft)]}
    fs=assessment_findings(draft,result,query)
    assert any(f['code']=='training_action_conflict' for f in fs)
    proof=dict(instance=ident,source='unmodified archived final and tool results; frozen boundary replay',business_rescored=False,query=query,draft=draft,findings=fs,branches=[])
    for mode in ['clarifies','persists']:
        good=(CURRENT_CARE_REPLY if ident=='HO06-r1' else '')+GOOD
        model=AsyncMock();model.ainvoke.return_value=AIMessage(content=good if mode=='clarifies' else draft)
        out=await recover_assessment_answer(copy.deepcopy(result),model=model,budget=ReadDeliveryBudget(1),query=query,source_query=query,history=[],system_prompt='offline acceptance')
        receipt=out['assessment_delivery'];final=out['messages'][-1].content
        assert model.ainvoke.await_count==1
        if mode=='clarifies':
            assert receipt['status']=='corrected' and final==good
            assert '？' in final and '确认后再' in final
        else:
            assert receipt['status']=='blocked' and draft not in final
            assert any(f['code']=='training_action_conflict' for f in receipt['remaining_findings'])
        if ident=='HO06-r1':assert final.startswith(CURRENT_CARE_REPLY)
        proof['branches'].append(dict(scripted_repair_mode=mode,status=receipt['status'],final=final,receipt=receipt))
    (Path(os.environ['GUARD_OUTPUT'])/(ident+'-boundary-proof.json')).write_text(json.dumps(proof,ensure_ascii=False,indent=2))
