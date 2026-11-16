"""Deterministic display estimates. Not dietary targets or measured expenditure.

Mifflin: https://pubmed.ncbi.nlm.nih.gov/2305711/
MET references: https://pacompendium.com/conditioning-exercise/
The non-exercise multipliers and interruption cutoff are explicit product assumptions.
"""
from datetime import timezone
import math

from sqlalchemy import select
from app.models.profile import UserProfile, WeightLog
from app.models.exercise import Exercise
from app.models.workout import WorkoutSession, SessionExercise
from app.services.exercise_energy import recorded_category, valid_sets

ACTIVITY = {'sedentary': 1.2, 'walking': 1.4, 'physical_work': 1.6}
VERSION = 'daily_energy_v2'


def resting_energy(*, age, height, weight, gender):
    if gender not in {'male', 'female'}:
        return None
    return 10 * weight + 6.25 * height - 5 * age + (5 if gender == 'male' else -161)


def finite(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def estimate_session(session, rows, *, weight, daily_baseline):
    performed = [(record, exercise) for record, exercise in rows if valid_sets(record)]
    result = dict(session_id=session.id, started_at=session.started_at, completed_at=session.completed_at,
                  trained_at=getattr(session, 'trained_at', None), plan_name=getattr(session, 'plan_name', None),
                  effective_minutes=None, net_kcal=None, met=None, reason=None, reason_code=None,
                  unestimated_exercises=[], performed_exercises=[dict(
                      session_exercise_id=getattr(record, 'id', None),
                      exercise_name=getattr(record, 'exercise_name', None) or getattr(exercise, 'name_zh', None) or '未命名动作',
                  ) for record, exercise in performed])
    def invalid(reason, code='invalid_timing'):
        return {**result, 'reason': reason, 'reason_code': code}
    if session.status not in {'completed', 'ended_early'}:
        return invalid('训练尚未结束，不计入已完成消耗', 'not_ended')
    if session.started_at is None or session.completed_at is None:
        return invalid('缺少自动开始或结束时间')
    start = session.started_at
    end = session.completed_at
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    elapsed = (end - start).total_seconds()
    if elapsed <= 0 or elapsed > 4 * 3600:
        return invalid('自动计时时长异常，未计入本次训练')
    active = []
    rest_total = 0.0
    interruption = 0.0
    for record, exercise in rows:
        valid = valid_sets(record)
        if not valid:
            continue
        active.append((record, exercise, recorded_category(record, exercise)))
        for row in valid:
            rest = row.get('actual_rest_seconds')
            if rest is None:
                continue
            if not finite(rest) or rest < 0:
                return invalid('组间休息记录无效')
            rest_total += rest
            interruption += max(0, rest - 600)
    if not active:
        return invalid('没有有效完成组，不计入训练消耗', 'no_valid_sets')
    if rest_total > elapsed or interruption >= elapsed:
        return invalid('组间休息与自动训练时长不一致')
    result['unestimated_exercises'] = [dict(
        session_exercise_id=getattr(record, 'id', None),
        exercise_name=getattr(record, 'exercise_name', None) or getattr(exercise, 'name_zh', None) or '未命名动作',
        reason_code='classification_missing' if exercise is not None and exercise.owner_id is not None else 'unsupported_exercise',
    ) for record, exercise, category in active if category is None]
    if result['unestimated_exercises']:
        missing = any(row['reason_code'] == 'classification_missing' for row in result['unestimated_exercises'])
        return invalid('实际训练的自定义动作尚未补充估算分类' if missing else '训练类型缺少可靠耗能参数，未估算整场训练',
                       'classification_missing' if missing else 'unsupported_exercise')
    met = 3.0 if all(category == 'bodyweight_resistance' for _, _, category in active) else 3.5
    hours = (elapsed - interruption) / 3600
    net = max(0, (met * weight - daily_baseline / 24) * hours)
    return {**result, 'effective_minutes': hours * 60, 'net_kcal': net, 'met': met,
            'excluded_interruption_minutes': interruption / 60}


async def build_energy_estimate(db, user_id, summary):
    profile = await db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    level = getattr(profile, 'daily_activity_level', None)
    result = dict(version=VERSION, status='unavailable', bmr_kcal=None, total_kcal=None,
                  baseline_kcal=None, training_kcal=None, balance_kcal=None, reasons=[],
                  activity_level=level or 'sedentary', activity_defaulted=level not in ACTIVITY,
                  activity_factor=ACTIVITY.get(level, 1.2), profile_inputs=None, workouts=[])
    if profile is None:
        return {**result, 'reasons': ['请先完善个人资料']}
    weight = profile.weight_kg
    weight_source = 'profile'
    if not finite(weight) or not 25 <= weight <= 350:
        weight = await db.scalar(select(WeightLog.weight_kg).where(WeightLog.user_id == user_id, WeightLog.weight_kg.between(25, 350))
                                 .order_by(WeightLog.recorded_at.desc(), WeightLog.id).limit(1))
        weight_source = 'weight_log'
    missing = [label for label, value in [('年龄', profile.age), ('身高', profile.height_cm), ('体重', weight)]
               if not finite(value) or value <= 0]
    if profile.gender not in {'male', 'female'}:
        missing.append('公式所需的性别信息')
    if missing:
        return {**result, 'reasons': ['请补充：' + '、'.join(missing)]}
    # Keep the established medical boundary unchanged; never expose health details in logs.
    from app.services.agent_daily_meal_plans import MEDICAL_NUTRITION_MARKERS
    health = ' '.join(str(value) for value in (profile.injuries or []) + (profile.chronic_conditions or []))
    if profile.age < 18 or any(marker in health for marker in MEDICAL_NUTRITION_MARKERS):
        return {**result, 'reasons': ['当前资料不适合使用普通成人能量估算，请咨询专业人士；仍可正常记录饮食']}
    bmr = resting_energy(age=profile.age, height=profile.height_cm, weight=weight, gender=profile.gender)
    if bmr is None or not finite(bmr) or bmr <= 0:
        return {**result, 'reasons': ['个人资料无法形成有效能量估算']}
    baseline = bmr * result['activity_factor']
    sessions = list((await db.execute(select(WorkoutSession).where(
        WorkoutSession.user_id == user_id, WorkoutSession.trained_at == summary.date,
        WorkoutSession.status.in_(['completed', 'ended_early']),
    ).order_by(WorkoutSession.started_at, WorkoutSession.id))).scalars().all())
    rows = list((await db.execute(select(SessionExercise, Exercise).outerjoin(
        Exercise, SessionExercise.exercise_id == Exercise.id,
    ).where(SessionExercise.session_id.in_([item.id for item in sessions])))).all()) if sessions else []
    by_session = {}
    for record, exercise in rows:
        by_session.setdefault(record.session_id, []).append((record, exercise))
    workouts = [estimate_session(item, by_session.get(item.id, []), weight=weight, daily_baseline=baseline) for item in sessions]
    total_training = sum(item['net_kcal'] or 0 for item in workouts)
    partial = any(item['reason'] for item in workouts)
    total = baseline + total_training
    return {**result, 'status': 'partial' if partial else 'estimated',
            'bmr_kcal': bmr, 'baseline_kcal': baseline, 'training_kcal': total_training,
            'total_kcal': total, 'balance_kcal': summary.total_calories - total if summary.meals and not partial else None,
            'profile_inputs': dict(age=profile.age, height_cm=profile.height_cm, weight_kg=weight,
                                   gender=profile.gender, weight_source=weight_source),
            'workouts': workouts,
            'reasons': ['有训练未计入，暂不判断缺口或盈余'] if partial else ([] if summary.meals else ['尚未记录今日餐次，暂不判断缺口或盈余'])}
