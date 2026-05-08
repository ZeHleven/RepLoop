from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.deps import get_current_user
from app.models.user import User
from app.models.profile import UserProfile, WeightLog
from app.schemas.profile import ProfileUpdateRequest, ProfileResponse, WeightLogRequest, WeightLogResponse
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


@router.put("", response_model=ProfileResponse)
async def update_profile(
    body: ProfileUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await db.scalar(
        select(UserProfile).where(UserProfile.user_id == current_user.id)
    )
    update_data = body.model_dump(exclude_none=True)

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
    return profile


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
    return WeightLogResponse(
        id=log.id,
        weight_kg=log.weight_kg,
        recorded_at=log.recorded_at.isoformat(),
    )
