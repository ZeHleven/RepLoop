"""Evidence semantics: no meal-count inference, no fabricated stored targets."""
from datetime import date
from uuid import uuid4
import pytest
import pytest_asyncio
from evals.postgres_test_fixtures import engine, session_factory, db_session
from app.models.user import User
from app.models.profile import UserProfile
from app.models.meal import MealLog, MealItem
from app.services.agent_intent import IntentResolution, normalize_resolution, route_tools
from app.services.agent_tools import build_read_tools
from app.services.business_clock import BUSINESS_DATE


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    # Isolated function-scoped PostgreSQL fixture owns schema lifecycle.
    yield


def assessment(**kwargs):
    return IntentResolution(**dict(dict(primary_intent='nutrition_history_query', intent_domain='nutrition',
        request_kind='assessment', requested_effect='read', evidence_requirements=['nutrition_history'],
        resolved_query='评估近三天营养记录和目标达成情况', confidence=0.99), **kwargs))


def test_existing_profile_required_for_nutrition_assessment():
    r=normalize_resolution('我这几天吃得怎么样，三大营养素是否达标，是否需要调整。',assessment())
    from app.services.agent_intent import required_read_tools
    assert set(route_tools(r))=={'nutrition.list_history','nutrition.get_recent_context','profile.get_summary'}
    assert set(required_read_tools(r))=={'nutrition.get_recent_context','profile.get_summary'}
    assert r.optional_evidence_requirements==['nutrition_history']


@pytest.mark.parametrize('kind',['query','generation','mutation'])
def test_non_assessment_does_not_gain_profile_authority(kind):
    r=assessment(request_kind=kind)
    assert 'profile.get_summary' not in route_tools(r)


@pytest.mark.parametrize('tool_id',['nutrition.get_today','nutrition.list_history','nutrition.get_recent_context'])
@pytest.mark.parametrize('meal_count',[0,1,3])
@pytest.mark.asyncio
async def test_database_meal_count_cannot_prove_full_day_intake(db_session,tool_id,meal_count):
    uid='r47-'+uuid4().hex
    db_session.add(User(id=uid,email=uid+'@example.invalid',password_hash='synthetic'))
    await db_session.flush()
    for mt in ['早餐','午餐','晚餐'][:meal_count]:
        m=MealLog(user_id=uid,logged_at=date(2026,10,9),meal_type=mt);db_session.add(m);await db_session.flush()
        db_session.add(MealItem(meal_id=m.id,food_name='合成食物',amount_g=100,calories=310,protein_g=36,carbs_g=28,fat_g=5.5))
    await db_session.commit()
    token=BUSINESS_DATE.set(date(2026,10,9))
    try:
        tool=build_read_tools(db_session,user_id=uid,allowlist=[tool_id])[0]
        result=await tool.ainvoke({})
        assert result['evidence_limits']['intake_basis']=='logged_items_only'
        assert result['evidence_limits']['whole_day_completeness']=='unknown'
        assert result['evidence_limits']['unlogged_intake_is_zero'] is False
    finally:BUSINESS_DATE.reset(token)


@pytest.mark.asyncio
async def test_stored_weight_and_goal_are_not_numeric_macro_targets(db_session):
    uid='r47-'+uuid4().hex
    db_session.add(User(id=uid,email=uid+'@example.invalid',password_hash='synthetic'));await db_session.flush()
    db_session.add(UserProfile(user_id=uid,weight_kg=65,primary_goal='保持体重'));await db_session.commit()
    result=await build_read_tools(db_session,user_id=uid,allowlist=['profile.get_summary'])[0].ainvoke({})
    assert result['weight_kg']==65 and result['primary_goal']=='保持体重'
    assert result['nutrition_targets'] is None


@pytest.mark.asyncio
async def test_absent_profile_is_not_default_personal_data(db_session):
    uid='r47-'+uuid4().hex
    db_session.add(User(id=uid,email=uid+'@example.invalid',password_hash='synthetic'));await db_session.commit()
    result=await build_read_tools(db_session,user_id=uid,allowlist=['profile.get_summary'])[0].ainvoke({})
    assert result=={'found':False}
