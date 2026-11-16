"""User-owned immutable exercise definitions, never public recommendations."""
from sqlalchemy import or_
from app.models.exercise import Exercise

CUSTOM_EXERCISE_NOTICE = (
    '本平台仅提供记录与计划管理，请自行核对动作方法、训练负荷及身体适用性；'
    '如有疑问，请咨询专业人士。'
)


def visible_exercise(user_id: str):
    return or_(Exercise.owner_id.is_(None), Exercise.owner_id == user_id)


def safety_notice(exercise: Exercise) -> str | None:
    return CUSTOM_EXERCISE_NOTICE if exercise.owner_id else None
