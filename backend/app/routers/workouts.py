from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.deps import get_current_user
from app.models.user import User
from app.models.workout import WorkoutPlan, PlannedExercise, WorkoutSession, SessionExercise
from app.schemas.workout import (
    WorkoutPlanCreate, WorkoutPlanResponse, WorkoutPlanDetail,
    WorkoutSessionCreate, WorkoutSessionResponse, WorkoutSessionDetail,
    GeneratePlanRequest,
)

router = APIRouter(prefix="/workouts", tags=["workouts"])


# ── Plans ─────────────────────────────────────────────────────────────────────

@router.post("/plans", response_model=WorkoutPlanResponse, status_code=201)
async def create_plan(
    body: WorkoutPlanCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    plan = WorkoutPlan(
        user_id=current_user.id,
        name=body.name,
        goal=body.goal,
        duration_weeks=body.duration_weeks,
        days_per_week=body.days_per_week,
        notes=body.notes,
    )
    db.add(plan)
    await db.flush()

    for ex in body.exercises:
        db.add(PlannedExercise(plan_id=plan.id, **ex.model_dump()))

    await db.commit()
    return plan


@router.get("/plans", response_model=list[WorkoutPlanResponse])
async def list_plans(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(WorkoutPlan)
        .where(WorkoutPlan.user_id == current_user.id)
        .order_by(WorkoutPlan.created_at.desc())
    )).scalars().all()
    return rows


@router.get("/plans/{plan_id}", response_model=WorkoutPlanDetail)
async def get_plan(
    plan_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    plan = await db.scalar(
        select(WorkoutPlan).where(
            WorkoutPlan.id == plan_id,
            WorkoutPlan.user_id == current_user.id,
        )
    )
    if not plan:
        raise HTTPException(status_code=404, detail="训练计划不存在")

    exercises = (await db.execute(
        select(PlannedExercise)
        .where(PlannedExercise.plan_id == plan_id)
        .order_by(PlannedExercise.day_of_week, PlannedExercise.order_index)
    )).scalars().all()

    result = WorkoutPlanDetail.model_validate(plan)
    result.exercises = [e for e in exercises]
    return result


@router.delete("/plans/{plan_id}", status_code=204)
async def delete_plan(
    plan_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    plan = await db.scalar(
        select(WorkoutPlan).where(
            WorkoutPlan.id == plan_id,
            WorkoutPlan.user_id == current_user.id,
        )
    )
    if not plan:
        raise HTTPException(status_code=404, detail="训练计划不存在")
    await db.delete(plan)
    await db.commit()


@router.post("/plans/generate", response_model=WorkoutPlanResponse, status_code=201)
async def generate_plan(
    body: GeneratePlanRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services.planner import generate_workout_plan
    plan = await generate_workout_plan(
        db,
        user_id=current_user.id,
        goal=body.goal,
        duration_weeks=body.duration_weeks,
    )
    return plan


# ── Sessions ──────────────────────────────────────────────────────────────────

@router.post("/sessions", response_model=WorkoutSessionResponse, status_code=201)
async def log_session(
    body: WorkoutSessionCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = WorkoutSession(
        user_id=current_user.id,
        plan_id=body.plan_id,
        trained_at=body.trained_at,
        duration_min=body.duration_min,
        notes=body.notes,
    )
    db.add(session)
    await db.flush()

    for ex in body.exercises:
        db.add(SessionExercise(
            session_id=session.id,
            exercise_id=ex.exercise_id,
            sets_data=[s.model_dump() for s in ex.sets_data],
        ))

    await db.commit()
    return session


@router.get("/sessions", response_model=list[WorkoutSessionResponse])
async def list_sessions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(WorkoutSession)
        .where(WorkoutSession.user_id == current_user.id)
        .order_by(WorkoutSession.trained_at.desc())
    )).scalars().all()
    return rows
