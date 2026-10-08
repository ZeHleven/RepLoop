"""SQL ownership/lifecycle checks for hydration of an unconfirmed meal draft."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.models.agent import AgentConversation, AgentProposal, AgentRun
from app.models.user import User
from app.schemas.agent_task import ConversationTask, PendingMealDraft, TaskSnapshot
from app.services.agent_task_state import load_task_snapshot


@pytest.mark.parametrize('scenario', ['pending', 'expired', 'applied', 'rejected', 'other_user', 'other_conversation'])
async def test_pending_meal_is_loaded_only_from_live_owned_proposal(db_session, scenario):
    # The loader does not query the food catalog. Avoid seeding unrelated foods
    # into the main suite's shared catalog (which has bounded list endpoints).
    user = User(id=uuid4().hex, email=uuid4().hex+'@example.invalid', password_hash='synthetic')
    other = User(id=uuid4().hex, email=uuid4().hex+'@example.invalid', password_hash='synthetic')
    db_session.add_all([user,other]); await db_session.flush()
    conversation = AgentConversation(user_id=user.id)
    other_conversation = AgentConversation(user_id=other.id)
    db_session.add_all([conversation,other_conversation]); await db_session.flush()
    same_user_conversation = AgentConversation(user_id=user.id)
    db_session.add(same_user_conversation)
    await db_session.flush()
    now = datetime.now(timezone.utc)
    meal = PendingMealDraft(logged_at='2026-09-19', meal_type='午餐', items=[
        {'food_id':'synthetic-rice', 'food_name':'米饭', 'amount_g':170}])
    proposal = AgentProposal(user_id=other.id if scenario=='other_user' else user.id,
        conversation_id=other_conversation.id if scenario=='other_user' else
            same_user_conversation.id if scenario=='other_conversation' else conversation.id,
        proposal_type='meal_log_create_v1', payload_data={'proposal_type':'meal_log_create_v1', 'after':meal.model_dump()},
        created_at=now-timedelta(hours=2), expires_at=now+timedelta(hours=1), status='pending_confirmation')
    if scenario=='expired':
        proposal.expires_at=now-timedelta(hours=1)  # still pending; expiry must be checked independently
    elif scenario=='applied':
        proposal.status='applied'; proposal.decision_action='confirm'
        proposal.decision_client_request_id='synthetic-confirm'; proposal.confirmed_at=now
        proposal.applied_at=now; proposal.result_data={'synthetic':True}
    elif scenario=='rejected':
        proposal.status='rejected'; proposal.decision_action='reject'
        proposal.decision_client_request_id='synthetic-reject'; proposal.rejected_at=now
    db_session.add(proposal); await db_session.flush()
    # Persisted state falsely claims pending; only the proposal row can establish it.
    task = ConversationTask(id='task', request='记录午餐', last_run_id='previous',
        proposal_id=proposal.id, proposal_pending=True, pending_meal=meal)
    previous = AgentRun(user_id=user.id, conversation_id=conversation.id, status='completed',
        queued_at=now-timedelta(seconds=5), execution_trace={'task_state':TaskSnapshot(active_task_id='task',tasks=[task]).model_dump(mode='json')})
    current = AgentRun(user_id=user.id, conversation_id=conversation.id, status='queued', queued_at=now)
    db_session.add_all([previous,current]); await db_session.commit()
    loaded = (await load_task_snapshot(db_session,current)).tasks[0]
    assert loaded.proposal_pending is (scenario=='pending')
    assert loaded.pending_meal == (meal if scenario=='pending' else None)
