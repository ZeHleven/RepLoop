"""Correct structured intent must survive plan-gap and meal-draft boundaries."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import func, select

from app.config import settings
from app.models.agent import AgentConversation, AgentProposal, AgentRun
from app.models.food import Food
from app.models.meal import MealLog
from app.models.user import User
from app.schemas.agent_task import ConversationTask, PlanInputGap, TaskSnapshot, TaskUpdate
from app.schemas.meal import MealLogCreate
from app.schemas.plan_management_proposal import GenericProposalDecisionRequest
from app.services.agent_domain_proposals import create_agent_meal_create_proposal, decide_agent_domain_proposal
from app.services.agent_intent import ChangeRequest, IntentResolution
from app.services.agent_plan_completeness import enforce_plan_completeness
from app.services.agent_runtime import _create_structured_mutation_proposal
from app.services.agent_task_state import _pending_meal, load_task_snapshot, revise_pending_meal
from app.services.plan_management_proposals import PlanProposalError
from evals.postgres_test_fixtures import engine, session_factory, db_session  # noqa: F401


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    yield  # Database tests use the explicitly isolated per-test schema above.


def change(field, target, value):
    return ChangeRequest(resource='workout_plan', operation='update', field_path=field,
                         target_reference=target, value=value)


def resolution(changes, gaps=()):
    return IntentResolution(primary_intent='plan_query', intent_domain='workout_plan',
        request_kind='mutation', requested_effect='update', confidence=.95,
        change_requests=changes, plan_input_gaps=list(gaps))


def task_state():
    return TaskSnapshot(active_task_id='task', tasks=[ConversationTask(
        id='task', request='调整当前训练计划', last_run_id='run')], transition='new')


@pytest.mark.parametrize('punctuation', ['，', ','])
def test_explicit_sets_are_not_deferred_by_later_reps_clause(punctuation):
    state=task_state()
    first=change('exercise.sets', '卧推', 4)
    result=enforce_plan_completeness(resolution([first]), state,
        f'把当前训练计划的卧推组数改为4组{punctuation}次数稍后再补。')
    assert {(g.field_path, g.target_reference) for g in result.plan_input_gaps} == {('exercise.reps','卧推')}
    state.transition='continue'
    completed=enforce_plan_completeness(resolution([first,change('exercise.reps','卧推',8)]), state,
        '卧推次数设为8次，现在生成完整提案。')
    assert not completed.clarification_required and not completed.plan_input_gaps


@pytest.mark.parametrize('provide_typed_gap', [True, False])
def test_other_exercise_never_borrows_target_from_known_change(provide_typed_gap):
    state=task_state()
    first=change('exercise.sets','卧推',4)
    gaps=[{'field_path':'exercise.rest_seconds','target_reference':'深蹲','quote':'深蹲休息稍后补'}] if provide_typed_gap else []
    result=enforce_plan_completeness(resolution([first],gaps),state,
        '把当前训练计划的卧推改为4组，深蹲休息稍后补。')
    assert all(g.target_reference != '卧推' for g in result.plan_input_gaps)
    assert len(result.plan_input_gaps)==1
    state.transition='continue'
    completed=enforce_plan_completeness(resolution([first,change('exercise.rest_seconds','深蹲',90)]),state,
        '深蹲休息90秒，现在生成完整提案。')
    assert not completed.clarification_required and not completed.plan_input_gaps


def test_typed_gap_without_any_known_values_does_not_gain_an_unknown_target():
    state=task_state()
    result=enforce_plan_completeness(resolution([], [
        {'field_path':'exercise.reps','target_reference':'卧推','quote':'卧推次数稍后再补'}]),state,
        '调整当前训练计划，卧推次数稍后再补。')
    assert [(g.field_path,g.target_reference) for g in result.plan_input_gaps] == [('exercise.reps','卧推')]
    state.transition='continue'
    completed=enforce_plan_completeness(resolution([change('exercise.reps','卧推',8)]),state,
        '卧推次数设为8次，现在生成完整提案。')
    assert not completed.clarification_required


def test_parallel_deferred_fields_keep_both_goals():
    state=task_state()
    result=enforce_plan_completeness(resolution([change('exercise.sets','卧推',4)], [
        {'field_path':'exercise.reps','target_reference':'卧推','quote':'次数稍后补'},
        {'field_path':'exercise.rest_seconds','target_reference':'深蹲','quote':'深蹲休息待定'}]),state,
        '卧推组数改为4组，次数稍后补，深蹲休息待定。')
    assert {(g.field_path,g.target_reference) for g in result.plan_input_gaps} == {
        ('exercise.reps','卧推'),('exercise.rest_seconds','深蹲')}
    state.transition='continue'
    result=enforce_plan_completeness(resolution([change('exercise.sets','卧推',4),change('exercise.reps','卧推',8)]),state,
        '卧推次数8次。')
    assert [(g.field_path,g.target_reference) for g in result.plan_input_gaps] == [('exercise.rest_seconds','深蹲')]


@pytest.mark.parametrize('message', ['卧推次数稍后补', '卧推次数8次，次数仍待补'])
def test_current_deferral_cannot_be_fulfilled_by_a_model_guessed_number(message):
    result=enforce_plan_completeness(resolution([change('exercise.reps','卧推',8)]),task_state(),message)
    assert result.clarification_required
    assert [(g.field_path,g.target_reference) for g in result.plan_input_gaps] == [('exercise.reps','卧推')]


def test_unresolved_other_target_is_not_fulfilled_by_unrelated_number():
    state=task_state()
    enforce_plan_completeness(resolution([change('exercise.sets','卧推',4)]),state,
        '卧推改为4组，深蹲休息稍后补。')
    state.transition='continue'
    result=enforce_plan_completeness(resolution([change('exercise.rest_seconds','卧推',90)]),state,'卧推休息90秒。')
    assert result.clarification_required


@pytest.mark.parametrize('connector',['但','不过','而','先','至于'])
@pytest.mark.parametrize('quote_kind',['clause','sentence','no_typed_gap'])
def test_deferred_parameter_connectors_keep_the_same_exercise(connector,quote_kind):
    state=task_state()
    first=change('exercise.sets','卧推',4)
    message=f'卧推改为4组，{connector}次数稍后补。'
    quote=message if quote_kind=='sentence' else f'{connector}次数稍后补'
    gaps=[] if quote_kind=='no_typed_gap' else [{
        'field_path':'exercise.reps','target_reference':'卧推','quote':quote}]
    result=enforce_plan_completeness(resolution([first],gaps),state,message)
    assert [(g.field_path,g.target_reference) for g in result.plan_input_gaps]==[('exercise.reps','卧推')]
    state.transition='continue'
    result=enforce_plan_completeness(resolution([first,change('exercise.reps','卧推',8)]),state,
        '卧推次数8次，现在生成完整提案。')
    assert not result.clarification_required and not result.plan_input_gaps


@pytest.mark.parametrize('connector',['但','不过','而','先','至于'])
def test_connector_before_an_unfamiliar_exercise_does_not_inherit_previous_target(connector):
    state=task_state()
    result=enforce_plan_completeness(resolution([change('exercise.sets','卧推',4)]),state,
        f'卧推改为4组，{connector}深蹲次数稍后补。')
    assert len(result.plan_input_gaps)==1
    assert result.plan_input_gaps[0].target_reference is None
    state.transition='continue'
    unrelated=enforce_plan_completeness(resolution([change('exercise.reps','卧推',8)]),state,'卧推次数8次。')
    assert unrelated.clarification_required


def test_sourced_typed_gap_is_reused_without_requiring_a_known_connector():
    state=task_state()
    quote='另外有关次数我稍后补'
    result=enforce_plan_completeness(resolution([change('exercise.sets','卧推',4)],[{
        'field_path':'exercise.reps','target_reference':'卧推','quote':quote}]),state,
        '卧推改为4组，另外有关次数我稍后补。')
    assert [(g.field_path,g.target_reference,g.quote) for g in result.plan_input_gaps]==[('exercise.reps','卧推',quote)]


def test_old_quote_cannot_authorize_target_in_an_unfamiliar_current_clause():
    state=task_state()
    state.transition='continue'
    state.tasks[0].plan_input_gaps=[PlanInputGap(field_path='exercise.reps',target_reference='卧推',quote='卧推次数待定')]
    result=enforce_plan_completeness(resolution([change('exercise.sets','卧推',4)]),state,
        '另外有关次数我稍后补。')
    assert any(g.target_reference is None for g in result.plan_input_gaps)


def meal_items(count):
    return [{'food_id':f'synthetic-food-{i}','food_name':f'合成食品{i}','amount_g':10,
             'calories':10,'protein_g':0,'carbs_g':0,'fat_g':0} for i in range(count)]


@pytest.mark.parametrize('count',[20,21,30])
def test_pending_meal_accepts_every_allowed_production_count(count):
    meal=MealLogCreate(logged_at='2026-09-20',meal_type='午餐',items=meal_items(count))
    draft=_pending_meal({'proposal_type':'meal_log_create_v1','after':meal.model_dump(mode='json')})
    assert draft is not None and len(draft.items)==count


def test_meal_limits_remain_thirty():
    with pytest.raises(ValidationError):
        MealLogCreate(logged_at='2026-09-20',meal_type='午餐',items=meal_items(31))
    assert _pending_meal({'proposal_type':'meal_log_create_v1','after':{
        'logged_at':'2026-09-20','meal_type':'午餐','items':meal_items(31)}}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('count',[21,30])
async def test_large_meal_revision_retires_old_proposal_without_writing_a_meal(db_session,monkeypatch,count):
    db=db_session
    monkeypatch.setattr(settings,'AGENT_NUTRITION_PROPOSALS_ENABLED',True)
    user=User(password_hash='round26-synthetic-no-login')
    db.add(user);await db.flush()
    conversation=AgentConversation(user_id=user.id)
    db.add(conversation);await db.flush()
    foods=[Food(name_zh=f'第26轮合成食品{i}',category='grain',calories_per_100g=100,
                protein_g=0,carbs_g=0,fat_g=0) for i in range(count)]
    db.add_all(foods);await db.flush()
    moment=datetime.now(timezone.utc)
    first_run=AgentRun(user_id=user.id,conversation_id=conversation.id,status='completed',
                       queued_at=moment-timedelta(seconds=2),queue_position=1)
    next_run=AgentRun(user_id=user.id,conversation_id=conversation.id,status='running',
                      queued_at=moment,queue_position=2)
    db.add_all([first_run,next_run]);await db.commit()
    first_change=ChangeRequest(resource='nutrition',operation='create',field_path='meal',value={
        'logged_at':'2026-09-20','meal_type':'午餐',
        'items':[{'food_id':food.id,'amount_g':10} for food in foods]})
    original=await create_agent_meal_create_proposal(db,enabled=True,user_id=user.id,
        conversation_id=conversation.id,run_id=first_run.id,changes=[first_change])
    first_run.execution_trace={'task_state':TaskSnapshot(active_task_id='meal',tasks=[ConversationTask(
        id='meal',request='记录午餐',last_run_id=first_run.id,proposal_id=original.id)]).model_dump(mode='json')}
    await db.commit()
    state=await load_task_snapshot(db,next_run)
    assert state.tasks[0].pending_meal is not None
    assert len(state.tasks[0].pending_meal.items)==count
    state.transition='continue'
    update=TaskUpdate(action='continue',task_id='meal',trigger='这份待确认餐食改为晚餐')
    revised=revise_pending_meal([{'role':'task_state','content':state.model_dump_json()}],update,[
        ChangeRequest(resource='nutrition',operation='update',field_path='meal.meal_type',value='晚餐')])
    intent=IntentResolution(primary_intent='nutrition_today_query',intent_domain='nutrition',
        request_kind='mutation',requested_effect='create',confidence=1,change_requests=revised)
    current=await _create_structured_mutation_proposal(db,run=next_run,conversation=conversation,
                                                     resolution=intent,task_state=state)
    old=await db.get(AgentProposal,original.id);await db.refresh(old)
    new=await db.get(AgentProposal,current.id)
    assert old.status=='stale' and new.status=='pending_confirmation'
    assert new.payload_data['target']['supersedes_proposal_id']==old.id
    assert len(new.payload_data['after']['items'])==count
    assert new.payload_data['after']['meal_type']=='晚餐'
    assert await db.scalar(select(func.count()).select_from(MealLog))==0
    with pytest.raises(PlanProposalError,match='提案已不能继续决策'):
        await decide_agent_domain_proposal(db,user_id=user.id,proposal_id=old.id,action='confirm',
            request=GenericProposalDecisionRequest(expected_version=original.version,client_request_id=uuid4().hex))
    assert await db.scalar(select(func.count()).select_from(MealLog))==0
