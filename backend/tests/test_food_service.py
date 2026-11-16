import pytest
import uuid
from app.models.food import Food, FoodAlias
from app.services.food import (
    normalize_food_reference,
    query_nutrition_database,
    resolve_food_reference,
)


def _make_food(**kwargs) -> Food:
    defaults = dict(
        id=str(uuid.uuid4()),
        name_zh="测试食物",
        name_en="Test Food",
        category="蛋白质",
        calories_per_100g=100.0,
        protein_g=10.0,
        is_active=True,
    )
    defaults.update(kwargs)
    return Food(**defaults)


@pytest.mark.asyncio
async def test_returns_empty_when_no_match(db_session):
    result = await query_nutrition_database(db_session, category="__no_such_category__")
    assert result == []


@pytest.mark.asyncio
async def test_returns_active_only(db_session):
    db_session.add(_make_food(name_en="Active Food"))
    db_session.add(_make_food(name_en="Inactive Food", is_active=False))
    await db_session.commit()

    result = await query_nutrition_database(db_session)
    names = [f.name_en for f in result]
    assert "Active Food" in names
    assert "Inactive Food" not in names


@pytest.mark.asyncio
async def test_filter_by_category(db_session):
    db_session.add(_make_food(name_en="Protein Food", category="蛋白质"))
    db_session.add(_make_food(name_en="Veggie Food", category="蔬菜"))
    await db_session.commit()

    result = await query_nutrition_database(db_session, category="蛋白质")
    names = [f.name_en for f in result]
    assert "Protein Food" in names
    assert "Veggie Food" not in names


@pytest.mark.asyncio
async def test_filter_by_diet_tag(db_session):
    db_session.add(_make_food(name_en="Vegan Food", diet_tags=["vegan", "gluten-free"]))
    db_session.add(_make_food(name_en="Keto Food", diet_tags=["keto"]))
    await db_session.commit()

    result = await query_nutrition_database(db_session, diet_tag="vegan")
    names = [f.name_en for f in result]
    assert "Vegan Food" in names
    assert "Keto Food" not in names


@pytest.mark.asyncio
async def test_filter_by_min_protein(db_session):
    db_session.add(_make_food(name_en="High Protein", protein_g=30.0))
    db_session.add(_make_food(name_en="Low Protein", protein_g=2.0))
    await db_session.commit()

    result = await query_nutrition_database(db_session, min_protein_g=20.0)
    names = [f.name_en for f in result]
    assert "High Protein" in names
    assert "Low Protein" not in names


@pytest.mark.asyncio
async def test_limit(db_session):
    for i in range(5):
        db_session.add(_make_food(name_en=f"Food Item {i}"))
    await db_session.commit()

    result = await query_nutrition_database(db_session, limit=3)
    assert len(result) <= 3


@pytest.mark.asyncio
async def test_exact_alias_resolves_to_one_canonical_food(db_session):
    food = _make_food(name_zh="杂粮饭", name_en="Mixed Grain Rice")
    alias = FoodAlias(
        id=str(uuid.uuid4()),
        food_id=food.id,
        alias="五谷杂粮饭",
        normalized_alias=normalize_food_reference("五谷杂粮饭"),
    )
    db_session.add_all([food, alias])
    await db_session.commit()

    resolved = await resolve_food_reference(
        db_session, reference="  五谷杂粮饭  "
    )
    searched = await query_nutrition_database(
        db_session, query="五谷杂粮饭"
    )

    assert [item.id for item in resolved] == [food.id]
    assert food.id in {item.id for item in searched}


@pytest.mark.asyncio
async def test_canonical_chinese_name_precedes_english_name_and_alias(db_session):
    chinese_match = _make_food(
        name_zh="解析顺序食品",
        name_en="Canonical Chinese Winner",
    )
    english_match = _make_food(
        name_zh="英文重名测试食品",
        name_en="解析顺序食品",
    )
    alias_target = _make_food(name_zh="别名重名测试食品")
    db_session.add_all([
        chinese_match,
        english_match,
        alias_target,
        FoodAlias(
            id=str(uuid.uuid4()),
            food_id=alias_target.id,
            alias="解析顺序食品",
            normalized_alias=normalize_food_reference("解析顺序食品"),
        ),
    ])
    await db_session.commit()

    resolved = await resolve_food_reference(
        db_session,
        reference="解析顺序食品",
    )

    assert [item.id for item in resolved] == [chinese_match.id]


@pytest.mark.asyncio
async def test_resolver_does_not_fuzzy_match_unregistered_food(db_session):
    food = _make_food(name_zh="糙米测试条目", name_en="Brown Rice Test Entry")
    db_session.add(food)
    await db_session.commit()

    resolved = await resolve_food_reference(
        db_session, reference="未登记混合谷物饭测试条目"
    )

    assert resolved == []
