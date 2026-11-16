import unicodedata

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select

from app.models.food import Food, FoodAlias


def normalize_food_reference(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().lower()
    return " ".join(normalized.split())


def normalized_food_name(column):
    """Normalize stored names for comparison without changing catalog values.

    PostgreSQL's UTF8 NFKC support avoids normalizing only the request. Use the
    whitespace set recognized by Python str.split, rather than a locale-based
    SQL whitespace class, so tabs and Unicode separators compare consistently.
    """
    whitespace = "[\t-\r\x1c-\x20\x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+"
    value = func.lower(func.normalize(column, sa.literal_column("NFKC")))
    return func.btrim(func.regexp_replace(value, whitespace, " ", "g"), type_=sa.Text())


async def resolve_food_reference(
    db: AsyncSession,
    *,
    food_id: str | None = None,
    reference: str | None = None,
) -> list[Food]:
    """Resolve only exact canonical names or server-managed exact aliases."""
    if food_id:
        result = await db.execute(
            select(Food)
            .where(Food.id == food_id, Food.is_active.is_(True))
            .limit(2)
        )
        return list(result.scalars().all())

    normalized = normalize_food_reference(reference or "")
    if not normalized:
        return []
    for name_column in (Food.name_zh, Food.name_en):
        canonical = list((await db.execute(
            select(Food)
            .where(
                Food.is_active.is_(True),
                normalized_food_name(name_column) == normalized,
            )
            .order_by(Food.id)
            .limit(2)
        )).scalars().all())
        if canonical:
            return canonical
    aliases = list((await db.execute(
        select(Food)
        .join(FoodAlias, FoodAlias.food_id == Food.id)
        .where(
            Food.is_active.is_(True),
            FoodAlias.normalized_alias == normalized,
        )
        .order_by(Food.id)
        .limit(2)
    )).scalars().all())
    return aliases


async def query_nutrition_database(
    db: AsyncSession,
    *,
    category: str | None = None,
    diet_tag: str | None = None,
    min_protein_g: float | None = None,
    query: str | None = None,
    limit: int = 10,
) -> list[Food]:
    stmt = select(Food).where(Food.is_active.is_(True))
    if category:
        stmt = stmt.where(Food.category == category)
    if diet_tag:
        stmt = stmt.where(
            sa.cast(Food.diet_tags, sa.String).contains(f'"{diet_tag}"')
        )
    if min_protein_g is not None:
        stmt = stmt.where(Food.protein_g >= min_protein_g)
    if query:
        normalized = normalize_food_reference(query)
        if normalized:
            stmt = stmt.where(
                normalized_food_name(Food.name_zh).like(f"%{normalized}%")
                | normalized_food_name(Food.name_en).like(f"%{normalized}%")
                | sa.exists(
                    select(FoodAlias.id).where(
                        FoodAlias.food_id == Food.id,
                        FoodAlias.normalized_alias.ilike(f"%{normalized}%"),
                    )
                )
            )
    stmt = stmt.order_by(Food.is_common_in_china.desc(), Food.name_zh).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())
