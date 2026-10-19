from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import BaseTool

from app.schemas.agent_planning import (
    ExecutorDecision,
    FinalizationDecision,
    FinalizationOutcome,
    FinalResponse,
    MicroPlan,
    MicroPlanDraft,
    ModelInvocationMetrics,
    ProposalFinalizationDecision,
)
from app.services.agent_tools import TOOL_ID_BY_LANGCHAIN_NAME
from app.services.agent_response_style import RESPONSE_STYLE_PROMPT
from app.services.ai_client import AIServiceError
from app.services.agent_structured_errors import (
    safe_error_category,
    safe_structured_error_category,
)


PLANNER_SYSTEM_PROMPT = """你是 Fitness Agent 的轻量 Planner。只规划，不回答，不调用工具。

输出 1–3 个线性取证步骤，不生成回答步骤、DAG 或子计划。优先级：
1. 同一结论同时必需 2–3 项独立只读证据，且工具与参数现在即可确定：只生成一个 parallel_read，把全部必需证据放进同一批次，不得把第三项另拆 direct。
2. 后一工具是否需要取决于前一观察：使用一个 bounded_react，不得预取条件分支。
3. 其余单次读取：使用 direct。

硬规则：
- parallel_read 必须有 2–3 个唯一 planned_actions；candidate_tools 与动作工具同序完全一致；completion_policy=after_all_observations。
- direct 使用 after_successful_observation；bounded_react 使用 executor_decides；两者 planned_actions=[]。
- 进度与历史服务同一目标时，进度是主证据，历史仅为失败/不足后的备用，不与进度同时预取。
- 只用输入白名单；私有事实只能来自工具；objective 与 success_signal 简短。

三证据正例：
输入语义目标=读取用户偏好、当前计划、最近四周进度并判断适配度；工具还包含历史备用。
输出={"steps":[{"objective":"并行取得计划适配所需三项证据","candidate_tools":["profile.get_summary","plan.get_active","workout.get_progress"],"execution_strategy":"parallel_read","completion_policy":"after_all_observations","planned_actions":[{"tool_id":"profile.get_summary","arguments":{}},{"tool_id":"plan.get_active","arguments":{}},{"tool_id":"workout.get_progress","arguments":{"weeks":4}}],"success_signal":"偏好、计划和四周进度均已返回"}]}

条件反例：
输入语义目标=先查未完成训练，不存在时才查下一练。
输出={"steps":[{"objective":"按活动训练状态选择继续或下一练","candidate_tools":["workout.get_active_session","workout.get_next"],"execution_strategy":"bounded_react","completion_policy":"executor_decides","planned_actions":[],"success_signal":"已按活动训练观察取得必要证据"}]}

顶层只能是 steps；步骤字段只能是 objective、candidate_tools、execution_strategy、completion_policy、planned_actions、success_signal；动作字段只能是 tool_id、arguments。只输出 JSON。
"""


EXECUTOR_SYSTEM_PROMPT = """你是 Fitness Agent 的单步 Executor，只处理当前计划步骤。

你可以选择：call_tool、complete_step、request_replan、clarify、safe_stop。
- call_tool 只能选择当前步骤候选工具，并提供符合工具参数 schema 的 arguments。
- complete_step 只在 success_signal 已满足或现有证据足以说明无法满足时使用。
- 当前计划不再适用、需要改变步骤策略或候选工具时，必须 request_replan；不得自行扩权。
- 工具调用成功且返回 found=false、count=0 或空列表，是有效的否定观察，不是工具故障；应按当前条件分支继续或完成，不要因此重规划。
- 当前步骤已有候选替代工具时，首选工具超时或报错后先调用替代工具；只有候选集无法完成目标时才 request_replan。
- 缺少会实质改变结论的信息时 clarify。
- 出现健康红旗时 safe_stop。
- 不得重复相同工具与相同参数，不得声称执行写操作。
- reason 和 step_summary 只写简洁结论，不输出内部推理过程。
- 严格输出：{"decision":"call_tool | complete_step | request_replan | clarify | safe_stop","tool_id":null,"arguments":{},"step_summary":null,"reason":"...","message":null,"missing_slot":null}。
- call_tool 时填写 tool_id 和 arguments；其余决定的 tool_id 必须为 null 且 arguments 必须为空对象。
- 不要使用 action、tool、args 等别名；只输出上述 JSON。
"""


REPLANNER_SYSTEM_PROMPT = """你是 Fitness Agent 的轻量 Replanner。

根据已经完成的步骤、真实工具观察和修订原因，只重新规划尚未完成的部分。
输出 1 到给定上限个线性步骤；不要修改已完成事实，不要重做相同工具与相同参数，
不要生成 DAG、子计划或综合回答步骤。每步仍须显式标记 direct、parallel_read 或 bounded_react。
每个保留步骤必须至少包含一个候选工具；若现有观察已经足够，不要生成空工具步骤。
direct 步骤优先使用 after_successful_observation；bounded_react 必须使用 executor_decides。
仅当 2 到 3 个只读动作相互独立且工具与参数都能预先确定时使用 parallel_read；必须逐项填写 planned_actions，并使用 after_all_observations。direct 和 bounded_react 的 planned_actions 必须为空数组。
只使用提供的工具白名单。严格输出：{"steps":[{"objective":"...","candidate_tools":["白名单工具 ID"],"execution_strategy":"direct 或 parallel_read 或 bounded_react","completion_policy":"executor_decides 或 after_successful_observation 或 after_all_observations","planned_actions":[{"tool_id":"白名单工具 ID","arguments":{}}],"success_signal":"..."}]}。
不要使用 step、tools、strategy、actions 等别名；只输出上述 JSON，不输出推理过程。
"""


from app.services.nutrition_assessment_evidence import NUTRITION_ASSESSMENT_PROMPT

FINALIZER_SYSTEM_PROMPT = """你是 Fitness Agent 的最终回答器。

根据用户目标和实际工具观察给出简洁中文回答。用户私有事实只能来自工具观察；证据不足或
工具失败时明确说明局限，不得编造。本阶段只读取事实，不能声称已开始、记录、完成或修改数据。
涉及疼痛或伤病时保持训练安全边界，不做医疗诊断。

普通查询、评估和饮食建议不是可确认 Proposal，不得把它们称为“提案”“待确认记录”，也不得
邀请用户确认、提交或应用。跨领域写入由服务端在进入本阶段前根据结构化 change_requests 创建
并持久化；本阶段不能承诺在下一轮替用户生成 Proposal。

你不直接决定 terminal_action，而是选择一个允许的语义结果 outcome：
- informational_answer：普通查询或建议，不形成调整提案；
- no_change_needed：用户询问是否调整，但证据表明维持当前安排更合适；
- insufficient_evidence：现有证据不足以可靠形成调整方向；
- adjustment_proposal：仅用于 allowed_outcomes 明确允许的训练计划调整草案，必须说明待确认且尚未执行。
只能从输入的 allowed_outcomes 中选择，不能自行扩展结果类型。
判断终态时区分“缺少精确聚合量”和“完全没有依据”：若聚合进度工具失败，但活动计划频率与
近期历史场次仍显示明显执行差距，可以给出保守、可撤回的降频或观察期 proposal，并透明说明
缺失数据；不要把缺少组数或容量当成拒绝任何调整提案的理由。若现有证据连方向性判断也不支持，
才选择 insufficient_evidence 并说明暂不调整。
严格输出：{"outcome":"允许的语义结果之一","reply":"..."}。
不要使用 action、response、content 等别名；只输出上述 JSON，不输出内部推理过程。
""" + RESPONSE_STYLE_PROMPT + NUTRITION_ASSESSMENT_PROMPT


PROPOSAL_DRAFT_SYSTEM_PROMPT = """

本轮服务端已开启训练计划调整 Proposal 草案。只有 outcome=adjustment_proposal 时填写
proposal_draft；其他 outcome 必须为 null。草案不是执行授权，只描述显式变更：
{"proposal_type":"plan_adjustment_v1","changes":[...],"rationale":["..."],
"safety_notes":[],"requested_ttl_hours":null}。

每个 change 都必须完整包含 change_type、stable_display_key、before、after、reason 和
safety_priority，不得添加其他字段。changes 只允许以下精确形状：
- adjust_exercise_target：
  {"change_type":"adjust_exercise_target","stable_display_key":"day-1-order-0",
  "before":{"sets":4},"after":{"sets":3},"reason":"近期完成率偏低，先降低单次训练量。",
  "safety_priority":false}。stable_display_key 必须来自观察中的 day-{星期}-order-{顺序}；
  before/after 只列出实际变化的 sets、reps、rest_seconds 或 recommended_weight_kg，且两侧
  字段集合完全相同、值不能相同。
- update_plan_schedule：
  {"change_type":"update_plan_schedule","stable_display_key":"plan-schedule",
  "before":{"duration_weeks":4},"after":{"duration_weeks":6},
  "reason":"延长计划周期以降低每周推进压力。","safety_priority":false}。允许修改
  duration_weeks；当个人目标频率比当前计划少 1 天且近期完成率不超过一半时，也允许仅将
  days_per_week 下调 1 天。频率调整不得与其他 change 混用；服务端会确定性移除总组数最少的
  训练日，并在完整 before/after 中展示结果。before/after 字段集合必须相同、值不能相同。

rationale 必须是 1–12 条非空字符串；safety_notes 必须是 0–12 条非空字符串。
requested_ttl_hours 只能是 null 或 1–72 的整数，不能使用字符串数字。

不要输出完整计划、user_id、plan_id、数据库状态、任意 patch、动作替换或 evidence。服务端会从
本轮只读观察重建完整 before/after 和证据。信息不足以给出上述严格变更时选择
insufficient_evidence，不要伪造 proposal_draft。
"""


def _json_for_prompt(value: Any, *, max_chars: int = 18000) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    if len(serialized) <= max_chars:
        return serialized
    half = max_chars // 2
    return json.dumps({
        "truncated": True,
        "content_prefix": serialized[:half],
        "content_suffix": serialized[-half:],
    }, ensure_ascii=False)


@dataclass(frozen=True)
class StructuredInvocation:
    parsed: Any
    input_chars: int
    output_chars: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: str | None = None


class PlanningModelError(AIServiceError):
    """A safe, stage-aware planning model error for logs and evaluations."""

    def __init__(self, message: str, *, stage: str, category: str):
        self.stage = stage[:40]
        self.category = category[:160]
        super().__init__(message)


def build_tool_catalog(tools: list[BaseTool]) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for tool in tools:
        tool_id = TOOL_ID_BY_LANGCHAIN_NAME.get(tool.name, tool.name)
        args_schema = getattr(tool, "args_schema", None)
        parameters: dict[str, Any] = {}
        if args_schema is not None and hasattr(args_schema, "model_json_schema"):
            parameters = args_schema.model_json_schema()
        catalog.append({
            "tool_id": tool_id,
            "description": tool.description,
            "parameters": parameters,
        })
    return catalog


def _compact_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    properties = parameters.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    compact_properties: dict[str, dict[str, Any]] = {}
    allowed_fields = (
        "type",
        "default",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "enum",
    )
    for name, raw_schema in properties.items():
        if not isinstance(name, str) or not isinstance(raw_schema, dict):
            continue
        compact_properties[name] = {
            key: raw_schema[key]
            for key in allowed_fields
            if key in raw_schema
        }
    required = parameters.get("required")
    return {
        "properties": compact_properties,
        "required": (
            [item for item in required if isinstance(item, str)]
            if isinstance(required, list)
            else []
        ),
    }


def compact_planner_tool_catalog(
    tool_catalog: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Strip display-only JSON Schema fields from Planner input."""
    compact: list[dict[str, Any]] = []
    for item in tool_catalog:
        description = " ".join(str(item.get("description") or "").split())
        parameters = item.get("parameters")
        compact.append({
            "tool_id": item["tool_id"],
            "purpose": description[:180],
            "arguments": _compact_parameters(
                parameters if isinstance(parameters, dict) else {}
            ),
        })
    return compact


def _compact_observations(
    observations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in observations[-8:]:
        result = item.get("result")
        serialized = json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        compact.append({
            "step_id": item.get("step_id"),
            "tool_id": item.get("tool_id"),
            "status": item.get("status"),
            "result": (
                result
                if len(serialized) <= 1200
                else {"truncated": True, "preview": serialized[:1200]}
            ),
        })
    return compact


def _compact_finalizer_steps(
    steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep conclusions while dropping planning fields duplicated in trace."""
    compact: list[dict[str, Any]] = []
    for item in steps[-3:]:
        value = {
            "id": item.get("id"),
            "objective": item.get("objective"),
            "status": item.get("status"),
            "summary": item.get("summary"),
        }
        compact.append({
            key: field_value
            for key, field_value in value.items()
            if field_value is not None
        })
    return compact


def _compact_finalizer_observations(
    observations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Remove invocation IDs and exact duplicates without dropping facts."""
    compact: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in observations[-8:]:
        value = {
            "step_id": item.get("step_id"),
            "tool_id": item.get("tool_id"),
            "status": item.get("status"),
            "result": item.get("result"),
        }
        fingerprint = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        compact.append(value)
    return compact


def _message_content_chars(raw: Any, parsed: Any) -> int:
    content = getattr(raw, "content", None)
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return len(json.dumps(content, ensure_ascii=False, default=str))
    if hasattr(parsed, "model_dump"):
        parsed = parsed.model_dump(mode="json")
    return len(json.dumps(parsed, ensure_ascii=False, default=str))


def _finish_reason(raw: Any) -> str | None:
    metadata = getattr(raw, "response_metadata", None)
    if not isinstance(metadata, dict):
        return None
    reason = metadata.get("finish_reason")
    if not isinstance(reason, str) or not reason.strip():
        return None
    normalized = reason.strip().lower()
    if normalized in {
        "stop",
        "length",
        "tool_calls",
        "content_filter",
    }:
        return normalized
    return "other"


async def _invoke_structured(
    model: Any,
    schema: type[Any],
    *,
    system_prompt: str,
    payload: dict[str, Any],
    stage: str,
    max_payload_chars: int = 18000,
) -> StructuredInvocation:
    user_content = (
        "输入："
        f"{_json_for_prompt(payload, max_chars=max_payload_chars)}\n"
        "请输出 JSON。"
    )
    structured = model.with_structured_output(
        schema,
        method="json_mode",
        include_raw=True,
    )
    try:
        result = await structured.ainvoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ])
    except Exception as exc:
        raise PlanningModelError(
            "Agent 规划模型暂时不可用",
            stage=stage,
            category=safe_error_category(exc),
        ) from exc
    if not isinstance(result, dict):
        raise PlanningModelError(
            "Agent 规划模型未返回结构化结果",
            stage=stage,
            category="structured_result_not_mapping",
        )
    parsed = result.get("parsed")
    if isinstance(parsed, schema):
        raw = result.get("raw")
        usage = getattr(raw, "usage_metadata", None)
        metrics = {
            "input_chars": len(system_prompt) + len(user_content),
            "output_chars": _message_content_chars(raw, parsed),
            "finish_reason": _finish_reason(raw),
        }
        if isinstance(usage, dict):
            return StructuredInvocation(
                parsed=parsed,
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                **metrics,
            )
        return StructuredInvocation(parsed=parsed, **metrics)
    parsing_error = result.get("parsing_error")
    raise PlanningModelError(
        "Agent 规划模型返回结果未通过结构校验",
        stage=stage,
        category=(
            safe_structured_error_category(parsing_error)
            if isinstance(parsing_error, BaseException)
            else "structured_result_empty"
        ),
    )


def _validate_plan_tools(
    plan: MicroPlan,
    *,
    allowed_tools: set[str],
    min_steps: int,
    max_steps: int,
) -> MicroPlan:
    if not min_steps <= len(plan.steps) <= max_steps:
        raise AIServiceError("Agent 微计划步骤数超出边界")
    for step in plan.steps:
        unknown = set(step.candidate_tools) - allowed_tools
        if unknown:
            raise AIServiceError("Agent 微计划请求了未授权工具")
    return plan


class ModelPlanningPolicy:
    """Model-backed policy; orchestration and hard limits stay in Controller."""

    def __init__(self, model: Any):
        self._model = model
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None

    def _record_usage(self, invocation: StructuredInvocation) -> None:
        if invocation.input_tokens is not None:
            self.input_tokens = (
                (self.input_tokens or 0) + invocation.input_tokens
            )
        if invocation.output_tokens is not None:
            self.output_tokens = (
                (self.output_tokens or 0) + invocation.output_tokens
            )

    async def create_plan(
        self,
        *,
        goal: str,
        subtasks: list[str],
        tool_catalog: list[dict[str, Any]],
    ) -> MicroPlan:
        invocation = await _invoke_structured(
            self._model,
            MicroPlanDraft,
            system_prompt=PLANNER_SYSTEM_PROMPT,
            payload={
                "request": goal[:1600],
                "semantic_goals": [
                    item[:180] for item in subtasks[:6]
                ],
                "tools": compact_planner_tool_catalog(tool_catalog),
                "limits": {"max_steps": 3, "parallel_actions": [2, 3]},
            },
            stage="planner",
            max_payload_chars=8000,
        )
        self._record_usage(invocation)
        draft: MicroPlanDraft = invocation.parsed
        plan = MicroPlan(goal=goal, steps=draft.steps)
        allowed_tools = {item["tool_id"] for item in tool_catalog}
        validated = _validate_plan_tools(
            plan,
            allowed_tools=allowed_tools,
            min_steps=1,
            max_steps=3,
        )
        return validated

    async def decide_step(
        self,
        *,
        goal: str,
        step: dict[str, Any],
        observations: list[dict[str, Any]],
        tool_catalog: list[dict[str, Any]],
        remaining_step_tool_calls: int,
        remaining_global_tool_calls: int,
        guard_error: str | None,
    ) -> ExecutorDecision:
        invocation = await _invoke_structured(
            self._model,
            ExecutorDecision,
            system_prompt=EXECUTOR_SYSTEM_PROMPT,
            payload={
                "goal": goal,
                "current_step": step,
                "observations": observations,
                "tool_catalog": tool_catalog,
                "remaining_step_tool_calls": remaining_step_tool_calls,
                "remaining_global_tool_calls": remaining_global_tool_calls,
                "previous_decision_rejected_because": guard_error,
            },
            stage="executor",
        )
        self._record_usage(invocation)
        return invocation.parsed

    async def revise_plan(
        self,
        *,
        goal: str,
        completed_steps: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        reason: str,
        tool_catalog: list[dict[str, Any]],
        max_steps: int,
    ) -> MicroPlan:
        invocation = await _invoke_structured(
            self._model,
            MicroPlanDraft,
            system_prompt=REPLANNER_SYSTEM_PROMPT,
            payload={
                "request": goal[:1600],
                "completed_steps": completed_steps[-3:],
                "observations": _compact_observations(observations),
                "revision_reason": reason[:500],
                "tools": compact_planner_tool_catalog(tool_catalog),
                "limits": {
                    "max_steps": max_steps,
                    "parallel_actions": [2, 3],
                },
            },
            stage="replanner",
            max_payload_chars=12000,
        )
        self._record_usage(invocation)
        draft: MicroPlanDraft = invocation.parsed
        plan = MicroPlan(goal=goal, steps=draft.steps)
        allowed_tools = {item["tool_id"] for item in tool_catalog}
        validated = _validate_plan_tools(
            plan,
            allowed_tools=allowed_tools,
            min_steps=1,
            max_steps=max_steps,
        )
        return validated

    async def finalize(
        self,
        *,
        goal: str,
        steps: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        allowed_outcomes: list[FinalizationOutcome],
        proposal_creation_enabled: bool = False,
    ) -> FinalResponse:
        decision_schema = (
            ProposalFinalizationDecision
            if proposal_creation_enabled
            else FinalizationDecision
        )
        invocation = await _invoke_structured(
            self._model,
            decision_schema,
            system_prompt=(
                FINALIZER_SYSTEM_PROMPT + PROPOSAL_DRAFT_SYSTEM_PROMPT
                if proposal_creation_enabled
                else FINALIZER_SYSTEM_PROMPT
            ),
            payload={
                "goal": goal,
                "step_results": _compact_finalizer_steps(steps),
                "tool_observations": _compact_finalizer_observations(
                    observations
                ),
                "allowed_outcomes": allowed_outcomes,
            },
            stage="finalizer",
        )
        self._record_usage(invocation)
        decision: FinalizationDecision = invocation.parsed
        if decision.outcome not in allowed_outcomes:
            raise PlanningModelError(
                "Agent 最终结果超出本轮终止动作契约",
                stage="finalizer",
                category="finalization_outcome_not_allowed",
            )
        return FinalResponse(
            terminal_action=(
                "proposal"
                if decision.outcome == "adjustment_proposal"
                else "answer"
            ),
            reply=decision.reply,
            outcome=decision.outcome,
            proposal_draft=(
                decision.proposal_draft.model_dump(
                    mode="json",
                    exclude_unset=True,
                )
                if (
                    isinstance(decision, ProposalFinalizationDecision)
                    and decision.proposal_draft is not None
                )
                else None
            ),
            invocation_metrics=ModelInvocationMetrics(
                input_chars=invocation.input_chars,
                output_chars=invocation.output_chars,
                input_tokens=invocation.input_tokens,
                output_tokens=invocation.output_tokens,
                finish_reason=invocation.finish_reason,
            ),
        )
