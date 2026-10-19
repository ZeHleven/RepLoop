from __future__ import annotations

import asyncio
from functools import wraps
from app.services.agent_attempt_journal import journal_read_tool
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Literal

from langchain_core.tools import BaseTool
from langchain.tools import tool
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.business_clock import business_today, day_end
from app.services.history_status_scope import HistoryStatus, effective_history_statuses

from app.schemas.nutrition_history import NutritionHistoryArguments
from app.models.food import Food
from app.models.profile import UserProfile, WeightLog
from app.models.workout import WorkoutPlan
from app.services.food import query_nutrition_database
from app.services.nutrition_queries import (
    build_daily_nutrition_summary,
    list_nutrition_history,
    nutrition_history_scope,
)
from app.services.workout_queries import (
    build_plan_detail,
    build_session_detail,
    get_active_user_session,
    get_workout_progress_summary,
    list_user_workout_sessions,
    list_user_workout_sessions_in_range,
)


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkoutHistoryArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"limit": 5}]},
    )

    limit: int = Field(default=5, ge=1, le=20)
    start_date: str = Field(default='', description='可选日历区间开始日期YYYY-MM-DD，须与end_date同时提供；空串表示最近N次。')
    end_date: str = Field(default='', description='可选日历区间结束日期YYYY-MM-DD，包含该日，最多366天。')
    completed_only: bool = Field(default=False, description='仅用于日历区间；为true时仅统计completed，不含提前结束或进行中场次。')

    statuses: list[HistoryStatus] = Field(default=[], description='日历区间的明确状态集合；空数组沿用completed_only或默认已完成/提前结束。不得传用户身份。')

    @model_validator(mode='after')
    def validate_range(self):
        effective_history_statuses(self.statuses, completed_only=self.completed_only)
        if self.statuses and not self.start_date:
            raise ValueError('statuses requires a calendar range')
        if bool(self.start_date) != bool(self.end_date):
            raise ValueError('start_date and end_date must be supplied together')
        if self.completed_only and not self.start_date:
            raise ValueError('completed_only requires a calendar range')
        if self.start_date:
            start, end = date.fromisoformat(self.start_date), date.fromisoformat(self.end_date)
            if start.isoformat() != self.start_date or end.isoformat() != self.end_date or not 0 <= (end-start).days < 366:
                raise ValueError('history range must be ISO calendar dates spanning at most 366 days')
        return self


class WorkoutProgressArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"weeks": 8}]},
    )

    weeks: int = Field(default=8, ge=1, le=52, description='仅在未指定日期范围时使用；完整start_date/end_date优先，传入weeks也不会改变日历范围。')
    start_date: str = Field(default='', description='与end_date成对提供时，改为该范围内全部匹配场次的精确计数，不受历史列表20条上限影响。')
    end_date: str = Field(default='', description='含首尾、最多366天；实际截止不晚于本次业务日期。')
    statuses: list[HistoryStatus] = Field(default=[], description='范围计数的明确状态集合，空数组默认completed/ended_early；不得丢失用户筛选。')

    @model_validator(mode='after')
    def validate_count_scope(self):
        WorkoutHistoryArguments(start_date=self.start_date, end_date=self.end_date, statuses=self.statuses)
        return self


class WeightHistoryArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=30, ge=1, le=365)


class FoodSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(default="常见食品", min_length=1, max_length=50)
    limit: int = Field(default=10, ge=1, le=20)


READ_TOOL_IDS: tuple[str, ...] = (
    "profile.get_summary",
    "health.get_screening_summary",
    "plan.get_active",
    "workout.get_next",
    "workout.get_active_session",
    "workout.list_history",
    "workout.get_progress",
    "workout.get_daily_context",
    "weight.list_history",
    "nutrition.get_today",
    "nutrition.list_history",
    "nutrition.get_recent_context",
    "food.search",
    "food.list_candidates",
)


@dataclass(frozen=True)
class ConditionalReadEvidenceGroup:
    """Server-owned direction and trigger for one conditional read fallback."""

    primary_tool_id: str
    fallback_tool_id: str
    fallback_trigger: Literal["on_error", "on_not_found"]


# These are evidence alternatives, not extra model permissions. The Controller
# may invoke a fallback only when both tools were routed into the run allowlist
# and the primary observation satisfies the fixed server-side trigger.
CONDITIONAL_READ_EVIDENCE_GROUPS = (
    ConditionalReadEvidenceGroup(
        primary_tool_id="workout.get_progress",
        fallback_tool_id="workout.list_history",
        fallback_trigger="on_error",
    ),
    ConditionalReadEvidenceGroup(
        primary_tool_id="workout.get_active_session",
        fallback_tool_id="workout.get_next",
        fallback_trigger="on_not_found",
    ),
)

# Only side-effect-free tools may appear in Planner-owned concurrent batches.
# Pairs below are observation-dependent alternatives and must never be
# speculatively placed in the same Planner-owned batch. The Controller or a
# bounded ReAct step must observe the primary before invoking the fallback.
PARALLEL_READ_SAFE_TOOL_IDS = frozenset(READ_TOOL_IDS)
PARALLEL_READ_CONDITIONAL_TOOL_PAIRS = tuple(
    frozenset((item.primary_tool_id, item.fallback_tool_id))
    for item in CONDITIONAL_READ_EVIDENCE_GROUPS
)

LANGCHAIN_TOOL_NAMES: dict[str, str] = {
    "profile.get_summary": "profile_get_summary",
    "health.get_screening_summary": "health_get_screening_summary",
    "plan.get_active": "plan_get_active",
    "workout.get_next": "workout_get_next",
    "workout.get_active_session": "workout_get_active_session",
    "workout.list_history": "workout_list_history",
    "workout.get_progress": "workout_get_progress",
    "workout.get_daily_context": "workout_get_daily_context",
    "weight.list_history": "weight_list_history",
    "nutrition.get_today": "nutrition_get_today",
    "nutrition.list_history": "nutrition_list_history",
    "nutrition.get_recent_context": "nutrition_get_recent_context",
    "food.search": "food_search",
    "food.list_candidates": "food_list_candidates",
}
TOOL_ID_BY_LANGCHAIN_NAME = {
    tool_name: tool_id for tool_id, tool_name in LANGCHAIN_TOOL_NAMES.items()
}


def _profile_summary(profile: UserProfile) -> dict[str, Any]:
    return {
        "found": True,
        "age": profile.age,
        "gender": profile.gender,
        "height_cm": profile.height_cm,
        "weight_kg": profile.weight_kg,
        "bmi": profile.bmi,
        "bmi_category": profile.bmi_category,
        "experience_level": profile.experience_level,
        "primary_goal": profile.primary_goal,
        # UserProfile stores a directional goal, not calorie/macro targets.
        "nutrition_targets": None,
        "training_days_per_week": profile.training_days_per_week,
        "session_duration_min": profile.session_duration_min,
        "training_location": profile.training_location,
        "diet_restriction": profile.diet_restriction,
        "onboarding_completed": profile.onboarding_completed,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }


def _health_summary(profile: UserProfile) -> dict[str, Any]:
    return {
        "found": True,
        "injuries": profile.injuries or [],
        "chronic_conditions": profile.chronic_conditions or [],
        "screening_completed": profile.onboarding_completed,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
        "medical_boundary": "该信息仅用于训练安全筛查，不构成医疗诊断。",
    }


async def _active_plan(db: AsyncSession, user_id: str) -> WorkoutPlan | None:
    return await db.scalar(
        select(WorkoutPlan)
        .where(
            WorkoutPlan.user_id == user_id,
            WorkoutPlan.is_active.is_(True),
        )
        .order_by(WorkoutPlan.created_at.desc())
        .limit(1)
    )


def _serialize_session_read(coroutine: Callable[..., Any], db: AsyncSession):
    # Metadata-only callers and mocked services may not supply a session.
    fallback_info: dict[str, Any] = {}

    @wraps(coroutine)
    async def invoke(*args, **kwargs):
        # A single AsyncSession cannot issue concurrent SQL, including while
        # provisioning its connection. Keep the caller's transaction and scope.
        session_info = getattr(db, "info", fallback_info)
        lock = session_info.setdefault("_agent_read_tool_lock", asyncio.Lock())
        async with lock:
            return await coroutine(*args, **kwargs)
    return invoke


def build_read_tools(
    db: AsyncSession,
    *,
    user_id: str,
    allowlist: list[str],
) -> list[BaseTool]:
    """Build only the routed tools; user identity is closed over, never model input."""
    unknown = set(allowlist) - set(READ_TOOL_IDS)
    if unknown:
        raise ValueError(f"Unknown or non-read Agent tools: {sorted(unknown)}")

    @tool(
        LANGCHAIN_TOOL_NAMES["profile.get_summary"],
        args_schema=NoArguments,
        description=(
            "读取当前登录用户的基础资料和训练偏好摘要，也用于个人饮食评估的目标和饮食限制。用于回答年龄、身高体重、"
            "训练目标、经验、频率、时长、地点或饮食偏好；不要用它查询伤病、慢性病、"
            "训练计划或训练记录。示例：‘我的训练目标是什么？’"
        ),
    )
    async def profile_get_summary() -> dict[str, Any]:
        profile = await db.scalar(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        return _profile_summary(profile) if profile else {"found": False}

    @tool(
        LANGCHAIN_TOOL_NAMES["health.get_screening_summary"],
        args_schema=NoArguments,
        description=(
            "读取当前登录用户已保存的健康筛查摘要，只包含伤病、慢性疾病和筛查状态。"
            "仅在用户询问训练限制、伤病或健康风险时使用；不要查询一般资料、计划或进度。"
            "示例：‘我的膝盖情况会限制哪些训练？’"
        ),
    )
    async def health_get_screening_summary() -> dict[str, Any]:
        profile = await db.scalar(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        return _health_summary(profile) if profile else {"found": False}

    @tool(
        LANGCHAIN_TOOL_NAMES["plan.get_active"],
        args_schema=NoArguments,
        description=(
            "读取当前登录用户完整的活动训练计划及各训练日动作。仅用于查看整个当前计划；"
            "如果只问下一次练什么，应使用 workout_get_next。不要查询已经完成的训练记录。"
            "示例：‘把我现在的一周训练计划列出来。’"
        ),
    )
    async def plan_get_active() -> dict[str, Any]:
        plan = await _active_plan(db, user_id)
        if plan is None:
            return {"found": False}
        as_of = business_today()
        detail = await build_plan_detail(db, plan, as_of=as_of)
        return {"found": True, "plan": detail.model_dump(mode="json"), "scope": {
            "as_of": as_of.isoformat(), "timezone": "Asia/Shanghai",
            "week_start": detail.week_start.isoformat(),
            "completed_days_basis": "current_plan_continuity_and_scheduled_weekday",
            "history_session_count_is_distinct": True,
            "note": "weekly_completed_days仅计本周截至查询日、与当前计划安排连续关联的已完成训练日；不是全部历史训练场次。旧记录可能缺少计划周/训练日关联，0不表示没有训练记录。",
        }}

    @tool(
        LANGCHAIN_TOOL_NAMES["workout.get_next"],
        args_schema=NoArguments,
        description=(
            "根据当前活动计划和今天的星期，读取下一次计划训练日及动作。仅用于‘下一练’或"
            "‘今天练什么’；不要用它展示整份计划、历史或正在执行的训练。"
            "示例：‘我下一练要做哪些动作？’"
        ),
    )
    async def workout_get_next() -> dict[str, Any]:
        plan = await _active_plan(db, user_id)
        if plan is None:
            return {"found": False, "reason": "no_active_plan"}
        detail = await build_plan_detail(db, plan, as_of=business_today())
        scheduled_days = sorted({item.day_of_week for item in detail.exercises})
        if not scheduled_days:
            return {"found": False, "reason": "active_plan_has_no_exercises"}
        today_weekday = business_today().isoweekday()
        candidates = [day for day in scheduled_days if day >= today_weekday]
        next_day = candidates[0] if candidates else scheduled_days[0]
        days_until = (next_day - today_weekday) % 7
        return {
            "found": True,
            "plan_id": detail.id,
            "plan_name": detail.name,
            "day_of_week": next_day,
            "days_until": days_until,
            "exercises": [
                item.model_dump(mode="json")
                for item in detail.exercises
                if item.day_of_week == next_day
            ],
        }

    @tool(
        LANGCHAIN_TOOL_NAMES["workout.get_active_session"],
        args_schema=NoArguments,
        description=(
            "读取当前登录用户正在进行的训练、目标和已记录训练组。仅用于继续训练或查看"
            "当前练到哪里；不要查询历史训练或计划安排。示例：‘我刚才记录到第几组？’"
        ),
    )
    async def workout_get_active_session() -> dict[str, Any]:
        session = await get_active_user_session(db, user_id=user_id)
        if session is None:
            return {"found": False}
        detail = await build_session_detail(db, session)
        return {"found": True, "session": detail.model_dump(mode="json")}

    @tool(
        LANGCHAIN_TOOL_NAMES["workout.list_history"],
        args_schema=WorkoutHistoryArguments,
        description=(
            "按时间倒序读取当前登录用户近期训练场次详情，limit 为 1 到 20。仅用于具体历史"
            "记录；按日历区间查记录须成对指定start_date/end_date，先筛日期再限制条数，返回总数与截断标记。"
            "日期区间可以用statuses选择completed/in_progress/ended_early/skipped/abandoned的组合；须保留用户筛选条件。"
            "趋势汇总应使用 workout_get_progress。示例：‘列出最近 5 次训练。’"
        ),
    )
    async def workout_list_history(limit: int = 5, start_date: str = '', end_date: str = '', completed_only: bool = False, statuses: list[HistoryStatus] | None = None) -> dict[str, Any]:
        bounds = WorkoutHistoryArguments(limit=limit, start_date=start_date, end_date=end_date, completed_only=completed_only, statuses=statuses or [])
        metadata = {}
        if bounds.start_date:
            from app.services.workout_reporting import report_today
            today = report_today()
            sessions, total = await list_user_workout_sessions_in_range(db, user_id=user_id,
                start=date.fromisoformat(start_date), end=min(date.fromisoformat(end_date), today), limit=limit,
                completed_only=completed_only, statuses=bounds.statuses)
            metadata = dict(range_start=start_date, range_end=end_date, as_of=today.isoformat(),
                timezone='Asia/Shanghai', total_count=total, truncated=total > len(sessions))
            metadata['status_filter'] = list(effective_history_statuses(bounds.statuses, completed_only=completed_only))
        else:
            sessions = await list_user_workout_sessions(db, user_id=user_id, limit=limit)
        details = [await build_session_detail(db, item) for item in sessions]
        return {
            **metadata,
            "count": len(details),
            "sessions": [item.model_dump(mode="json") for item in details],
        }

    @tool(
        LANGCHAIN_TOOL_NAMES["workout.get_progress"],
        args_schema=WorkoutProgressArguments,
        description=(
            "汇总当前登录用户最近若干周的训练次数、组数、次数和负重容量，weeks 为 1 到 52。"
            "成对提供start_date/end_date及statuses时只返回精确范围计数，涵盖全部匹配记录，不返回趋势。"
            "显式日期范围优先于weeks；同时传入weeks也不改变该范围。"
            "具体某次训练应使用 workout_list_history。"
            "示例：‘总结我最近 8 周的训练进度。’"
        ),
    )
    async def workout_get_progress(weeks: int = 8, start_date: str = '', end_date: str = '', statuses: list[HistoryStatus] | None = None) -> dict[str, Any]:
        scope = WorkoutProgressArguments(weeks=weeks, start_date=start_date, end_date=end_date, statuses=statuses or [])
        if scope.start_date:
            from app.services.workout_queries import count_user_workout_sessions_in_range
            today = business_today()
            selected = effective_history_statuses(scope.statuses)
            actual_end = min(date.fromisoformat(end_date), today)
            count = await count_user_workout_sessions_in_range(db, user_id=user_id,
                start=date.fromisoformat(start_date), end=actual_end, statuses=selected)
            return {'aggregation': 'all_matching_sessions', 'total_sessions': count,
                    'count_scope': {'start_date': start_date, 'end_date': actual_end.isoformat(),
                                    'requested_end_date': end_date, 'as_of': today.isoformat(),
                                    'timezone': 'Asia/Shanghai', 'statuses': list(selected)},
                    'evidence_limits': {'actual_training_is_not_proven': True,
                                        'plan_completed_days_is_distinct': True}}
        progress = await get_workout_progress_summary(
            db,
            user_id=user_id,
            weeks=weeks,
        )
        return progress.model_dump(mode="json")

    @tool(
        LANGCHAIN_TOOL_NAMES["workout.get_daily_context"],
        args_schema=NoArguments,
        description=(
            "聚合读取今天是否为训练日、当天计划动作及近 4 周执行情况。"
            "用于需要结合当日训练量的跨领域评估，不会修改计划。"
        ),
    )
    async def workout_get_daily_context() -> dict[str, Any]:
        plan = await _active_plan(db, user_id)
        progress = await get_workout_progress_summary(
            db,
            user_id=user_id,
            weeks=4,
        )
        exercises: list[dict[str, Any]] = []
        plan_data: dict[str, Any] | None = None
        if plan is not None:
            detail = await build_plan_detail(db, plan, as_of=business_today())
            exercises = [
                item.model_dump(mode="json")
                for item in detail.exercises
                if item.day_of_week == business_today().isoweekday()
            ]
            plan_data = {
                "id": detail.id,
                "name": detail.name,
                "days_per_week": detail.days_per_week,
            }
        return {
            "date": business_today().isoformat(),
            "is_training_day": bool(exercises),
            "plan": plan_data,
            "exercises": exercises,
            "progress_4_weeks": progress.model_dump(mode="json"),
        }

    @tool(
        LANGCHAIN_TOOL_NAMES["weight.list_history"],
        args_schema=WeightHistoryArguments,
        description=(
            "读取当前登录用户的体重历史，limit 为 1 到 365。仅用于体重记录和趋势；"
            "不要用个人档案中的当前体重替代历史。"
        ),
    )
    async def weight_list_history(limit: int = 30) -> dict[str, Any]:
        rows = list((await db.execute(
            select(WeightLog)
            .where(WeightLog.user_id == user_id, WeightLog.recorded_at < day_end(business_today()))
            .order_by(WeightLog.recorded_at.desc())
            .limit(limit)
        )).scalars().all())
        return {
            "count": len(rows),
            "scope": {"as_of": business_today().isoformat(), "timezone": "Asia/Shanghai",
                      "selection": "latest_records", "requested_limit": limit},
            "records": [{
                "id": item.id,
                "weight_kg": item.weight_kg,
                "recorded_at": item.recorded_at.isoformat(),
            } for item in rows],
        }

    @tool(
        LANGCHAIN_TOOL_NAMES["nutrition.get_today"],
        args_schema=NoArguments,
        description="读取当前登录用户今天的营养汇总和餐次明细。",
    )
    async def nutrition_get_today() -> dict[str, Any]:
        from app.services.nutrition_assessment_evidence import intake_evidence_limits
        summary = await build_daily_nutrition_summary(
            db, user_id=user_id, target_date=business_today()
        )
        return {
            **summary.model_dump(mode="json"),
            "count": len(summary.meals),
            "evidence_limits": intake_evidence_limits(),
        }

    @tool(
        LANGCHAIN_TOOL_NAMES["nutrition.list_history"],
        args_schema=NutritionHistoryArguments,
        description="按最近有记录日期数、完整日历区间或离散日期读取饮食汇总，可筛选餐次；days仅为记录日期上限，不是自然日窗口，未来记录不返回。",
    )
    async def nutrition_list_history(days: int = 30, start_date: str = "", end_date: str = "",
                                     logged_dates: list[str] | None = None, meal_types: list[str] | None = None) -> dict[str, Any]:
        from app.services.nutrition_assessment_evidence import intake_evidence_limits
        summaries = await list_nutrition_history(
            db, user_id=user_id, days=days, as_of=business_today(),
            start_date=start_date, end_date=end_date, logged_dates=logged_dates, meal_types=meal_types
        )
        return {
            "count": len(summaries),
            "evidence_limits": intake_evidence_limits(),
            "scope": nutrition_history_scope(summaries, as_of=business_today(), limit=days,
                                             start_date=start_date, end_date=end_date, logged_dates=logged_dates, meal_types=meal_types),
            "days": [item.model_dump(mode="json") for item in summaries],
        }

    @tool(
        LANGCHAIN_TOOL_NAMES["nutrition.get_recent_context"],
        args_schema=NoArguments,
        description=(
            "聚合读取今天餐次与近 14 个有记录日期的营养趋势，供跨领域评估使用。"
            "不会创建、修改或删除饮食记录。"
        ),
    )
    async def nutrition_get_recent_context() -> dict[str, Any]:
        from app.services.nutrition_assessment_evidence import intake_evidence_limits
        today = await build_daily_nutrition_summary(
            db, user_id=user_id, target_date=business_today()
        )
        history = await list_nutrition_history(
            db, user_id=user_id, days=14, as_of=business_today()
        )
        return {
            "today": today.model_dump(mode="json"),
            "scope": nutrition_history_scope(history, as_of=business_today(), limit=14),
            "recent_days": [item.model_dump(mode="json") for item in history],
            "evidence_limits": intake_evidence_limits(),
        }

    @tool(
        LANGCHAIN_TOOL_NAMES["food.search"],
        args_schema=FoodSearchArguments,
        description=(
            "按中英文名称搜索服务端食品库，返回每 100 克营养值。"
            "仅用于寻找可记录的食品，不代表已经新增饮食记录。"
        ),
    )
    async def food_search(query: str = "常见食品", limit: int = 10) -> dict[str, Any]:
        foods = await query_nutrition_database(
            db, query=query, limit=limit
        )
        return {
            "count": len(foods),
            "foods": [{
                "id": item.id,
                "name_zh": item.name_zh,
                "name_en": item.name_en,
                "category": item.category,
                "calories_per_100g": item.calories_per_100g,
                "protein_g": item.protein_g,
                "carbs_g": item.carbs_g,
                "fat_g": item.fat_g,
                "common_portion_g": item.common_portion_g,
            } for item in foods],
        }

    @tool(
        LANGCHAIN_TOOL_NAMES["food.list_candidates"],
        args_schema=NoArguments,
        description=(
            "读取可用于结构化饮食方案的标准食品候选、稳定 ID、营养和饮食标签。"
            "模型只能选择候选，不能据此直接写入饮食记录。"
        ),
    )
    async def food_list_candidates() -> dict[str, Any]:
        foods = list((await db.execute(
            select(Food)
            .where(Food.is_active.is_(True))
            .order_by(Food.is_common_in_china.desc(), Food.name_zh, Food.id)
            .limit(80)
        )).scalars().all())
        return {
            "count": len(foods),
            "foods": [{
                "id": item.id,
                "name_zh": item.name_zh,
                "category": item.category,
                "calories_per_100g": item.calories_per_100g,
                "protein_g": item.protein_g,
                "carbs_g": item.carbs_g,
                "fat_g": item.fat_g,
                "diet_tags": item.diet_tags or [],
            } for item in foods],
        }

    factories: dict[str, Callable[[], BaseTool]] = {
        "profile.get_summary": lambda: profile_get_summary,
        "health.get_screening_summary": lambda: health_get_screening_summary,
        "plan.get_active": lambda: plan_get_active,
        "workout.get_next": lambda: workout_get_next,
        "workout.get_active_session": lambda: workout_get_active_session,
        "workout.list_history": lambda: workout_list_history,
        "workout.get_progress": lambda: workout_get_progress,
        "workout.get_daily_context": lambda: workout_get_daily_context,
        "weight.list_history": lambda: weight_list_history,
        "nutrition.get_today": lambda: nutrition_get_today,
        "nutrition.list_history": lambda: nutrition_list_history,
        "nutrition.get_recent_context": lambda: nutrition_get_recent_context,
        "food.search": lambda: food_search,
        "food.list_candidates": lambda: food_list_candidates,
    }
    tools = [factories[tool_id]() for tool_id in allowlist]
    # Shared across tool sets built from the same session; separate sessions
    # (including Planner-owned parallel reads) remain independent.
    for read_tool in {id(item): item for item in tools}.values():
        read_tool.coroutine = journal_read_tool(read_tool.name, _serialize_session_read(read_tool.coroutine, db))
    return tools
