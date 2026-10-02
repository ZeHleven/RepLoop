from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.services.business_clock import business_today, day_end
from app.services.structured_schema import strict_tool_schema
from app.models.agent import AgentArtifact, AgentProposal
from app.models.food import Food
from app.models.profile import UserProfile, WeightLog
from app.models.workout import WorkoutPlan
from app.services.nutrition_queries import (
    build_daily_nutrition_summary,
    list_nutrition_history,
    nutrition_history_scope,
)
from app.services.ai_client import (
    StructuredAIServiceError,
    StructuredCompletionResult,
    structured_chat_completion,
)
from app.services.daily_meal_optimizer import (
    SOLVER_VERSION,
    DailyMealOptimizationError,
    OptimizedMealDraft,
    nutrition_fit,
    optimize_daily_meal_amounts,
    target_gaps,
)
from app.services.workout_queries import (
    build_plan_detail,
    get_workout_progress_summary,
)


MEAL_TYPES = ("早餐", "午餐", "晚餐", "加餐")
DAILY_MEAL_EVIDENCE = (
    "profile_summary",
    "health_screening",
    "weight_history",
    "workout_daily_context",
    "nutrition_recent_context",
    "food_catalog",
)
MEDICAL_NUTRITION_MARKERS = (
    "糖尿病",
    "肾病",
    "肾功能",
    "心血管",
    "冠心病",
    "心脏病",
    "高血压",
    "进食障碍",
    "厌食",
    "暴食",
    "孕",
    "哺乳",
)
logger = logging.getLogger("uvicorn.error")


class DailyMealPlanError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        missing_slots: list[str] | None = None,
        evidence_audits: tuple[Any, ...] = (),
        generation_attempts: tuple[Any, ...] = (),
        optimization_attempts: tuple[Any, ...] = (),
    ):
        self.code = code
        self.message = message
        self.missing_slots = missing_slots or []
        self.evidence_audits = evidence_audits
        self.generation_attempts = generation_attempts
        self.optimization_attempts = optimization_attempts
        super().__init__(message)


class DailyMealDraftItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    food_id: str = Field(min_length=1, max_length=100)
    amount_g: float = Field(gt=0, le=500)


class DailyMealDraftMeal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meal_type: Literal["早餐", "午餐", "晚餐", "加餐"]
    items: list[DailyMealDraftItem] = Field(min_length=1, max_length=8)


class DailyMealDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meals: list[DailyMealDraftMeal] = Field(min_length=1, max_length=4)
    rationale: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("rationale", mode="before")
    @classmethod
    def preserve_bounded_explanations(cls, value: Any) -> Any:
        # Formatting repair only. Do not delete safety conditions or edit meal
        # choices/amounts. Invalid types and unbounded text remain errors.
        if not isinstance(value, list):
            return value
        if len(value) > 20:
            raise ValueError("rationale exceeds the 20-item repair budget")
        if all(isinstance(item, str) for item in value):
            if sum(len(item) for item in value) > 8000:
                raise ValueError("rationale exceeds the 8000-character budget")
            if len(value) > 5:
                return [*value[:4], "\n".join(value[4:])]
        return value


DAILY_MEAL_DRAFT_SCHEMA = strict_tool_schema(DailyMealDraft)


class EphemeralNutritionInputs(BaseModel):
    """Explicit one-run facts; these are never written back to the profile."""

    model_config = ConfigDict(extra="forbid")

    age: int | None = Field(default=None, ge=18, le=100)
    height_cm: float | None = Field(default=None, ge=100, le=250)
    weight_kg: float | None = Field(default=None, ge=25, le=350)
    primary_goal: str | None = Field(default=None, min_length=1, max_length=50)
    diet_restriction_known: bool | None = None
    diet_restriction: str | None = Field(default=None, max_length=100)


@dataclass(frozen=True)
class EvidenceAudit:
    tool_id: str
    fields: tuple[str, ...]
    duration_ms: int
    result_fingerprint: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "fields": list(self.fields),
            "duration_ms": self.duration_ms,
            "result_fingerprint": self.result_fingerprint,
        }


@dataclass(frozen=True)
class GenerationAttemptAudit:
    attempt: int
    transport: str
    status: Literal["completed", "failed"]
    duration_ms: int
    output_chars: int = 0
    finish_reason: str | None = None
    error_code: str | None = None
    validation_paths: tuple[str, ...] = ()
    fallback_reason: str | None = None
    normalizations: tuple[str, ...] = ()

    def result_data(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "transport": self.transport,
            "finish_reason": self.finish_reason,
            "output_chars": self.output_chars,
            "validation_paths": list(self.validation_paths),
            "fallback_reason": self.fallback_reason,
            "normalizations": list(self.normalizations),
        }


@dataclass(frozen=True)
class OptimizationAttemptAudit:
    attempt: int
    mode: Literal["ideal", "acceptable"]
    status: Literal["completed", "infeasible", "failed"]
    duration_ms: int
    error_code: str | None = None
    violated_metrics: tuple[str, ...] = ()
    target_deviations: tuple[dict[str, Any], ...] = ()
    objective_value: float | None = None
    nutrition_score: float | None = None
    portion_score: float | None = None
    solver_status: Literal["optimal", "feasible_incumbent"] | None = None

    def result_data(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "mode": self.mode,
            "solver_version": SOLVER_VERSION,
            "violated_metrics": list(self.violated_metrics),
            "target_deviations": list(self.target_deviations),
            "objective_value": self.objective_value,
            "nutrition_score": self.nutrition_score,
            "portion_score": self.portion_score,
            "solver_status": self.solver_status,
        }


@dataclass(frozen=True)
class DailyMealEvidence:
    values: dict[str, Any]
    fingerprints: dict[str, str]
    audits: tuple[EvidenceAudit, ...]


@dataclass(frozen=True)
class DailyMealArtifactResult:
    artifact: AgentArtifact
    card: dict[str, Any]
    reply: str
    audits: tuple[EvidenceAudit, ...]
    generation_attempts: tuple[GenerationAttemptAudit, ...]
    optimization_attempts: tuple[OptimizationAttemptAudit, ...]


@dataclass(frozen=True)
class _OptimizedCandidate:
    draft: DailyMealDraft
    meals: list[dict[str, Any]]
    totals: dict[str, float]
    fit: dict[str, Any]
    optimization: OptimizedMealDraft


def canonical_fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _profile_value(profile: UserProfile | None) -> dict[str, Any]:
    if profile is None:
        return {"found": False}
    return {
        "found": True,
        "age": profile.age,
        "gender": profile.gender,
        "height_cm": profile.height_cm,
        "weight_kg": profile.weight_kg,
        "primary_goal": profile.primary_goal,
        "training_days_per_week": profile.training_days_per_week,
        "diet_restriction": profile.diet_restriction,
        "diet_restriction_status_known": bool(profile.onboarding_completed),
        "updated_at": profile.updated_at,
    }


def _health_value(profile: UserProfile | None) -> dict[str, Any]:
    if profile is None:
        return {"found": False}
    return {
        "found": True,
        "injuries": profile.injuries or [],
        "chronic_conditions": profile.chronic_conditions or [],
        "screening_completed": bool(profile.onboarding_completed),
        "updated_at": profile.updated_at,
    }


async def collect_daily_meal_evidence(
    db: AsyncSession,
    *,
    user_id: str,
    target_date: date | None = None,
    use_isolated_sessions: bool = True,
) -> DailyMealEvidence:
    """Collect the six server-approved evidence groups for one owned task.

    The values are used only inside the bounded generator.  Audits deliberately
    contain field names and fingerprints rather than sensitive source values.
    """
    target = target_date or business_today()
    async def profile_for(read_db: AsyncSession) -> UserProfile | None:
        return await read_db.scalar(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )

    async def load_profile(read_db: AsyncSession) -> dict[str, Any]:
        return _profile_value(await profile_for(read_db))

    async def load_health(read_db: AsyncSession) -> dict[str, Any]:
        return _health_value(await profile_for(read_db))

    async def load_weight(read_db: AsyncSession) -> dict[str, Any]:
        rows = list((await read_db.execute(
            select(WeightLog)
            .where(WeightLog.user_id == user_id, WeightLog.recorded_at < day_end(target))
            .order_by(WeightLog.recorded_at.desc())
            .limit(30)
        )).scalars().all())
        return {
            "count": len(rows),
            "records": [{
                "id": item.id,
                "weight_kg": float(item.weight_kg),
                "recorded_at": item.recorded_at,
            } for item in rows],
        }

    async def load_workout(read_db: AsyncSession) -> dict[str, Any]:
        profile = await profile_for(read_db)
        active = await read_db.scalar(
            select(WorkoutPlan)
            .where(
                WorkoutPlan.user_id == user_id,
                WorkoutPlan.is_active.is_(True),
            )
            .order_by(WorkoutPlan.created_at.desc())
            .limit(1)
        )
        progress = await get_workout_progress_summary(
            read_db,
            user_id=user_id,
            weeks=4,
            today=target,
        )
        plan = None
        exercises: list[dict[str, Any]] = []
        if active is not None:
            detail = await build_plan_detail(read_db, active, profile=profile)
            plan = {
                "id": active.id,
                "name": active.name,
                "days_per_week": active.days_per_week,
                "goal": active.goal,
                "fingerprint": canonical_fingerprint(
                    detail.model_dump(mode="json")
                ),
            }
            exercises = [
                {
                    "exercise_id": item.exercise_id,
                    "name": item.exercise_name,
                    "sets": item.sets,
                    "reps": item.reps,
                }
                for item in detail.exercises
                if item.day_of_week == target.isoweekday()
            ]
        return {
            "target_date": target,
            "is_training_day": bool(exercises),
            "plan": plan,
            "today_exercises": exercises,
            "progress_4_weeks": progress.model_dump(mode="json"),
        }

    async def load_nutrition(read_db: AsyncSession) -> dict[str, Any]:
        today_summary = await build_daily_nutrition_summary(
            read_db,
            user_id=user_id,
            target_date=target,
        )
        history = await list_nutrition_history(
            read_db, user_id=user_id, days=14, as_of=target
        )
        return {
            "today": today_summary.model_dump(mode="json"),
            "scope": nutrition_history_scope(history, as_of=target, limit=14),
            "recent_days": [item.model_dump(mode="json") for item in history],
        }

    async def load_foods(read_db: AsyncSession) -> dict[str, Any]:
        foods = list((await read_db.execute(
            select(Food)
            .where(Food.is_active.is_(True))
            .order_by(Food.is_common_in_china.desc(), Food.name_zh, Food.id)
            .limit(80)
        )).scalars().all())
        return {
            "count": len(foods),
            "foods": [{
                "id": item.id,
                "name": item.name_zh,
                "category": item.category,
                "calories_per_100g": float(item.calories_per_100g),
                "protein_g": float(item.protein_g),
                "carbs_g": float(item.carbs_g),
                "fat_g": float(item.fat_g),
                "common_portion_g": (
                    float(item.common_portion_g)
                    if item.common_portion_g is not None
                    else None
                ),
                "diet_tags": item.diet_tags or [],
            } for item in foods],
        }

    loaders: tuple[tuple[str, tuple[str, ...], Any], ...] = (
        (
            "profile_summary",
            (
                "age", "gender", "height_cm", "primary_goal",
                "training_days_per_week", "diet_restriction",
            ),
            load_profile,
        ),
        (
            "health_screening",
            ("injuries", "chronic_conditions", "screening_completed"),
            load_health,
        ),
        ("weight_history", ("weight_kg", "recorded_at"), load_weight),
        (
            "workout_daily_context",
            ("is_training_day", "plan", "today_exercises", "progress_4_weeks"),
            load_workout,
        ),
        (
            "nutrition_recent_context",
            ("today", "recent_days"),
            load_nutrition,
        ),
        (
            "food_catalog",
            (
                "id", "name", "category", "nutrition_per_100g",
                "common_portion_g", "diet_tags",
            ),
            load_foods,
        ),
    )
    read_session_factory = async_sessionmaker(
        bind=db.bind,
        expire_on_commit=False,
    )

    async def run_isolated(
        tool_id: str,
        fields: tuple[str, ...],
        loader: Any,
    ) -> tuple[str, Any, EvidenceAudit]:
        started = time.perf_counter()
        async with read_session_factory() as read_db:
            await read_db.execute(text("SET TRANSACTION READ ONLY"))
            value = await loader(read_db)
        fingerprint = canonical_fingerprint(value)
        return tool_id, value, EvidenceAudit(
            tool_id=tool_id,
            fields=fields,
            duration_ms=max(0, round((time.perf_counter() - started) * 1000)),
            result_fingerprint=fingerprint,
        )

    async def run_shared(
        tool_id: str,
        fields: tuple[str, ...],
        loader: Any,
    ) -> tuple[str, Any, EvidenceAudit]:
        started = time.perf_counter()
        value = await loader(db)
        fingerprint = canonical_fingerprint(value)
        return tool_id, value, EvidenceAudit(
            tool_id=tool_id,
            fields=fields,
            duration_ms=max(0, round((time.perf_counter() - started) * 1000)),
            result_fingerprint=fingerprint,
        )

    results = (
        await asyncio.gather(*(
            run_isolated(tool_id, fields, loader)
            for tool_id, fields, loader in loaders
        ))
        if use_isolated_sessions
        else [
            await run_shared(tool_id, fields, loader)
            for tool_id, fields, loader in loaders
        ]
    )
    values = {tool_id: value for tool_id, value, _ in results}
    audits = tuple(audit for _, _, audit in results)
    return DailyMealEvidence(
        values=values,
        fingerprints={item.tool_id: item.result_fingerprint for item in audits},
        audits=audits,
    )


def _latest_weight(evidence: DailyMealEvidence) -> float | None:
    records = evidence.values["weight_history"].get("records", [])
    if records:
        return float(records[0]["weight_kg"])
    value = evidence.values["profile_summary"].get("weight_kg")
    return float(value) if value is not None else None


def _missing_critical_fields(evidence: DailyMealEvidence) -> list[str]:
    profile = evidence.values["profile_summary"]
    missing: list[str] = []
    for key, label in (
        ("age", "年龄"),
        ("height_cm", "身高"),
        ("primary_goal", "训练目标"),
    ):
        if profile.get(key) is None:
            missing.append(label)
    if _latest_weight(evidence) is None:
        missing.append("当前体重")
    if not profile.get("diet_restriction_status_known"):
        missing.append("饮食限制状态（包括是否有食物过敏或忌口）")
    return missing


async def _extract_ephemeral_inputs(
    *,
    user_message: str,
    missing_slots: list[str],
) -> EphemeralNutritionInputs:
    structured = _model().with_structured_output(
        EphemeralNutritionInputs,
        method="json_mode",
        include_raw=True,
    )
    result = await structured.ainvoke([
        {
            "role": "system",
            "content": (
                "只提取用户在本轮文字中明确提供的营养估算资料。不得猜测、推导或补全；"
                "未明确提供的字段必须为 null。若用户明确说没有过敏、忌口或饮食限制，"
                "diet_restriction_known=true 且 diet_restriction=null。若明确给出限制，"
                "known=true 并原样概括。只输出符合 schema 的 JSON。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps({
                "missing_fields": missing_slots,
                "user_message": user_message[:2000],
            }, ensure_ascii=False),
        },
    ])
    parsed = result.get("parsed") if isinstance(result, dict) else None
    return parsed if isinstance(parsed, EphemeralNutritionInputs) else EphemeralNutritionInputs()


def apply_ephemeral_inputs(
    evidence: DailyMealEvidence,
    inputs: EphemeralNutritionInputs,
    *,
    reject_current_conflicts: bool = False,
) -> DailyMealEvidence:
    values = dict(evidence.values)
    profile = dict(values["profile_summary"])
    weight = dict(values["weight_history"])
    records = list(weight.get("records", []))

    def set_profile(field: str, value: Any) -> None:
        if value is None:
            return
        current = profile.get(field)
        if reject_current_conflicts and current is not None and current != value:
            raise DailyMealPlanError(
                "artifact_context_changed",
                f"{field} 已与生成方案时提供的临时值不同",
            )
        if current is None:
            profile[field] = value

    set_profile("age", inputs.age)
    set_profile("height_cm", inputs.height_cm)
    set_profile("primary_goal", inputs.primary_goal)
    if inputs.diet_restriction_known:
        current_known = bool(profile.get("diet_restriction_status_known"))
        current_restriction = profile.get("diet_restriction")
        if reject_current_conflicts and current_known and current_restriction != inputs.diet_restriction:
            raise DailyMealPlanError(
                "artifact_context_changed",
                "饮食限制状态已与生成方案时不同",
            )
        if not current_known:
            profile["diet_restriction_status_known"] = True
            profile["diet_restriction"] = inputs.diet_restriction
    if inputs.weight_kg is not None:
        if reject_current_conflicts and records:
            raise DailyMealPlanError(
                "artifact_context_changed",
                "生成方案后新增了体重记录",
            )
        if not records and profile.get("weight_kg") is None:
            records = [{
                "id": "ephemeral",
                "weight_kg": float(inputs.weight_kg),
                "recorded_at": "current_generation",
            }]
            weight["records"] = records
            weight["count"] = 1
        elif (
            reject_current_conflicts
            and profile.get("weight_kg") is not None
            and float(profile["weight_kg"]) != float(inputs.weight_kg)
        ):
            raise DailyMealPlanError(
                "artifact_context_changed",
                "档案体重已与生成方案时提供的临时值不同",
            )

    values["profile_summary"] = profile
    values["weight_history"] = weight
    fingerprints = dict(evidence.fingerprints)
    fingerprints["profile_summary"] = canonical_fingerprint(profile)
    fingerprints["weight_history"] = canonical_fingerprint(weight)
    audits = tuple(
        EvidenceAudit(
            tool_id=item.tool_id,
            fields=item.fields,
            duration_ms=item.duration_ms,
            result_fingerprint=fingerprints[item.tool_id],
        )
        for item in evidence.audits
    )
    return DailyMealEvidence(
        values=values,
        fingerprints=fingerprints,
        audits=audits,
    )


def _medical_boundary_reason(evidence: DailyMealEvidence) -> str | None:
    profile = evidence.values["profile_summary"]
    age = profile.get("age")
    if age is not None and int(age) < 18:
        return "当前仅向成年人生成带能量目标且可保存的全天饮食方案"
    health = evidence.values["health_screening"]
    combined = " ".join([
        *[str(value) for value in health.get("injuries", [])],
        *[str(value) for value in health.get("chronic_conditions", [])],
    ])
    marker = next((item for item in MEDICAL_NUTRITION_MARKERS if item in combined), None)
    if marker:
        return f"已记录的“{marker}”可能需要医学营养治疗，当前只能提供一般安全提示"
    return None


def calculate_nutrition_targets(evidence: DailyMealEvidence) -> dict[str, Any]:
    profile = evidence.values["profile_summary"]
    workout = evidence.values["workout_daily_context"]
    age = int(profile["age"])
    height = float(profile["height_cm"])
    weight = float(_latest_weight(evidence))
    base = 10 * weight + 6.25 * height - 5 * age
    gender = profile.get("gender")
    if gender == "male":
        ree_low = ree_high = base + 5
    elif gender == "female":
        ree_low = ree_high = base - 161
    else:
        ree_low, ree_high = sorted((base - 161, base + 5))

    actual_days = int(
        workout.get("progress_4_weeks", {}).get("total_sessions", 0) / 4
    )
    preferred_days = int(profile.get("training_days_per_week") or 0)
    days = max(actual_days, preferred_days)
    if days == 0:
        activity = (1.20, 1.30)
    elif days <= 2:
        activity = (1.30, 1.45)
    elif days <= 4:
        activity = (1.40, 1.60)
    else:
        activity = (1.55, 1.75)
    if workout.get("is_training_day"):
        midpoint = (activity[0] + activity[1]) / 2
        activity = (midpoint, activity[1])
    else:
        midpoint = (activity[0] + activity[1]) / 2
        activity = (activity[0], midpoint)

    goal = str(profile.get("primary_goal") or "").lower()
    if any(item in goal for item in ("减脂", "减重", "fat_loss")):
        goal_factor = (0.85, 0.90)
        protein_factor = (1.6, 2.0)
    elif any(item in goal for item in ("增肌", "力量", "muscle", "strength")):
        goal_factor = (1.05, 1.10)
        protein_factor = (1.6, 2.0)
    else:
        goal_factor = (0.95, 1.05)
        protein_factor = (1.2, 1.6)
    calories = (
        round(ree_low * activity[0] * goal_factor[0]),
        round(ree_high * activity[1] * goal_factor[1]),
    )
    if calories[0] < 1200 or calories[1] > 5000:
        raise DailyMealPlanError(
            "nutrition_target_out_of_bounds",
            "根据当前资料估算出的能量目标超出可安全生成范围",
        )
    return {
        "strategy": "mifflin_st_jeor_amdr_v1",
        "calories_kcal": {"min": calories[0], "max": calories[1]},
        "protein_g": {
            "min": round(weight * protein_factor[0], 1),
            "max": round(weight * protein_factor[1], 1),
        },
        "fat_energy_percent": {"min": 20, "max": 35},
        "carb_energy_percent": {"min": 45, "max": 65},
        "weight_kg": weight,
        "training_day": bool(workout.get("is_training_day")),
    }


def _allowed_foods(evidence: DailyMealEvidence) -> list[dict[str, Any]]:
    foods = list(evidence.values["food_catalog"].get("foods", []))
    restriction = str(
        evidence.values["profile_summary"].get("diet_restriction") or ""
    ).strip().lower()
    if not restriction or restriction in {"无", "none", "no", "不限"}:
        return foods
    if "纯素" in restriction or "vegan" in restriction:
        return [item for item in foods if "vegan" in item.get("diet_tags", [])]
    if "素食" in restriction or "vegetarian" in restriction:
        return [
            item for item in foods
            if set(item.get("diet_tags", [])) & {"vegan", "vegetarian"}
        ]
    if "乳糖" in restriction or "lactose" in restriction:
        return [item for item in foods if item.get("category") != "dairy"]
    if "麸质" in restriction or "gluten" in restriction:
        return [item for item in foods if "gluten_free" in item.get("diet_tags", [])]
    raise DailyMealPlanError(
        "diet_restriction_not_machine_verifiable",
        f"已记录饮食限制“{restriction}”，当前食品库标签不足以可靠核验",
    )


def _nutrition_totals(items: list[dict[str, Any]]) -> dict[str, float]:
    return {
        "calories": round(sum(float(item["calories"]) for item in items), 1),
        "protein_g": round(sum(float(item["protein_g"]) for item in items), 1),
        "carbs_g": round(sum(float(item["carbs_g"]) for item in items), 1),
        "fat_g": round(sum(float(item["fat_g"]) for item in items), 1),
    }


def _existing_meals(evidence: DailyMealEvidence) -> list[dict[str, Any]]:
    return list(
        evidence.values["nutrition_recent_context"].get("today", {}).get("meals", [])
    )


async def canonicalize_daily_meal_draft(
    db: AsyncSession,
    *,
    draft: DailyMealDraft,
    evidence: DailyMealEvidence,
) -> list[dict[str, Any]]:
    existing_types = {str(item.get("meal_type")) for item in _existing_meals(evidence)}
    draft_types = [meal.meal_type for meal in draft.meals]
    missing_main_meals = {"早餐", "午餐", "晚餐"} - existing_types
    if not missing_main_meals:
        raise DailyMealPlanError(
            "daily_meal_main_meals_complete",
            "今天三顿主餐都已有记录，无需再生成待新增的全天方案",
        )
    if not missing_main_meals.issubset(draft_types):
        raise DailyMealPlanError(
            "daily_meal_missing_main_meal",
            "方案必须补齐今天尚未记录的三顿主餐",
        )
    if len(set(draft_types)) != len(draft_types):
        raise DailyMealPlanError("daily_meal_duplicate_type", "全天方案中的餐次不能重复")
    if set(draft_types) & existing_types:
        raise DailyMealPlanError("daily_meal_existing_conflict", "方案不能替换今天已有的餐次")
    if len(set(draft_types) | existing_types) > 4:
        raise DailyMealPlanError("daily_meal_too_many_meals", "全天最多只能包含 4 个餐次")
    total_item_count = sum(len(meal.items) for meal in draft.meals)
    if total_item_count > 24:
        raise DailyMealPlanError("daily_meal_too_many_items", "全天方案最多包含 24 项食品")

    allowed = {item["id"]: item for item in _allowed_foods(evidence)}
    requested_ids = {item.food_id for meal in draft.meals for item in meal.items}
    if not requested_ids.issubset(allowed):
        raise DailyMealPlanError("food_candidate_invalid", "方案选择了候选范围之外的食品")
    rows = list((await db.execute(
        select(Food).where(
            Food.id.in_(requested_ids),
            Food.is_active.is_(True),
        )
    )).scalars().all())
    foods = {item.id: item for item in rows}
    if len(foods) != len(requested_ids):
        raise DailyMealPlanError("food_candidate_unavailable", "部分食品已不可用")

    meals: list[dict[str, Any]] = []
    for meal in draft.meals:
        canonical_items: list[dict[str, Any]] = []
        for item in meal.items:
            food = foods[item.food_id]
            is_oil = "油" in food.name_zh or food.category in {"oil", "fats"}
            minimum, maximum = (1.0, 50.0) if is_oil else (5.0, 500.0)
            amount = float(item.amount_g)
            if not minimum <= amount <= maximum:
                raise DailyMealPlanError(
                    "food_amount_out_of_bounds",
                    f"{food.name_zh}的克数不在允许范围内",
                )
            factor = amount / 100
            canonical_items.append({
                "food_id": food.id,
                "food_name": food.name_zh,
                "amount_g": round(amount, 1),
                "calories": round(float(food.calories_per_100g) * factor, 1),
                "protein_g": round(float(food.protein_g) * factor, 1),
                "carbs_g": round(float(food.carbs_g) * factor, 1),
                "fat_g": round(float(food.fat_g) * factor, 1),
            })
        meals.append({
            "meal_type": meal.meal_type,
            "items": canonical_items,
            "totals": _nutrition_totals(canonical_items),
        })
    return meals


def calculate_daily_nutrition_totals(
    evidence: DailyMealEvidence,
    generated_meals: list[dict[str, Any]],
) -> dict[str, float]:
    today = evidence.values["nutrition_recent_context"].get("today", {})
    generated = _nutrition_totals([
        item for meal in generated_meals for item in meal["items"]
    ])
    return {
        "calories": round(float(today.get("total_calories", 0)) + generated["calories"], 1),
        "protein_g": round(float(today.get("total_protein_g", 0)) + generated["protein_g"], 1),
        "carbs_g": round(float(today.get("total_carbs_g", 0)) + generated["carbs_g"], 1),
        "fat_g": round(float(today.get("total_fat_g", 0)) + generated["fat_g"], 1),
    }


def _existing_nutrition_totals(evidence: DailyMealEvidence) -> dict[str, float]:
    today = evidence.values["nutrition_recent_context"].get("today", {})
    return {
        "calories": float(today.get("total_calories", 0)),
        "protein_g": float(today.get("total_protein_g", 0)),
        "carbs_g": float(today.get("total_carbs_g", 0)),
        "fat_g": float(today.get("total_fat_g", 0)),
    }


def validate_daily_totals(totals: dict[str, float], targets: dict[str, Any]) -> None:
    calories = float(totals["calories"])
    if not targets["calories_kcal"]["min"] <= calories <= targets["calories_kcal"]["max"]:
        raise DailyMealPlanError("daily_calories_out_of_target", "全天能量未落入目标区间")
    protein = float(totals["protein_g"])
    if not targets["protein_g"]["min"] <= protein <= targets["protein_g"]["max"]:
        raise DailyMealPlanError("daily_protein_out_of_target", "全天蛋白质未落入目标区间")
    if calories <= 0:
        raise DailyMealPlanError("daily_energy_invalid", "全天能量必须大于零")
    fat_percent = float(totals["fat_g"]) * 9 / calories * 100
    carb_percent = float(totals["carbs_g"]) * 4 / calories * 100
    if not 20 <= fat_percent <= 35 or not 45 <= carb_percent <= 65:
        raise DailyMealPlanError("daily_macros_out_of_target", "全天脂肪或碳水比例未落入目标区间")


def _model() -> ChatOpenAI:
    if not settings.AGENT_ENABLED or not settings.DEEPSEEK_API_KEY:
        raise DailyMealPlanError("daily_meal_model_unavailable", "全天饮食方案生成服务尚未配置")
    return ChatOpenAI(
        model=settings.AGENT_MODEL,
        api_key=settings.DEEPSEEK_API_KEY,
        base_url=settings.DEEPSEEK_BASE_URL.rstrip("/"),
        temperature=0.2,
        timeout=settings.AGENT_TIMEOUT_SECONDS,
        max_tokens=1800,
        max_retries=0,
        use_responses_api=False,
    )


def _validation_paths(exc: ValidationError) -> tuple[str, ...]:
    paths: list[str] = []
    for item in exc.errors(include_url=False)[:8]:
        location = ".".join(
            str(value)
            if isinstance(value, int)
            else (
                str(value)
                if re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,39}", str(value))
                else "<field>"
            )
            for value in item.get("loc", ())
        )
        error_type = str(item.get("type") or "validation_error")
        paths.append(f"{location or '$'}:{error_type}")
    return tuple(paths)


def _safe_repair_draft(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep only structural draft values that are safe to send for repair."""

    if payload is None:
        return None
    safe: dict[str, Any] = {}
    meals = payload.get("meals")
    if isinstance(meals, list):
        safe_meals: list[Any] = []
        for meal in meals[:4]:
            if not isinstance(meal, dict):
                safe_meals.append({"invalid_value_type": type(meal).__name__})
                continue
            safe_meal: dict[str, Any] = {
                "meal_type": (
                    str(meal.get("meal_type"))[:20]
                    if isinstance(meal.get("meal_type"), str)
                    else f"<{type(meal.get('meal_type')).__name__}>"
                ),
            }
            items = meal.get("items")
            if isinstance(items, list):
                safe_meal["items"] = [
                    {
                        "food_id": (
                            str(item.get("food_id"))[:100]
                            if isinstance(item.get("food_id"), str)
                            else f"<{type(item.get('food_id')).__name__}>"
                        ),
                        "amount_g": (
                            item.get("amount_g")
                            if isinstance(item.get("amount_g"), (int, float))
                            else f"<{type(item.get('amount_g')).__name__}>"
                        ),
                        **(
                            {"unexpected_field_count": sum(
                                1 for key in item
                                if key not in {"food_id", "amount_g"}
                            )}
                            if isinstance(item, dict)
                            and any(
                                key not in {"food_id", "amount_g"}
                                for key in item
                            )
                            else {}
                        ),
                    }
                    if isinstance(item, dict)
                    else {"invalid_value_type": type(item).__name__}
                    for item in items[:8]
                ]
            else:
                safe_meal["items_type"] = type(items).__name__
            unexpected_count = sum(
                1 for key in meal
                if key not in {"meal_type", "items"}
            )
            if unexpected_count:
                safe_meal["unexpected_field_count"] = unexpected_count
            safe_meals.append(safe_meal)
        safe["meals"] = safe_meals
    else:
        safe["meals_type"] = type(meals).__name__
    rationale = payload.get("rationale")
    safe["rationale_type"] = type(rationale).__name__
    if isinstance(rationale, list):
        safe["rationale_count"] = len(rationale)
    unexpected_count = sum(
        1 for key in payload
        if key not in {"meals", "rationale"}
    )
    if unexpected_count:
        safe["unexpected_field_count"] = unexpected_count
    return safe


def _draft_example(foods: list[dict[str, Any]]) -> dict[str, Any]:
    food_ids = [str(item["id"]) for item in foods[:3]]
    while len(food_ids) < 3:
        food_ids.append(food_ids[0] if food_ids else "candidate-food-id")
    return {
        "meals": [
            {
                "meal_type": meal_type,
                "items": [
                    {"food_id": food_ids[index], "amount_g": 100},
                ],
            }
            for index, meal_type in enumerate(("早餐", "午餐", "晚餐"))
        ],
        "rationale": ["根据目标区间搭配三餐"],
    }


async def _generate_draft(
    *,
    user_message: str,
    evidence: DailyMealEvidence,
    foods: list[dict[str, Any]],
    targets: dict[str, Any],
    revision_source: dict[str, Any] | None,
    repair_context: dict[str, Any] | None,
) -> StructuredCompletionResult:
    existing_types = [item.get("meal_type") for item in _existing_meals(evidence)]
    prompt_data = {
        "request": user_message[:1000],
        "targets": targets,
        "training": evidence.values["workout_daily_context"],
        "existing_today": evidence.values["nutrition_recent_context"].get("today", {}),
        "existing_meal_types": existing_types,
        "food_candidates": foods,
        "revision_source": revision_source,
        "repair_context": repair_context,
    }
    return await structured_chat_completion(
        [
            {
                "role": "system",
                "content": (
                    "你是全天饮食结构化方案生成器。只能使用 food_candidates 中的稳定 food_id，"
                    "不得输出食品名称或营养数值。amount_g 是建议份量，服务端会用确定性算法"
                    "在安全边界内调整克数。只生成今天尚未记录的餐次；默认补足三顿主餐，"
                    "只有确有需要才增加加餐。每餐 1-8 项，全天最多 4 餐。目标是让已有餐次加"
                    "新餐次后的全天热量、蛋白质和供能比例落入 targets。revision_source 仅表示"
                    "用户正在修改的已有方案；repair_context 仅表示上一次失败及需要修复的字段。"
                    "如果 repair_context 表示当前食品组合无法配平，应更换食品组合，而不是只"
                    "改 JSON 格式。rationale 最多5条简短说明；安全条件必须保留。"
                    "严格按提交函数的参数结构和描述中的数量限制返回。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    prompt_data,
                    ensure_ascii=False,
                    default=str,
                ),
            },
        ],
        model=settings.AGENT_MODEL,
        max_tokens=1800,
        temperature=0,
        function_name="submit_daily_meal_draft",
        function_description="提交仅含候选食品稳定 ID 和克数的全天饮食草案",
        json_schema=DAILY_MEAL_DRAFT_SCHEMA,
        json_example=_draft_example(foods),
    )


def artifact_reference(artifact: AgentArtifact) -> dict[str, Any]:
    return {
        "id": artifact.id,
        "artifact_type": artifact.artifact_type,
        "status": artifact.status,
        "version": artifact.version,
        "expires_at": artifact.expires_at.isoformat(),
        "payload_fingerprint": artifact.payload_fingerprint,
    }


def artifact_card(artifact: AgentArtifact) -> dict[str, Any]:
    payload = artifact.payload_data
    return {
        "type": "daily_meal_plan",
        "title": "今日全天饮食方案",
        "data": {
            "artifact": artifact_reference(artifact),
            "target_date": payload["target_date"],
            "existing_meals": payload["existing_meals"],
            "meals": payload["meals"],
            "nutrition_targets": payload["nutrition_targets"],
            "daily_totals": payload["daily_totals"],
            "nutrition_fit": payload.get("nutrition_fit"),
            "evidence_sources": payload["evidence_sources"],
            "assumptions": payload["assumptions"],
            "safety_notes": payload["safety_notes"],
            "can_save": True,
        },
    }


async def generate_daily_meal_artifact(
    db: AsyncSession,
    *,
    user_id: str,
    conversation_id: str,
    run_id: str,
    user_message: str,
    revise_latest: bool = False,
    now: datetime | None = None,
) -> DailyMealArtifactResult:
    moment = now or datetime.now(timezone.utc)
    target = business_today()
    evidence = await collect_daily_meal_evidence(
        db,
        user_id=user_id,
        target_date=target,
    )
    missing = _missing_critical_fields(evidence)
    ephemeral_inputs = EphemeralNutritionInputs()
    if missing:
        try:
            ephemeral_inputs = await _extract_ephemeral_inputs(
                user_message=user_message,
                missing_slots=missing,
            )
        except Exception:
            # Extraction failure cannot turn a generation request into a write
            # or a guessed profile value.  Ask for the server-verified missing
            # fields and let the next turn retry safely.
            ephemeral_inputs = EphemeralNutritionInputs()
        evidence = apply_ephemeral_inputs(evidence, ephemeral_inputs)
        missing = _missing_critical_fields(evidence)
        if missing:
            raise DailyMealPlanError(
                "daily_meal_critical_fields_missing",
                f"生成可保存方案前还缺少：{'、'.join(missing)}",
                missing_slots=missing,
                evidence_audits=evidence.audits,
            )
    boundary = _medical_boundary_reason(evidence)
    if boundary:
        raise DailyMealPlanError(
            "daily_meal_medical_boundary",
            boundary,
            evidence_audits=evidence.audits,
        )
    if not evidence.values["food_catalog"].get("foods"):
        raise DailyMealPlanError(
            "food_catalog_empty",
            "食品库暂无可用于生成方案的食品",
            evidence_audits=evidence.audits,
        )
    try:
        compatible_foods = _allowed_foods(evidence)
    except DailyMealPlanError as exc:
        raise DailyMealPlanError(
            exc.code,
            exc.message,
            evidence_audits=evidence.audits,
        ) from exc
    if not compatible_foods:
        raise DailyMealPlanError(
            "food_catalog_no_compatible_candidates",
            "食品库暂无符合当前饮食限制的可用食品",
            evidence_audits=evidence.audits,
        )

    artifacts = list((await db.execute(
        select(AgentArtifact)
        .where(
            AgentArtifact.user_id == user_id,
            AgentArtifact.conversation_id == conversation_id,
            AgentArtifact.artifact_type == "daily_meal_plan_v1",
            AgentArtifact.status.in_(("active", "proposed")),
            AgentArtifact.expires_at > moment,
        )
        .order_by(AgentArtifact.created_at.desc())
        .with_for_update()
    )).scalars().all())
    latest = artifacts[0] if artifacts else None
    revision_source = (
        latest.payload_data
        if revise_latest and latest is not None
        else None
    )
    try:
        targets = calculate_nutrition_targets(evidence)
    except DailyMealPlanError as exc:
        raise DailyMealPlanError(
            exc.code,
            exc.message,
            evidence_audits=evidence.audits,
        ) from exc
    existing_today = evidence.values["nutrition_recent_context"].get("today", {})
    if (
        float(existing_today.get("total_calories", 0))
        >= float(targets["calories_kcal"]["max"])
        or float(existing_today.get("total_protein_g", 0))
        >= float(targets["protein_g"]["max"])
    ):
        raise DailyMealPlanError(
            "daily_meal_existing_total_exceeds_target",
            "今天已记录饮食已达到或超过估算目标，不能再生成可保存的补充方案",
            evidence_audits=evidence.audits,
        )
    attempts: list[GenerationAttemptAudit] = []
    optimization_attempts: list[OptimizationAttemptAudit] = []
    acceptable_candidates: list[_OptimizedCandidate] = []
    repair_context: dict[str, Any] | None = None
    last_failure_stage = "structure"
    last_failure_message = "全天饮食方案生成服务暂时未能形成有效结构，请稍后重试"
    selected: _OptimizedCandidate | None = None
    existing_totals = _existing_nutrition_totals(evidence)
    for attempt_number in (1, 2):
        last_failure_stage = "structure"
        try:
            completion = await _generate_draft(
                user_message=user_message,
                evidence=evidence,
                foods=compatible_foods,
                targets=targets,
                revision_source=revision_source,
                repair_context=repair_context,
            )
        except StructuredAIServiceError as exc:
            audit = GenerationAttemptAudit(
                attempt=attempt_number,
                transport=exc.mode,
                status="failed",
                duration_ms=exc.duration_ms,
                error_code=exc.category,
            )
            attempts.append(audit)
            logger.warning(
                "daily_meal_generation_attempt run_id=%s attempt=%s "
                "transport=%s status=failed error_code=%s duration_ms=%s",
                run_id,
                attempt_number,
                audit.transport,
                audit.error_code,
                audit.duration_ms,
            )
            raise DailyMealPlanError(
                "daily_meal_generation_unavailable",
                "全天饮食方案生成服务暂时不可用，请稍后重试",
                evidence_audits=evidence.audits,
                generation_attempts=tuple(attempts),
                optimization_attempts=tuple(optimization_attempts),
            ) from exc

        if completion.payload is None:
            error_code = completion.parse_error or "structured_payload_missing"
            audit = GenerationAttemptAudit(
                attempt=attempt_number,
                transport=completion.mode,
                status="failed",
                duration_ms=completion.duration_ms,
                output_chars=completion.output_chars,
                finish_reason=completion.finish_reason,
                error_code=error_code,
                fallback_reason=completion.fallback_reason,
            )
            attempts.append(audit)
            repair_context = {
                "error_code": error_code,
                "finish_reason": completion.finish_reason,
                "instruction": "返回完整 JSON 对象并严格匹配字段结构",
            }
            logger.warning(
                "daily_meal_generation_attempt run_id=%s attempt=%s "
                "transport=%s status=failed error_code=%s finish_reason=%s "
                "output_chars=%s duration_ms=%s fallback_reason=%s",
                run_id,
                attempt_number,
                audit.transport,
                audit.error_code,
                audit.finish_reason,
                audit.output_chars,
                audit.duration_ms,
                audit.fallback_reason,
            )
            continue

        try:
            draft = DailyMealDraft.model_validate(completion.payload)
        except ValidationError as exc:
            validation_paths = _validation_paths(exc)
            audit = GenerationAttemptAudit(
                attempt=attempt_number,
                transport=completion.mode,
                status="failed",
                duration_ms=completion.duration_ms,
                output_chars=completion.output_chars,
                finish_reason=completion.finish_reason,
                error_code="schema_validation_failed",
                validation_paths=validation_paths,
                fallback_reason=completion.fallback_reason,
            )
            attempts.append(audit)
            repair_context = {
                "error_code": "schema_validation_failed",
                "validation_paths": list(validation_paths),
                "invalid_draft": _safe_repair_draft(completion.payload),
                "finish_reason": completion.finish_reason,
                "constraint_schema": DAILY_MEAL_DRAFT_SCHEMA,
            }
            logger.warning(
                "daily_meal_generation_attempt run_id=%s attempt=%s "
                "transport=%s status=failed error_code=%s validation_paths=%s "
                "finish_reason=%s output_chars=%s duration_ms=%s "
                "fallback_reason=%s",
                run_id,
                attempt_number,
                audit.transport,
                audit.error_code,
                list(audit.validation_paths),
                audit.finish_reason,
                audit.output_chars,
                audit.duration_ms,
                audit.fallback_reason,
            )
            continue

        try:
            canonical_draft_meals = await canonicalize_daily_meal_draft(
                db,
                draft=draft,
                evidence=evidence,
            )
        except (DailyMealPlanError, ValidationError) as exc:
            last_failure_stage = "food_validation"
            error_code = getattr(
                exc,
                "code",
                "schema_validation_failed",
            )
            validation_paths = (
                _validation_paths(exc)
                if isinstance(exc, ValidationError)
                else ()
            )
            audit = GenerationAttemptAudit(
                attempt=attempt_number,
                transport=completion.mode,
                status="failed",
                duration_ms=completion.duration_ms,
                output_chars=completion.output_chars,
                finish_reason=completion.finish_reason,
                error_code=error_code,
                validation_paths=validation_paths,
                fallback_reason=completion.fallback_reason,
            )
            attempts.append(audit)
            repair_context = {
                "error_code": error_code,
                "validation_paths": list(validation_paths),
                "invalid_draft": draft.model_dump(mode="json"),
                "instruction": (
                    "食品、餐次或克数不符合硬性安全约束，请只使用候选食品并修正结构"
                ),
            }
            last_failure_message = "模型选择的食品、餐次或克数未通过安全校验"
            logger.warning(
                "daily_meal_generation_attempt run_id=%s attempt=%s "
                "transport=%s status=failed error_code=%s validation_paths=%s "
                "finish_reason=%s output_chars=%s duration_ms=%s "
                "fallback_reason=%s",
                run_id,
                attempt_number,
                audit.transport,
                audit.error_code,
                list(audit.validation_paths),
                audit.finish_reason,
                audit.output_chars,
                audit.duration_ms,
                audit.fallback_reason,
            )
            continue

        audit = GenerationAttemptAudit(
            attempt=attempt_number,
            transport=completion.mode,
            status="completed",
            duration_ms=completion.duration_ms,
            output_chars=completion.output_chars,
            finish_reason=completion.finish_reason,
            fallback_reason=completion.fallback_reason,
            normalizations=(
                ("rationale_items_coalesced",)
                if len(completion.payload.get("rationale", [])) > 5 else ()
            ),
        )
        attempts.append(audit)
        logger.info(
            "daily_meal_generation_attempt run_id=%s attempt=%s "
            "transport=%s status=completed finish_reason=%s output_chars=%s "
            "duration_ms=%s fallback_reason=%s",
            run_id,
            attempt_number,
            audit.transport,
            audit.finish_reason,
            audit.output_chars,
            audit.duration_ms,
            audit.fallback_reason,
        )

        initial_totals = calculate_daily_nutrition_totals(
            evidence, canonical_draft_meals
        )
        candidate_for_repair: _OptimizedCandidate | None = None
        optimization_deadline = (
            time.monotonic() + settings.DAILY_MEAL_OPTIMIZER_TIMEOUT_SECONDS
        )
        for optimization_mode in ("ideal", "acceptable"):
            remaining_optimizer_seconds = optimization_deadline - time.monotonic()
            if remaining_optimizer_seconds <= 0:
                optimization_audit = OptimizationAttemptAudit(
                    attempt=attempt_number,
                    mode=optimization_mode,
                    status="failed",
                    duration_ms=0,
                    error_code="daily_meal_optimizer_timeout_no_solution",
                    violated_metrics=tuple(
                        item["metric"]
                        for item in target_gaps(initial_totals, targets)
                    ),
                    target_deviations=tuple(
                        target_gaps(initial_totals, targets)
                    ),
                )
                optimization_attempts.append(optimization_audit)
                break
            try:
                optimized = await asyncio.to_thread(
                    optimize_daily_meal_amounts,
                    meals=[{
                        "meal_type": meal.meal_type,
                        "items": [item.model_dump(mode="json") for item in meal.items],
                    } for meal in draft.meals],
                    food_candidates=compatible_foods,
                    existing_totals=existing_totals,
                    targets=targets,
                    mode=optimization_mode,
                    time_limit_seconds=remaining_optimizer_seconds,
                )
                optimized_draft = DailyMealDraft.model_validate({
                    "meals": optimized.meals,
                    "rationale": draft.rationale,
                })
                optimized_meals = await canonicalize_daily_meal_draft(
                    db,
                    draft=optimized_draft,
                    evidence=evidence,
                )
                optimized_totals = calculate_daily_nutrition_totals(
                    evidence, optimized_meals
                )
                fit = nutrition_fit(optimized_totals, targets)
            except DailyMealOptimizationError as exc:
                violated_metrics = exc.violated_metrics or tuple(
                    item["metric"] for item in target_gaps(initial_totals, targets)
                )
                status = (
                    "infeasible"
                    if exc.code == "daily_meal_optimization_infeasible"
                    else "failed"
                )
                optimization_audit = OptimizationAttemptAudit(
                    attempt=attempt_number,
                    mode=optimization_mode,
                    status=status,
                    duration_ms=exc.duration_ms,
                    error_code=exc.code,
                    violated_metrics=violated_metrics,
                    target_deviations=tuple(
                        target_gaps(initial_totals, targets)
                    ),
                )
                optimization_attempts.append(optimization_audit)
                logger.warning(
                    "daily_meal_optimization_attempt run_id=%s attempt=%s "
                    "mode=%s status=%s error_code=%s violated_metrics=%s "
                    "duration_ms=%s solver_version=%s",
                    run_id,
                    attempt_number,
                    optimization_mode,
                    status,
                    exc.code,
                    list(violated_metrics),
                    exc.duration_ms,
                    SOLVER_VERSION,
                )
                if exc.code == "daily_meal_optimizer_unavailable":
                    raise DailyMealPlanError(
                        exc.code,
                        exc.message,
                        evidence_audits=evidence.audits,
                        generation_attempts=tuple(attempts),
                        optimization_attempts=tuple(optimization_attempts),
                    ) from exc
                continue
            except (DailyMealPlanError, ValidationError) as exc:
                optimization_audit = OptimizationAttemptAudit(
                    attempt=attempt_number,
                    mode=optimization_mode,
                    status="failed",
                    duration_ms=0,
                    error_code="daily_meal_optimizer_result_invalid",
                )
                optimization_attempts.append(optimization_audit)
                raise DailyMealPlanError(
                    "daily_meal_optimizer_unavailable",
                    "营养配平结果未通过服务端复验",
                    evidence_audits=evidence.audits,
                    generation_attempts=tuple(attempts),
                    optimization_attempts=tuple(optimization_attempts),
                ) from exc

            optimization_audit = OptimizationAttemptAudit(
                attempt=attempt_number,
                mode=optimization_mode,
                status="completed",
                duration_ms=optimized.duration_ms,
                target_deviations=tuple(
                    target_gaps(optimized_totals, targets)
                ),
                objective_value=round(optimized.objective_value, 6),
                nutrition_score=round(optimized.nutrition_score, 6),
                portion_score=round(optimized.portion_score, 6),
                solver_status=optimized.solver_status,
            )
            optimization_attempts.append(optimization_audit)
            logger.info(
                "daily_meal_optimization_attempt run_id=%s attempt=%s "
                "mode=%s status=completed solver_status=%s fit_status=%s "
                "duration_ms=%s solver_version=%s",
                run_id,
                attempt_number,
                optimization_mode,
                optimized.solver_status,
                fit["status"],
                optimized.duration_ms,
                SOLVER_VERSION,
            )
            candidate = _OptimizedCandidate(
                draft=optimized_draft,
                meals=optimized_meals,
                totals=optimized_totals,
                fit=fit,
                optimization=optimized,
            )
            if fit["status"] == "within_target":
                selected = candidate
                break
            candidate_for_repair = candidate
            acceptable_candidates.append(candidate)
        if selected is not None:
            break

        last_failure_stage = "optimization"
        last_failure_message = "当前食品组合无法在允许偏差内满足全天营养目标"
        repair_totals = (
            candidate_for_repair.totals
            if candidate_for_repair is not None
            else initial_totals
        )
        repair_gaps = target_gaps(repair_totals, targets)
        repair_context = {
            "error_code": "daily_meal_ideal_optimization_infeasible",
            "candidate_totals": repair_totals,
            "target_gaps": repair_gaps,
            "infeasible_metrics": [item["metric"] for item in repair_gaps],
            "acceptable_candidate_found": candidate_for_repair is not None,
            "instruction": (
                "当前食品组合无法完全落入理想区间；请更换食品组合，服务端会重新配平克数"
            ),
        }

    if selected is None and acceptable_candidates:
        selected = min(
            acceptable_candidates,
            key=lambda item: (
                float(item.fit.get("nutrition_score", 0)),
                item.optimization.portion_score,
                canonical_fingerprint(item.meals),
            ),
        )
    if selected is None:
        code = {
            "structure": "daily_meal_generation_invalid",
            "food_validation": "daily_meal_food_validation_failed",
            "optimization": "daily_meal_optimization_infeasible",
        }.get(last_failure_stage, "daily_meal_generation_invalid")
        raise DailyMealPlanError(
            code,
            last_failure_message,
            evidence_audits=evidence.audits,
            generation_attempts=tuple(attempts),
            optimization_attempts=tuple(optimization_attempts),
        )

    draft = selected.draft
    meals = selected.meals
    totals = selected.totals
    fit = selected.fit

    today = evidence.values["nutrition_recent_context"].get("today", {})
    assumptions: list[str] = []
    ephemeral_data = ephemeral_inputs.model_dump(exclude_none=True)
    if ephemeral_data:
        assumptions.append("本轮明确补充的资料只用于本方案，没有写回个人档案。")
    if not evidence.values["weight_history"].get("records"):
        assumptions.append("近期没有体重记录，使用个人档案中的体重。")
    if not evidence.values["nutrition_recent_context"].get("recent_days"):
        assumptions.append("近期没有饮食记录，未使用历史饮食趋势。")
    if evidence.values["workout_daily_context"].get("progress_4_weeks", {}).get("total_sessions") == 0:
        assumptions.append("近 4 周没有完成记录，活动量按档案训练偏好估算。")
    payload = {
        "schema_version": "1.0.0",
        "artifact_type": "daily_meal_plan_v1",
        "target_date": target.isoformat(),
        "existing_meals": today.get("meals", []),
        "meals": meals,
        "nutrition_targets": targets,
        "daily_totals": totals,
        "nutrition_fit": fit,
        "rationale": draft.rationale,
        "evidence_sources": list(DAILY_MEAL_EVIDENCE),
        "assumptions": assumptions,
        "ephemeral_inputs": ephemeral_data,
        "safety_notes": [
            "营养目标为一般健身估算，不构成医疗处方。",
            "请自行核对尚未记录的食物过敏和禁忌。",
            "方案尚未写入饮食记录；保存后仍需再次确认提案。",
        ],
    }
    superseded_ids = [item.id for item in artifacts]
    for item in artifacts:
        item.status = "superseded"
        item.version += 1
        item.updated_at = moment
    if superseded_ids:
        pending = list((await db.execute(
            select(AgentProposal)
            .where(
                AgentProposal.user_id == user_id,
                AgentProposal.conversation_id == conversation_id,
                AgentProposal.proposal_type == "daily_meal_log_create_v1",
                AgentProposal.target_id.in_(superseded_ids),
                AgentProposal.status == "pending_confirmation",
            )
            .with_for_update()
        )).scalars().all())
        for proposal in pending:
            proposal.status = "stale"
            proposal.version += 1
            proposal.last_error_code = "artifact_superseded"
            proposal.updated_at = moment
        await db.flush()
    artifact = AgentArtifact(
        user_id=user_id,
        conversation_id=conversation_id,
        source_run_id=run_id,
        artifact_type="daily_meal_plan_v1",
        schema_version="1.0.0",
        status="active",
        version=1,
        payload_data=payload,
        payload_fingerprint=canonical_fingerprint(payload),
        context_fingerprints=evidence.fingerprints,
        expires_at=moment + timedelta(hours=23, minutes=59),
        updated_at=moment,
    )
    db.add(artifact)
    await db.flush()
    card = artifact_card(artifact)
    return DailyMealArtifactResult(
        artifact=artifact,
        card=card,
        reply=(
            "我已根据你的档案、健康、体重趋势、今天训练情况、近期饮食和食品库，"
            "生成今天的全天饮食方案。"
            + (
                "其中有少量营养指标接近但未完全落入理想区间，已在卡片中明确标注。"
                if fit["status"] == "acceptable_deviation"
                else "各项营养指标已落入理想范围。"
            )
            + "它目前只是可审阅方案，没有写入饮食记录；"
            "核对后可以选择“保存为待确认提案”。"
        ),
        audits=evidence.audits,
        generation_attempts=tuple(attempts),
        optimization_attempts=tuple(optimization_attempts),
    )
