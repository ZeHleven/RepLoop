from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.deps import get_current_user
from app.models.user import User
from app.models.profile import UserProfile, WeightLog
from app.schemas.profile import (
    ProfileResponse,
    ProfileUpdateRequest,
    ProfileUpdateResponse,
    WeightLogRequest,
    WeightLogResponse,
)
from app.services.profile import calculate_bmi

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("", response_model=ProfileResponse)
async def get_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await db.scalar(
        select(UserProfile).where(UserProfile.user_id == current_user.id)
    )
    return profile


@router.put("", response_model=ProfileUpdateResponse)
async def update_profile(
    body: ProfileUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await db.scalar(
        select(UserProfile).where(UserProfile.user_id == current_user.id)
    )
    update_data = body.model_dump(exclude_none=True)

    if update_data.get("onboarding_completed") is True:
        required_fields = {
            "age": "年龄",
            "gender": "性别",
            "height_cm": "身高",
            "weight_kg": "体重",
            "experience_level": "训练经验",
            "primary_goal": "训练目标",
            "training_days_per_week": "每周训练天数",
            "training_location": "训练地点",
        }
        missing = [
            label
            for field, label in required_fields.items()
            if update_data.get(field, getattr(profile, field)) in (None, "")
        ]
        health_missing = [
            label
            for field, label in (
                ("injuries", "伤病情况"),
                ("chronic_conditions", "慢性疾病情况"),
            )
            if update_data.get(field, getattr(profile, field)) is None
        ]
        missing.extend(health_missing)
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"完成引导前请填写：{'、'.join(missing)}",
            )

    height = update_data.get("height_cm", profile.height_cm)
    weight = update_data.get("weight_kg", profile.weight_kg)
    if height and weight:
        bmi, category = calculate_bmi(height, weight)
        update_data["bmi"] = bmi
        update_data["bmi_category"] = category

    for key, value in update_data.items():
        setattr(profile, key, value)

    await db.commit()
    await db.refresh(profile)
    from app.models.workout import WorkoutPlan
    from app.services.plan_safety import evaluate_plan_safety

    active_plan = await db.scalar(select(WorkoutPlan).where(
        WorkoutPlan.user_id == current_user.id,
        WorkoutPlan.is_active.is_(True),
    ).limit(1))
    safety_status = None
    safety_reasons: list[str] = []
    if active_plan is not None:
        evaluation = await evaluate_plan_safety(
            db, plan=active_plan, profile=profile
        )
        safety_status = evaluation.status
        safety_reasons = list(evaluation.reasons)
    return ProfileUpdateResponse.model_validate(profile).model_copy(update={
        "active_plan_safety_status": safety_status,
        "active_plan_safety_reasons": safety_reasons,
    })


@router.post("/weight", response_model=WeightLogResponse, status_code=201)
async def log_weight(
    body: WeightLogRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    log = WeightLog(user_id=current_user.id, weight_kg=body.weight_kg)
    db.add(log)

    profile = await db.scalar(
        select(UserProfile).where(UserProfile.user_id == current_user.id)
    )
    profile.weight_kg = body.weight_kg
    if profile.height_cm:
        bmi, category = calculate_bmi(profile.height_cm, body.weight_kg)
        profile.bmi = bmi
        profile.bmi_category = category

    await db.commit()
    await db.refresh(log)
    return log


@router.get("/weight", response_model=list[WeightLogResponse])
async def get_weight_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(WeightLog)
        .where(WeightLog.user_id == current_user.id)
        .order_by(WeightLog.recorded_at.asc())
        .limit(365)
    )).scalars().all()
    return rows
