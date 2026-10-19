import json
from datetime import date, datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select
from app.database import get_db
from app.deps import get_current_user
from app.models.user import User
from app.models.profile import UserProfile
from app.models.exercise import Exercise
from app.models.workout import WorkoutPlan, PlannedExercise, WorkoutSession, SessionExercise
from app.schemas.workout import (
    GeneratePlanRequest, PersonalizedPlanConfirmRequest,
    PersonalizedPlanPreview, PersonalizedPlanPreviewRequest,
    WorkoutPlanCreate, WorkoutPlanDetail,
    WorkoutProgressResponse, WorkoutSessionComplete, WorkoutSessionCreate,
    WorkoutFeedback, WorkoutSessionDetail,
    WorkoutSessionStart, WorkoutSetRecord,
    WorkoutRestRecord,
)
from app.services.training_lifecycle import lock_training_user, training_today, training_week
from app.services.training_day_continuity import load_training_day_continuity
from app.services.custom_exercises import visible_exercise
from app.services.exercise_energy import classification_snapshot, update_session_categories
from app.schemas.exercise_energy import SessionEnergyUpdate
from app.schemas.plan_management_proposal import (
    CreatePlanAdjustmentProposalRequest,
    CreatePlanDeletionProposalRequest,
    PlanEditContext,
    PlanProposalReference,
)
from app.services.workout_queries import (
    build_plan_detail,
    build_session_detail,
    get_active_user_session,
    get_personal_record_baseline,
    get_user_plan,
    get_user_workout_session,
    get_workout_progress_summary,
    is_personal_record,
    list_user_plans,
    list_user_workout_sessions,
    normalized_sets,
    sets_metrics,
)

router = APIRouter(prefix="/workouts", tags=["workouts"])


async def _validate_owned_exercises(db, user_id, exercise_ids):
    if not exercise_ids:
        return {}
    rows = (await db.execute(select(Exercise).where(
        Exercise.id.in_(exercise_ids), Exercise.is_active.is_(True), visible_exercise(user_id),
    ))).scalars().all()
    if len(rows) != len(exercise_ids):
        raise HTTPException(422, '动作不存在、已停用或不属于当前用户')
    profile = await db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    if profile is not None:
        from app.services.personalized_planner import is_exercise_compatible
        if any(not is_exercise_compatible(profile, row) for row in rows):
            raise HTTPException(409, '动作与当前健康或训练条件存在冲突，请先复核')
    return {row.id: row for row in rows}


# ── Plans ─────────────────────────────────────────────────────────────────────

@router.post("/plans", response_model=WorkoutPlanDetail, status_code=201)
async def create_plan(
    body: WorkoutPlanCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await lock_training_user(db, current_user.id)
    await _validate_owned_exercises(db, current_user.id, {item.exercise_id for item in body.exercises})
    existing_active = await db.scalar(select(WorkoutPlan.id).where(
        WorkoutPlan.user_id == current_user.id,
        WorkoutPlan.is_active.is_(True),
    ).limit(1))
    if existing_active is not None:
        raise HTTPException(
            status_code=409,
            detail="已有活动计划，请通过计划编辑器生成调整提案",
        )
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
    return await build_plan_detail(db, plan)


@router.get("/plans", response_model=list[WorkoutPlanDetail])
async def list_plans(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = await list_user_plans(db, user_id=current_user.id)
    profile = await db.scalar(
        select(UserProfile).where(UserProfile.user_id == current_user.id)
    )
    weekly_sessions = list((await db.execute(select(WorkoutSession).where(
        WorkoutSession.user_id == current_user.id,
        WorkoutSession.week_start == training_week(),
        WorkoutSession.status.in_(['in_progress', 'completed']),
    ).order_by(WorkoutSession.started_at, WorkoutSession.id))).scalars().all())
    day_continuity = await load_training_day_continuity(
        db, user_id=current_user.id, family_ids={plan.family_id for plan in rows},
    )
    return [
        await build_plan_detail(db, plan, profile=profile, weekly_sessions=weekly_sessions,
                                day_continuity=day_continuity)
        for plan in rows
    ]


@router.post("/plans/personalized/preview", response_model=PersonalizedPlanPreview)
async def preview_personalized_workout_plan(
    body: PersonalizedPlanPreviewRequest = Body(default_factory=PersonalizedPlanPreviewRequest),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services.personalized_planner import (
        PersonalizedPlanError,
        preview_personalized_plan,
    )

    try:
        return await preview_personalized_plan(
            db,
            user_id=current_user.id,
            request=body,
        )
    except PersonalizedPlanError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/plans/personalized/confirm",
    response_model=WorkoutPlanDetail,
    status_code=201,
)
async def confirm_personalized_workout_plan(
    body: PersonalizedPlanConfirmRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await lock_training_user(db, current_user.id)
    from app.services.personalized_planner import (
        PersonalizedPlanError,
        validate_personalized_selection,
    )

    profile = await db.scalar(
        select(UserProfile).where(UserProfile.user_id == current_user.id)
    )
    if profile is None or not profile.onboarding_completed:
        raise HTTPException(status_code=400, detail="请先完成新用户资料与健康筛查")
    existing_active = await db.scalar(select(WorkoutPlan.id).where(
        WorkoutPlan.user_id == current_user.id,
        WorkoutPlan.is_active.is_(True),
    ).limit(1))
    if existing_active is not None:
        raise HTTPException(
            status_code=409,
            detail="已有活动计划，请通过计划编辑器调整",
        )

    scheduled_days = {item.day_of_week for item in body.exercises}
    if len(scheduled_days) != body.days_per_week:
        raise HTTPException(status_code=400, detail="训练日数量与计划设置不一致")

    day_exercise_keys = [
        (item.day_of_week, item.exercise_id) for item in body.exercises
    ]
    if len(day_exercise_keys) != len(set(day_exercise_keys)):
        raise HTTPException(status_code=400, detail="同一训练日不能重复安排相同动作")

    exercise_ids = {item.exercise_id for item in body.exercises}
    exercise_rows = (await db.execute(
        select(Exercise).where(
            Exercise.id.in_(exercise_ids),
            Exercise.is_active.is_(True),
            visible_exercise(current_user.id),
        )
    )).scalars().all()
    if len(exercise_rows) != len(exercise_ids):
        raise HTTPException(status_code=400, detail="计划包含不存在或已停用的动作")
    try:
        validate_personalized_selection(profile, exercise_rows)
    except PersonalizedPlanError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    plan = WorkoutPlan(
        user_id=current_user.id,
        name=body.name,
        goal=body.goal,
        duration_weeks=body.duration_weeks,
        days_per_week=body.days_per_week,
        is_active=True,
        ai_generated=True,
        notes=json.dumps(
            {
                "session_duration_min": body.session_duration_min,
                "rationale": body.rationale,
                "safety_notes": body.safety_notes,
                "generation_strategy": "profile_rules_v1",
            },
            ensure_ascii=False,
        ),
    )
    db.add(plan)
    await db.flush()
    for item in body.exercises:
        db.add(PlannedExercise(
            plan_id=plan.id,
            exercise_id=item.exercise_id,
            day_of_week=item.day_of_week,
            sets=item.sets,
            reps=item.reps,
            rest_seconds=item.rest_seconds,
            order_index=item.order_index,
        ))
    await db.commit()
    return await build_plan_detail(db, plan)


def _plan_proposal_error(exc) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message},
    )


@router.get(
    "/plans/{plan_id}/edit-context",
    response_model=PlanEditContext,
)
async def get_plan_edit_context(
    plan_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.config import settings
    from app.services.plan_management_proposals import (
        PlanProposalError,
        build_plan_edit_context,
    )

    try:
        return await build_plan_edit_context(
            db,
            user_id=current_user.id,
            plan_id=plan_id,
            proposals_enabled=settings.MANUAL_PLAN_PROPOSALS_ENABLED,
        )
    except PlanProposalError as exc:
        return _plan_proposal_error(exc)


@router.post(
    "/plans/{plan_id}/adjustment-proposals",
    response_model=PlanProposalReference,
    status_code=201,
)
async def create_plan_adjustment_proposal(
    plan_id: str,
    body: CreatePlanAdjustmentProposalRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.config import settings
    from app.services.plan_management_proposals import (
        PlanProposalError,
        create_manual_plan_adjustment_proposal,
    )

    try:
        return await create_manual_plan_adjustment_proposal(
            db,
            enabled=settings.MANUAL_PLAN_PROPOSALS_ENABLED,
            user_id=current_user.id,
            plan_id=plan_id,
            request=body,
        )
    except PlanProposalError as exc:
        return _plan_proposal_error(exc)


@router.post(
    "/plans/{plan_id}/deletion-proposals",
    response_model=PlanProposalReference,
    status_code=201,
)
async def create_plan_deletion_proposal(
    plan_id: str,
    body: CreatePlanDeletionProposalRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.config import settings
    from app.services.plan_management_proposals import (
        PlanProposalError,
        create_manual_plan_deletion_proposal,
    )

    try:
        return await create_manual_plan_deletion_proposal(
            db,
            enabled=settings.MANUAL_PLAN_PROPOSALS_ENABLED,
            user_id=current_user.id,
            plan_id=plan_id,
            request=body,
        )
    except PlanProposalError as exc:
        return _plan_proposal_error(exc)


@router.get("/plans/{plan_id}", response_model=WorkoutPlanDetail)
async def get_plan(
    plan_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    plan = await get_user_plan(db, user_id=current_user.id, plan_id=plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="训练计划不存在")

    return await build_plan_detail(db, plan)


@router.delete("/plans/{plan_id}", status_code=204)
async def delete_plan(
    plan_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await lock_training_user(db, current_user.id)
    plan = await db.scalar(
        select(WorkoutPlan).where(
            WorkoutPlan.id == plan_id,
            WorkoutPlan.user_id == current_user.id,
        )
    )
    if not plan:
        raise HTTPException(status_code=404, detail="训练计划不存在")
    if plan.is_active:
        raise HTTPException(
            status_code=409,
            detail="活动计划必须通过删除提案确认后删除",
        )
    if await db.scalar(select(WorkoutSession.id).where(
        WorkoutSession.plan_id == plan.id, WorkoutSession.status == 'in_progress',
    ).limit(1)):
        raise HTTPException(409, '请先完成或保留记录并结束该计划中进行中的训练')
    plan.hidden_at = plan.hidden_at or datetime.now(timezone.utc)
    await db.commit()


@router.post("/plans/generate", response_model=WorkoutPlanDetail, status_code=201)
async def generate_plan(
    body: GeneratePlanRequest = Body(default_factory=GeneratePlanRequest),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await lock_training_user(db, current_user.id)
    existing_active = await db.scalar(select(WorkoutPlan.id).where(
        WorkoutPlan.user_id == current_user.id,
        WorkoutPlan.is_active.is_(True),
    ).limit(1))
    if existing_active is not None:
        raise HTTPException(
            status_code=409,
            detail="已有活动计划，请通过计划编辑器调整",
        )
    from app.services.planner import generate_workout_plan
    plan = await generate_workout_plan(
        db,
        user_id=current_user.id,
        goal=body.goal,
        duration_weeks=body.duration_weeks,
    )
    return await build_plan_detail(db, plan)


# ── Sessions ──────────────────────────────────────────────────────────────────

@router.post("/sessions/start", response_model=WorkoutSessionDetail, status_code=201)
async def start_session(
    body: WorkoutSessionStart,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await lock_training_user(db, current_user.id)
    active = await db.scalar(
        select(WorkoutSession)
        .where(
            WorkoutSession.user_id == current_user.id,
            WorkoutSession.status == "in_progress",
        )
        .order_by(WorkoutSession.started_at.desc())
        .limit(1)
    )
    if active:
        raise HTTPException(status_code=409, detail="已有进行中的训练，请先继续或保留记录并结束")

    plan = await db.scalar(
        select(WorkoutPlan).where(
            WorkoutPlan.id == body.plan_id,
            WorkoutPlan.user_id == current_user.id,
        )
    )
    if not plan:
        raise HTTPException(status_code=404, detail="训练计划不存在")
    if not plan.is_active:
        raise HTTPException(status_code=400, detail="训练计划已归档，请选择当前计划")

    week = training_week()
    day_continuity = await load_training_day_continuity(
        db, user_id=current_user.id, family_ids={plan.family_id},
    )
    candidates = (await db.execute(select(WorkoutSession).where(
        WorkoutSession.user_id == current_user.id,
        WorkoutSession.plan_family_id == plan.family_id,
        WorkoutSession.week_start == week,
        WorkoutSession.day_of_week == body.day_of_week,
        WorkoutSession.status == 'completed',
    ).order_by(WorkoutSession.started_at, WorkoutSession.id))).scalars().all()
    completed = next((row.id for row in candidates if day_continuity.matches(plan, row)), None)
    if completed:
        raise HTTPException(409, detail={'code': 'training_day_completed', 'message': '本周该训练日已完成，请查看本次训练', 'session_id': completed})

    from app.services.plan_safety import evaluate_plan_safety

    safety = await evaluate_plan_safety(db, plan=plan)
    if safety.status == "needs_review":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "plan_health_review_required",
                "message": "当前计划与最新健康或训练条件不兼容，请先复核计划",
                "reasons": list(safety.reasons),
            },
        )

    planned = (await db.execute(
        select(PlannedExercise)
        .where(
            PlannedExercise.plan_id == plan.id,
            PlannedExercise.day_of_week == body.day_of_week,
        )
        .order_by(PlannedExercise.order_index)
    )).scalars().all()
    if not planned:
        raise HTTPException(status_code=400, detail="该训练日没有动作安排")

    now = datetime.now(timezone.utc)
    catalog = await _validate_owned_exercises(db, current_user.id, {item.exercise_id for item in planned})
    session = WorkoutSession(
        user_id=current_user.id,
        plan_id=plan.id,
        plan_name=plan.name,
        plan_family_id=plan.family_id,
        week_start=week,
        day_of_week=body.day_of_week,
        status="in_progress",
        trained_at=training_today(),
        started_at=now,
    )
    db.add(session)
    await db.flush()
    for item in planned:
        db.add(SessionExercise(
            session_id=session.id,
            exercise_id=item.exercise_id,
            exercise_name=catalog[item.exercise_id].name_zh,
            **classification_snapshot(catalog[item.exercise_id]),
            order_index=item.order_index,
            target_sets=item.sets,
            target_reps=item.reps,
            target_weight_kg=item.recommended_weight_kg,
            rest_seconds=item.rest_seconds,
            sets_data=[],
        ))
    await db.commit()
    return await build_session_detail(db, session)


@router.get("/sessions/active", response_model=WorkoutSessionDetail | None)
async def get_active_session(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await get_active_user_session(db, user_id=current_user.id)
    return await build_session_detail(db, session) if session else None


@router.get("/sessions/progress", response_model=WorkoutProgressResponse)
async def get_workout_progress(
    weeks: int = Query(default=8, ge=1, le=52),
    week_start: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_workout_progress_summary(
        db,
        user_id=current_user.id,
        weeks=weeks,
        selected_week=week_start,
    )


@router.post("/sessions", response_model=WorkoutSessionDetail, status_code=201)
async def log_session(
    body: WorkoutSessionCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await lock_training_user(db, current_user.id)
    catalog = await _validate_owned_exercises(db, current_user.id, {item.exercise_id for item in body.exercises})
    plan_name = None
    if body.plan_id:
        plan = await db.scalar(
            select(WorkoutPlan).where(
                WorkoutPlan.id == body.plan_id,
                WorkoutPlan.user_id == current_user.id,
            )
        )
        if not plan:
            raise HTTPException(status_code=404, detail="训练计划不存在")
        plan_name = plan.name
    now = datetime.now(timezone.utc)
    session = WorkoutSession(
        user_id=current_user.id,
        plan_id=body.plan_id,
        plan_name=plan_name,
        status="completed",
        trained_at=body.trained_at,
        duration_min=body.duration_min,
        notes=body.notes,
        started_at=now,
        completed_at=now,
    )
    db.add(session)
    await db.flush()

    for ex in body.exercises:
        db.add(SessionExercise(
            session_id=session.id,
            exercise_id=ex.exercise_id,
            exercise_name=catalog[ex.exercise_id].name_zh,
            **classification_snapshot(catalog[ex.exercise_id]),
            target_sets=len(ex.sets_data),
            sets_data=[s.model_dump() for s in ex.sets_data],
        ))

    await db.commit()
    return await build_session_detail(db, session)


@router.put('/sessions/{session_id}/energy-classifications', response_model=WorkoutSessionDetail)
async def set_session_energy_categories(session_id: str, body: SessionEnergyUpdate,
                                         current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    session = await update_session_categories(db, current_user.id, session_id, body)
    return await build_session_detail(db, session)


@router.get("/sessions", response_model=list[WorkoutSessionDetail])
async def list_sessions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = await list_user_workout_sessions(db, user_id=current_user.id)
    return [await build_session_detail(db, session) for session in rows]


@router.get("/sessions/{session_id}", response_model=WorkoutSessionDetail)
async def get_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await get_user_workout_session(
        db,
        user_id=current_user.id,
        session_id=session_id,
    )
    if not session:
        raise HTTPException(status_code=404, detail="训练记录不存在")
    return await build_session_detail(db, session)


@router.put(
    "/sessions/{session_id}/exercises/{session_exercise_id}/sets/{set_number}",
    response_model=WorkoutSessionDetail,
)
async def record_workout_set(
    session_id: str,
    session_exercise_id: str,
    set_number: int,
    body: WorkoutSetRecord,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await lock_training_user(db, current_user.id)
    if set_number < 1 or set_number > 100:
        raise HTTPException(status_code=422, detail="组号必须在 1 到 100 之间")
    session = await db.scalar(
        select(WorkoutSession).where(
            WorkoutSession.id == session_id,
            WorkoutSession.user_id == current_user.id,
        )
    )
    if not session:
        raise HTTPException(status_code=404, detail="训练记录不存在")
    if session.status != "in_progress":
        raise HTTPException(status_code=409, detail="已完成的训练不能修改")

    exercise = await db.scalar(
        select(SessionExercise).where(
            SessionExercise.id == session_exercise_id,
            SessionExercise.session_id == session.id,
        )
    )
    if not exercise:
        raise HTTPException(status_code=404, detail="训练动作不存在")

    sets_data = normalized_sets(exercise.sets_data)
    baseline = await get_personal_record_baseline(
        db,
        user_id=current_user.id,
        exercise_id=exercise.exercise_id,
    )
    baseline.extend(
        item for item in sets_data if item.get("set_number") != set_number
    )
    record = {
        "set_number": set_number,
        "reps": body.reps,
        "weight_kg": body.weight_kg,
    }
    record["is_personal_record"] = is_personal_record(record, baseline)
    existing_index = next(
        (
            index
            for index, item in enumerate(sets_data)
            if item.get("set_number") == set_number
        ),
        None,
    )
    if existing_index is None:
        record['recorded_at'] = datetime.now(timezone.utc).isoformat()
        if body.finished_at is not None:
            elapsed = (datetime.now(timezone.utc) - body.finished_at).total_seconds()
            if elapsed < -300 or elapsed > 86400:
                raise HTTPException(422, '计时时间超出有效范围，请核对设备时间')
            record['rest_started_at'] = body.finished_at.isoformat()
        sets_data.append(record)
    else:
        sets_data[existing_index] = {**sets_data[existing_index], **record}
    sets_data.sort(key=lambda item: item.get("set_number", 0))
    exercise.sets_data = sets_data
    await db.commit()
    return await build_session_detail(db, session)


@router.post(
    "/sessions/{session_id}/complete",
    response_model=WorkoutSessionDetail,
)
async def complete_session(
    session_id: str,
    body: WorkoutSessionComplete = Body(default_factory=WorkoutSessionComplete),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await lock_training_user(db, current_user.id)
    session = await db.scalar(
        select(WorkoutSession).where(
            WorkoutSession.id == session_id,
            WorkoutSession.user_id == current_user.id,
        ).with_for_update()
    )
    if not session:
        raise HTTPException(status_code=404, detail="训练记录不存在")
    if session.status != "in_progress":
        raise HTTPException(status_code=409, detail="训练已经完成")

    exercises = (await db.execute(
        select(SessionExercise).where(SessionExercise.session_id == session.id)
    )).scalars().all()
    if not any(sets_metrics(item.sets_data)[0] > 0 for item in exercises):
        raise HTTPException(status_code=409, detail="至少记录一组后才能完成训练")

    feedback = WorkoutFeedback(
        difficulty_feedback=body.difficulty_feedback,
        perceived_exertion=body.perceived_exertion,
        energy_level=body.energy_level,
        pain_level=body.pain_level,
        pain_areas=body.pain_areas,
        feedback_notes=body.feedback_notes,
    )
    from app.services.adaptive_planner import build_adaptive_adjustment_proposals
    from app.services.plan_management_proposals import (
        PlanProposalError,
        create_workout_completion_adjustment_proposal,
    )

    adaptive_proposals = await build_adaptive_adjustment_proposals(
        db,
        session=session,
        session_exercises=list(exercises),
        feedback=feedback,
    )
    try:
        proposal, adaptive_status, adjustments = (
            await create_workout_completion_adjustment_proposal(
                db,
                session=session,
                proposals=adaptive_proposals,
            )
        )
    except PlanProposalError:
        # A stale or otherwise unsuitable plan must never prevent the completed
        # workout itself from being saved.
        proposal = None
        adaptive_status = "failed"
        adjustments = []
    feedback_fields = {
        "difficulty_feedback",
        "perceived_exertion",
        "energy_level",
        "pain_level",
        "pain_areas",
        "feedback_notes",
    }
    session.feedback_data = (
        feedback.model_dump(exclude_none=True)
        if feedback_fields.intersection(body.model_fields_set)
        else {}
    )
    session.adjustments_data = adjustments
    session.adaptive_proposal_id = proposal.id if proposal is not None else None

    now = datetime.now(timezone.utc)
    started_at = session.started_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    elapsed_minutes = max(1, round((now - started_at).total_seconds() / 60))
    session.status = "completed"
    session.completed_at = now
    session.duration_min = body.duration_min or elapsed_minutes
    if body.notes is not None:
        session.notes = body.notes
    await db.commit()
    result = await build_session_detail(db, session)
    if adaptive_status in {"blocked_by_existing", "failed"}:
        result.adaptive_adjustment_status = adaptive_status
    return result


@router.put('/sessions/{session_id}/exercises/{session_exercise_id}/sets/{set_number}/rest', response_model=WorkoutSessionDetail)
async def record_rest(session_id: str, session_exercise_id: str, set_number: int,
                      body: WorkoutRestRecord, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await lock_training_user(db, current_user.id)
    session = await get_user_workout_session(db, user_id=current_user.id, session_id=session_id)
    if session is None:
        raise HTTPException(404, '训练记录不存在')
    exercise = await db.scalar(select(SessionExercise).where(
        SessionExercise.id == session_exercise_id, SessionExercise.session_id == session.id,
    ))
    if exercise is None:
        raise HTTPException(404, '训练动作不存在')
    records = normalized_sets(exercise.sets_data)
    record = next((row for row in records if row.get('set_number') == set_number), None)
    if record is None:
        raise HTTPException(404, '请先保存本组记录')
    expected = {'actual_rest_seconds': body.actual_rest_seconds, 'rest_event_id': body.event_id, 'rest_end_reason': body.end_reason}
    if record.get('rest_event_id'):
        if all(record.get(key) == value for key, value in expected.items()):
            return await build_session_detail(db, session)
        raise HTTPException(409, '该组休息已经记录，不可重复覆盖')
    if session.status != 'in_progress':
        raise HTTPException(409, '训练已结束，不能补写未知休息')
    record.update(expected)
    record['rest_source'] = 'client_timer'
    exercise.sets_data = records
    await db.commit()
    return await build_session_detail(db, session)


@router.post('/sessions/{session_id}/finish-early', response_model=WorkoutSessionDetail)
async def finish_early(session_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await lock_training_user(db, current_user.id)
    session = await get_user_workout_session(db, user_id=current_user.id, session_id=session_id)
    if session is None:
        raise HTTPException(404, '训练记录不存在')
    if session.status == 'ended_early':
        return await build_session_detail(db, session)
    if session.status != 'in_progress':
        raise HTTPException(409, '训练已经完成，不能改为提前结束')
    now = datetime.now(timezone.utc)
    started = session.started_at.replace(tzinfo=timezone.utc) if session.started_at.tzinfo is None else session.started_at
    session.status = 'ended_early'
    session.completed_at = now
    session.duration_min = max(1, round((now - started).total_seconds() / 60))
    await db.commit()
    return await build_session_detail(db, session)


@router.delete("/sessions/{session_id}", status_code=204)
async def abandon_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await lock_training_user(db, current_user.id)
    session = await db.scalar(
        select(WorkoutSession).where(
            WorkoutSession.id == session_id,
            WorkoutSession.user_id == current_user.id,
        )
    )
    if not session:
        raise HTTPException(status_code=404, detail="训练记录不存在")
    if session.status != "in_progress":
        raise HTTPException(status_code=409, detail="已完成的训练不能放弃")
    await db.execute(
        delete(SessionExercise).where(SessionExercise.session_id == session.id)
    )
    await db.delete(session)
    await db.commit()
