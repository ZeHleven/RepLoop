"""Owned diagnostic API plus scoped, read-only SQL; no execution or budget reset."""
import copy
import json
from datetime import datetime, timezone
from sqlalchemy import select, text
from app.models.agent import AgentRun, AgentAttemptJournal


async def collect_attempt_snapshot(request, factory, *, run_id, user_id, conversation_id):
    # Caller must finish business latency measurement BEFORE this diagnostic.
    observation={"run_id":run_id,"history":None,"sql":None,"capture_errors":[]}
    try:
        response=await request('GET','/api/v1/agent/runs/'+run_id+'/attempts')
        if response.status_code!=200:
            observation['capture_errors'].append('attempt_api_http_'+str(response.status_code))
            if response.status_code in (401,403,404):
                return observation
        else:
            observation['history']=response.json()
    except Exception as exc:
        observation['capture_errors'].append('attempt_api_'+type(exc).__name__)
    # SQL collection is scoped by the independent account/conversation registry.
    # It does not claim, renew, retry, modify rows or override a stopped budget.
    try:
        async with factory() as db:
            await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
            if (await db.execute(text('SHOW transaction_read_only'))).scalar_one() != 'on':
                raise RuntimeError('SQL observer is not read-only')
            run=await db.scalar(select(AgentRun).where(AgentRun.id==run_id,AgentRun.user_id==user_id,AgentRun.conversation_id==conversation_id))
            if run is None:
                observation['capture_errors'].append('sql_scope_mismatch')
            else:
                rows=(await db.scalars(select(AgentAttemptJournal).where(AgentAttemptJournal.run_id==run_id).order_by(AgentAttemptJournal.worker_attempt))).all()
                observation['sql']={"run_id":run_id,"user_id":user_id,"conversation_id":conversation_id,
                    "run_status":run.status,"attempt_count":run.attempt_count,"read_only":True,"isolation":"repeatable read",
                    "attempts":[json.loads(json.dumps({c.name:getattr(r,c.name) for c in r.__table__.columns},default=str)) for r in rows]}
    except Exception as exc:
        observation['capture_errors'].append('attempt_sql_'+type(exc).__name__)
    return observation


def record_attempt_snapshot(record, observation):
    """Keep capture history; latest selection is explicit, never add duplicates."""
    record['attempt_observation_version']=1
    events=record.setdefault('attempt_observation_events',[])
    events.append({'captured_at':datetime.now(timezone.utc).isoformat(),'observation':copy.deepcopy(observation)})
    rows=record.setdefault('attempt_observations',[])
    index=next((i for i,r in enumerate(rows) if r['run_id']==observation['run_id']),None)
    if index is None:rows.append(copy.deepcopy(observation))
    else:rows[index]=copy.deepcopy(observation)
