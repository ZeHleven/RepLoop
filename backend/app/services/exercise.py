import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.exercise import Exercise


async def query_exercise_library(
    db: AsyncSession,
    *,
    muscle_group: str | None = None,
    equipment: str | None = None,
    difficulty: str | None = None,
    movement_pattern: str | None = None,
    category: str | None = None,
    limit: int = 10,
) -> list[Exercise]:
    stmt = select(Exercise).where(Exercise.is_active.is_(True))
    if muscle_group:
        stmt = stmt.where(
            sa.cast(Exercise.muscle_primary, sa.String).contains(f'"{muscle_group}"')
        )
    if equipment:
        stmt = stmt.where(
            sa.cast(Exercise.equipment, sa.String).contains(f'"{equipment}"')
        )
    if difficulty:
        stmt = stmt.where(Exercise.difficulty == difficulty)
    if movement_pattern:
        stmt = stmt.where(Exercise.movement_pattern == movement_pattern)
    if category:
        stmt = stmt.where(Exercise.category == category)
    stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())
