"""Real PostgreSQL regressions from the production R27 food-name failure."""
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select

from evals.postgres_test_fixtures import engine, session_factory, db_session  # noqa: F401
from app.models.agent import AgentConversation, AgentProposal, AgentRun
from app.models.food import CustomFood, Food, FoodAlias
from app.models.meal import MealItem, MealLog
from app.models.user import User
from app.schemas.plan_management_proposal import GenericProposalDecisionRequest
from app.services.agent_domain_proposals import (
    create_agent_meal_create_proposal, decide_agent_domain_proposal,
)
from app.services.agent_intent import ChangeRequest
from app.services.food import normalize_food_reference, query_nutrition_database, resolve_food_reference
from app.services.food_library import query_library
from app.services.plan_management_proposals import PlanProposalError


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    yield  # Per-test PostgreSQL schema, not the shared suite's tables.


def food(name="白米饭（中粒米，熟）", **values):
    return Food(id=uuid4().hex, name_zh=name, category="碳水",
                calories_per_100g=130, protein_g=2.38, carbs_g=28.6,
                fat_g=0.21, **values)


@pytest.mark.parametrize("stored,reference", [
    ("白米饭（中粒米，熟）", "白米饭（中粒米，熟）"),
    ("白米饭（中粒米，熟）", "白米饭(中粒米,熟)"),
    ("白米饭(中粒米,熟)", "白米饭（中粒米，熟）"),
    ("\u3000白米饭（中粒米，熟）\t", " 白米饭(中粒米,熟) "),
    ("糙米\t\n饭", "糙米 饭"),
    ("糙米\u0085\u001c饭", "糙米 饭"),
    ("糙米\u00a0\u3000饭", "糙米 饭"),
    ("Café", "Cafe\u0301"),
])
async def test_exact_canonical_name_accepts_equivalent_unicode(db_session, stored, reference):
    row = food(stored)
    db_session.add(row)
    await db_session.commit()
    assert [f.id for f in await resolve_food_reference(db_session, reference=reference)] == [row.id]


async def test_normalized_chinese_name_precedes_english_and_alias(db_session):
    canonical = food()
    english = food("英文对应项", name_en="白米饭(中粒米,熟)")
    alias_target = food("别名对应项")
    db_session.add_all([canonical, english, alias_target,
        FoodAlias(id=uuid4().hex, food_id=alias_target.id, alias="白米饭(中粒米,熟)",
                  normalized_alias=normalize_food_reference("白米饭(中粒米,熟)"))])
    await db_session.commit()
    assert [f.id for f in await resolve_food_reference(db_session, reference="白米饭（中粒米，熟）")] == [canonical.id]


async def test_english_name_normalizes_width_case_and_whitespace(db_session):
    row = food("英文食品", name_en=" ＲＩＣＥ\t\u00a0ＣＯＯＫＥＤ（ＭＥＤＩＵＭ） ")
    db_session.add(row)
    await db_session.commit()
    assert [f.id for f in await resolve_food_reference(db_session, reference="rice cooked(medium)")] == [row.id]


async def test_normalization_collision_remains_ambiguous(db_session):
    rows = [food(), food("白米饭(中粒米,熟)")]
    db_session.add_all(rows)
    await db_session.commit()
    matched = await resolve_food_reference(db_session, reference="白米饭（中粒米，熟）")
    assert {f.id for f in matched} == {f.id for f in rows}
    assert [f.id for f in await resolve_food_reference(db_session, food_id=rows[0].id, reference="无关名称")] == [rows[0].id]
    assert await resolve_food_reference(db_session, food_id="missing", reference=rows[0].name_zh) == []


@pytest.mark.parametrize("reference", ["白米饭", "白米饭（中粒米，生）", "%", " "])
async def test_similar_unknown_or_empty_name_never_resolves(db_session, reference):
    db_session.add(food())
    await db_session.commit()
    assert await resolve_food_reference(db_session, reference=reference) == []


async def test_inactive_food_cannot_win_after_normalization(db_session):
    inactive, active = food(is_active=False), food("白米饭(中粒米,熟)")
    db_session.add_all([inactive, active])
    await db_session.commit()
    assert [f.id for f in await resolve_food_reference(db_session, reference=inactive.name_zh)] == [active.id]
    assert await resolve_food_reference(db_session, food_id=inactive.id) == []


@pytest.mark.parametrize("reference", ["白米饭（中粒米，熟）", "白米饭(中粒米,熟)", "（中粒米，熟）"])
async def test_nutrition_and_library_search_accept_copied_names(db_session, reference):
    row = food()
    db_session.add(row)
    await db_session.commit()
    assert [f.id for f in await query_nutrition_database(db_session, query=reference)] == [row.id]
    assert [f.id for f in await query_library(db_session, "synthetic-no-private-food", query=reference)] == [row.id]


async def test_private_library_search_is_normalized_and_owner_scoped(db_session):
    owner, other = User(email=uuid4().hex + "@example.com", password_hash="synthetic"), User(email=uuid4().hex + "@example.com", password_hash="synthetic")
    db_session.add_all([owner, other])
    await db_session.flush()
    rows = [CustomFood(user_id=u.id, name="自制米饭（熟）", amount_g=100,
                       calories=130, protein_g=2, carbs_g=28, fat_g=0,
                       client_request_id=uuid4().hex, creation_fingerprint=uuid4().hex)
            for u in (owner, other)]
    db_session.add_all(rows)
    await db_session.commit()
    assert [f.id for f in await query_library(db_session, owner.id, query="自制米饭(熟)", scope="mine")] == [rows[0].id]


async def test_fullwidth_meal_revision_and_duplicate_confirmation(db_session):
    db = db_session
    row = food()
    user = User(email=uuid4().hex + "@example.com", password_hash="synthetic")
    db.add_all([row, user])
    await db.flush()
    conversation = AgentConversation(user_id=user.id)
    db.add(conversation)
    await db.flush()
    runs = [AgentRun(user_id=user.id, conversation_id=conversation.id, status="completed") for _ in range(2)]
    db.add_all(runs)
    await db.commit()

    async def propose(index, name, amount, previous=None):
        return await create_agent_meal_create_proposal(
            db, enabled=True, user_id=user.id, conversation_id=conversation.id,
            run_id=runs[index].id, supersedes_proposal_id=previous,
            changes=[ChangeRequest(resource="nutrition", operation="create", field_path="meal", value={
                "logged_at": "2026-10-08", "meal_type": "午餐",
                "items": [{"food_name": name, "amount_g": amount}],
            })],
        )

    old = await propose(0, "白米饭（中粒米，熟）", 160)
    latest = await propose(1, "白米饭(中粒米,熟)", 200, old.id)
    old_row = await db.get(AgentProposal, old.id)
    assert old_row.status == "stale"
    new_row = await db.get(AgentProposal, latest.id)
    after = new_row.payload_data["after"]
    assert after["logged_at"] == "2026-10-08" and after["meal_type"] == "午餐"
    assert len(after["items"]) == 1 and after["items"][0]["food_id"] == row.id
    assert after["items"][0]["amount_g"] == 200 and after["items"][0]["calories"] == 260
    assert list((await db.scalars(select(MealLog))).all()) == []
    with pytest.raises(PlanProposalError):
        await decide_agent_domain_proposal(db, user_id=user.id, proposal_id=old.id, action="confirm",
            request=GenericProposalDecisionRequest(expected_version=old.version, client_request_id=uuid4().hex))
    decision = GenericProposalDecisionRequest(expected_version=latest.version, client_request_id=uuid4().hex)
    applied = await decide_agent_domain_proposal(db, user_id=user.id, proposal_id=latest.id, action="confirm", request=decision)
    replay = await decide_agent_domain_proposal(db, user_id=user.id, proposal_id=latest.id, action="confirm", request=decision)
    assert applied.result_data == replay.result_data
    assert len(list((await db.scalars(select(MealLog))).all())) == 1
    items = list((await db.scalars(select(MealItem))).all())
    assert len(items) == 1 and items[0].food_id == row.id and items[0].amount_g == 200
