"""Clock skew/rollback and item-specific meal gaps, exercised against PostgreSQL."""
from datetime import datetime,timedelta,timezone
from unittest.mock import patch,AsyncMock
from uuid import uuid4
import pytest
import pytest_asyncio
from sqlalchemy import select,func,update
from app.config import settings
from app.models.user import User
from app.models.agent import AgentConversation,AgentRun
from app.services import agent_jobs as jobs
from app.services.agent_intent import ChangeRequest
from app.services.agent_change_validation import validate_semantic_changes
from evals.postgres_test_fixtures import engine,session_factory,db_session  # noqa: F401

@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    # Override the suite's shared schema. Claiming consumes a global queue,
    # so each clock scenario needs the existing isolated-schema fixture.
    yield

async def run_row(db,status='running',attempt=1):
    user=User(password_hash='synthetic-not-a-login');db.add(user);await db.flush()
    conv=AgentConversation(user_id=user.id,title='synthetic clock test');db.add(conv);await db.flush()
    now=await db.scalar(select(func.clock_timestamp()))
    run=AgentRun(user_id=user.id,conversation_id=conv.id,status=status,attempt_count=attempt,lease_expires_at=now+timedelta(seconds=settings.AGENT_RUN_LEASE_SECONDS),idempotency_key=str(uuid4()))
    db.add(run);await db.commit();return run,now

@pytest.mark.asyncio
@pytest.mark.parametrize('skew',[-120,120])
async def test_worker_clock_does_not_shorten_or_overextend_lease(db_session,skew):
    run,now=await run_row(db_session);old=run.lease_expires_at
    with patch.object(jobs,'datetime') as clock:
        clock.now.return_value=now+timedelta(seconds=skew)
        assert await jobs.renew_agent_run_lease(db_session,run_id=run.id,expected_attempt_count=1)
    await db_session.refresh(run)
    assert old<=run.lease_expires_at<=now+timedelta(seconds=settings.AGENT_RUN_LEASE_SECONDS+5)

@pytest.mark.asyncio
@pytest.mark.parametrize('skew',[-120,120])
async def test_claim_uses_same_clock_as_renewal(db_session,skew):
    run,now=await run_row(db_session,'queued',0)
    with patch.object(jobs,'datetime') as clock:
        clock.now.return_value=now+timedelta(seconds=skew)
        assert await jobs.claim_next_agent_run(db_session)==run.id
    await db_session.refresh(run)
    assert now<=run.processing_started_at<=now+timedelta(seconds=5)
    assert now+timedelta(seconds=settings.AGENT_RUN_LEASE_SECONDS)<=run.lease_expires_at<=now+timedelta(seconds=settings.AGENT_RUN_LEASE_SECONDS+5)

@pytest.mark.asyncio
async def test_database_clock_rollback_preserves_deadline(db_session,monkeypatch):
    run,now=await run_row(db_session);old=run.lease_expires_at
    monkeypatch.setattr(jobs,'_lease_now',AsyncMock(return_value=now-timedelta(seconds=120)),raising=False)
    with patch.object(jobs,'datetime') as clock:
        clock.now.return_value=now-timedelta(seconds=120)
        assert await jobs.renew_agent_run_lease(db_session,run_id=run.id,expected_attempt_count=1)
    await db_session.refresh(run);assert run.lease_expires_at==old

@pytest.mark.asyncio
@pytest.mark.parametrize('status,attempt,expected',[('running',2,1),('completed',1,1),('failed',1,1)])
async def test_stale_or_terminal_owner_cannot_renew(db_session,status,attempt,expected):
    run,now=await run_row(db_session,status,attempt);old=run.lease_expires_at
    assert not await jobs.renew_agent_run_lease(db_session,run_id=run.id,expected_attempt_count=expected)
    await db_session.refresh(run);assert run.lease_expires_at==old

@pytest.mark.asyncio
async def test_late_heartbeat_cannot_overwrite_newer_deadline(db_session,session_factory,monkeypatch):
    run,now=await run_row(db_session)
    # An earlier sample arrives after another heartbeat has extended the row.
    newer=now+timedelta(seconds=settings.AGENT_RUN_LEASE_SECONDS+60)
    async with session_factory() as other:
        await other.execute(update(AgentRun).where(AgentRun.id==run.id).values(lease_expires_at=newer));await other.commit()
    monkeypatch.setattr(jobs,'_lease_now',AsyncMock(return_value=now))
    assert await jobs.renew_agent_run_lease(db_session,run_id=run.id,expected_attempt_count=1)
    await db_session.refresh(run);assert run.lease_expires_at==newer

@pytest.mark.asyncio
async def test_fast_worker_cannot_steal_active_run_but_expiry_is_recoverable(db_session):
    run,now=await run_row(db_session)
    with patch.object(jobs,'datetime') as clock:
        clock.now.return_value=now+timedelta(hours=1)
        assert await jobs.claim_next_agent_run(db_session) is None
    run.lease_expires_at=now-timedelta(seconds=1);await db_session.commit()
    assert await jobs.claim_next_agent_run(db_session)==run.id
    await db_session.refresh(run);assert run.attempt_count==2
    assert not await jobs.renew_agent_run_lease(db_session,run_id=run.id,expected_attempt_count=1)

def meal(items,**extra):
    value={'logged_at':'2026-09-20','meal_type':'午餐','items':items,**extra}
    return validate_semantic_changes(intent_domain='nutrition',request_kind='mutation',requested_effect='create',change_requests=[ChangeRequest(resource='nutrition',operation='create',field_path='meal',value=value)])

@pytest.mark.parametrize('amount',[None,0,-1,True,'2',float('inf'),10001])
def test_only_oil_quantity_is_requested(amount):
    result=meal([{'food_name':'米饭','amount_g':160},{'food_name':'鸡胸','amount_g':80},{'food_name':'橄榄油','amount_g':amount}])
    assert not result.complete
    assert result.clarification_question=='请补充橄榄油的克数。'

@pytest.mark.parametrize('items,question',[
 ([{'food_name':'米饭','amount_g':160},{'food_name':'鸡胸'},{'food_name':'橄榄油'}],'请补充鸡胸的克数、橄榄油的克数。'),
 ([{'food_name':'米饭','amount_g':160},{'amount_g':80}],'请补充第2项的食品名称。'),
 ([{'food_name':'米饭','amount_g':160},{}],'请补充第2项的食品名称、第2项的克数。'),
 ([{'food_id':'private-db-id','amount_g':None}],'请补充第1项的克数。'),
 ([{'food_name':'米饭','amount_g':160},{'food_name':'米饭'}],'请补充第2项（米饭）的克数。'),
 ([{'food_name':'米饭','amount_g':160},None],'请补充第2项的食品名称、第2项的克数。'),
])
def test_gaps_identify_only_unknown_items_and_fields(items,question):
    result=meal(items);assert result.clarification_question==question

def test_combined_metadata_and_item_gaps():
    result=meal([{'food_name':'米饭','amount_g':160},{'food_name':'橄榄油'}],logged_at=None,meal_type=None)
    assert result.clarification_question=='请补充记录日期、餐次、橄榄油的克数。'

def test_complete_meal_needs_no_clarification():
    assert meal([{'food_name':'米饭','amount_g':160},{'food_name':'橄榄油','amount_g':2}]).complete
