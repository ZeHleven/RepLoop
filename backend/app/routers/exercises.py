from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Literal
from app.database import get_db
from app.schemas.exercise import ExerciseResponse, CustomExerciseCreate
from app.services.exercise import query_exercise_library
from app.deps import get_current_user
from app.models.user import User
from app.models.profile import UserProfile
from app.models.exercise import Exercise
from app.schemas.workout import PersonalizedExerciseOption
from app.services.custom_exercises import safety_notice
from app.services.personalized_planner import is_exercise_compatible
from app.schemas.exercise_energy import EnergyCategoryUpdate
from app.services.exercise_energy import update_library_category
from app.services.exercise_search import selection_metadata, normalize_query

router = APIRouter(prefix="/exercises", tags=["exercises"])


def custom_option(exercise: Exercise) -> PersonalizedExerciseOption:
    return PersonalizedExerciseOption(
        **selection_metadata(exercise),
        exercise_id=exercise.id, exercise_name=exercise.name_zh,
        category=exercise.category, difficulty=exercise.difficulty,
        equipment=exercise.equipment or [], safety_notice=safety_notice(exercise),
        energy_category=exercise.energy_category, energy_category_version=exercise.energy_category_version,
    )


@router.get('/custom', response_model=list[PersonalizedExerciseOption])
async def list_custom_exercises(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    profile = await db.scalar(select(UserProfile).where(UserProfile.user_id == current_user.id))
    rows = (await db.execute(select(Exercise).where(
        Exercise.owner_id == current_user.id, Exercise.is_active.is_(True),
    ).order_by(Exercise.name_zh, Exercise.id))).scalars().all()
    return [custom_option(row) for row in rows if profile is None or is_exercise_compatible(profile, row)]


@router.post('/custom', response_model=PersonalizedExerciseOption, status_code=201)
async def create_custom_exercise(body: CustomExerciseCreate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from app.services.training_lifecycle import lock_training_user
    await lock_training_user(db, current_user.id)
    profile = await db.scalar(select(UserProfile).where(UserProfile.user_id == current_user.id).with_for_update())
    exercise = Exercise(
        owner_id=current_user.id, name_zh=body.name, name_en=body.name,
        category='力量', difficulty='未知', technique_cues=body.description,
        muscle_primary=body.muscles, equipment=body.equipment,
        contraindications=body.contraindications, is_active=True,
        energy_category=body.energy_category, energy_category_version=1 if body.energy_category else 0,
    )
    public = (await db.execute(select(Exercise).where(
        Exercise.owner_id.is_(None), Exercise.is_active.is_(True),
    ))).scalars().all()
    canonical = [row for row in public if normalize_query(body.name) in {
        normalize_query(value) for value in [row.name_zh, *selection_metadata(row)['search_aliases']]
    }]
    if profile is not None and any(not is_exercise_compatible(profile, row) for row in canonical):
        raise HTTPException(409, '动作库中的同名动作与当前健康或训练条件存在冲突，不能通过自定义名称绕过')
    if profile is not None and not is_exercise_compatible(profile, exercise):
        raise HTTPException(409, '该动作与已记录的健康或训练条件存在明确冲突，不能添加；请核对动作方法、器械和禁忌。')
    # Repeating the same immutable definition is idempotent (including lost responses).
    existing = (await db.execute(select(Exercise).where(
        Exercise.owner_id == current_user.id, Exercise.name_zh == body.name,
    ))).scalars().all()
    for row in existing:
        if (row.technique_cues, row.muscle_primary, row.equipment, row.contraindications) == (
            body.description, body.muscles, body.equipment, body.contraindications,
        ):
            if body.energy_category is not None and body.energy_category != row.energy_category:
                raise HTTPException(409, '相同动作已存在且分类不同，请使用修改估算分类入口')
            return custom_option(row)
    db.add(exercise)
    await db.commit()
    return custom_option(exercise)


@router.put('/custom/{exercise_id}/energy-category', response_model=PersonalizedExerciseOption)
async def set_energy_category(exercise_id: str, body: EnergyCategoryUpdate,
                              current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return custom_option(await update_library_category(db, current_user.id, exercise_id, body))


@router.get("", response_model=list[ExerciseResponse])
async def list_exercises(
    q: str | None = Query(None, max_length=100),
    body_part: Literal['胸部', '背部', '肩部', '手臂', '臀部', '腿部', '核心'] | None = Query(None),
    muscle_group: str | None = Query(None),
    equipment: str | None = Query(None),
    difficulty: str | None = Query(None),
    movement_pattern: str | None = Query(None),
    category: str | None = Query(None),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    rows = await query_exercise_library(
        db,
        muscle_group=muscle_group,
        equipment=equipment,
        difficulty=difficulty,
        movement_pattern=movement_pattern,
        category=category,
        limit=limit,
        query=q,
        body_part=body_part,
    )
    return [ExerciseResponse.model_validate(row).model_copy(update=selection_metadata(row)) for row in rows]
