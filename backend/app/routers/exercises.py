from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.schemas.exercise import ExerciseResponse
from app.services.exercise import query_exercise_library

router = APIRouter(prefix="/exercises", tags=["exercises"])


@router.get("", response_model=list[ExerciseResponse])
async def list_exercises(
    muscle_group: str | None = Query(None),
    equipment: str | None = Query(None),
    difficulty: str | None = Query(None),
    movement_pattern: str | None = Query(None),
    category: str | None = Query(None),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    return await query_exercise_library(
        db,
        muscle_group=muscle_group,
        equipment=equipment,
        difficulty=difficulty,
        movement_pattern=movement_pattern,
        category=category,
        limit=limit,
    )
