from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.meal import MealItem, MealLog
from app.schemas.meal import DailySummary, MealItemResponse, MealLogDetail


async def build_daily_nutrition_summary(
    db: AsyncSession,
    *,
    user_id: str,
    target_date: date,
) -> DailySummary:
    """Build one user-owned daily summary without trusting caller-owned totals."""
    meals = list((await db.execute(
        select(MealLog)
        .where(MealLog.user_id == user_id, MealLog.logged_at == target_date)
        .order_by(MealLog.created_at.asc())
    )).scalars().all())
    meal_ids = [meal.id for meal in meals]
    items = list((await db.execute(
        select(MealItem)
        .where(MealItem.meal_id.in_(meal_ids))
        .order_by(MealItem.meal_id, MealItem.id)
    )).scalars().all()) if meal_ids else []
    by_meal: dict[str, list[MealItem]] = {meal_id: [] for meal_id in meal_ids}
    for item in items:
        by_meal[item.meal_id].append(item)

    total_calories = sum(item.calories for item in items)
    total_protein = sum(item.protein_g for item in items)
    total_carbs = sum(item.carbs_g for item in items)
    total_fat = sum(item.fat_g for item in items)
    details: list[MealLogDetail] = []
    for meal in meals:
        detail = MealLogDetail.model_validate(meal)
        detail.items = [
            MealItemResponse.model_validate(item)
            for item in by_meal[meal.id]
        ]
        details.append(detail)
    return DailySummary(
        date=target_date,
        total_calories=round(total_calories, 1),
        total_protein_g=round(total_protein, 1),
        total_carbs_g=round(total_carbs, 1),
        total_fat_g=round(total_fat, 1),
        meals=details,
    )


async def list_nutrition_history(
    db: AsyncSession,
    *,
    user_id: str,
    days: int = 30,
    as_of: date | None = None,
) -> list[DailySummary]:
    filters = [MealLog.user_id == user_id]
    if as_of is not None:
        filters.append(MealLog.logged_at <= as_of)
    logged_dates = list((await db.execute(
        select(MealLog.logged_at)
        .where(*filters)
        .distinct()
        .order_by(MealLog.logged_at.desc())
        .limit(days)
    )).scalars().all())
    return [
        await build_daily_nutrition_summary(
            db,
            user_id=user_id,
            target_date=logged_at,
        )
        for logged_at in logged_dates
    ]


def nutrition_history_scope(summaries: list[DailySummary], *, as_of: date, limit: int) -> dict:
    """A count of recorded dates is not a continuous calendar-day window."""
    return {
        "as_of": as_of.isoformat(), "timezone": "Asia/Shanghai",
        "selection": "latest_logged_dates", "requested_limit": limit,
        "returned_dates": len(summaries),
        "first_logged_date": min((item.date.isoformat() for item in summaries), default=None),
        "last_logged_date": max((item.date.isoformat() for item in summaries), default=None),
    }
