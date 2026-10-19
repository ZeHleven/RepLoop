"""New prefetch path through real PostgreSQL, API, worker and persisted audit."""
import asyncio
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import AsyncMock
import pytest
import pytest_asyncio
from httpx import AsyncClient,ASGITransport
from sqlalchemy import select,text,func
from evals.postgres_test_fixtures import engine,session_factory,db_session
from app.config import settings
from app.main import app
from app.database import get_db
from app.models.agent import AgentToolCall,AgentProposal
from app.services import agent_runtime as runtime
from app.services.agent_intent import IntentResolution,IntentResolverOutcome
from app.services.agent_jobs import claim_next_agent_run,process_agent_run
from app.services.auth import create_access_token
from test_agent_domain_proposals import _context
from test_round49_required_evidence import Model
from langchain_core.messages import AIMessage

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

@pytest.mark.asyncio
@pytest.mark.parametrize('scenario,count,code',[('normal',2,None),('sql_timeout_once',3,None),('sql_error',1,'read_tool_execution_failed')])
async def test_prefetch_worker_sql_recovery_audit_and_user_scope(db_session,session_factory,monkeypatch,scenario,count,code):
    user,profile,conv,_=await _context(db_session,'r49-'+uuid4().hex)
    foreign,other,_,_=await _context(db_session,'r49-other-'+uuid4().hex)
    other.weight_kg=180
    profile.updated_at=datetime(2026,10,9,12,0,0,180123,tzinfo=timezone.utc)
    await db_session.commit()
    async def override():
        async with session_factory() as db:yield db
    app.dependency_overrides[get_db]=override
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','offline')
    monkeypatch.setattr(settings,'AGENT_PLANNED_EXECUTION_ENABLED',False)
    r=IntentResolution(primary_intent='nutrition_today_query',intent_domain='nutrition',request_kind='assessment',requested_effect='read',evidence_requirements=['profile_summary','nutrition_today'],resolved_query='结合资料评价今天的饮食记录',confidence=.99)
    monkeypatch.setattr(runtime,'resolve_intent_with_fallback',AsyncMock(return_value=IntentResolverOutcome(resolution=r,source='model',attempt_count=1)))
    monkeypatch.setattr(runtime,'_build_model',lambda:Model(responses=[AIMessage(content='今天没有饮食记录，无法判断全天摄入；体重资料为65kg。')]))
    original=runtime.build_read_tools;calls=[];raw_results=[]
    def builder(db,**kwargs):
        tools=original(db,**kwargs)
        for tool in tools:
            coroutine=tool.coroutine
            async def injected(_orig=coroutine,**args):
                calls.append(args)
                if scenario=='sql_error':await db.execute(text('SELECT 1/0'))
                if scenario=='sql_timeout_once' and len(calls)==1:
                    try:await db.execute(text('SELECT 1/0'))
                    except Exception as exc:raise TimeoutError() from exc
                result=await _orig(**args)
                raw_results.append(result)
                return result
            tool.coroutine=injected
        return tools
    monkeypatch.setattr(runtime,'build_read_tools',builder)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
            res=await client.post('/api/v1/agent/runs',json={'conversation_id':conv.id,'message':r.resolved_query,'client_request_id':uuid4().hex})
            assert res.status_code==202,res.text
            rid=res.json()['run_id']
            async with session_factory() as db:assert await claim_next_agent_run(db)==rid
            await asyncio.wait_for(process_agent_run(session_factory,rid),10)
            body=(await client.get('/api/v1/agent/runs/'+rid)).json()
            assert body['status']=='completed' and body.get('error_code')==code,body
            trace=body['execution_trace']
            assert trace['budget_usage']['tool_calls']==count
            assert trace['budget_usage']['model_calls']==(0 if code else 1)
            assert trace['terminal_action']==('safe_stop' if code else 'answer')
            assert trace['evidence_contract']['status']==('partial' if code else 'complete')
            if not code:
                card=next(x for x in body['cards'] if x['type']=='profile.get_summary')
                if os.environ.get('R57_DIAGNOSTICS_DIR'):
                    Path(os.environ['R57_DIAGNOSTICS_DIR'], 'r49-isolation-'+rid+'.json').write_text(json.dumps({'scenario':scenario,'cards':body['cards'],'own_user':user.id,'foreign_user':foreign.id,'expected_weight':65,'foreign_weight':180},ensure_ascii=False,default=str),encoding='utf8')
                _assert_owned_profile(card['data'])
                assert card['data']['updated_at']=='2026-10-09T12:00:00.180123+00:00'
                assert foreign.id not in json.dumps(body['cards'])
            assert (await client.get('/api/v1/agent/runs/'+rid)).json()['reply']==body['reply']
        async with session_factory() as db:
            audits=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==rid))).all()
            assert len(audits)==count and all(x.duration_ms is not None for x in audits)
            if not code:
                profile_audits=[x for x in audits if x.tool_name=='profile.get_summary' and x.status=='completed']
                assert len(profile_audits)==1, [(x.tool_name,x.status) for x in audits]
                assert profile_audits[0].result_data['found'] is True
                assert 'weight_kg' in profile_audits[0].result_data['fields_returned']
                assert 'weight_kg' not in profile_audits[0].result_data  # persisted audit deliberately redacts values
                profiles=[x for x in raw_results if 'weight_kg' in x]
                assert len(profiles)==1
                _assert_owned_profile(profiles[0])
            assert sum(x.status=='failed' for x in audits)==(0 if scenario=='normal' else 1)
            assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id==user.id))==0
        assert len(calls)==count and all(x=={} for x in calls)
    finally:app.dependency_overrides.clear()


def _assert_owned_profile(data):
    assert data['weight_kg']==65, data
    assert data['height_cm']==170 and data['age']==30, data
    assert data['training_days_per_week']==3, data


def test_owned_profile_check_rejects_foreign_weight():
    with pytest.raises(AssertionError):
        _assert_owned_profile({'weight_kg':180,'height_cm':170,'age':30,'training_days_per_week':3})
