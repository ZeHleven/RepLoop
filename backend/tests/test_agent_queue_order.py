"""Queue order must not be derived from a transaction's start timestamp."""
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
import pytest_asyncio
from evals.postgres_test_fixtures import engine, session_factory  # noqa: F401

from app.models.agent import AgentConversation, AgentRun
from app.models.user import User
from app.schemas.agent_task import ConversationTask, TaskSnapshot
from app.services.agent_jobs import enqueue_agent_run
from app.services.agent_task_state import load_task_snapshot

@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    # Override the main suite's shared schema setup; engine creates isolated tables.
    yield


async def test_delayed_transaction_retains_previous_completed_task(session_factory):
    samples=[]
    async def sample(session,tag):
        row=(await session.execute(text('select transaction_timestamp()::text, clock_timestamp()::text, pg_backend_pid()'))).one()
        samples.append({'tag':tag,'txn':row[0],'clock':row[1],'pid':row[2],'host':datetime.now(timezone.utc).isoformat()})
    async with session_factory() as setup:
        user=User(id=uuid4().hex,email=uuid4().hex+'@example.invalid',password_hash='synthetic')
        setup.add(user); await setup.flush()
        conv=AgentConversation(user_id=user.id); setup.add(conv); await setup.commit()
        uid,cid=user.id,conv.id
    async with session_factory() as delayed:
        txn_start=await delayed.scalar(text('select transaction_timestamp()'))
        await sample(delayed,'delayed_begin')
        await asyncio.sleep(.03)
        async with session_factory() as first_session:
            await sample(first_session,'first_begin')
            first=await enqueue_agent_run(first_session,user_id=uid,user_message='卧推4组7次',client_request_id=uuid4().hex,
                conversation=await first_session.get(AgentConversation,cid))
            first.run.status='completed'
            first.run.execution_trace={'task_state':TaskSnapshot(active_task_id='task',tasks=[
                ConversationTask(id='task',last_run_id=first.run.id,request='卧推4组7次')]).model_dump(mode='json')}
            await first_session.commit()
            await sample(first_session,'first_done')
            first_id,first_time=first.run.id,first.run.queued_at
        second=await enqueue_agent_run(delayed,user_id=uid,user_message='只改5组',client_request_id=uuid4().hex,
            conversation=await delayed.get(AgentConversation,cid))
        state=await load_task_snapshot(delayed,second.run)
        await sample(delayed,'second_done')
        evidence={'transaction_started':str(txn_start),'first_id':first_id,'first_queued_at':str(first_time),
                  'second_id':second.run.id,'second_queued_at':str(second.run.queued_at),'restored_task':state.active_task_id,'samples':samples}
        if os.environ.get('QUEUE_ORDER_EVIDENCE'):
            Path(os.environ['QUEUE_ORDER_EVIDENCE']).write_text(json.dumps(evidence,indent=2),encoding='utf-8')
        assert state.active_task_id=='task',evidence


async def test_queue_position_handles_reversed_time_and_keeps_future_out(session_factory):
    from datetime import timedelta
    from app.models.agent import AgentMessage
    from app.services.agent_jobs import claim_next_agent_run
    from app.services.agent_runtime import _load_history
    async with session_factory() as db:
        user=User(id=uuid4().hex,email=uuid4().hex+'@example.invalid',password_hash='synthetic')
        db.add(user); await db.flush()
        conv=AgentConversation(user_id=user.id); db.add(conv); await db.commit()
        runs=[]
        for message in ['first','second','future']:
            result=await enqueue_agent_run(db,user_id=user.id,user_message=message,client_request_id=uuid4().hex,conversation=conv)
            runs.append(result.run)
        now=datetime.now(timezone.utc)
        for i,run in enumerate(runs): run.queued_at=now-timedelta(seconds=i)
        await db.commit()
        assert [r.queue_position for r in runs]==[1,2,3]
        assert await claim_next_agent_run(db)==runs[0].id
        runs[0].status='completed'
        runs[0].execution_trace={'task_state':TaskSnapshot(active_task_id='old',tasks=[ConversationTask(id='old',request='first',last_run_id=runs[0].id)]).model_dump(mode='json')}
        db.add(AgentMessage(conversation_id=conv.id,run_id=runs[0].id,role='assistant',content='first answer',created_at=now-timedelta(days=1)))
        # A completed future state must not leak backwards even with an earlier clock.
        runs[2].status='completed'
        runs[2].execution_trace={'task_state':TaskSnapshot(active_task_id='future',tasks=[ConversationTask(id='future',request='future',last_run_id=runs[2].id)]).model_dump(mode='json')}
        await db.commit()
        state=await load_task_snapshot(db,runs[1]); assert state.active_task_id=='old'
        history=await _load_history(db,conversation_id=conv.id,before_run=runs[1])
        assert [m['content'] for m in history]==['first','first answer']
        assert await claim_next_agent_run(db)==runs[1].id


async def test_concurrent_enqueue_allocates_unique_positions_and_replays(session_factory):
    async with session_factory() as db:
        user=User(id=uuid4().hex,email=uuid4().hex+'@example.invalid',password_hash='synthetic')
        db.add(user); await db.flush()
        conv=AgentConversation(user_id=user.id); db.add(conv); await db.commit()
        uid,cid=user.id,conv.id
    async def submit(key):
        async with session_factory() as db:
            result=await enqueue_agent_run(db,user_id=uid,user_message=key,client_request_id=key,conversation=await db.get(AgentConversation,cid))
            return result.run.id,result.run.queue_position
    rows=await asyncio.gather(*(submit('request-'+str(i)) for i in range(6)))
    assert sorted(p for _,p in rows)==list(range(1,7))
    replay=await asyncio.gather(submit('duplicate'),submit('duplicate'))
    assert replay[0]==replay[1] and replay[0][1]==7
