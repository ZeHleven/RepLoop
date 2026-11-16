import json
from datetime import date
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.config import settings
from app.models.profile import UserProfile
from app.models.meal import MealLog, MealItem
from app.services.ai_client import chat_completion


async def get_daily_nutrition_advice(
    db: AsyncSession,
    *,
    user_id: str,
    target_date: date | None = None,
) -> str:
    if target_date is None:
        target_date = date.today()

    profile = await db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))

    meals = (await db.execute(
        select(MealLog).where(MealLog.user_id == user_id, MealLog.logged_at == target_date)
    )).scalars().all()

    total_cal = total_prot = total_carbs = total_fat = 0.0
    meal_summary = []

    for meal in meals:
        items = (await db.execute(
            select(MealItem).where(MealItem.meal_id == meal.id)
        )).scalars().all()
        for item in items:
            total_cal += item.calories
            total_prot += item.protein_g
            total_carbs += item.carbs_g
            total_fat += item.fat_g
        meal_summary.append({
            "meal_type": meal.meal_type,
            "items": [{"name": i.food_name, "amount_g": i.amount_g, "calories": i.calories} for i in items],
        })

    goal = profile.primary_goal if profile else "均衡饮食"
    weight = profile.weight_kg if profile else None
    bmi = profile.bmi if profile else None

    prompt = f"""请根据用户今日饮食记录给出专业的营养建议，返回简洁的中文文本（不超过300字）。

用户信息：
- 目标：{goal}
- 体重：{f"{weight}kg" if weight else "未知"}
- BMI：{bmi if bmi else "未知"}

今日饮食摘要（{target_date}）：
- 总热量：{total_cal:.0f} kcal
- 蛋白质：{total_prot:.1f}g
- 碳水化合物：{total_carbs:.1f}g
- 脂肪：{total_fat:.1f}g

各餐详情：
{json.dumps(meal_summary, ensure_ascii=False, indent=2)}

请给出：1) 今日营养摄入评价 2) 具体改善建议 3) 明日饮食推荐"""

    messages = [
        {"role": "system", "content": "你是专业营养师，提供科学、实用的饮食建议。"},
        {"role": "user", "content": prompt},
    ]

    return await chat_completion(
        messages,
        model=settings.DEEPSEEK_REASONING_MODEL,
        max_tokens=512,
        temperature=0.5,
        thinking=False,
    )
