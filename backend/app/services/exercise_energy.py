"""Explicit classification metadata; never authorizes a plan or estimates per-exercise kcal."""
import math

from fastapi import HTTPException
from sqlalchemy import select

from app.models.exercise import Exercise
from app.models.workout import SessionExercise, WorkoutSession
from app.services.training_lifecycle import lock_training_user

RULE_VERSION = 'strength_met_v1'
CATEGORIES = {'resistance_training', 'bodyweight_resistance'}


def valid_sets(record):
    rows = record.sets_data if isinstance(record.sets_data, list) else []
    return [row for row in rows if isinstance(row, dict)
            and isinstance(row.get('reps'), (int, float)) and not isinstance(row['reps'], bool)
            and math.isfinite(row['reps']) and row['reps'] > 0]


def standard_category(exercise):
    if exercise is None or exercise.owner_id is not None or exercise.category not in {'力量', '核心'}:
        return None
    return 'bodyweight_resistance' if exercise.equipment == ['bodyweight'] else 'resistance_training'


def classification_snapshot(exercise):
    custom = exercise.owner_id is not None
    category = exercise.energy_category if custom else standard_category(exercise)
    return dict(energy_category=category,
                energy_category_source=('user_snapshot' if category else 'unclassified') if custom else 'standard_mapping',
                energy_rule_version=RULE_VERSION)


def recorded_category(record, exercise):
    # Null in a saved snapshot is meaningful. Never consult a later library classification.
    if getattr(record, 'energy_rule_version', None) is not None:
        category = getattr(record, 'energy_category', None)
        return category if category in CATEGORIES else None
    # Compatibility for legacy standard rows only. 0029 freezes these mappings on upgrade.
    return standard_category(exercise)


def conflict(message):
    raise HTTPException(409, {'code': 'energy_classification_conflict', 'message': message})


async def update_library_category(db, user_id, exercise_id, body):
    await lock_training_user(db, user_id)
    exercise = await db.scalar(select(Exercise).where(
        Exercise.id == exercise_id, Exercise.owner_id == user_id, Exercise.is_active.is_(True),
    ).with_for_update())
    if exercise is None:
        raise HTTPException(404, '未找到可修改的本人自定义动作')
    if exercise.energy_category_version != body.expected_version:
        conflict('动作分类已变化，请重新读取后核对；输入仍可保留')
    exercise.energy_category = body.energy_category
    exercise.energy_category_version += 1
    await db.commit()
    return exercise


async def update_session_categories(db, user_id, session_id, body):
    await lock_training_user(db, user_id)
    session = await db.scalar(select(WorkoutSession).where(
        WorkoutSession.id == session_id, WorkoutSession.user_id == user_id,
    ).with_for_update())
    if session is None:
        raise HTTPException(404, '训练记录不存在')
    if session.status not in {'completed', 'ended_early'}:
        raise HTTPException(409, '仅可修改已结束训练的估算分类')
    if session.energy_classification_version != body.expected_version:
        conflict('本次训练分类已变化，请重新读取后核对；输入仍可保留')
    pairs = (await db.execute(select(SessionExercise, Exercise).join(
        Exercise, Exercise.id == SessionExercise.exercise_id,
    ).where(SessionExercise.session_id == session_id).order_by(SessionExercise.id).with_for_update())).all()
    records = {row.id: (row, exercise) for row, exercise in pairs}
    pending = []
    future = {}
    # Validate the entire batch before modifying any object, including inactive history.
    for item in body.exercises:
        pair = records.get(item.session_exercise_id)
        if pair is None or pair[1].owner_id != user_id:
            raise HTTPException(404, '未找到本次训练中可修改的自定义动作')
        row, exercise = pair
        if not valid_sets(row):
            raise HTTPException(422, '仅可补充实际记录过有效组的动作分类')
        if body.update_future:
            if not exercise.is_active:
                raise HTTPException(409, '动作已停用，可仅修改本次训练分类')
            if exercise.energy_category_version != item.expected_exercise_version:
                conflict('动作库分类已变化，本次与今后分类均未修改')
            if exercise.id in future and future[exercise.id][1] != item.energy_category:
                raise HTTPException(422, '同一动作的今后分类必须一致')
            future[exercise.id] = (exercise, item.energy_category)
        pending.append((row, item.energy_category))
    for row, category in pending:
        row.energy_category = category
        row.energy_category_source = 'user_correction'
        row.energy_rule_version = RULE_VERSION
    for exercise, category in future.values():
        exercise.energy_category = category
        exercise.energy_category_version += 1
    session.energy_classification_version += 1
    await db.commit()
    return session
