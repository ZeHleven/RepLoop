import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.exercise import Exercise
from app.services.exercise_search import matches_exercise


async def query_exercise_library(
    db: AsyncSession,
    *,
    muscle_group: str | None = None,
    equipment: str | None = None,
    difficulty: str | None = None,
    movement_pattern: str | None = None,
    category: str | None = None,
    limit: int = 10,
    query: str | None = None,
    body_part: str | None = None,
) -> list[Exercise]:
    stmt = select(Exercise).where(Exercise.is_active.is_(True), Exercise.owner_id.is_(None))
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
    stmt = stmt.order_by(Exercise.name_zh, Exercise.id)
    # One batch for this small standard library. Filter before limit, never per-row queries.
    if not query and not body_part:
        stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    return [row for row in result.scalars().all()
            if matches_exercise(row, query=query, body_part=body_part)][:limit]
