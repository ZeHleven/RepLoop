from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.schemas.food import FoodResponse
from app.services.food import query_nutrition_database

router = APIRouter(prefix="/foods", tags=["foods"])


@router.get("", response_model=list[FoodResponse])
async def list_foods(
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
        limit=limit,
    )
