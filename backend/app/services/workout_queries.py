from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.exercise import Exercise
from app.models.agent import AgentProposal
from app.models.profile import UserProfile
from app.models.workout import PlannedExercise, SessionExercise, WorkoutPlan, WorkoutSession
from app.schemas.workout import (
    PlannedExerciseResponse,
    SessionExerciseResponse,
    WeeklyWorkoutProgress,
    WorkoutAdjustmentResponse,
    AdaptiveAdjustmentProposalResponse,
    WorkoutFeedback,
    WorkoutPlanDetail,
    WorkoutProgressResponse,
    WorkoutSessionDetail,
    DailyWorkoutProgress, WeeklySessionReference,
)
from app.services.training_lifecycle import training_today, training_week, display_plan_name
from app.services.training_day_continuity import TrainingDayContinuity, load_training_day_continuity
from app.services.custom_exercises import safety_notice, visible_exercise
from app.services.exercise_energy import valid_sets


def sets_metrics(sets_data: object) -> tuple[int, int, float]:
    """Return valid set count, repetitions and external-load volume."""
    if not isinstance(sets_data, list):
        return 0, 0, 0
    reps = 0
    volume = 0.0
    valid_sets = 0
    for item in sets_data:
        if not isinstance(item, dict):
            continue
        item_reps = item.get("reps")
        if not isinstance(item_reps, int) or item_reps < 1:
            continue
        weight = item.get("weight_kg")
        item_weight = float(weight) if isinstance(weight, (int, float)) else 0.0
        valid_sets += 1
        reps += item_reps
        volume += item_reps * item_weight
    return valid_sets, reps, round(volume, 1)


def normalized_sets(sets_data: object) -> list[dict]:
    if not isinstance(sets_data, list):
        return []
    return [dict(item) for item in sets_data if isinstance(item, dict)]


def best_performance(sets_data: list[dict]) -> tuple[float | None, int | None]:
    weighted: list[tuple[float, int]] = []
    bodyweight_reps: list[int] = []
    for item in sets_data:
        reps = item.get("reps")
        if not isinstance(reps, int) or reps < 1:
            continue
        weight = item.get("weight_kg")
        if isinstance(weight, (int, float)) and float(weight) > 0:
            weighted.append((float(weight), reps))
        else:
            bodyweight_reps.append(reps)
    if weighted:
        best_weight, best_reps = max(weighted, key=lambda value: (value[0], value[1]))
        return best_weight, best_reps
    if bodyweight_reps:
        return None, max(bodyweight_reps)
    return None, None


def is_personal_record(candidate: dict, baseline: list[dict]) -> bool:
    reps = candidate.get("reps")
    if not isinstance(reps, int) or reps < 1:
        return False
    weight = candidate.get("weight_kg")
    if isinstance(weight, (int, float)) and float(weight) > 0:
        prior_weighted = [
            (float(item["weight_kg"]), item["reps"])
            for item in baseline
            if isinstance(item.get("reps"), int)
            and item["reps"] > 0
            and isinstance(item.get("weight_kg"), (int, float))
            and float(item["weight_kg"]) > 0
        ]
        return not prior_weighted or (float(weight), reps) > max(prior_weighted)
    prior_bodyweight_reps = [
        item["reps"]
        for item in baseline
        if isinstance(item.get("reps"), int)
        and item["reps"] > 0
        and not (
            isinstance(item.get("weight_kg"), (int, float))
            and float(item["weight_kg"]) > 0
        )
    ]
    return not prior_bodyweight_reps or reps > max(prior_bodyweight_reps)


async def list_user_plans(db: AsyncSession, *, user_id: str) -> list[WorkoutPlan]:
    return list((await db.execute(
        select(WorkoutPlan)
        .where(WorkoutPlan.user_id == user_id, WorkoutPlan.hidden_at.is_(None))
        .order_by(WorkoutPlan.created_at.desc())
    )).scalars().all())


async def get_user_plan(
    db: AsyncSession, *, user_id: str, plan_id: str
) -> WorkoutPlan | None:
    return await db.scalar(
        select(WorkoutPlan).where(
            WorkoutPlan.id == plan_id,
            WorkoutPlan.user_id == user_id,
        )
    )


async def build_plan_detail(
    db: AsyncSession,
    plan: WorkoutPlan,
    *,
    profile: UserProfile | None = None,
    weekly_sessions: list[WorkoutSession] | None = None,
    day_continuity: TrainingDayContinuity | None = None,
) -> WorkoutPlanDetail:
    exercises = (await db.execute(
        select(PlannedExercise)
        .where(PlannedExercise.plan_id == plan.id)
        .order_by(PlannedExercise.day_of_week, PlannedExercise.order_index)
    )).scalars().all()

    exercise_ids = {item.exercise_id for item in exercises}
    names: dict[str, str] = {}
    notices: dict[str, str | None] = {}
    if exercise_ids:
        rows = (await db.execute(
            select(Exercise).where(Exercise.id.in_(exercise_ids), visible_exercise(plan.user_id))
        )).scalars().all()
        names = {row.id: row.name_zh for row in rows}
        notices = {row.id: safety_notice(row) for row in rows}

    result = WorkoutPlanDetail.model_validate(plan)
    result.display_name = display_plan_name(plan.name, generated=plan.ai_generated)
    result.week_start = training_week()
    sessions = weekly_sessions
    if sessions is None:
        sessions = (await db.execute(select(WorkoutSession).where(
            WorkoutSession.user_id == plan.user_id,
            WorkoutSession.plan_family_id == plan.family_id,
            WorkoutSession.week_start == result.week_start,
            WorkoutSession.status.in_(['in_progress', 'completed']),
        ).order_by(WorkoutSession.started_at, WorkoutSession.id))).scalars().all()
    if day_continuity is None:
        day_continuity = await load_training_day_continuity(
            db, user_id=plan.user_id, family_ids={plan.family_id},
        )
    # One reference per uninterrupted weekday arrangement, not all old versions.
    by_day = {}
    for session in sessions:
        if session.week_start != result.week_start or not day_continuity.matches(plan, session):
            continue
        day = session.day_of_week
        if day not in {item.day_of_week for item in exercises}:
            continue
        if day not in by_day or by_day[day].status != 'completed':
            by_day[day] = session
    result.weekly_sessions = [WeeklySessionReference(day_of_week=day, session_id=row.id, status=row.status) for day, row in sorted(by_day.items())]
    result.weekly_completed_days = sum(row.status == 'completed' for row in by_day.values())
    result.exercises = [
        PlannedExerciseResponse(
            id=item.id,
            plan_id=item.plan_id,
            exercise_id=item.exercise_id,
            exercise_name=names.get(item.exercise_id),
            safety_notice=notices.get(item.exercise_id),
            day_of_week=item.day_of_week,
            sets=item.sets,
            reps=item.reps,
            rest_seconds=item.rest_seconds,
            recommended_weight_kg=item.recommended_weight_kg,
            order_index=item.order_index,
        )
        for item in exercises
    ]
    from app.services.plan_safety import evaluate_plan_safety

    safety = await evaluate_plan_safety(
        db,
        plan=plan,
        profile=profile,
        planned_exercises=list(exercises),
    )
    result.safety_status = safety.status
    result.safety_reasons = list(safety.reasons)
    from app.config import settings

    result.manual_proposals_enabled = settings.MANUAL_PLAN_PROPOSALS_ENABLED
    return result


async def get_active_user_session(
    db: AsyncSession, *, user_id: str
) -> WorkoutSession | None:
    return await db.scalar(
        select(WorkoutSession)
        .where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.status == "in_progress",
        )
        .order_by(WorkoutSession.started_at.desc())
        .limit(1)
    )


async def list_user_workout_sessions(
    db: AsyncSession, *, user_id: str, limit: int | None = None
) -> list[WorkoutSession]:
    query = (
        select(WorkoutSession)
        .where(WorkoutSession.user_id == user_id)
        .order_by(WorkoutSession.trained_at.desc(), WorkoutSession.created_at.desc())
    )
    if limit is not None:
        query = query.limit(limit)
    return list((await db.execute(query)).scalars().all())


async def get_user_workout_session(
    db: AsyncSession, *, user_id: str, session_id: str
) -> WorkoutSession | None:
    return await db.scalar(
        select(WorkoutSession).where(
            WorkoutSession.id == session_id,
            WorkoutSession.user_id == user_id,
        )
    )


async def get_completed_exercise_history(
    db: AsyncSession,
    *,
    user_id: str,
    exercise_ids: set[str],
    exclude_session_id: str | None = None,
) -> dict[str, list[SessionExercise]]:
    history_by_exercise: dict[str, list[SessionExercise]] = {
        exercise_id: [] for exercise_id in exercise_ids
    }
    if not exercise_ids:
        return history_by_exercise

    conditions = [
        WorkoutSession.user_id == user_id,
        WorkoutSession.status == "completed",
        SessionExercise.exercise_id.in_(exercise_ids),
    ]
    if exclude_session_id is not None:
        conditions.append(WorkoutSession.id != exclude_session_id)
    rows = (await db.execute(
        select(SessionExercise)
        .join(WorkoutSession, WorkoutSession.id == SessionExercise.session_id)
        .where(*conditions)
        .order_by(
            WorkoutSession.completed_at.desc().nullslast(),
            WorkoutSession.created_at.desc(),
        )
    )).scalars().all()
    for item in rows:
        history_by_exercise[item.exercise_id].append(item)
    return history_by_exercise


async def get_personal_record_baseline(
    db: AsyncSession, *, user_id: str, exercise_id: str
) -> list[dict]:
    history = await get_completed_exercise_history(
        db,
        user_id=user_id,
        exercise_ids={exercise_id},
    )
    return [
        recorded_set
        for item in history.get(exercise_id, [])
        for recorded_set in normalized_sets(item.sets_data)
    ]


async def build_session_detail(
    db: AsyncSession, session: WorkoutSession
) -> WorkoutSessionDetail:
    exercises = (await db.execute(
        select(SessionExercise)
        .where(SessionExercise.session_id == session.id)
        .order_by(SessionExercise.order_index, SessionExercise.id)
    )).scalars().all()

    exercise_ids = {item.exercise_id for item in exercises}
    names: dict[str, str] = {}
    notices: dict[str, str | None] = {}
    catalog: dict[str, Exercise] = {}
    if exercise_ids:
        rows = (await db.execute(
            select(Exercise).where(Exercise.id.in_(exercise_ids), visible_exercise(session.user_id))
        )).scalars().all()
        catalog = {row.id: row for row in rows}
        names = {row.id: row.name_zh for row in rows}
        notices = {row.id: safety_notice(row) for row in rows}

    history_by_exercise = await get_completed_exercise_history(
        db,
        user_id=session.user_id,
        exercise_ids=exercise_ids,
        exclude_session_id=session.id,
    )

    plan_name = session.plan_name
    if plan_name is None and session.plan_id:
        plan_name = await db.scalar(
            select(WorkoutPlan.name).where(WorkoutPlan.id == session.plan_id)
        )

    response_exercises = []
    total_sets = 0
    total_reps = 0
    total_volume = 0.0
    for item in exercises:
        sets_data = normalized_sets(item.sets_data)
        source = catalog.get(item.exercise_id)
        custom_owned = source is not None and source.owner_id == session.user_id
        history = history_by_exercise.get(item.exercise_id, [])
        previous_sets = normalized_sets(history[0].sets_data) if history else []
        all_sets = [
            history_set
            for history_item in history
            for history_set in normalized_sets(history_item.sets_data)
        ]
        best_weight, best_reps = best_performance([*all_sets, *sets_data])
        sets_count, reps, volume = sets_metrics(sets_data)
        total_sets += sets_count
        total_reps += reps
        total_volume += volume
        response_exercises.append(SessionExerciseResponse(
            id=item.id,
            session_id=item.session_id,
            exercise_id=item.exercise_id,
            exercise_name=item.exercise_name or names.get(item.exercise_id),
            safety_notice=notices.get(item.exercise_id),
            order_index=item.order_index,
            target_sets=item.target_sets,
            target_reps=item.target_reps,
            target_weight_kg=item.target_weight_kg,
            rest_seconds=item.rest_seconds,
            sets_data=sets_data,
            previous_sets_data=previous_sets,
            personal_best_weight_kg=best_weight,
            personal_best_reps=best_reps,
            energy_category=item.energy_category,
            energy_category_source=item.energy_category_source,
            energy_rule_version=item.energy_rule_version,
            energy_classification_editable=bool(custom_owned and valid_sets(item) and session.status in {'completed', 'ended_early'}),
            library_energy_category=source.energy_category if custom_owned else None,
            library_energy_category_version=source.energy_category_version if custom_owned else None,
            library_energy_editable=bool(custom_owned and source.is_active),
        ))

    result = WorkoutSessionDetail.model_validate(session)
    result.orphaned = session.plan_id is None and session.status == 'in_progress'
    result.plan_name = plan_name
    result.exercises = response_exercises
    result.total_sets = total_sets
    result.total_reps = total_reps
    result.total_volume_kg = round(total_volume, 1)
    feedback_data = session.feedback_data if isinstance(session.feedback_data, dict) else {}
    result.feedback = WorkoutFeedback.model_validate(feedback_data) if feedback_data else None
    adjustment_data = session.adjustments_data if isinstance(session.adjustments_data, list) else []
    result.adjustments = [
        WorkoutAdjustmentResponse.model_validate(item)
        for item in adjustment_data
        if isinstance(item, dict)
    ]
    if session.adaptive_proposal_id:
        proposal = await db.scalar(select(AgentProposal).where(
            AgentProposal.id == session.adaptive_proposal_id,
            AgentProposal.user_id == session.user_id,
            AgentProposal.proposal_type == "plan_adjustment_v2",
        ))
        if (
            proposal is not None
            and proposal.expires_at is not None
            and proposal.payload_fingerprint is not None
        ):
            proposal_status = proposal.status
            if (
                proposal_status == "pending_confirmation"
                and datetime.now(timezone.utc) >= proposal.expires_at
            ):
                proposal_status = "expired"
            result.adaptive_adjustment_status = proposal_status
            result.adaptive_adjustment_proposal = (
                AdaptiveAdjustmentProposalResponse(
                    id=proposal.id,
                    proposal_type="plan_adjustment_v2",
                    status=proposal_status,
                    version=proposal.version,
                    expires_at=proposal.expires_at,
                    payload_fingerprint=proposal.payload_fingerprint,
                )
            )
    elif result.adjustments:
        # Sessions completed before 0.5.32 applied adjustments immediately.
        result.adaptive_adjustment_status = "applied"
    return result


async def get_workout_progress_summary(
    db: AsyncSession,
    *,
    user_id: str,
    weeks: int,
    today: date | None = None,
    selected_week: date | None = None,
) -> WorkoutProgressResponse:
    from app.services.workout_reporting import report_today
    today = today or report_today()
    current_week = today - timedelta(days=today.weekday())
    first_week = current_week - timedelta(weeks=weeks - 1)
    if selected_week is not None:
        selected_week = training_week(selected_week)
        if selected_week < first_week or selected_week > current_week:
            from fastapi import HTTPException
            raise HTTPException(422, '所选周不在查询范围内')
    range_start = selected_week or first_week
    range_end = (selected_week or current_week) + timedelta(days=7)
    sessions = (await db.execute(
        select(WorkoutSession).where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.status.in_(['completed', 'ended_early']),
            WorkoutSession.trained_at >= range_start,
            WorkoutSession.trained_at < range_end,
            WorkoutSession.trained_at <= today,
        )
    )).scalars().all()

    buckets = {
        range_start + timedelta(weeks=index): {
            "sessions": 0,
            "sets": 0,
            "reps": 0,
            "volume": 0.0,
        }
        for index in range(1 if selected_week else weeks)
    }
    daily = {selected_week + timedelta(days=i): DailyWorkoutProgress(date=selected_week + timedelta(days=i)) for i in range(7)} if selected_week else {}
    session_dates = {}
    session_weeks: dict[str, date] = {}
    for session in sessions:
        week_start = session.trained_at - timedelta(days=session.trained_at.weekday())
        if week_start in buckets:
            buckets[week_start]["sessions"] += 1
            session_weeks[session.id] = week_start
            session_dates[session.id] = session.trained_at
            if session.trained_at in daily:
                daily[session.trained_at].sessions += 1

    if session_weeks:
        exercises = (await db.execute(
            select(SessionExercise).where(SessionExercise.session_id.in_(session_weeks))
        )).scalars().all()
        for exercise in exercises:
            week_start = session_weeks[exercise.session_id]
            sets_count, reps, volume = sets_metrics(exercise.sets_data)
            buckets[week_start]["sets"] += sets_count
            buckets[week_start]["reps"] += reps
            buckets[week_start]["volume"] += volume
            day = daily.get(session_dates[exercise.session_id])
            if day is not None:
                day.sets += sets_count
                day.reps += reps
                day.volume_kg = round(day.volume_kg + volume, 1)

    weekly = [
        WeeklyWorkoutProgress(
            week_start=week_start,
            sessions=values["sessions"],
            sets=values["sets"],
            reps=values["reps"],
            volume_kg=round(values["volume"], 1),
        )
        for week_start, values in buckets.items()
    ]
    response = WorkoutProgressResponse(
        weeks=weeks,
        total_sessions=sum(item.sessions for item in weekly),
        total_sets=sum(item.sets for item in weekly),
        total_reps=sum(item.reps for item in weekly),
        total_volume_kg=round(sum(item.volume_kg for item in weekly), 1),
        weekly=weekly,
        selected_week=selected_week,
        daily=list(daily.values()),
    )
    from app.services.workout_reporting import enrich_progress
    return WorkoutProgressResponse.model_validate(enrich_progress(response.model_dump(mode="json"), today))
