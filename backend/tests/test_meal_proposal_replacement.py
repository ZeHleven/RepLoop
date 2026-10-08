"""Real PostgreSQL draft lifecycle checks, isolated from device acceptance data."""
import asyncio
from datetime import datetime,timedelta,timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from evals.postgres_test_fixtures import engine,session_factory,db_session  # noqa: F401
from evals.tests.test_revision_tasks import seed_task
from app.models.agent import AgentConversation,AgentProposal,AgentRun
from app.models.meal import MealLog
from app.schemas.agent_task import ConversationTask,PendingMealDraft,TaskSnapshot
from app.schemas.plan_management_proposal import GenericProposalDecisionRequest
from app.services.agent_domain_proposals import create_agent_meal_create_proposal,decide_agent_domain_proposal
from app.services.agent_intent import ChangeRequest,IntentResolution
from app.services.agent_runtime import _create_structured_mutation_proposal
from app.services.plan_management_proposals import PlanProposalError
from app.services.training_lifecycle import lock_training_user

@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    yield  # Use the explicit per-test schema, never the main suite's shared tables.

def changes(amount=90):
    return [ChangeRequest(resource='nutrition',operation='create',field_path='meal',value={
        'logged_at':'2026-09-20','meal_type':'晚餐','items':[
            {'food_reference':'米饭','amount_g':150},{'food_reference':'鸡胸','amount_g':amount}]} )]

async def new_run(db,uid,cid):
    run=AgentRun(user_id=uid,conversation_id=cid,status='completed')
    db.add(run);await db.commit();return run.id

async def create(db,uid,cid,rid,old=None,amount=90):
    return await create_agent_meal_create_proposal(db,enabled=True,user_id=uid,conversation_id=cid,
        run_id=rid,changes=changes(amount),supersedes_proposal_id=old)

async def confirm(db,uid,ref):
    return await decide_agent_domain_proposal(db,user_id=uid,proposal_id=ref.id,action='confirm',
        request=GenericProposalDecisionRequest(expected_version=ref.version,client_request_id=uuid4().hex))

async def setup(db):
    uid,cid=await seed_task(db);rid=await new_run(db,uid,cid)
    first=await create(db,uid,cid,rid)
    rid2=await new_run(db,uid,cid)
    return uid,cid,first,rid2

async def test_replacement_replay_and_independent_meal(db_session):
    db=db_session;uid,cid,first,rid=await setup(db)
    independent=await create(db,uid,cid,await new_run(db,uid,cid))
    second=await create(db,uid,cid,rid,first.id,110)
    again=await create(db,uid,cid,rid,first.id,110)
    assert again.id==second.id
    old=await db.get(AgentProposal,first.id);await db.refresh(old)
    assert old.status=='stale' and old.version==first.version+1 and old.last_error_code=='proposal_superseded'
    assert (await db.get(AgentProposal,independent.id)).status=='pending_confirmation'
    with pytest.raises(PlanProposalError,match='提案已不能继续决策'):await confirm(db,uid,first)
    assert not list((await db.scalars(select(MealLog))).all())
    applied=await confirm(db,uid,second);replayed=await confirm(db,uid,second)
    assert applied.result_data==replayed.result_data
    assert len(list((await db.scalars(select(MealLog))).all()))==1

@pytest.mark.parametrize('state',['applied','rejected','expired','other_conversation','other_user','invalid_food'])
async def test_unavailable_or_invalid_revision_preserves_proposals(db_session,state):
    db=db_session;uid,cid,first,rid=await setup(db)
    old=await db.get(AgentProposal,first.id)
    if state=='applied':await confirm(db,uid,first)
    elif state=='rejected':old.status=state
    elif state=='expired':
        old.created_at=datetime.now(timezone.utc)-timedelta(hours=1)
        old.expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)
    elif state=='other_conversation':
        conv=AgentConversation(user_id=uid);db.add(conv);await db.flush();cid=conv.id
    elif state=='other_user':
        from app.models.user import User
        user=User(email=uuid4().hex+'@example.com',password_hash='synthetic');db.add(user);await db.flush();uid=user.id
    before=old.status;await db.commit()
    request_changes=changes()
    if state=='invalid_food':request_changes[0].value['items'][0]['food_reference']='不存在的测试食品'
    with pytest.raises(PlanProposalError):
        await create_agent_meal_create_proposal(db,enabled=True,user_id=uid,conversation_id=cid,run_id=rid,
            changes=request_changes,supersedes_proposal_id=first.id)
    await db.commit()  # A handled validation error must not retire the old card.
    await db.refresh(old)
    assert old.status==before
    assert len(list((await db.scalars(select(AgentProposal))).all()))==1
    assert len(list((await db.scalars(select(MealLog))).all()))==(1 if state=='applied' else 0)

async def test_creation_failure_rolls_back_retirement(db_session,monkeypatch):
    from unittest.mock import AsyncMock
    db=db_session;uid,cid,first,rid=await setup(db)
    monkeypatch.setattr('app.services.agent_domain_proposals._persist',AsyncMock(side_effect=RuntimeError('injected persistence failure')))
    with pytest.raises(RuntimeError,match='injected persistence failure'):
        await create(db,uid,cid,rid,first.id,110)
    await db.commit()
    old=await db.get(AgentProposal,first.id)
    assert old.status=='pending_confirmation'
    assert len(list((await db.scalars(select(AgentProposal))).all()))==1

@pytest.mark.parametrize('transition',['new','continue','resume'])
async def test_runtime_only_retires_draft_for_same_task_revision(db_session,monkeypatch,transition):
    from app.config import settings
    monkeypatch.setattr(settings,'AGENT_NUTRITION_PROPOSALS_ENABLED',True)
    db=db_session;uid,cid,first,rid=await setup(db)
    old=await db.get(AgentProposal,first.id)
    after=old.payload_data['after']
    draft=PendingMealDraft(logged_at=after['logged_at'],meal_type=after['meal_type'],items=[
        {k:item[k] for k in ('food_id','food_name','amount_g')} for item in after['items']])
    state=TaskSnapshot(active_task_id='meal-task',transition=transition,tasks=[ConversationTask(
        id='meal-task',request='修订餐食',last_run_id=rid,proposal_id=first.id,proposal_pending=True,pending_meal=draft)])
    resolution=IntentResolution(primary_intent='nutrition_today_query',intent_domain='nutrition',
        request_kind='mutation',requested_effect='create',resolved_query='鸡胸110克',confidence=1,change_requests=changes(110))
    await _create_structured_mutation_proposal(db,run=await db.get(AgentRun,rid),
        conversation=await db.get(AgentConversation,cid),resolution=resolution,task_state=state)
    await db.refresh(old)
    assert old.status==('pending_confirmation' if transition=='new' else 'stale')

@pytest.mark.parametrize('winner',['revision','confirmation'])
async def test_confirmation_and_revision_serialize_even_with_cached_old_row(session_factory,winner):
    async with session_factory() as db:uid,cid,first,rid=await setup(db)
    entered=asyncio.Event()
    async with session_factory() as holder,session_factory() as waiter:
        cached=await waiter.get(AgentProposal,first.id)
        await lock_training_user(holder,uid)
        async def waiting():
            entered.set()
            try:
                result=await (confirm(waiter,uid,first) if winner=='revision' else create(waiter,uid,cid,rid,first.id,110))
                return result
            except PlanProposalError as exc:
                await waiter.rollback();return exc.code
        pending=asyncio.create_task(waiting());await entered.wait()
        try:
            if winner=='revision':await create(holder,uid,cid,rid,first.id,110)
            else:await confirm(holder,uid,first)
            assert await asyncio.wait_for(pending,5)=='proposal_not_pending'
        finally:
            if not pending.done():pending.cancel()
            await asyncio.gather(pending,return_exceptions=True)
    async with session_factory() as db:
        rows=list((await db.scalars(select(AgentProposal))).all())
        assert sorted(r.status for r in rows)==(['pending_confirmation','stale'] if winner=='revision' else ['applied'])
        assert len(list((await db.scalars(select(MealLog))).all()))==(0 if winner=='revision' else 1)
