from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Literal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.schemas.food import FoodResponse, LibraryFood, CustomFoodCreate, CustomFoodUpdate
from app.models.food import CustomFood
from app.models.user import User
from app.deps import get_current_user
from app.services.custom_foods import library_food, values_dict, fingerprint, lock_library_user, owned_food, check_version
from app.services.food import query_nutrition_database
from app.services.food_library import BrowseCategory, query_library

router = APIRouter(prefix="/foods", tags=["foods"])


@router.get('/library', response_model=list[LibraryFood])
async def get_library(q: str | None = Query(None, min_length=1, max_length=50),
                      scope: Literal['all', 'mine'] = 'all', limit: int = Query(20, ge=1, le=50),
                      offset: int = Query(0, ge=0, le=100000), browse_category: BrowseCategory | None = None,
                      current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await query_library(db,current_user.id,q,scope,browse_category,limit,offset)


@router.post('/custom', response_model=LibraryFood, status_code=201)
async def create_custom_food(body: CustomFoodCreate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await lock_library_user(db, current_user.id)
    previous = await db.scalar(select(CustomFood).where(
        CustomFood.user_id == current_user.id, CustomFood.client_request_id == body.client_request_id,
    ))
    if previous:
        if previous.creation_fingerprint != fingerprint(body):
            raise HTTPException(409, '同一创建请求不能用于不同食品，请核对后重试')
        check_version(previous, 1)
        return library_food(previous)
    row = CustomFood(user_id=current_user.id, **values_dict(body),
                     client_request_id=body.client_request_id, creation_fingerprint=fingerprint(body))
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return library_food(row)


@router.get('/custom/requests/{request_id}', response_model=LibraryFood)
async def read_custom_creation(request_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # Read-after-uncertain-write; never expose another user's request or infer by name.
    row = await db.scalar(select(CustomFood).where(CustomFood.user_id == current_user.id,
                                                   CustomFood.client_request_id == request_id))
    if row is None:
        raise HTTPException(404, '尚未找到此创建请求，可以用原请求重试')
    check_version(row, 1)
    return library_food(row)


@router.put('/custom/{food_id}', response_model=LibraryFood)
async def update_custom_food(food_id: str, body: CustomFoodUpdate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await lock_library_user(db, current_user.id)
    row = await owned_food(db, current_user.id, food_id)
    # Retrying the very same completed update is safe; different updates conflict.
    if row.is_active and row.version == body.version + 1 and values_dict(row) == values_dict(body):
        return library_food(row)
    check_version(row, body.version)
    for name, value in values_dict(body).items():
        setattr(row, name, value)
    row.version += 1
    await db.commit()
    return library_food(row)


@router.delete('/custom/{food_id}', status_code=204)
async def delete_custom_food(food_id: str, version: int = Query(..., ge=1), current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await lock_library_user(db, current_user.id)
    row = await owned_food(db, current_user.id, food_id)
    if not row.is_active and row.version == version + 1:
        return
    check_version(row, version)
    row.is_active = False
    row.version += 1
    await db.commit()


@router.get("", response_model=list[FoodResponse])
async def list_foods(
    q: str | None = Query(None, min_length=1, max_length=50),
    category: str | None = Query(None),
    diet_tag: str | None = Query(None),
    min_protein_g: float | None = Query(None, ge=0),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    return await query_nutrition_database(
        db,
        category=category,
        diet_tag=diet_tag,
        min_protein_g=min_protein_g,
        query=q,
        limit=limit,
    )
