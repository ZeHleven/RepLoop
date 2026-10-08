from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, replace
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.config import settings
from app.services.business_clock import business_today
from app.schemas.agent_task import TaskUpdate, PendingPlanWithdrawal
from app.services.agent_task_state import (
    TASK_STATE_PROMPT, advance_task_state, state_from_context, is_read_only_revision, active_task,
    normalize_explicit_task_return, is_calendar_query_revision,
    preserve_pending_plan_changes, revise_pending_meal,
)
from app.services.structured_schema import strict_tool_schema
from app.services.agent_intent import (
    ChangeRequest,
    EvidenceRequirement,
    IntentDomain,
    IntentName,
    IntentResolution,
    IntentAttemptTiming,
    IntentResolverOutcome,
    RequestKind,
    RequestedEffect,
    RequestedOutput,
    normalize_resolution,
    pending_clarification_to_resolution,
    resolve_intent,
    resolve_pending_clarification,
)
from app.services.ai_client import (
    StructuredAIServiceError,
    StructuredCompletionResult,
    structured_chat_completion,
)
from app.services.agent_structured_errors import (
    safe_error_category,
    safe_structured_error_category,
)


logger = logging.getLogger(__name__)


_MAX_INTENT_ATTEMPTS = 2


SEMANTIC_ROUTE_DOMAIN_GUIDANCE: dict[IntentDomain, str] = {
    "general": "不依赖当前用户私有数据且不属于其他业务领域的一般健身和运动生理知识问答",
    "profile": "用户基础档案、训练目标、经验、偏好，以及体重记录、体重历史和体重趋势",
    "health": "用户个人健康筛查、伤病、慢性病、当前症状、训练禁忌和安全限制",
    "workout_plan": "整份当前训练计划、计划适配评估，以及训练计划的创建、修改或删除",
    "workout_session": "下一练、当前应执行的训练，以及正在进行的训练和已记录训练组",
    "workout_history": "按日期或范围读取具体训练场次、动作和记录，支持已完成、进行中、提前结束、跳过和放弃；状态为进行中也不等于只查当前会话",
    "workout_progress": "跨训练场次聚合的次数、组数、容量、完成率和进度趋势",
    "nutrition": "食品库、饮食记录、营养查询、配餐建议和全天饮食方案",
}

SEMANTIC_ROUTE_BOUNDARY_GUIDANCE = (
    "为当前训练计划创建修改提案是update，不是create计划；只有新建整份计划才是create",
    "指定日期或日历范围的训练记录查询属于workout_history，即使状态为进行中；当前正在练什么才查active_workout_session",
    "体重记录和趋势的业务领域是 profile；需要读取历史时另选 read_targets=weight_history",
    "下一练属于 workout_session；只有查询或改变整份计划时才属于 workout_plan",
    "不涉及当前用户个体症状或私有数据的知识解释属于 general；个人筛查、症状和伤病才属于 health",
    "风险依据用户是否报告个人状况判断：仅查看无症状筛查和一般知识解释为 low；用户报告本人存在或要求记录非急性疼痛、旧伤、伤病史或慢性病为 medium；高危红旗为 high",
)

_SEMANTIC_ROUTE_DOMAIN_TEXT = "\n".join(
    f"- {domain}: {description}"
    for domain, description in SEMANTIC_ROUTE_DOMAIN_GUIDANCE.items()
)
_SEMANTIC_ROUTE_BOUNDARY_TEXT = "\n".join(
    f"- {item}" for item in SEMANTIC_ROUTE_BOUNDARY_GUIDANCE
)
_SEMANTIC_ROUTE_DOMAIN_DESCRIPTION = (
    "主业务领域。领域定义："
    + "；".join(
        f"{domain}={description}"
        for domain, description in SEMANTIC_ROUTE_DOMAIN_GUIDANCE.items()
    )
)


class IntentRouteDecision(BaseModel):
    """SemanticRouteV2: provider-owned semantics without legacy authority."""

    model_config = ConfigDict(extra="forbid")

    intent_domain: IntentDomain
    request_kind: RequestKind
    requested_effect: RequestedEffect
    requested_output: RequestedOutput
    read_targets: list[EvidenceRequirement] = Field(
        max_length=6
    )
    decision_action: Literal["confirm", "reject"] | None
    artifact_action: Literal["save_as_proposal"] | None
    normalized_request: str = Field(max_length=4000)
    risk_level: Literal["low", "medium", "high"]
    confidence: float = Field(ge=0, le=1)
    task_update: TaskUpdate | None = None

    @field_validator("decision_action", mode="before")
    @classmethod
    def normalize_no_decision_sentinel(cls, value: Any) -> Any:
        # DeepSeek strict tools are substantially more reliable with a plain
        # string enum than a required nullable anyOf. Keep the provider schema
        # simple, then restore the domain model's None at the trust boundary.
        return None if value == "none" else value

    @field_validator("artifact_action", mode="before")
    @classmethod
    def normalize_no_artifact_action_sentinel(cls, value: Any) -> Any:
        return None if value == "none" else value

    @model_validator(mode="after")
    def validate_action_contract(self) -> "IntentRouteDecision":
        if self.request_kind in {"query", "assessment", "generation"}:
            if self.requested_effect != "read":
                raise ValueError("read request_kind requires requested_effect=read")
        if self.request_kind == "generation":
            if (
                self.intent_domain != "nutrition"
                or self.requested_output != "daily_meal_plan"
            ):
                raise ValueError(
                    "generation requires nutrition/daily_meal_plan"
                )
            if self.read_targets:
                # Daily-meal evidence is selected and bounded by the server.
                self.read_targets = []
        elif self.requested_output != "answer":
            raise ValueError(
                "only generation may request daily_meal_plan output"
            )
        if self.request_kind == "proposal_decision":
            if self.requested_effect != "decide" or not self.decision_action:
                raise ValueError(
                    "proposal_decision requires decide and decision_action"
                )
        elif self.decision_action is not None:
            raise ValueError(
                "decision_action is only valid for proposal_decision"
            )
        if self.artifact_action == "save_as_proposal":
            if (
                self.intent_domain != "nutrition"
                or self.request_kind != "mutation"
                or self.requested_effect != "create"
                or self.requested_output != "answer"
            ):
                raise ValueError(
                    "save_as_proposal requires nutrition/mutation/create/answer"
                )
        elif self.artifact_action is not None:
            raise ValueError("unsupported artifact_action")
        if self.request_kind == "proposal_decision" and self.artifact_action:
            raise ValueError(
                "proposal_decision cannot also be an artifact action"
            )
        if self.request_kind == "mutation" and self.requested_effect not in {
            "create", "update", "delete"
        }:
            raise ValueError("mutation requires create/update/delete")
        if (self.intent_domain == "health" and self.request_kind == "mutation"
                and self.requested_effect != "update"):
            raise ValueError("health mutation requires update of existing profile fields")
        if self.request_kind in {"mutation", "proposal_decision"}:
            self.read_targets = []
        return self


@dataclass(frozen=True)
class _ModelIntentInvocation:
    resolution: IntentResolution
    transport: str | None = None
    finish_reason: str | None = None
    output_chars: int | None = None


class _ExtractedChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource: IntentDomain
    operation: Literal["create", "update", "delete"]
    field_path: str | None = Field(max_length=120)
    target_reference: str | None = Field(max_length=120)
    value_json: str = Field(max_length=12000)
    preserve_unspecified: bool


from app.schemas.agent_task import PlanInputGap


class _MutationExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_requests: list[_ExtractedChange] = Field(max_length=12)
    normalized_request: str = Field(max_length=4000)
    confidence: float = Field(ge=0, le=1)
    pending_plan_withdrawals: list[PendingPlanWithdrawal] = Field(default_factory=list, max_length=12)
    plan_input_gaps: list[PlanInputGap] = Field(default_factory=list, max_length=12)


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _finish_outcome(
    outcome: IntentResolverOutcome,
    *,
    started: float,
    attempt_timings: list[IntentAttemptTiming] | None = None,
) -> IntentResolverOutcome:
    return replace(
        outcome,
        latency_ms=_elapsed_ms(started),
        attempt_timings=tuple(attempt_timings or ()),
    )


class IntentStructuredOutputError(ValueError):
    """A privacy-safe description of why structured intent parsing failed."""

    def __init__(self, category: str):
        self.category = category[:160]
        super().__init__(self.category)


_structured_error_category = safe_structured_error_category


def _error_category(error: Exception) -> str:
    if isinstance(error, IntentStructuredOutputError):
        return error.category
    if isinstance(error, StructuredAIServiceError):
        return error.category[:160]
    return safe_error_category(error)


def _is_retryable_timeout(error: Exception) -> bool:
    return (
        "timeout" in type(error).__name__.lower()
        or isinstance(error, StructuredAIServiceError)
        and error.category == "request_timeout"
    )


INTENT_ROUTE_SYSTEM_PROMPT = f"""你只负责 Fitness Agent 的业务语义路由，不回答问题、不调用工具、不提取写入字段。输出必须符合提供的 schema。

领域契约：
{_SEMANTIC_ROUTE_DOMAIN_TEXT}

领域和风险边界：
{_SEMANTIC_ROUTE_BOUNDARY_TEXT}

先判断领域，再判断动作：query=查事实或提供建议，assessment=依据事实评估，generation=仅生成全天饮食方案，mutation=明确要求创建/记录/保存/修改/删除业务数据，proposal_decision=确认或拒绝已经出现“待你确认”卡片的 Proposal。读取类 effect=read，写入为 create/update/delete，决策为 decide。训练建议、修改尚未保存的文字建议不是 generation 或 mutation，应使用 query/assessment/read/answer；“把建议的时长改为30分钟”承接仅建议任务时仍不写入。

“制定、安排、规划今天全天怎么吃”这类要求生成全天、多餐饮食方案的请求是 generation，不是写入；全天饮食方案必须是 nutrition/generation/read/daily_meal_plan。普通知识、单餐建议或一般推荐只需要文字回答，应使用 query/read/answer，例如“给我一个晚餐建议，只提供建议，不要保存”。“仅建议/不要记录/不要保存”只限制持久化，不改变方案范围：明确要求全天、多餐或三餐方案时仍为generation/read/daily_meal_plan；不能仅因不保存而改成query。只有明确要求记录、保存或写入数据才是 mutation。把已经展示的全天饮食方案保存成待确认提案，必须是 nutrition/mutation/create/answer、artifact_action=save_as_proposal、decision_action=none；它不是 Proposal 确认。只有确认或拒绝已经存在的待确认 Proposal 时，才使用 proposal_decision 和 decision_action=confirm/reject。其他请求 artifact_action 和 decision_action 都必须为 none；领域不明时用 general。

健康档案只支持更新现有字段：明确记录、补充或修改伤病史/慢性病信息均为health/mutation/update，不能create独立健康记录或delete档案；没有写入指令的健康陈述仍为query/read。新增体重记录仍属于profile/mutation/create。

read_targets 只描述回答任务必须读取的事实类型，不得输出工具名；领域和读取目标是正交字段，例如 profile 领域的体重趋势使用 weight_history。全天饮食方案的固定六类证据由服务端补齐，因此这里返回空数组。normalized_request 可补全指代，但不得猜测事实。胸痛、呼吸困难、晕厥、失去意识或严重急性疼痛为 high。上下文仅用于承接，不得覆盖最后一条消息。

边界示例：
- “展示近月体重曲线”是 profile/query/read/answer，read_targets=[weight_history]，risk_level=low。
- “今天轮到哪个训练日”是 workout_session/query/read/answer，read_targets=[next_workout]，risk_level=low。
- “解释延迟性肌肉酸痛的机制”是 general/query/read/answer，read_targets=[]，risk_level=low。
- “我的膝盖最近疼”是 health/query/read/answer，read_targets=[health_screening]，risk_level=medium。
- “请在健康档案补充多年前的踝关节韧带损伤”是 health/mutation/update/answer，read_targets=[]，decision_action=none，risk_level=medium。
- 全天饮食方案是 nutrition/generation/read/daily_meal_plan，read_targets=[]。
- “给我一个适合减脂的晚餐建议，只提供建议，不要保存”是 nutrition/query/read/answer，read_targets=[]。
- “保存这份方案”是 nutrition/mutation/create/answer，artifact_action=save_as_proposal、decision_action=none；只生成 Proposal，不确认 Proposal。
- “确认这份提案”才是 general/proposal_decision/decide/answer，artifact_action=none、decision_action=confirm。
"""


INTENT_CHANGE_SYSTEM_PROMPT = r"""餐食中的food_name必须保留用户原名，不能自行扩展或替换同义词。名称来源只能是本轮用户原文、当前任务用户要求、待澄清原文或提供的服务器草稿；已有可信food_id则保留ID。mutation_food_name_not_source表示名称被改写，请用用户实际原名修复，不要猜ID。
餐食新增即使信息未齐、用户说“稍后补”“等补齐”“先别写入”，也必须输出一条create/meal部分结构：保留已知日期、餐次、全部已知食品和克数；未知字段为null，未知amount_g为null，不省略该食品、不猜数值。空change_requests不代表待补任务，meal_partial_structure_missing时必须修复为部分结构。例如米饭100克并加尚未定量的油，要同时保留米饭100克和油的amount_g:null；由服务端精确追问，完整前不能生成提案。
如果本轮明确“恢复原来90秒/还原3组”等带数值重设，按update该值输出，不另列同字段pending_plan_withdrawals。无数值地撤回某项未应用修改才用withdrawals；解除等待生成条件不是字段撤回。
计划调整必须列出尚缺信息的 plan_input_gaps：field_path为缺失标量字段，target_reference为对应动作（计划周期则null），quote逐字摘录本轮缺信息/待补原文；无法定位字段用null。其他领域此列表为空。已知字段仍提取，未知目标不能省略成无要求、不能用当前计划值填充。用户说补齐再生成，缺口未解决前不生成部分提案。旧plan_input_gaps只在用户明确补充或撤回对应要求后消除。
你只负责把已判定的 mutation 路由提取为结构化 change_requests，不重新分类、不回答问题。每项 value_json 必须是一个 JSON 值编码成的字符串，服务端会再次 JSON 解码和类型校验。

每项变更只包含 resource、operation、field_path、target_reference、value_json、preserve_unspecified。只提取用户明确给出的值，不猜测；缺失信息由服务端验证器追问。
修改尚未确认的提案时，任务状态中的 pending_plan_changes 是尚未应用的用户要求；“其他不变”以待确认提案为基准，不是数据库原计划。提取本轮明确变更，服务端会在旧提案仍有效时保留整个提案中未覆盖的字段（包括其他动作）。pending_plan_withdrawals默认为[]：只有明确撤回某项尚未应用的计划修改时，列出对应field_path、target_reference及逐字摘录本轮撤回指令的quote；字段必须来自pending_plan_changes。这不删除动作或业务记录。“其他不变”不能撤回；不要把被撤回字段同时放入change_requests。若撤回某动作全部修改，列全该动作的待改字段。
任务状态中的pending_meal是已验证且尚未保存的餐次提案，不是已保存记录。修改它仍输出一条create/meal的完整修订后餐次：保留未修改的日期、餐次、食品和克数；删除某食品时从完整items中去掉它。不要输出update已保存记录，也不要要求用户重复已知信息。不存在有效pending_meal时不可借此新建或修改已保存的餐次。

字段约定：
- 为已有计划创建提案不等于create计划。现有动作的sets/reps/rest_seconds/recommended_weight_kg始终operation=update，新增动作才是create/exercise.add。
- 计划：schedule.duration_weeks、schedule.days_per_week、exercise.sets、exercise.reps、exercise.rest_seconds、exercise.recommended_weight_kg、exercise.add、exercise.delete、exercise.exercise_id、exercise.day_of_week。动作目标写入 target_reference；如果用户用训练日限定重复动作，必须保留限定，例如“周二的卧推”，不能缩写成“卧推”。组数、休息秒数和训练日使用 JSON 整数，建议重量使用 JSON 数值；exercise.reps 必须使用 JSON 字符串，即使单次目标也写成 value_json="\"8\""，范围写成 value_json="\"8-12\""。
- 档案：profile.age、profile.gender、profile.height_cm、profile.weight_kg、profile.experience_level、profile.primary_goal、profile.training_days_per_week、profile.session_duration_min、profile.training_location、profile.diet_restriction。
- 健康：health.injuries、health.chronic_conditions，value 是用户明确要求保存的完整列表。健康信息写入现有档案，operation始终update；记录或补充内容不代表create独立健康记录。
- 体重：create/weight_log.weight_kg。
- 饮食新增：create/meal，value={logged_at,meal_type,items}；item 只含 food_name 或 food_id 以及 amount_g。g 保持数值，kg 换算为克，绝不生成营养值。
- 饮食删除：delete/meal，target_reference 是记录 ID 或用户明确指出的餐次。
- 保存全天方案：create/daily_meal_plan.save，target_reference=latest_active_artifact_in_conversation。

最小示例：
{"change_requests":[{"resource":"nutrition","operation":"create","field_path":"meal","target_reference":null,"value_json":"{\"logged_at\":\"today\",\"meal_type\":\"午餐\",\"items\":[{\"food_name\":\"鸡胸肉\",\"amount_g\":150}]}","preserve_unspecified":true}],"normalized_request":"记录今天午餐鸡胸肉150克","confidence":0.98}

训练目标示例：
{"change_requests":[{"resource":"workout_plan","operation":"update","field_path":"exercise.sets","target_reference":"卧推","value_json":"4","preserve_unspecified":true},{"resource":"workout_plan","operation":"update","field_path":"exercise.reps","target_reference":"卧推","value_json":"\"8\"","preserve_unspecified":true},{"resource":"workout_plan","operation":"update","field_path":"exercise.rest_seconds","target_reference":"卧推","value_json":"120","preserve_unspecified":true},{"resource":"workout_plan","operation":"update","field_path":"exercise.recommended_weight_kg","target_reference":"卧推","value_json":"50","preserve_unspecified":true}],"normalized_request":"把计划里的卧推改为4组，每组8次，组间休息120秒，建议重量50公斤","confidence":0.98}
"""


_SEMANTIC_ROUTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent_domain": {
            "type": "string",
            "description": _SEMANTIC_ROUTE_DOMAIN_DESCRIPTION,
            "enum": [
                "general", "profile", "health", "workout_plan",
                "workout_session", "workout_history", "workout_progress",
                "nutrition",
            ],
        },
        "request_kind": {"type": "string", "enum": [
            "query", "assessment", "generation", "mutation",
            "proposal_decision",
        ]},
        "requested_effect": {"type": "string", "enum": [
            "read", "create", "update", "delete", "decide",
        ]},
        "requested_output": {"type": "string", "enum": [
            "answer", "daily_meal_plan",
        ]},
        "read_targets": {
            "type": "array",
            "description": (
                "回答任务必须读取的事实类型，与 intent_domain 正交；"
                "不需要私有事实时为空，全天饮食方案由服务端补齐所以也为空"
            ),
            "maxItems": 6,
            "items": {"type": "string", "enum": [
                "profile_summary", "health_screening", "weight_history",
                "active_plan", "next_workout", "active_workout_session",
                "workout_history", "workout_progress",
                "workout_daily_context", "nutrition_today",
                "nutrition_history", "nutrition_recent_context",
                "food_search", "food_catalog",
            ]},
        },
        "decision_action": {
            "type": "string",
            "description": (
                "确认提案为 confirm，拒绝提案为 reject；其他请求固定为 none"
            ),
            "enum": ["none", "confirm", "reject"],
        },
        "artifact_action": {
            "type": "string",
            "description": (
                "把已展示的全天饮食 Artifact 保存成 Proposal 时为 "
                "save_as_proposal；其他请求固定为 none"
            ),
            "enum": ["none", "save_as_proposal"],
        },
        "normalized_request": {"type": "string", "maxLength": 4000},
        "risk_level": {
            "type": "string",
            "description": (
                "仅查看无症状筛查和一般知识解释为 low；用户报告本人存在或要求记录"
                "非急性疼痛、旧伤、伤病史或慢性病必须为 medium；胸痛、呼吸困难、"
                "晕厥、失去意识或严重急性疼痛为 high"
            ),
            "enum": ["low", "medium", "high"],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "intent_domain", "request_kind", "requested_effect",
        "requested_output", "read_targets", "decision_action",
        "artifact_action",
        "normalized_request", "risk_level", "confidence",
    ],
    "additionalProperties": False,
}

_SEMANTIC_ROUTE_EXAMPLE = {
    "intent_domain": "nutrition",
    "request_kind": "generation",
    "requested_effect": "read",
    "requested_output": "daily_meal_plan",
    "read_targets": [],
    "decision_action": "none",
    "artifact_action": "none",
    "normalized_request": "结合当前用户情况生成今天全天饮食方案",
    "risk_level": "low",
    "confidence": 0.98,
}

INTENT_ROUTE_SYSTEM_PROMPT += TASK_STATE_PROMPT
_SEMANTIC_ROUTE_SCHEMA["properties"]["task_update"] = strict_tool_schema(TaskUpdate)
_SEMANTIC_ROUTE_SCHEMA["required"].append("task_update")
_SEMANTIC_ROUTE_EXAMPLE["task_update"] = {
    "action": "new", "task_id": "", "trigger": "今天全天饮食方案",
    "requirements": [],
}

_MUTATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "change_requests": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {
                    "resource": _SEMANTIC_ROUTE_SCHEMA["properties"]["intent_domain"],
                    "operation": {"type": "string", "enum": ["create", "update", "delete"]},
                    "field_path": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "target_reference": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "value_json": {"type": "string", "maxLength": 12000},
                    "preserve_unspecified": {"type": "boolean"},
                },
                "required": [
                    "resource", "operation", "field_path", "target_reference",
                    "value_json", "preserve_unspecified",
                ],
                "additionalProperties": False,
            },
        },
        "normalized_request": {"type": "string", "maxLength": 4000},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "plan_input_gaps": {"type": "array", "maxItems": 12, "items": strict_tool_schema(PlanInputGap)},
        "pending_plan_withdrawals": {"type": "array", "maxItems": 12, "items": strict_tool_schema(PendingPlanWithdrawal)},
    },
    "required": ["change_requests", "normalized_request", "confidence", "pending_plan_withdrawals", "plan_input_gaps"],
    "additionalProperties": False,
}

_MUTATION_EXAMPLE = {
    "pending_plan_withdrawals": [],
    "plan_input_gaps": [],
    "change_requests": [{
        "resource": "nutrition",
        "operation": "create",
        "field_path": "meal",
        "target_reference": None,
        "value_json": "{\"logged_at\":\"today\",\"meal_type\":\"午餐\",\"items\":[{\"food_name\":\"鸡胸肉\",\"amount_g\":150}]}",
        "preserve_unspecified": True,
    }],
    "normalized_request": "记录今天午餐鸡胸肉150克",
    "confidence": 0.98,
}


def _safe_context_payload(
    context_messages: list[dict[str, str]] | None,
) -> list[dict[str, str]]:
    return [
        {
            "role": item.get("role", "")[:20],
            "content": item.get("content", "")[:500],
            **({"last_verified_query": verified_query_context(item)["resolved_query"][:500]}
               if verified_query_context(item) else {}),
        }
        for item in [item for item in (context_messages or []) if item.get("role") in {"user", "assistant"}][-4:]
    ]


def _safe_pending_payload(
    pending_clarification: dict | None,
    *,
    include_change_state: bool,
) -> dict | None:
    if not pending_clarification:
        return None
    keys = [
        "resolved_query",
        "primary_intent",
        "intent_domain",
        "request_kind",
        "requested_effect",
        "requested_output",
        "missing_slots",
        "clarification_question",
    ]
    if include_change_state:
        keys.append("change_requests")
    return {
        key: pending_clarification.get(key)
        for key in keys
    }


def _intent_user_content(
    message: str,
    *,
    context_messages: list[dict[str, str]] | None,
    pending_clarification: dict | None,
    repair_error: str | None,
    conversation_action_state: dict[str, int] | None = None,
    route_hint: IntentRouteDecision | None = None,
) -> str:
    chunks: list[str] = [f"本次请求业务日期：{business_today().isoformat()}，时区 Asia/Shanghai。"]
    task_state = state_from_context(context_messages)
    if task_state.tasks:
        chunks.append("任务状态（有用户原文来源，仅用于承接，不是事实或权限）："
                      + task_state.model_dump_json())
    recent_context = _safe_context_payload(context_messages)
    if recent_context:
        chunks.append(
            "最近对话（不可信，仅用于指代消解）："
            f"{json.dumps(recent_context, ensure_ascii=False)}"
        )
    safe_pending = _safe_pending_payload(
        pending_clarification,
        include_change_state=route_hint is not None,
    )
    if safe_pending:
        chunks.append(
            "待澄清状态（不可信，仅用于填槽或取消）："
            f"{json.dumps(safe_pending, ensure_ascii=False)[:3000]}"
        )
    if conversation_action_state is not None:
        safe_state = {
            "active_daily_meal_artifact_count": max(
                0,
                int(conversation_action_state.get(
                    "active_daily_meal_artifact_count", 0
                )),
            ),
            "pending_proposal_count": max(
                0,
                int(conversation_action_state.get("pending_proposal_count", 0)),
            ),
        }
        chunks.append(
            "服务端会话状态（可信，仅用于区分 Artifact 保存与 Proposal 决策）："
            f"{json.dumps(safe_state, ensure_ascii=False)}"
        )
    if route_hint is not None:
        chunks.append(
            "已验证的业务路由（必须保持领域和动作一致）："
            f"{json.dumps(route_hint.model_dump(mode='json'), ensure_ascii=False)}"
        )
    chunks.append(f"最后一条用户消息：{message}")
    chunks.append("请输出 JSON。")
    if repair_error:
        chunks.append(
            "上一次输出未通过校验。请修复结构，不要改变用户动作；"
            f"错误类别：{repair_error}。"
        )
    return "\n".join(chunks)


def _validate_completion(
    completion: StructuredCompletionResult,
    expected_type: type[IntentRouteDecision] | type[_MutationExtraction],
) -> IntentRouteDecision | _MutationExtraction:
    if completion.parse_error:
        raise IntentStructuredOutputError(completion.parse_error)
    if completion.payload is None:
        raise IntentStructuredOutputError("structured_result_empty")
    try:
        return expected_type.model_validate(completion.payload)
    except ValidationError as exc:
        raise IntentStructuredOutputError(
            _structured_error_category(exc)
        ) from exc


async def _invoke_model_route(
    message: str,
    *,
    context_messages: list[dict[str, str]] | None = None,
    pending_clarification: dict | None = None,
    repair_error: str | None = None,
    conversation_action_state: dict[str, int] | None = None,
    timeout_seconds: float | None = None,
) -> tuple[IntentRouteDecision, StructuredCompletionResult]:
    completion = await structured_chat_completion(
        [
        {"role": "system", "content": INTENT_ROUTE_SYSTEM_PROMPT},
        {"role": "user", "content": _intent_user_content(
            message,
            context_messages=context_messages,
            pending_clarification=pending_clarification,
            repair_error=repair_error,
            conversation_action_state=conversation_action_state,
        )},
        ],
        model=settings.AGENT_INTENT_MODEL,
        max_tokens=settings.AGENT_INTENT_ROUTE_MAX_TOKENS,
        temperature=0,
        function_name="submit_semantic_route",
        function_description="提交 Fitness Agent 的领域、动作和读取证据路由",
        json_schema=_SEMANTIC_ROUTE_SCHEMA,
        json_example=_SEMANTIC_ROUTE_EXAMPLE,
        timeout_seconds=timeout_seconds,
    )
    parsed = _validate_completion(completion, IntentRouteDecision)
    assert isinstance(parsed, IntentRouteDecision)
    return parsed, completion


def _route_to_resolution(route: IntentRouteDecision) -> IntentResolution:
    changes: list[ChangeRequest] = []
    if route.request_kind == "proposal_decision":
        changes.append(ChangeRequest(
            resource=route.intent_domain,
            operation="update",
            field_path="proposal.status",
            value=route.decision_action,
        ))
    elif route.artifact_action == "save_as_proposal":
        changes.append(ChangeRequest(
            resource="nutrition",
            operation="create",
            field_path="daily_meal_plan.save",
            target_reference="latest_active_artifact_in_conversation",
            value=None,
        ))
    primary_by_domain: dict[IntentDomain, IntentName] = {
        "general": "general_qa",
        "profile": "profile_query",
        "health": "health_query",
        "workout_plan": "plan_query",
        "workout_session": "active_workout_query",
        "workout_history": "workout_history_query",
        "workout_progress": "workout_progress_query",
        "nutrition": "nutrition_today_query",
    }
    return IntentResolution(
        primary_intent=primary_by_domain[route.intent_domain],
        intent_domain=route.intent_domain,
        request_kind=route.request_kind,
        requested_effect=route.requested_effect,
        change_requests=changes,
        evidence_requirements=route.read_targets,
        requested_output=route.requested_output,
        resolved_query=route.normalized_request,
        references=[],
        expanded_intents=[],
        subtasks=[],
        risk_level=route.risk_level,
        confidence=route.confidence,
        task_update=route.task_update,
    )


def _validate_meal_name_sources(value, message, context_messages, pending_clarification):
    """Reject fabricated names, rather than guessing catalog aliases or IDs."""
    items = value.get("items", []) if isinstance(value, dict) else value if isinstance(value, list) else []
    sources = [message]
    state = state_from_context(context_messages)
    task = next((t for t in state.tasks if t.id == state.active_task_id and t.phase != "cancelled"), None)
    if task:
        sources.extend(r.quote for r in task.requirements)
        if task.proposal_pending and task.pending_meal:
            sources.extend(i.food_name for i in task.pending_meal.items)
    if isinstance(pending_clarification, dict):
        sources.append(str(pending_clarification.get("original_message", "")))
    for item in items:
        if not isinstance(item, dict) or item.get("food_id"):
            continue  # normal capability validation still resolves every ID
        name = item.get("food_name")
        if isinstance(name, str) and name and not any(name in source for source in sources):
            raise IntentStructuredOutputError("mutation_food_name_not_source")


async def _invoke_model_change_extraction(
    message: str,
    *,
    route: IntentRouteDecision,
    context_messages: list[dict[str, str]] | None = None,
    pending_clarification: dict | None = None,
    repair_error: str | None = None,
    conversation_action_state: dict[str, int] | None = None,
    timeout_seconds: float | None = None,
) -> tuple[IntentResolution, StructuredCompletionResult]:
    completion = await structured_chat_completion(
        [
        {"role": "system", "content": INTENT_CHANGE_SYSTEM_PROMPT},
        {"role": "user", "content": _intent_user_content(
            message,
            context_messages=context_messages,
            pending_clarification=pending_clarification,
            repair_error=repair_error,
            conversation_action_state=conversation_action_state,
            route_hint=route,
        )},
        ],
        model=settings.AGENT_INTENT_MODEL,
        max_tokens=settings.AGENT_INTENT_MAX_TOKENS,
        temperature=0,
        function_name="submit_domain_changes",
        function_description="提交已确认领域中的结构化字段变化",
        json_schema=_MUTATION_SCHEMA,
        json_example=_MUTATION_EXAMPLE,
        timeout_seconds=timeout_seconds,
    )
    extracted = _validate_completion(completion, _MutationExtraction)
    assert isinstance(extracted, _MutationExtraction)
    if (route.intent_domain == "nutrition" and route.requested_effect == "create"
            and not extracted.change_requests):
        # Missing values belong in a partial meal, not in an empty operation.
        # Reuse the bounded structured-output repair path; never invent data.
        raise IntentStructuredOutputError("meal_partial_structure_missing")
    changes: list[ChangeRequest] = []
    for item in extracted.change_requests:
        if item.resource != route.intent_domain:
            raise IntentStructuredOutputError(
                "semantic_route_change_domain_conflict"
            )
        operation = item.operation
        if (route.intent_domain == "workout_plan" and route.requested_effect == "update"
                and operation == "create" and _current_plan_reference(message)
                and item.field_path in _PLAN_SCALAR_FIELDS):
            operation = "update"
        if operation != route.requested_effect:
            raise IntentStructuredOutputError("semantic_route_change_effect_conflict")
        try:
            value = json.loads(item.value_json)
        except ValueError as exc:
            raise IntentStructuredOutputError(
                "change_value_json_invalid"
            ) from exc
        if item.resource == "nutrition" and item.field_path in {"meal", "meal_log", "meal.items"}:
            _validate_meal_name_sources(value, message, context_messages, pending_clarification)
        changes.append(ChangeRequest(
            resource=item.resource,
            operation=operation,
            field_path=item.field_path,
            target_reference=item.target_reference,
            value=value,
            preserve_unspecified=item.preserve_unspecified,
        ))
    base = _route_to_resolution(route)
    return base.model_copy(update={
        "change_requests": changes,
        "pending_plan_withdrawals": extracted.pending_plan_withdrawals,
        "plan_input_gaps": extracted.plan_input_gaps,
        "resolved_query": extracted.normalized_request,
        "confidence": min(route.confidence, extracted.confidence),
    }), completion


_PLAN_SCALAR_FIELDS = {"exercise.sets", "exercise.reps", "exercise.rest_seconds", "exercise.recommended_weight_kg", "schedule.duration_weeks", "schedule.days_per_week"}


def _current_plan_reference(message):
    return bool(re.search(r"当前(?:训练)?计划|现有(?:训练)?计划", message)
                and not re.search(r"新建|新计划|全新|复制|重新生成|创建.{0,4}计划|新增动作|添加动作", message))


def _normalize_query_and_plan_target(route, message):
    """Correct an incompatible tool/effect label using explicit user scope."""
    if (route.intent_domain == "workout_plan" and route.request_kind == "mutation"
            and route.requested_effect == "create" and _current_plan_reference(message)
            and re.search(r"\d+(?:\.\d+)?(?:组|次|秒|公斤|kg|周|天)", message)):
        route = route.model_copy(update={"requested_effect": "update"})
    if (route.intent_domain == "workout_session" and route.request_kind == "query"
            and route.requested_effect == "read" and route.risk_level == "low"
            and route.read_targets == ["active_workout_session"]):
        from app.services.agent_query_reports import explicit_history_range
        if (explicit_history_range(route.normalized_request) is not None
                and re.search(r"\d{4}[-年]|\d{1,2}月\d{1,2}日", message)
                and not re.search(r"当前|正在练|开始|结束|保存|删除|标记", message)):
            route = route.model_copy(update={"intent_domain": "workout_history", "read_targets": ["workout_history"]})
    return route


async def _invoke_model_intent(
    message: str,
    *,
    context_messages: list[dict[str, str]] | None = None,
    pending_clarification: dict | None = None,
    repair_error: str | None = None,
    conversation_action_state: dict[str, int] | None = None,
    timeout_seconds: float | None = None,
) -> _ModelIntentInvocation:
    """Route cheaply first; request the large change schema only for writes."""
    started = time.monotonic()
    route_result = await _invoke_model_route(
        message,
        context_messages=context_messages,
        pending_clarification=pending_clarification,
        repair_error=repair_error,
        conversation_action_state=conversation_action_state,
        timeout_seconds=timeout_seconds,
    )
    if isinstance(route_result, tuple):
        route, route_completion = route_result
    else:  # backwards-compatible test seam
        route = route_result
        route_completion = None
    from app.services.agent_task_state import normalize_requirement_replacements
    route = _normalize_query_and_plan_target(route, message)
    route = route.model_copy(update={"task_update": normalize_requirement_replacements(context_messages, route.task_update, message)})
    route = route.model_copy(update={"task_update": normalize_explicit_task_return(
        context_messages, route.task_update, message,
    )})
    try:
        advance_task_state(state_from_context(context_messages), route.task_update,
                           message=message, run_id="current", normalized_request=route.normalized_request)
    except ValueError as exc:
        # Only server-owned codes; never echo source quotes or arbitrary errors.
        code = str(exc)
        raise IntentStructuredOutputError(code if code.startswith("task_") and code.isascii() and len(code) < 100
                                          else "task_state_validation_failed") from exc
    if route.request_kind != "mutation" or route.artifact_action is not None:
        return _ModelIntentInvocation(
            resolution=_route_to_resolution(route),
            transport=(route_completion.mode if route_completion else None),
            finish_reason=(
                route_completion.finish_reason if route_completion else None
            ),
            output_chars=(
                route_completion.output_chars if route_completion else None
            ),
        )

    remaining = (
        timeout_seconds - (time.monotonic() - started)
        if timeout_seconds is not None
        else settings.AGENT_INTENT_TIMEOUT_SECONDS
    )
    if remaining <= 0.01:
        raise TimeoutError("intent_change_extraction_deadline_exhausted")
    projected = advance_task_state(state_from_context(context_messages), route.task_update,
                                   message=message, run_id="current", normalized_request=route.normalized_request)
    extraction_context = [
        {"role": "task_state", "content": projected.model_dump_json()},
        *(item for item in (context_messages or []) if item.get("role") != "task_state"),
    ]
    extraction_result = await _invoke_model_change_extraction(
        message,
        route=route,
        context_messages=extraction_context,
        pending_clarification=pending_clarification,
        repair_error=repair_error,
        conversation_action_state=conversation_action_state,
        timeout_seconds=min(settings.AGENT_INTENT_TIMEOUT_SECONDS, remaining),
    )
    if isinstance(extraction_result, tuple):
        resolution, extraction_completion = extraction_result
    else:  # backwards-compatible test seam
        resolution = extraction_result
        extraction_completion = None
    transports = [
        item.mode for item in (route_completion, extraction_completion) if item
    ]
    from app.services.agent_task_state import normalize_sourced_plan_resets
    route = route.model_copy(update={"task_update": normalize_sourced_plan_resets(
        context_messages, route.task_update, resolution.change_requests, message,
    )})
    try:
        changes = preserve_pending_plan_changes(
            context_messages, route.task_update, resolution.change_requests,
            withdrawals=resolution.pending_plan_withdrawals, message=message,
        )
        changes = revise_pending_meal(context_messages, route.task_update, changes)
    except ValueError as exc:
        code = str(exc)
        raise IntentStructuredOutputError(code if code.startswith("task_") and code.isascii() and len(code) < 100
                                          else "task_revision_validation_failed") from exc
    from app.services.agent_plan_completeness import enforce_plan_completeness
    resolution = resolution.model_copy(update={"task_update": route.task_update, "change_requests": changes})
    resolution = enforce_plan_completeness(resolution, projected, message)
    return _ModelIntentInvocation(
        resolution=resolution,
        transport="+".join(transports) or None,
        finish_reason=(
            extraction_completion.finish_reason
            if extraction_completion
            else route_completion.finish_reason if route_completion else None
        ),
        output_chars=sum(
            item.output_chars
            for item in (route_completion, extraction_completion)
            if item
        ) or None,
    )


def _fallback_reason(exc: Exception) -> str:
    if isinstance(exc, StructuredAIServiceError):
        if exc.category == "request_timeout":
            return "model_timeout"
        return "model_unavailable"
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return "model_timeout"
    if isinstance(exc, ValueError):
        return "schema_validation_failed"
    return "model_unavailable"


def _should_use_rules_first(
    message: str,
    resolution: IntentResolution,
    *,
    context_messages: list[dict[str, str]] | None,
) -> bool:
    """Return true only for deterministic safety/state-machine decisions."""
    if resolution.request_kind == "proposal_decision":
        return True
    if (
        resolution.risk_level == "high"
        and any(marker in message for marker in ("健康资料", "健康情况", "伤病", "慢性"))
        and any(marker in message for marker in ("更新", "修改", "记录", "保存", "设为", "改为"))
    ):
        return False
    return resolution.risk_level == "high"


def _safe_rules_fallback_resolution(
    resolution: IntentResolution,
    *,
    pending_clarification: dict | None,
) -> IntentResolution:
    """Never promote regex-extracted write slots to trusted semantic state."""
    trusted_state = (
        bool(pending_clarification)
        or resolution.request_kind == "proposal_decision"
        or resolution.risk_level == "high"
    )
    if trusted_state:
        return resolution
    return resolution.model_copy(update={
        "change_requests": [],
        "evidence_requirements": [],
        "expanded_intents": [],
        "subtasks": [],
        "missing_slots": [],
        "clarification_required": False,
        "clarification_question": None,
    })


def _intent_attempt_timeout_seconds(
    *,
    attempt: int,
    remaining_seconds: float,
) -> float:
    """Allocate one attempt while reserving a bounded retry window."""
    remaining = max(0.0, remaining_seconds)
    if remaining <= 0:
        return 0.0
    cap = (
        settings.AGENT_INTENT_ROUTE_TIMEOUT_SECONDS
        if attempt == 1
        else settings.AGENT_INTENT_TIMEOUT_SECONDS
    )
    reserve = (
        min(settings.AGENT_INTENT_TIMEOUT_SECONDS, remaining / 2)
        if attempt == 1
        else 0.0
    )
    return min(max(0.001, cap), max(0.0, remaining - reserve))


def _is_trusted_rules_fallback(
    resolution: IntentResolution,
    *,
    pending_clarification: dict | None,
) -> bool:
    """Return whether rules may safely retain the requested action.

    Ordinary reads, generations and writes are never trusted after a model
    failure. Only persisted clarification state, Proposal decisions and health
    red flags are deterministic runtime authorities.
    """
    if pending_clarification:
        return True
    if resolution.request_kind == "proposal_decision":
        return True
    return resolution.risk_level == "high"


from app.services.agent_query_context import (
    discard_stale_read_clarification, resume_verified_query, verified_query_context,
)


async def resolve_intent_with_fallback(
    message: str,
    *,
    context_messages: list[dict[str, str]] | None = None,
    pending_clarification: dict | None = None,
    conversation_action_state: dict[str, int] | None = None,
    use_model: bool | None = None,
) -> IntentResolverOutcome:
    """Resolve with structured model output, one repair, then deterministic rules."""
    started = time.perf_counter()
    pending_clarification = discard_stale_read_clarification(message, pending_clarification)
    continuation = resume_verified_query(message, context_messages) if not pending_clarification else None
    if continuation is not None:
        task = active_task(state_from_context(context_messages))
        context = verified_query_context(context_messages[-1]) if context_messages else None
        if task and context and task.last_run_id == context["source_run_id"]:
            continuation = continuation.model_copy(update={"task_update": TaskUpdate(
                action="continue", task_id=task.id, trigger=message,
                requirements=[{"key":"date_scope", "quote":message}],
            )})
        return _finish_outcome(IntentResolverOutcome(
            resolution=continuation, source="rules", fallback_reason="verified_query_followup",
        ), started=started)
    pending_outcome = resolve_pending_clarification(
        message,
        pending_clarification,
    )
    if pending_outcome is not None:
        resolution, reason = pending_outcome
        task = active_task(state_from_context(context_messages))
        if task and pending_clarification.get("origin_run_id") == task.last_run_id:
            resolution = resolution.model_copy(update={"task_update": TaskUpdate(
                action="cancel" if reason == "clarification_cancelled" else "continue",
                task_id=task.id, trigger=message,
                requirements=[] if reason == "clarification_cancelled" else [
                    {"key": "date_scope" if any("时间" in slot or "范围" in slot for slot in pending_clarification.get("missing_slots", [])) else "pending_slot",
                     "quote": message[:400]},
                ],
            )})
        resolution = normalize_resolution(
            message,
            resolution,
            context_messages=context_messages,
        )
        return _finish_outcome(IntentResolverOutcome(
            resolution=resolution,
            source="rules",
            fallback_reason=reason,
        ), started=started)

    direct_rules_resolution = resolve_intent(message)
    pending_rules_resolution = pending_clarification_to_resolution(
        pending_clarification
    )
    rules_resolution = (
        pending_rules_resolution
        if pending_rules_resolution is not None
        and direct_rules_resolution.primary_intent == "general_qa"
        and direct_rules_resolution.risk_level == "low"
        else direct_rules_resolution
    )
    model_enabled = (
        settings.AGENT_INTENT_MODEL_ENABLED if use_model is None else use_model
    )
    if not model_enabled:
        safe_resolution = _safe_rules_fallback_resolution(
            rules_resolution,
            pending_clarification=pending_clarification,
        )
        return _finish_outcome(IntentResolverOutcome(
            resolution=safe_resolution,
            source="rules",
            fallback_reason="model_disabled",
            understanding_failed=not _is_trusted_rules_fallback(
                safe_resolution,
                pending_clarification=pending_clarification,
            ),
        ), started=started)
    if (
        pending_rules_resolution is None
        and _should_use_rules_first(
            message,
            direct_rules_resolution,
            context_messages=context_messages,
        )
    ):
        return _finish_outcome(IntentResolverOutcome(
            resolution=direct_rules_resolution,
            source="rules",
            fallback_reason=(
                "proposal_decision_shortcut"
                if direct_rules_resolution.request_kind == "proposal_decision"
                else "health_safety_shortcut"
            ),
        ), started=started)
    if not settings.DEEPSEEK_API_KEY:
        safe_resolution = _safe_rules_fallback_resolution(
            rules_resolution,
            pending_clarification=pending_clarification,
        )
        return _finish_outcome(IntentResolverOutcome(
            resolution=safe_resolution,
            source="rules",
            fallback_reason="model_unconfigured",
            understanding_failed=not _is_trusted_rules_fallback(
                safe_resolution,
                pending_clarification=pending_clarification,
            ),
        ), started=started)

    last_error: Exception | None = None
    attempt_count = 0
    attempt_timings: list[IntentAttemptTiming] = []
    deadline = time.monotonic() + max(
        0.05, settings.AGENT_INTENT_TOTAL_TIMEOUT_SECONDS
    )
    for attempt in range(1, _MAX_INTENT_ATTEMPTS + 1):
        remaining_seconds = deadline - time.monotonic()
        attempt_timeout = _intent_attempt_timeout_seconds(
            attempt=attempt,
            remaining_seconds=remaining_seconds,
        )
        if attempt_timeout <= 0:
            break
        attempt_count = attempt
        attempt_started = time.perf_counter()
        try:
            invocation = await asyncio.wait_for(
                _invoke_model_intent(
                    message,
                    context_messages=context_messages,
                    pending_clarification=pending_clarification,
                    repair_error=(
                        _error_category(last_error) if last_error else None
                    ),
                    conversation_action_state=conversation_action_state,
                    timeout_seconds=attempt_timeout,
                ),
                timeout=attempt_timeout,
            )
            if isinstance(invocation, IntentResolution):
                invocation = _ModelIntentInvocation(resolution=invocation)
            resolution = invocation.resolution
            normalized_resolution = normalize_resolution(
                message,
                resolution,
                context_messages=context_messages,
            )
            from app.services.agent_query_reports import narrow_report_evidence
            normalized_resolution = narrow_report_evidence(normalized_resolution, message)
            if (
                direct_rules_resolution.request_kind == "mutation"
                and resolution.request_kind != "mutation"
                and not is_read_only_revision(context_messages, resolution.task_update, message)
                and not is_calendar_query_revision(context_messages, resolution, message)
            ):
                artifact_save_requested = any(
                    change.resource == "nutrition"
                    and change.operation == "create"
                    and change.field_path == "daily_meal_plan.save"
                    for change in direct_rules_resolution.change_requests
                )
                raise IntentStructuredOutputError(
                    "semantic_artifact_save_requires_mutation"
                    if artifact_save_requested
                    else "semantic_mutation_action_conflict"
                    if resolution.request_kind == "proposal_decision"
                    else "semantic_mutation_structure_missing"
                )
            if (
                direct_rules_resolution.request_kind == "generation"
                and (
                    resolution.request_kind != "generation"
                    or resolution.requested_effect != "read"
                    or bool(resolution.change_requests)
                )
            ):
                raise IntentStructuredOutputError(
                    "semantic_generation_conflict"
                )
            attempt_timings.append(IntentAttemptTiming(
                attempt=attempt,
                latency_ms=_elapsed_ms(attempt_started),
                status="success",
                transport=invocation.transport,
                output_chars=invocation.output_chars,
                finish_reason=invocation.finish_reason,
            ))
            return _finish_outcome(IntentResolverOutcome(
                resolution=normalized_resolution,
                source="model",
                attempt_count=attempt,
                error_category=(
                    _error_category(last_error) if last_error else None
                ),
            ), started=started, attempt_timings=attempt_timings)
        except Exception as exc:  # provider and schema errors share one safe fallback
            last_error = exc
            attempt_timings.append(IntentAttemptTiming(
                attempt=attempt,
                latency_ms=_elapsed_ms(attempt_started),
                status="error",
                error_category=_error_category(exc),
                transport=(exc.mode if isinstance(exc, StructuredAIServiceError) else None),
            ))
            logger.warning(
                "Intent model attempt failed: attempt=%s category=%s",
                attempt,
                _error_category(exc),
            )
            retryable = isinstance(exc, ValueError) or _is_retryable_timeout(exc)
            if isinstance(exc, StructuredAIServiceError):
                retryable = exc.category in {
                    "request_timeout", "request_error", "http_500",
                    "http_502", "http_503", "http_504",
                }
                if exc.category == "http_429":
                    retry_after = exc.retry_after_seconds
                    retry_remaining = deadline - time.monotonic()
                    retryable = (
                        retry_after is not None
                        and retry_after <= retry_remaining
                        and attempt < _MAX_INTENT_ATTEMPTS
                    )
                    if retryable and retry_after > 0:
                        await asyncio.sleep(retry_after)
            if not retryable:
                break
            retry_remaining = deadline - time.monotonic()
            if (
                attempt >= _MAX_INTENT_ATTEMPTS
                or retry_remaining <= 0.001
            ):
                break

    assert last_error is not None
    safe_rules_resolution = _safe_rules_fallback_resolution(
        rules_resolution,
        pending_clarification=pending_clarification,
    )
    return _finish_outcome(IntentResolverOutcome(
        resolution=safe_rules_resolution,
        source="rules",
        attempt_count=attempt_count,
        fallback_reason=_fallback_reason(last_error),
        error_category=_error_category(last_error),
        understanding_failed=not _is_trusted_rules_fallback(
            safe_rules_resolution,
            pending_clarification=pending_clarification,
        ),
    ), started=started, attempt_timings=attempt_timings)
