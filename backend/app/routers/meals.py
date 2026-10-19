from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select
from app.database import get_db
from app.deps import get_current_user
from app.models.user import User
from app.models.meal import MealLog, MealItem
from app.models.food import Food, CustomFood
from app.schemas.meal import MealItemCreate
from app.services.training_lifecycle import training_today
from pydantic import ValidationError
from app.schemas.meal import (
    MealLogCreate, MealLogDetail, MealLogUpdate, MealItemResponse, DailySummary, TodaySummary,
    NutritionAdviceResponse,
)
from app.services.nutrition_queries import (
    build_daily_nutrition_summary,
    list_nutrition_history,
)

router = APIRouter(prefix="/meals", tags=["meals"])


async def _hydrate_items(
    db: AsyncSession,
    *,
    items,
    user_id: str,
) -> list[dict]:
    food_ids = {item.food_id for item in items if item.food_id}
    foods = list((await db.execute(
        select(Food).where(
            Food.id.in_(food_ids),
            Food.is_active.is_(True),
        )
    )).scalars().all()) if food_ids else []
    foods_by_id = {item.id: item for item in foods}
    if len(foods_by_id) != len(food_ids):
        raise HTTPException(status_code=400, detail="饮食记录包含不存在或已停用的食品")

    private_ids = {item.custom_food_id for item in items if item.custom_food_id}
    private = list((await db.execute(select(CustomFood).where(
        CustomFood.id.in_(private_ids), CustomFood.user_id == user_id,
        CustomFood.is_active.is_(True),
    ).order_by(CustomFood.id).with_for_update())).scalars().all()) if private_ids else []
    private_by_id = {row.id: row for row in private}
    if len(private_by_id) != len(private_ids):
        raise HTTPException(409, '食品已不可用，请刷新食品库重新选择；当前餐次未保存')

    hydrated = []
    for item_data in items:
        values = item_data.model_dump()
        if item_data.food_id:
            food = foods_by_id[item_data.food_id]
            factor = item_data.amount_g / 100
            values.update({
                "food_name": food.name_zh,
                "calories": round(food.calories_per_100g * factor, 1),
                "protein_g": round(food.protein_g * factor, 1),
                "carbs_g": round(food.carbs_g * factor, 1),
                "fat_g": round(food.fat_g * factor, 1),
            })
        elif item_data.custom_food_id:
            food = private_by_id[item_data.custom_food_id]
            if food.version != item_data.custom_food_version:
                raise HTTPException(409, '食品已修改，请刷新食品库后重新选择；当前餐次未保存')
            values.update(food_name=food.name, **{
                field: round(getattr(food, field) / food.amount_g * item_data.amount_g, 1)
                for field in ('calories', 'protein_g', 'carbs_g', 'fat_g')
            })
            try:
                MealItemCreate.model_validate(values)
            except ValidationError:
                raise HTTPException(422, '该份量换算后的营养超出范围，请核对食品和克数') from None
        hydrated.append(values)
    return hydrated


def _meal_detail(meal: MealLog, items: list[MealItem]) -> MealLogDetail:
    result = MealLogDetail.model_validate(meal)
    result.items = [MealItemResponse.model_validate(item) for item in items]
    return result


@router.post("", response_model=MealLogDetail, status_code=201)
async def log_meal(
    body: MealLogCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    hydrated = await _hydrate_items(db, items=body.items, user_id=current_user.id)
    meal = MealLog(
        user_id=current_user.id,
        logged_at=body.logged_at,
        meal_type=body.meal_type,
    )
    db.add(meal)
    await db.flush()

    items = []
    for values in hydrated:
        item = MealItem(
            meal_id=meal.id,
            **values,
        )
        db.add(item)
        items.append(item)

    await db.commit()

    return _meal_detail(meal, items)


@router.put("/{meal_id}", response_model=MealLogDetail)
async def update_meal(
    meal_id: str,
    body: MealLogUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    meal = await db.scalar(
        select(MealLog).where(
            MealLog.id == meal_id,
            MealLog.user_id == current_user.id,
        ).with_for_update()
    )
    if not meal:
        raise HTTPException(status_code=404, detail="饮食记录不存在")
    earliest = training_today() - timedelta(days=29)
    if meal.logged_at < earliest or body.logged_at < earliest:
        raise HTTPException(status_code=409, detail="仅支持修改近 30 天的饮食记录")

    hydrated = await _hydrate_items(db, items=body.items, user_id=current_user.id)
    await db.execute(delete(MealItem).where(MealItem.meal_id == meal.id))
    meal.logged_at = body.logged_at
    meal.meal_type = body.meal_type
    items = [MealItem(meal_id=meal.id, **values) for values in hydrated]
    db.add_all(items)
    await db.commit()
    return _meal_detail(meal, items)


@router.get("/today", response_model=TodaySummary)
async def today_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    today = training_today()
    summary = await build_daily_nutrition_summary(
        db, user_id=current_user.id, target_date=today
    )
    summary = TodaySummary(**summary.model_dump())
    from app.services.nutrition_energy import build_energy_estimate
    import logging
    try:
        # A failed energy query must not poison the session or hide the meal data.
        async with db.begin_nested():
            summary.energy_estimate = await build_energy_estimate(db, current_user.id, summary)
    except Exception as error:
        logging.getLogger(__name__).warning('nutrition_energy_unavailable error_type=%s', type(error).__name__)
        summary.energy_estimate = {'status': 'unavailable', 'reasons': ['消耗估算暂不可用，请稍后刷新'],
                                   'bmr_kcal': None, 'total_kcal': None, 'balance_kcal': None}
    return summary


@router.get("/history", response_model=list[DailySummary])
async def meal_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_nutrition_history(db, user_id=current_user.id, days=30)


@router.delete("/{meal_id}", status_code=204)
async def delete_meal(
    meal_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    meal = await db.scalar(
        select(MealLog).where(
            MealLog.id == meal_id,
            MealLog.user_id == current_user.id,
        )
    )
    if not meal:
        raise HTTPException(status_code=404, detail="饮食记录不存在")
    await db.execute(delete(MealItem).where(MealItem.meal_id == meal.id))
    await db.delete(meal)
    await db.commit()


@router.get("/advice", response_model=NutritionAdviceResponse)
async def nutrition_advice(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services.nutritionist import get_daily_nutrition_advice
    advice = await get_daily_nutrition_advice(db, user_id=current_user.id)
    return NutritionAdviceResponse(advice=advice)
