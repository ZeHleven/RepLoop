from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.database import get_db
from app.deps import get_current_user
from app.models.user import User
from app.models.meal import MealLog, MealItem
from app.schemas.meal import (
    MealLogCreate, MealLogResponse, MealLogDetail,
    MealItemResponse, DailySummary, NutritionAdviceResponse,
)

router = APIRouter(prefix="/meals", tags=["meals"])


@router.post("", response_model=MealLogDetail, status_code=201)
async def log_meal(
    body: MealLogCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    meal = MealLog(
        user_id=current_user.id,
        logged_at=body.logged_at,
        meal_type=body.meal_type,
    )
    db.add(meal)
    await db.flush()

    items = []
    for item_data in body.items:
        item = MealItem(
            meal_id=meal.id,
            **item_data.model_dump(),
        )
        db.add(item)
        items.append(item)

    await db.commit()

    result = MealLogDetail.model_validate(meal)
    result.items = [MealItemResponse.model_validate(i) for i in items]
    return result


@router.get("/today", response_model=DailySummary)
async def today_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    today = date.today()
    return await _build_summary(db, current_user.id, today)


@router.get("/history", response_model=list[DailySummary])
async def meal_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(MealLog.logged_at)
        .where(MealLog.user_id == current_user.id)
        .distinct()
        .order_by(MealLog.logged_at.desc())
        .limit(30)
    )).scalars().all()

    summaries = []
    for d in rows:
        summaries.append(await _build_summary(db, current_user.id, d))
    return summaries


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


async def _build_summary(db: AsyncSession, user_id: str, target_date: date) -> DailySummary:
    meals = (await db.execute(
        select(MealLog)
        .where(MealLog.user_id == user_id, MealLog.logged_at == target_date)
        .order_by(MealLog.created_at.asc())
    )).scalars().all()

    meal_details = []
    total_cal = total_prot = total_carbs = total_fat = 0.0

    for meal in meals:
        items_rows = (await db.execute(
            select(MealItem).where(MealItem.meal_id == meal.id)
        )).scalars().all()

        for item in items_rows:
            total_cal += item.calories
            total_prot += item.protein_g
            total_carbs += item.carbs_g
            total_fat += item.fat_g

        detail = MealLogDetail.model_validate(meal)
        detail.items = [MealItemResponse.model_validate(i) for i in items_rows]
        meal_details.append(detail)

    return DailySummary(
        date=target_date,
        total_calories=round(total_cal, 1),
        total_protein_g=round(total_prot, 1),
        total_carbs_g=round(total_carbs, 1),
        total_fat_g=round(total_fat, 1),
        meals=meal_details,
    )
