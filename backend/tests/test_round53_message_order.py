"""Actual SQL/API order with equal clocks, clock reversal and legacy rows."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import pytest
from evals.postgres_test_fixtures import engine,session_factory,db_session
from test_round37_worker import client,setup_db
from app.models.user import User
from app.models.agent import AgentConversation,AgentRun,AgentMessage
from app.services.auth import create_access_token
from app.services.agent_runtime import _load_history

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['same_time','reversed_clock','legacy'])
async def test_baseline_message_order_uses_run_then_role(client,db_session,mode):
    suffix=uuid4().hex;stamp=datetime(2026,10,9,12,tzinfo=timezone.utc)
    user=User(id='order-'+suffix,email=suffix+'@example.invalid',password_hash='synthetic-no-login')
    other=User(id='other-'+suffix,email='other-'+suffix+'@example.invalid',password_hash='synthetic-no-login')
    db_session.add_all([user,other]);await db_session.flush()
    conv=AgentConversation(user_id=user.id);db_session.add(conv);await db_session.flush()
    runs=[]
    for index in (1,2):
        queued=stamp-timedelta(days=index) if mode=='reversed_clock' else stamp
        run=AgentRun(id=f'{index}-run-'+suffix,user_id=user.id,conversation_id=conv.id,status='completed',queue_position=None if mode=='legacy' else index,queued_at=queued)
        db_session.add(run);runs.append(run)
    await db_session.flush()
    for index,run in enumerate(runs,1):
        # Deliberately identical timestamps and IDs opposed to role order.
        db_session.add(AgentMessage(id=f'z-user-{index}-'+suffix,conversation_id=conv.id,run_id=run.id,role='user',content=f'U{index}',created_at=stamp))
        db_session.add(AgentMessage(id=f'a-answer-{index}-'+suffix,conversation_id=conv.id,run_id=run.id,role='assistant',content=f'A{index}',created_at=stamp))
    await db_session.commit()
    headers={'Authorization':'Bearer '+create_access_token(user.id)}
    route=f'/api/v1/agent/conversations/{conv.id}/messages'
    response=await client.get(route,headers=headers)
    assert response.status_code==200
    assert [m['content'] for m in response.json()]==['U1','A1','U2','A2']
    recent=await client.get(route+'?limit=3',headers=headers)
    assert [m['content'] for m in recent.json()]==['A1','U2','A2']
    latest=await client.get(route+'?limit=1',headers=headers)
    assert [m['content'] for m in latest.json()]==['A2']
    denied=await client.get(route,headers={'Authorization':'Bearer '+create_access_token(other.id)})
    assert denied.status_code==404
    future=AgentRun(id='3-run-'+suffix,user_id=user.id,conversation_id=conv.id,queue_position=None if mode=='legacy' else 3,queued_at=stamp+timedelta(days=1))
    history=await _load_history(db_session,conversation_id=conv.id,before_run=future)
    assert [m['content'] for m in history]==['U1','A1','U2','A2']
