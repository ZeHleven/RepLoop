"""Calendar and lifecycle invariants shared by manual and proposal entry points."""
from datetime import datetime, timedelta, timezone, date
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

TRAINING_TIMEZONE = timezone(timedelta(hours=8))


def training_today() -> date:
    return datetime.now(TRAINING_TIMEZONE).date()


def training_week(day: date | None = None) -> date:
    day = day or training_today()
    return day - timedelta(days=day.weekday())


async def lock_training_user(db: AsyncSession, user_id: str) -> None:
    # Stable lock even when the user has no plan/session yet. Shared by starts,
    # completion and plan decisions; never lock session before user.
    await db.scalar(select(User.id).where(User.id == user_id).with_for_update())


def display_plan_name(name: str, *, generated: bool) -> str:
    if generated and re.fullmatch(r'(减脂|增肌|力量提升|耐力提升|灵活性改善|综合体能) · [1-7]日入门计划', name):
        return name.replace('入门计划', '训练计划')
    return name
