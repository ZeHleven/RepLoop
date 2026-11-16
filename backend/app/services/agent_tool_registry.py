"""Declarative Tool Registry v2 used by non-authoritative shadow checks.

The v1 tool construction, routing, planning, and execution paths remain the
only runtime authority. Shadow consumers may read this immutable metadata.
"""

from __future__ import annotations

from app.schemas.agent_tool_registry import (
    ConditionalEvidenceContract,
    ToolArgumentContract,
    ToolArgumentFieldContract,
    ToolAuditContract,
    ToolFreshnessContract,
    ToolObservationContract,
    ToolRegistryEntry,
    ToolRegistryReadEnforcementContract,
    ToolRegistryV2,
)
from app.services.agent_intent import IntentResolution, route_tools


_SUMMARY_AND_FINGERPRINT_AUDIT = ToolAuditContract()


TOOL_REGISTRY_V2 = ToolRegistryV2(
    registry_version="2.0.0-draft.1",
    status="shadow",
    max_routed_tools=4,
    tools=(
        ToolRegistryEntry(
            tool_id="profile.get_summary",
            contract_version="1.0.0",
            langchain_name="profile_get_summary",
            title="个人训练资料摘要",
            mode="read",
            availability="active",
            side_effects="none",
            risk_level="low",
            data_sensitivity="personal",
            supported_intents=("profile_query",),
            use_cases=("读取训练目标、经验、频率、时长和地点偏好",),
            exclusions=("伤病与慢性病", "训练计划", "训练记录"),
            data_sources=("user_profiles",),
            parallel_safe=True,
            arguments=ToolArgumentContract(
                schema_ref="NoArguments",
                default_arguments={},
            ),
            observation=ToolObservationContract(
                current_shape="legacy_mapping",
                missing_data_signals=("found_false",),
                found_false_is_success=True,
            ),
            freshness=ToolFreshnessContract(
                reuse_scope="run",
                max_age_seconds=300,
                invalidation_events=("profile.updated",),
            ),
            audit=ToolAuditContract(
                sensitive_result_fields=(
                    "height_cm",
                    "weight_kg",
                    "bmi",
                    "diet_restriction",
                ),
            ),
        ),
        ToolRegistryEntry(
            tool_id="health.get_screening_summary",
            contract_version="1.0.0",
            langchain_name="health_get_screening_summary",
            title="健康筛查摘要",
            mode="read",
            availability="active",
            side_effects="none",
            risk_level="low",
            data_sensitivity="health_sensitive",
            supported_intents=("health_query",),
            use_cases=("读取伤病、慢性疾病和训练安全筛查状态",),
            exclusions=("医疗诊断", "一般资料", "计划与训练进度"),
            data_sources=("user_profiles",),
            parallel_safe=True,
            arguments=ToolArgumentContract(
                schema_ref="NoArguments",
                default_arguments={},
            ),
            observation=ToolObservationContract(
                current_shape="legacy_mapping",
                missing_data_signals=("found_false",),
                found_false_is_success=True,
            ),
            freshness=ToolFreshnessContract(
                reuse_scope="run",
                max_age_seconds=300,
                invalidation_events=("profile.updated",),
            ),
            audit=ToolAuditContract(
                sensitive_result_fields=(
                    "injuries",
                    "chronic_conditions",
                ),
            ),
        ),
        ToolRegistryEntry(
            tool_id="plan.get_active",
            contract_version="1.0.0",
            langchain_name="plan_get_active",
            title="当前活动训练计划",
            mode="read",
            availability="active",
            side_effects="none",
            risk_level="low",
            data_sensitivity="personal",
            supported_intents=("plan_query",),
            use_cases=("读取完整活动计划与各训练日动作",),
            exclusions=("只查询下一练", "已完成训练历史"),
            data_sources=(
                "workout_plans",
                "planned_exercises",
                "exercises",
            ),
            parallel_safe=True,
            arguments=ToolArgumentContract(
                schema_ref="NoArguments",
                default_arguments={},
            ),
            observation=ToolObservationContract(
                current_shape="legacy_mapping",
                missing_data_signals=("found_false",),
                found_false_is_success=True,
            ),
            freshness=ToolFreshnessContract(
                reuse_scope="run",
                max_age_seconds=60,
                invalidation_events=(
                    "plan.created",
                    "plan.updated",
                    "plan.activated",
                ),
            ),
            audit=_SUMMARY_AND_FINGERPRINT_AUDIT,
        ),
        ToolRegistryEntry(
            tool_id="workout.get_next",
            contract_version="1.0.0",
            langchain_name="workout_get_next",
            title="下一次计划训练",
            mode="read",
            availability="active",
            side_effects="none",
            risk_level="low",
            data_sensitivity="personal",
            supported_intents=("next_workout_query",),
            use_cases=("根据活动计划和当天日期读取下一练",),
            exclusions=("完整计划", "历史训练", "进行中训练"),
            data_sources=(
                "workout_plans",
                "planned_exercises",
                "exercises",
                "system_date",
            ),
            parallel_safe=True,
            arguments=ToolArgumentContract(
                schema_ref="NoArguments",
                default_arguments={},
            ),
            observation=ToolObservationContract(
                current_shape="legacy_mapping",
                missing_data_signals=("found_false", "reason_code"),
                found_false_is_success=True,
            ),
            freshness=ToolFreshnessContract(
                reuse_scope="run",
                max_age_seconds=60,
                invalidation_events=(
                    "plan.created",
                    "plan.updated",
                    "plan.activated",
                    "date.changed",
                ),
            ),
            audit=_SUMMARY_AND_FINGERPRINT_AUDIT,
        ),
        ToolRegistryEntry(
            tool_id="workout.get_active_session",
            contract_version="1.0.0",
            langchain_name="workout_get_active_session",
            title="进行中训练",
            mode="read",
            availability="active",
            side_effects="none",
            risk_level="low",
            data_sensitivity="personal",
            supported_intents=("active_workout_query",),
            use_cases=("读取进行中训练和已记录训练组",),
            exclusions=("历史训练", "计划安排"),
            data_sources=(
                "workout_sessions",
                "session_exercises",
                "exercises",
                "workout_plans",
            ),
            parallel_safe=True,
            arguments=ToolArgumentContract(
                schema_ref="NoArguments",
                default_arguments={},
            ),
            observation=ToolObservationContract(
                current_shape="legacy_mapping",
                missing_data_signals=("found_false",),
                found_false_is_success=True,
            ),
            freshness=ToolFreshnessContract(
                reuse_scope="run",
                max_age_seconds=15,
                invalidation_events=(
                    "workout.started",
                    "workout.set_recorded",
                    "workout.completed",
                ),
            ),
            audit=_SUMMARY_AND_FINGERPRINT_AUDIT,
        ),
        ToolRegistryEntry(
            tool_id="workout.list_history",
            contract_version="1.2.0",
            langchain_name="workout_list_history",
            title="近期训练历史",
            mode="read",
            availability="active",
            side_effects="none",
            risk_level="low",
            data_sensitivity="personal",
            supported_intents=("workout_history_query",),
            use_cases=("按时间倒序读取近期训练场次详情",),
            exclusions=("训练趋势聚合",),
            data_sources=(
                "workout_sessions",
                "session_exercises",
                "exercises",
                "workout_plans",
            ),
            parallel_safe=True,
            arguments=ToolArgumentContract(
                schema_ref="WorkoutHistoryArguments",
                default_arguments={"limit": 5, "start_date": "", "end_date": "", "completed_only": False, "statuses": []},
                fields=(ToolArgumentFieldContract(
                    name="limit",
                    json_type="integer",
                    has_default=True,
                    default=5,
                    minimum=1,
                    maximum=20,
                ), ToolArgumentFieldContract(name="start_date", json_type="string", has_default=True, default=""),
                   ToolArgumentFieldContract(name="end_date", json_type="string", has_default=True, default=""),
                   ToolArgumentFieldContract(name="completed_only", json_type="boolean", has_default=True, default=False),
                   ToolArgumentFieldContract(name="statuses", json_type="array", has_default=True, default=[])),
            ),
            observation=ToolObservationContract(
                current_shape="legacy_mapping",
                missing_data_signals=("count_zero",),
                empty_collection_is_success=True,
            ),
            freshness=ToolFreshnessContract(
                reuse_scope="run",
                max_age_seconds=60,
                invalidation_events=(
                    "workout.completed",
                    "workout.updated",
                    "workout.deleted",
                ),
            ),
            audit=_SUMMARY_AND_FINGERPRINT_AUDIT,
        ),
        ToolRegistryEntry(
            tool_id="workout.get_progress",
            contract_version="1.0.0",
            langchain_name="workout_get_progress",
            title="训练进度聚合",
            mode="read",
            availability="active",
            side_effects="none",
            risk_level="low",
            data_sensitivity="personal",
            supported_intents=("workout_progress_query",),
            use_cases=("读取指定周数的训练次数、组次、次数和容量趋势",),
            exclusions=("具体单次训练详情",),
            data_sources=("workout_sessions", "session_exercises"),
            parallel_safe=True,
            arguments=ToolArgumentContract(
                schema_ref="WorkoutProgressArguments",
                default_arguments={"weeks": 8},
                fields=(ToolArgumentFieldContract(
                    name="weeks",
                    json_type="integer",
                    has_default=True,
                    default=8,
                    minimum=1,
                    maximum=52,
                ),),
            ),
            observation=ToolObservationContract(
                current_shape="legacy_mapping",
                missing_data_signals=("empty_aggregate",),
                empty_collection_is_success=True,
            ),
            freshness=ToolFreshnessContract(
                reuse_scope="run",
                max_age_seconds=300,
                invalidation_events=(
                    "workout.completed",
                    "workout.updated",
                    "workout.deleted",
                ),
            ),
            audit=_SUMMARY_AND_FINGERPRINT_AUDIT,
        ),
        ToolRegistryEntry(
            tool_id="workout.get_daily_context",
            contract_version="1.0.0",
            langchain_name="workout_get_daily_context",
            title="当日训练聚合上下文",
            mode="read",
            availability="active",
            side_effects="none",
            risk_level="low",
            data_sensitivity="personal",
            supported_intents=("daily_meal_generation",),
            use_cases=("聚合读取当日计划训练与近 4 周执行情况",),
            exclusions=("修改训练计划", "记录训练"),
            data_sources=(
                "workout_plans",
                "planned_exercises",
                "workout_sessions",
                "session_exercises",
            ),
            parallel_safe=True,
            arguments=ToolArgumentContract(
                schema_ref="NoArguments",
                default_arguments={},
            ),
            observation=ToolObservationContract(
                current_shape="legacy_mapping",
                missing_data_signals=("empty_aggregate",),
                empty_collection_is_success=True,
            ),
            freshness=ToolFreshnessContract(
                reuse_scope="run",
                max_age_seconds=60,
                invalidation_events=(
                    "plan.updated",
                    "workout.completed",
                    "date.changed",
                ),
            ),
            audit=_SUMMARY_AND_FINGERPRINT_AUDIT,
        ),
        ToolRegistryEntry(
            tool_id="weight.list_history",
            contract_version="1.0.0",
            langchain_name="weight_list_history",
            title="体重历史",
            mode="read",
            availability="active",
            side_effects="none",
            risk_level="low",
            data_sensitivity="health_sensitive",
            supported_intents=("weight_history_query",),
            use_cases=("读取用户自己的体重记录和趋势",),
            exclusions=("新增体重记录", "删除体重记录"),
            data_sources=("weight_logs",),
            parallel_safe=True,
            arguments=ToolArgumentContract(
                schema_ref="WeightHistoryArguments",
                default_arguments={"limit": 30},
                fields=(ToolArgumentFieldContract(
                    name="limit",
                    json_type="integer",
                    has_default=True,
                    default=30,
                    minimum=1,
                    maximum=365,
                ),),
            ),
            observation=ToolObservationContract(
                current_shape="legacy_mapping",
                missing_data_signals=("count_zero",),
                empty_collection_is_success=True,
            ),
            freshness=ToolFreshnessContract(
                reuse_scope="run",
                max_age_seconds=60,
                invalidation_events=("weight.created",),
            ),
            audit=ToolAuditContract(
                sensitive_result_fields=("weight_kg", "recorded_at"),
            ),
        ),
        ToolRegistryEntry(
            tool_id="nutrition.get_today",
            contract_version="1.0.0",
            langchain_name="nutrition_get_today",
            title="今日饮食营养",
            mode="read",
            availability="active",
            side_effects="none",
            risk_level="low",
            data_sensitivity="health_sensitive",
            supported_intents=("nutrition_today_query",),
            use_cases=("读取今日餐次和服务端汇总营养",),
            exclusions=("新增或删除饮食记录",),
            data_sources=("meal_logs", "meal_items"),
            parallel_safe=True,
            arguments=ToolArgumentContract(
                schema_ref="NoArguments",
                default_arguments={},
            ),
            observation=ToolObservationContract(
                current_shape="legacy_mapping",
                missing_data_signals=("count_zero",),
                empty_collection_is_success=True,
            ),
            freshness=ToolFreshnessContract(
                reuse_scope="run",
                max_age_seconds=30,
                invalidation_events=("meal.created", "meal.deleted"),
            ),
            audit=_SUMMARY_AND_FINGERPRINT_AUDIT,
        ),
        ToolRegistryEntry(
            tool_id="nutrition.list_history",
            contract_version="1.0.0",
            langchain_name="nutrition_list_history",
            title="饮食历史",
            mode="read",
            availability="active",
            side_effects="none",
            risk_level="low",
            data_sensitivity="health_sensitive",
            supported_intents=("nutrition_history_query",),
            use_cases=("读取最近 30 个有饮食记录的日期和营养汇总",),
            exclusions=("新增或删除饮食记录",),
            data_sources=("meal_logs", "meal_items"),
            parallel_safe=True,
            arguments=ToolArgumentContract(
                schema_ref="NutritionHistoryArguments",
                default_arguments={"days": 30},
                fields=(ToolArgumentFieldContract(
                    name="days",
                    json_type="integer",
                    has_default=True,
                    default=30,
                    minimum=1,
                    maximum=30,
                ),),
            ),
            observation=ToolObservationContract(
                current_shape="legacy_mapping",
                missing_data_signals=("count_zero",),
                empty_collection_is_success=True,
            ),
            freshness=ToolFreshnessContract(
                reuse_scope="run",
                max_age_seconds=60,
                invalidation_events=("meal.created", "meal.deleted"),
            ),
            audit=_SUMMARY_AND_FINGERPRINT_AUDIT,
        ),
        ToolRegistryEntry(
            tool_id="nutrition.get_recent_context",
            contract_version="1.0.0",
            langchain_name="nutrition_get_recent_context",
            title="近期饮食聚合上下文",
            mode="read",
            availability="active",
            side_effects="none",
            risk_level="low",
            data_sensitivity="health_sensitive",
            supported_intents=("daily_meal_generation",),
            use_cases=("聚合读取今日餐次和近 14 个记录日的营养趋势",),
            exclusions=("新增或删除饮食记录",),
            data_sources=("meal_logs", "meal_items"),
            parallel_safe=True,
            arguments=ToolArgumentContract(
                schema_ref="NoArguments",
                default_arguments={},
            ),
            observation=ToolObservationContract(
                current_shape="legacy_mapping",
                missing_data_signals=("empty_aggregate",),
                empty_collection_is_success=True,
            ),
            freshness=ToolFreshnessContract(
                reuse_scope="run",
                max_age_seconds=60,
                invalidation_events=("meal.created", "meal.deleted"),
            ),
            audit=_SUMMARY_AND_FINGERPRINT_AUDIT,
        ),
        ToolRegistryEntry(
            tool_id="food.search",
            contract_version="1.0.0",
            langchain_name="food_search",
            title="食品库搜索",
            mode="read",
            availability="active",
            side_effects="none",
            risk_level="low",
            data_sensitivity="standard",
            supported_intents=("food_search_query",),
            use_cases=("按名称查找食品及每 100 克营养值",),
            exclusions=("记录餐次",),
            data_sources=("foods",),
            parallel_safe=True,
            arguments=ToolArgumentContract(
                schema_ref="FoodSearchArguments",
                default_arguments={"query": "常见食品", "limit": 10},
                fields=(
                    ToolArgumentFieldContract(
                        name="query",
                        json_type="string",
                        has_default=True,
                        default="常见食品",
                    ),
                    ToolArgumentFieldContract(
                        name="limit",
                        json_type="integer",
                        has_default=True,
                        default=10,
                        minimum=1,
                        maximum=20,
                    ),
                ),
            ),
            observation=ToolObservationContract(
                current_shape="legacy_mapping",
                missing_data_signals=("count_zero",),
                empty_collection_is_success=True,
            ),
            freshness=ToolFreshnessContract(
                reuse_scope="run",
                max_age_seconds=3600,
                invalidation_events=("food.updated",),
            ),
            audit=_SUMMARY_AND_FINGERPRINT_AUDIT,
        ),
        ToolRegistryEntry(
            tool_id="food.list_candidates",
            contract_version="1.0.0",
            langchain_name="food_list_candidates",
            title="饮食方案食品候选",
            mode="read",
            availability="active",
            side_effects="none",
            risk_level="low",
            data_sensitivity="standard",
            supported_intents=("daily_meal_generation",),
            use_cases=("读取结构化饮食方案可选择的标准食品与营养标签",),
            exclusions=("记录餐次", "生成自定义营养值"),
            data_sources=("foods",),
            parallel_safe=True,
            arguments=ToolArgumentContract(
                schema_ref="NoArguments",
                default_arguments={},
            ),
            observation=ToolObservationContract(
                current_shape="legacy_mapping",
                missing_data_signals=("count_zero",),
                empty_collection_is_success=True,
            ),
            freshness=ToolFreshnessContract(
                reuse_scope="run",
                max_age_seconds=3600,
                invalidation_events=("food.updated",),
            ),
            audit=_SUMMARY_AND_FINGERPRINT_AUDIT,
        ),
    ),
    conditional_evidence=(
        ConditionalEvidenceContract(
            group_id="progress_or_history",
            primary_tool_id="workout.get_progress",
            fallback_tool_id="workout.list_history",
            fallback_trigger="on_error",
            fallback_arguments={},
        ),
        ConditionalEvidenceContract(
            group_id="active_session_or_next_workout",
            primary_tool_id="workout.get_active_session",
            fallback_tool_id="workout.get_next",
            fallback_trigger="on_not_found",
            fallback_arguments={},
        ),
    ),
)

TOOL_REGISTRY_V2_BY_ID = {
    item.tool_id: item for item in TOOL_REGISTRY_V2.tools
}


# This list is intentionally explicit: a future registered tool must not enter
# the first enforce cohort without a reviewed contract change.
TOOL_REGISTRY_V2_INITIAL_READ_TOOL_IDS = (
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


TOOL_REGISTRY_V2_READ_ENFORCEMENT = ToolRegistryReadEnforcementContract(
    contract_version="1.0.0",
    tool_ids=TOOL_REGISTRY_V2_INITIAL_READ_TOOL_IDS,
    authority_surfaces=(
        "route_allowlist",
        "constructed_tools",
        "argument_schema",
        "parallel_policy",
        "conditional_evidence",
    ),
)


def route_registry_read_tool_ids(
    resolution: IntentResolution,
) -> tuple[str, ...]:
    """Project Registry entries to ordered candidates for one resolved route."""

    if (
        resolution.clarification_required
        or resolution.risk_level == "high"
        or resolution.request_kind in {
            "generation",
            "mutation",
            "proposal_decision",
        }
    ):
        return ()

    registered = set(TOOL_REGISTRY_V2_INITIAL_READ_TOOL_IDS)
    return tuple(
        tool_id for tool_id in route_tools(resolution)
        if tool_id in registered
    )[:TOOL_REGISTRY_V2.max_routed_tools]
