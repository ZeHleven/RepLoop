import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import select

from app.models.food import CustomFood
from app.models.user import User
from app.schemas.food import CustomFoodValues, LibraryFood


def values_dict(body):
    return {name: getattr(body, name) for name in CustomFoodValues.model_fields}


def fingerprint(body):
    return hashlib.sha256(json.dumps(values_dict(body), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def library_food(row):
    basis = CustomFoodValues(**values_dict(row))
    return LibraryFood(
        id=row.id, name_zh=row.name, name_en=None, category='自定义',
        calories_per_100g=row.calories / row.amount_g * 100,
        protein_g=row.protein_g / row.amount_g * 100,
        carbs_g=row.carbs_g / row.amount_g * 100, fat_g=row.fat_g / row.amount_g * 100,
        fiber_g=None, common_portion_g=row.amount_g, diet_tags=[],
        is_common_in_china=False, is_active=row.is_active,
        source='custom', version=row.version, basis=basis,
    )


async def lock_library_user(db, user_id):
    await db.scalar(select(User.id).where(User.id == user_id).with_for_update())


async def owned_food(db, user_id, food_id):
    row = await db.scalar(select(CustomFood).where(
        CustomFood.id == food_id, CustomFood.user_id == user_id,
    ).with_for_update())
    if row is None:
        raise HTTPException(404, '自定义食品不存在')
    return row


def check_version(row, version):
    if not row.is_active or row.version != version:
        raise HTTPException(409, '食品已修改或删除，请刷新食品库后重新选择；当前草稿未保存')
