from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.agent import (
    AgentArtifact,
    AgentConversation,
    AgentMessage,
    AgentProposal,
    AgentRun,
    AgentToolCall,
)
from app.schemas.agent import (
    AgentArtifactActionRequest,
    AgentArtifactReference,
    AgentClarificationActionRequest,
    AgentProposalReference,
)
from app.schemas.agent_plan_adjustment_proposal_api import (
    PlanAdjustmentProposalDecisionRequest,
)
from app.schemas.plan_management_proposal import GenericProposalDecisionRequest
from app.schemas.agent_trace import AgentExecutionTrace
from app.services.agent_controller import (
    ToolAuditEvent,
    execute_planned_agent,
)
from app.services.agent_intent import (
    ChangeRequest,
    IntentResolution,
    IntentResolverOutcome,
    parse_explicit_plan_adjustment_command,
    parse_explicit_proposal_decision,
    pending_clarification_to_resolution,
    route_tools,
)
from app.services.agent_plan_adjustment_proposal_decisions import (
    decide_plan_adjustment_proposal,
)
from app.services.agent_plan_adjustment_proposal_execution import (
    apply_confirmed_plan_adjustment_atomically,
)
from app.services.agent_intent_model import resolve_intent_with_fallback
from app.services.agent_plan_adjustment_proposal_persistence import (
    PlanAdjustmentProposalPersistenceRejected,
    persist_optional_plan_adjustment_proposal,
)
from app.services.agent_plan_adjustment_proposal_trace import (
    attach_proposal_creation_decision,
    mark_proposal_persistence_failed,
    mark_proposal_persistence_rejected,
    mark_proposal_persisted,
)
from app.services.agent_plan_adjustment_proposals import (
    build_runtime_plan_adjustment_proposal,
)
from app.services.agent_domain_proposals import (
    DOMAIN_PROPOSAL_TYPES,
    create_agent_daily_meal_proposal,
    create_agent_meal_create_proposal,
    create_agent_meal_delete_proposal,
    create_agent_plan_creation_proposal,
    create_agent_plan_management_proposal,
    create_agent_profile_update_proposal,
    create_agent_weight_proposal,
    decide_agent_domain_proposal,
)
from app.services.agent_daily_meal_plans import (
    DailyMealPlanError,
    artifact_reference,
    generate_daily_meal_artifact,
)
from app.services.plan_management_proposals import (
    PLAN_MANAGEMENT_TYPES,
    PlanProposalError,
    decide_manual_plan_proposal,
)
from app.services.agent_structured_errors import safe_error_category
from app.services.agent_tool_registry_shadow_metric_adapter import (
    emit_registry_shadow_metrics,
)
from app.services.agent_tool_registry_read_enforcement import (
    apply_optional_registry_read_enforcement,
)
from app.services.agent_tool_registry_shadow_trace import (
    ToolRegistryShadowSession,
    attach_registry_shadow_report,
    create_registry_shadow_session,
)
from app.services.agent_trace import (
    add_stage_timing,
    build_initial_execution_trace,
    complete_execution_trace,
    terminate_execution_trace,
)
from app.services.agent_tools import TOOL_ID_BY_LANGCHAIN_NAME, build_read_tools
from app.services.ai_client import AIServiceError
from app.services.agent_response_style import (
    RESPONSE_STYLE_PROMPT,
    INTENT_UNAVAILABLE_REPLY,
    WRITE_UNAVAILABLE_REPLY,
    PROPOSAL_PENDING_REPLY,
    PROPOSAL_DECISION_UNCLEAR_REPLY,
    clarification_field_labels,
)


logger = logging.getLogger(__name__)
production_diagnostic_logger = logging.getLogger("uvicorn.error")


SYSTEM_PROMPT = """你是 Fitness Agent，一位中文健身对话助手。

能力边界：
- 一般健身知识可以直接回答；用户自己的资料、计划、训练和进度必须以工具结果为准。
- 训练计划、档案、健康、体重和饮食的写入只能通过服务端生成待确认提案，不能声称已经直接修改。
- 本回答阶段不能创建提案。普通查询或建议不得称为“提案”或“待确认记录”，也不得邀请用户
  确认、提交或应用当前建议；只有服务端已经返回提案卡片时，用户才有可确认对象。
- 工具无结果时明确说明，不得编造用户数据。
- 简单问题使用简洁文本；有结构化训练数据时先回答结论，再概括关键数据。

健康安全：
- 不进行医疗诊断、处方或治疗承诺。
- 出现胸痛、呼吸困难、晕厥、严重或急性疼痛等红旗信息时，优先建议停止训练并及时寻求专业医疗帮助。
- 有疼痛或伤病时，不建议盲目加量；先说明训练边界和不确定性。
""" + RESPONSE_STYLE_PROMPT


_PROPOSAL_NOT_CREATED_REPLY = (
    "这次没有生成可确认的调整提案，当前计划未作修改。"
    "你希望调整哪一部分？可以说明具体项目和目标值。"
)
_PROPOSAL_REJECTED_SAFE_ANSWER_REASON = (
    "proposal_creation_rejected_safe_answer"
)
_CURRENT_PROPOSAL_REFERENCE_PATTERN = re.compile(
    r"(?:(?:这|该|当前|上述|刚才|本)(?:份|个|次)?"
    r"(?:提案|方案|调整|变更|记录)|"
    r"(?:待确认|等待确认).{0,8}(?:提案|方案|记录)|"
    r"(?:提案|方案|记录).{0,8}(?:待确认|等待确认))"
)
_UNPERSISTED_PROPOSAL_STATE_PATTERN = re.compile(
    # Require an assertion about an actual object, not the generic term
    # '待确认提案' in an explanation of how confirmation works.
    r"(?:(?:这是|这里是|以上是|以下是|已有|当前有|现在有|有一[份个条]|"
    r"已(?:经)?(?:为你)?(?:生成|创建|准备)|刚(?:刚)?(?:生成|创建))"
    r"[^。！？!?；;\n]{0,24}?(?:待确认|等待确认|尚待确认)"
    r"[^。！？!?；;\n]{0,8}?(?:提案|方案|记录)|"
    r"(?:(?:这|该|当前|上述|刚才|本)(?:份|个|次)?(?:提案|方案|记录)|"
    r"(?:提案|方案|记录)(?:已(?:经)?(?:生成|创建)))"
    r"[^。！？!?；;\n]{0,12}?(?:待确认|等待确认|尚待确认)|"
    r"(?:提案|方案|记录)(?:仍然?|正|目前|现在|尚)?(?:处于)?"
    r"(?:待确认|等待确认|尚待确认)|"
    r"已(?:经)?(?:为你)?(?:生成|创建)[^。！？!?；;\n]{0,16}?"
    r"(?:提案|方案|记录)[^。！？!?；;\n]{0,12}?(?:待确认|等待确认|尚待确认))"
)
_PENDING_PROPOSAL_HEADING_PATTERN = re.compile(
    r"[ \t#*]*(?:(?:待确认|等待确认|尚待确认).{0,8}?(?:提案|方案|记录)|"
    r"(?:训练|计划|饮食|调整|健康|资料|体重){0,4}(?:提案|方案|记录)"
    r"[ \t（(：:·-]*(?:待确认|等待确认|尚待确认)[）)]?)"
    r"[ \t*：:。]*"
)
_QUOTED_REPLY_TEXT_PATTERN = re.compile(
    r'“[^”\n]*”|‘[^’\n]*’|"[^"\n]*"|\'[^\'\n]*\'|`[^`\n]*`'
)
_PROPOSAL_DEFINITION_PREFIX_PATTERN = re.compile(
    r"(?:意思|含义|定义)(?:通常|一般)?(?:是|指)[^，,。！？!?；;\n]*$"
)
_CONFIRMATION_ACTION = r"(?<!待)(?:确认|提交|应用|执行)(?!后|前|之后|之前)"
_CONFIRMATION_INVITATION_PATTERN = re.compile(
    r"(?:(?:需要|请|可以|要不要).{0,20}?" + _CONFIRMATION_ACTION
    + r"|是否(?:需要|要)?[ \t]*" + _CONFIRMATION_ACTION
    + r"|" + _CONFIRMATION_ACTION + r".{0,12}?(?:吗|[？?]))"
)
_SUPPORTED_PLAN_MUTATION_FIELDS = frozenset({
    "schedule.duration_weeks",
    "schedule.days_per_week",
    "exercise.sets",
    "exercise.reps",
    "exercise.rest_seconds",
    "exercise.recommended_weight_kg",
})
_UNTRUSTED_WRITE_FALLBACK_REASONS = frozenset({
    "model_disabled",
    "model_unconfigured",
    "model_timeout",
    "model_unavailable",
    "schema_validation_failed",
})


def _explicit_proposal_created_reply(
    *,
    before_weeks: int,
    after_weeks: int,
) -> str:
    return (
        f"待确认提案已准备好：计划周期将从{before_weeks}周"
        f"调整为{after_weeks}周，其他内容保持不变。当前计划尚未修改，"
        "请查看详情后确认。"
    )


def _mutation_proposal_created_reply(built: Any) -> str:
    payload = built.payload
    change = payload.changes[0]
    if change.change_type == "update_plan_schedule":
        if "days_per_week" in change.before.model_fields_set:
            summary = (
                f"每周训练频率将从{change.before.days_per_week}天调整为"
                f"{change.after.days_per_week}天"
            )
        else:
            summary = (
                f"计划周期将从{change.before.duration_weeks}周调整为"
                f"{change.after.duration_weeks}周"
            )
    else:
        exercise = next(
            item for item in payload.before.exercises
            if item.slot_key == change.stable_display_key
        )
        summary = f"将调整{exercise.exercise_name}的训练目标"
    return (
        f"待确认提案已准备好：{summary}。当前计划尚未修改，"
        "请查看详情后确认。"
    )


@dataclass(frozen=True)
class AgentRuntimeResult:
    reply: str
    run_id: str
    cards: list[dict[str, Any]] = field(default_factory=list)
    execution_trace: AgentExecutionTrace | None = None
    proposal: AgentProposalReference | None = None
    artifact: AgentArtifactReference | None = None


class AgentRunOwnershipLost(RuntimeError):
    """Raised when a stale worker attempt tries to persist a run result."""


def _proposal_reference_from_data(
    value: Any,
) -> AgentProposalReference | None:
    if not isinstance(value, dict):
        return None
    try:
        return AgentProposalReference.model_validate(value)
    except ValueError:
        return None


def _artifact_reference_from_data(
    value: Any,
) -> AgentArtifactReference | None:
    if not isinstance(value, dict):
        return None
    try:
        return AgentArtifactReference.model_validate(value)
    except ValueError:
        return None


def _persist_daily_meal_diagnostics(
    db: AsyncSession,
    *,
    run_id: str,
    evidence_audits: tuple[Any, ...],
    generation_attempts: tuple[Any, ...],
    optimization_attempts: tuple[Any, ...] = (),
) -> None:
    for index, audit in enumerate(evidence_audits, start=1):
        db.add(AgentToolCall(
            run_id=run_id,
            call_id=f"evidence:{run_id}:{index}",
            tool_name=audit.tool_id,
            arguments_data={"identity_source": "server_context"},
            result_data={
                "fields": list(audit.fields),
                "result_fingerprint": audit.result_fingerprint,
            },
            status="completed",
            duration_ms=audit.duration_ms,
        ))
    for audit in generation_attempts:
        db.add(AgentToolCall(
            run_id=run_id,
            call_id=f"model:{run_id}:daily-meal:{audit.attempt}",
            tool_name="agent.daily_meal_generator",
            arguments_data={
                "attempt": audit.attempt,
                "transport": audit.transport,
            },
            result_data=audit.result_data(),
            status=audit.status,
            error_code=audit.error_code,
            duration_ms=audit.duration_ms,
        ))
    for audit in optimization_attempts:
        db.add(AgentToolCall(
            run_id=run_id,
            call_id=f"optimizer:{run_id}:daily-meal:{audit.attempt}:{audit.mode}",
            tool_name="agent.daily_meal_optimizer",
            arguments_data={
                "attempt": audit.attempt,
                "mode": audit.mode,
            },
            result_data=audit.result_data(),
            status=audit.status,
            error_code=audit.error_code,
            duration_ms=audit.duration_ms,
        ))


def _proposal_reference_from_model(proposal: Any) -> AgentProposalReference:
    return AgentProposalReference(
        id=proposal.id,
        proposal_type=proposal.proposal_type,
        status=proposal.status,
        version=proposal.version,
        expires_at=proposal.expires_at,
        payload_fingerprint=proposal.payload_fingerprint,
    )


def _exposes_unpersisted_proposal(reply: str) -> bool:
    """Presentation check only; never supplies decision/write authority.

    References and invitations must belong to the same sentence. Quoted
    examples cannot themselves assert state or invite a decision, but quoting
    the object in an actual assertion/invitation must not hide that assertion.
    Keep offsets so '已生成“待确认提案”' and '请确认“这个提案”' still fail.
    """
    unquoted = _QUOTED_REPLY_TEXT_PATTERN.sub(lambda match: " " * len(match[0]), reply)
    for claim in _UNPERSISTED_PROPOSAL_STATE_PATTERN.finditer(reply):
        if not unquoted[claim.start():claim.start() + 1].strip():
            continue
        # A definition's complement describes the term, not current state.
        # Scope ends at the next clause; a subsequent claim must still fail.
        # First-person/for-you assertions remain claims even in that scope.
        clause_start = max((reply.rfind(char, 0, claim.start()) for char in "，,。！？!?；;\n"), default=-1) + 1
        prefix = reply[clause_start:claim.start()]
        definition = _PROPOSAL_DEFINITION_PREFIX_PATTERN.search(prefix)
        personal_claim = re.search(r"我|为你|帮你", prefix + claim[0])
        if not definition or personal_claim:
            return True
    for line in reply.splitlines():
        heading = _PENDING_PROPOSAL_HEADING_PATTERN.fullmatch(line)
        if heading:
            return True
    # Split using unquoted punctuation so punctuation inside a label cannot
    # join an unrelated earlier reference with a later confirmation condition.
    for sentence in re.finditer(r"[^。！？!?；;\n]+[。！？!?；;\n]?", unquoted):
        original = reply[sentence.start():sentence.end()]
        for invitation in _CONFIRMATION_INVITATION_PATTERN.finditer(sentence[0]):
            if _CURRENT_PROPOSAL_REFERENCE_PATTERN.search(sentence[0]):
                return True
            # A quoted object immediately following the invitation is still
            # its target. A quoted label earlier in a definition is not.
            target = original[invitation.start():invitation.end() + 16]
            if _CURRENT_PROPOSAL_REFERENCE_PATTERN.search(target):
                return True
    return False


def _normalize_unpersisted_proposal_result(
    *,
    reply: str,
    execution_trace: AgentExecutionTrace,
    proposal_reference: AgentProposalReference | None,
    proposal_expected: bool = False,
    intent_domain: str = "general",
    request_kind: str = "query",
) -> tuple[str, AgentExecutionTrace]:
    """Never expose a proposal terminal action without a durable proposal."""

    if proposal_reference is not None:
        return reply, execution_trace

    exposes_phantom_proposal = _exposes_unpersisted_proposal(reply)
    if (
        execution_trace.terminal_action != "proposal"
        and not proposal_expected
        and not exposes_phantom_proposal
    ):
        return reply, execution_trace

    if request_kind in {"query", "assessment"}:
        if intent_domain == "nutrition":
            safe_reply = (
                "这次还没有生成可确认的饮食记录提案，数据没有改动。"
                "要记录一餐，请补充餐次、食品和克数。"
            )
        elif intent_domain == "workout_plan":
            safe_reply = (
                "这次还没有生成可确认的训练计划提案，计划没有改动。"
                "要修改计划，请补充要调整的项目和目标值。"
            )
        else:
            safe_reply = (
                "这条回复没有对应的待确认提案，数据没有改动。"
                "需要处理提案时，请以实际的提案卡片和详情为准。"
            )
    else:
        safe_reply = _PROPOSAL_NOT_CREATED_REPLY

    mode_reasons = [
        reason
        for reason in execution_trace.mode_reasons
        if reason != _PROPOSAL_REJECTED_SAFE_ANSWER_REASON
    ][-7:]
    mode_reasons.append(_PROPOSAL_REJECTED_SAFE_ANSWER_REASON)
    return safe_reply, execution_trace.model_copy(update={
        "terminal_action": "answer",
        "mode_reasons": mode_reasons,
    })


def _log_proposal_creation_diagnostic(
    *,
    run_id: str,
    execution_trace: AgentExecutionTrace,
) -> None:
    diagnostic = execution_trace.proposal_creation
    if diagnostic is None:
        return
    production_diagnostic_logger.info(
        "agent_plan_adjustment_proposal_creation %s",
        json.dumps(
            {
                "eligible": diagnostic.eligible,
                "persisted": diagnostic.persisted,
                "persistence_reason_code": (
                    diagnostic.persistence_reason_code
                ),
                "persistence_status": diagnostic.persistence_status,
                "reason_code": diagnostic.reason_code,
                "run_id": run_id,
                "terminal_action": execution_trace.terminal_action,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ),
    )


def _finalize_registry_shadow_trace(
    trace: AgentExecutionTrace,
    session: ToolRegistryShadowSession | None,
) -> AgentExecutionTrace:
    if session is None:
        return trace
    try:
        if trace.observations:
            session.record_final_observations(trace)
        report = session.build_report()
    except Exception:  # pragma: no cover - report loss must not fail v1
        return trace
    try:
        emit_registry_shadow_metrics(
            report,
            enabled=(
                settings.AGENT_TOOL_REGISTRY_SHADOW_EMIT_METRICS
            ),
        )
    except Exception:  # pragma: no cover - defensive adapter boundary
        pass
    return attach_registry_shadow_report(
        trace,
        report,
        persist_trace=(
            settings.AGENT_TOOL_REGISTRY_SHADOW_PERSIST_TRACE
        ),
    )


async def _lock_run_ownership(
    db: AsyncSession,
    *,
    run_id: str,
    expected_attempt_count: int | None,
) -> None:
    if expected_attempt_count is None:
        return
    with db.no_autoflush:
        owned_run_id = await db.scalar(
            select(AgentRun.id)
            .where(
                AgentRun.id == run_id,
                AgentRun.status == "running",
                AgentRun.attempt_count == expected_attempt_count,
            )
            .with_for_update()
        )
    if owned_run_id is None:
        raise AgentRunOwnershipLost(
            f"Agent run ownership lost: run_id={run_id} "
            f"attempt={expected_attempt_count}"
        )


async def _mark_owned_run_failed(
    db: AsyncSession,
    *,
    run_id: str,
    expected_attempt_count: int | None,
    error_code: str,
    duration_ms: int,
    execution_trace: AgentExecutionTrace | None = None,
) -> None:
    await db.rollback()
    filters = [
        AgentRun.id == run_id,
        AgentRun.status == "running",
    ]
    if expected_attempt_count is not None:
        filters.append(AgentRun.attempt_count == expected_attempt_count)
    values: dict[str, Any] = {
        "status": "failed",
        "error_code": error_code,
        "completed_at": datetime.now(timezone.utc),
        "duration_ms": duration_ms,
        "lease_expires_at": None,
    }
    if execution_trace is not None:
        values["execution_mode"] = execution_trace.execution_mode
        values["execution_trace"] = execution_trace.model_dump(mode="json")
    result = await db.execute(
        update(AgentRun)
        .where(*filters)
        .values(**values)
    )
    if result.rowcount != 1 and expected_attempt_count is not None:
        await db.rollback()
        raise AgentRunOwnershipLost(
            f"Agent run ownership lost while failing: run_id={run_id} "
            f"attempt={expected_attempt_count}"
        )
    await db.commit()


def _build_model(
    *,
    temperature: float = 0.2,
    max_tokens: int | None = None,
) -> ChatOpenAI:
    if not settings.AGENT_ENABLED:
        raise AIServiceError("Agent 服务尚未启用")
    if not settings.DEEPSEEK_API_KEY:
        raise AIServiceError("AI 服务尚未配置，请先设置 DEEPSEEK_API_KEY")
    model_options: dict[str, Any] = {
        "model": settings.AGENT_MODEL,
        "api_key": settings.DEEPSEEK_API_KEY,
        "base_url": settings.DEEPSEEK_BASE_URL.rstrip("/"),
        "temperature": temperature,
        "timeout": settings.AGENT_TIMEOUT_SECONDS,
        "max_retries": 1,
        "use_responses_api": False,
    }
    if max_tokens is not None:
        model_options["max_tokens"] = max_tokens
    return ChatOpenAI(
        **model_options,
    )


def _message_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        text_parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                text_parts.append(block)
            elif isinstance(block, dict):
                value = block.get("text")
                if isinstance(value, str):
                    text_parts.append(value)
        return "\n".join(item for item in text_parts if item).strip()
    return ""


def _conversation_summary(reply: str) -> str:
    return re.sub(r"\s+", " ", reply).strip()[:200]


def _json_result(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        return content
    if isinstance(content, list):
        return {"items": content}
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except (TypeError, ValueError):
            return {"content": content}
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    return {"value": str(content)}


def _audit_result_summary(tool_id: str, content: Any) -> dict[str, Any]:
    result = _json_result(content)
    if tool_id == "profile.get_summary":
        return {
            "found": bool(result.get("found")),
            "fields_returned": sorted(
                key for key, value in result.items()
                if key != "found" and value is not None
            ),
        }
    if tool_id == "health.get_screening_summary":
        injuries = result.get("injuries")
        chronic = result.get("chronic_conditions")
        return {
            "found": bool(result.get("found")),
            "injury_count": len(injuries) if isinstance(injuries, list) else 0,
            "chronic_condition_count": len(chronic) if isinstance(chronic, list) else 0,
            "screening_completed": bool(result.get("screening_completed")),
        }
    if tool_id == "plan.get_active":
        plan = result.get("plan")
        return {
            "found": bool(result.get("found")),
            "plan_id": plan.get("id") if isinstance(plan, dict) else None,
            "exercise_count": (
                len(plan.get("exercises", [])) if isinstance(plan, dict) else 0
            ),
        }
    if tool_id == "workout.get_next":
        exercises = result.get("exercises")
        return {
            "found": bool(result.get("found")),
            "plan_id": result.get("plan_id"),
            "day_of_week": result.get("day_of_week"),
            "exercise_count": len(exercises) if isinstance(exercises, list) else 0,
        }
    if tool_id == "workout.get_active_session":
        session = result.get("session")
        return {
            "found": bool(result.get("found")),
            "session_id": session.get("id") if isinstance(session, dict) else None,
            "exercise_count": (
                len(session.get("exercises", [])) if isinstance(session, dict) else 0
            ),
        }
    if tool_id == "workout.list_history":
        return {"count": result.get("count", 0)}
    if tool_id == "workout.get_progress":
        return {
            key: result.get(key)
            for key in (
                "weeks",
                "total_sessions",
                "total_sets",
                "total_reps",
                "total_volume_kg",
            )
        }
    if tool_id == "weight.list_history":
        return {"count": result.get("count", 0)}
    if tool_id in {"nutrition.get_today", "nutrition.list_history"}:
        return {
            key: result.get(key)
            for key in (
                "count",
                "date",
                "total_calories",
                "total_protein_g",
                "total_carbs_g",
                "total_fat_g",
            )
            if key in result
        }
    if tool_id == "food.search":
        return {"count": result.get("count", 0)}
    return {"keys_returned": sorted(result)}


def _extract_agent_output(result: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    messages = result.get("messages")
    if not isinstance(messages, list) or not messages:
        raise AIServiceError("Agent 未返回有效内容")
    reply = _message_content_text(getattr(messages[-1], "content", ""))
    if not reply:
        raise AIServiceError("Agent 未返回有效内容")

    cards: list[dict[str, Any]] = []
    for message in messages:
        if getattr(message, "type", None) != "tool":
            continue
        tool_name = getattr(message, "name", None)
        canonical_id = TOOL_ID_BY_LANGCHAIN_NAME.get(tool_name)
        if canonical_id:
            cards.append({
                "type": canonical_id,
                "data": _json_result(getattr(message, "content", "")),
            })
    return reply, cards


def _tool_call_records(result: dict[str, Any], run_id: str) -> list[AgentToolCall]:
    messages = result.get("messages")
    if not isinstance(messages, list):
        return []
    outputs: dict[str, Any] = {}
    for message in messages:
        if getattr(message, "type", None) == "tool":
            call_id = getattr(message, "tool_call_id", None)
            if isinstance(call_id, str):
                outputs[call_id] = getattr(message, "content", "")

    records: list[AgentToolCall] = []
    for message in messages:
        calls = getattr(message, "tool_calls", None)
        if not isinstance(calls, list):
            continue
        for call in calls:
            if not isinstance(call, dict):
                continue
            call_id = call.get("id") if isinstance(call.get("id"), str) else None
            langchain_name = call.get("name")
            if not isinstance(langchain_name, str):
                continue
            canonical_id = TOOL_ID_BY_LANGCHAIN_NAME.get(
                langchain_name, langchain_name
            )
            arguments = call.get("args")
            records.append(AgentToolCall(
                run_id=run_id,
                call_id=call_id,
                tool_name=canonical_id,
                arguments_data=(
                    arguments if isinstance(arguments, dict) else {"value": arguments}
                ),
                result_data=_audit_result_summary(
                    canonical_id,
                    outputs.get(call_id, ""),
                ),
                status="completed",
            ))
    return records


def _usage_totals(result: dict[str, Any]) -> tuple[int | None, int | None]:
    messages = result.get("messages")
    if not isinstance(messages, list):
        return None, None
    input_tokens = 0
    output_tokens = 0
    found = False
    for message in messages:
        usage = getattr(message, "usage_metadata", None)
        if not isinstance(usage, dict):
            continue
        found = True
        input_tokens += int(usage.get("input_tokens") or 0)
        output_tokens += int(usage.get("output_tokens") or 0)
    return (input_tokens, output_tokens) if found else (None, None)


def _clarification_reply(resolution: IntentResolution) -> str:
    if resolution.clarification_question:
        return resolution.clarification_question
    if resolution.missing_slots:
        fields = "、".join(clarification_field_labels(resolution.missing_slots))
        return f"还需要补充：{fields}。你可以直接回复这些信息。"
    return "你想了解哪方面？可以先说说训练、饮食，或你遇到的具体问题。"


def _mutation_missing_slots(resolution: IntentResolution) -> list[str]:
    if resolution.intent_domain == "nutrition":
        return (
            ["餐次、食品和克数"]
            if resolution.requested_effect == "create"
            else ["要删除的具体餐次记录"]
        )
    if resolution.intent_domain == "workout_plan":
        return ["要修改的计划项目和具体目标值"]
    if resolution.intent_domain in {"profile", "health"}:
        return ["要修改的资料字段和具体目标值"]
    return ["写入目标的完整字段、对象和数值"]


_WEEKDAY_SELECTION_ALIASES = {
    1: ("周一", "星期一", "礼拜一", "周1", "星期1", "礼拜1"),
    2: ("周二", "星期二", "礼拜二", "周2", "星期2", "礼拜2"),
    3: ("周三", "星期三", "礼拜三", "周3", "星期3", "礼拜3"),
    4: ("周四", "星期四", "礼拜四", "周4", "星期4", "礼拜4"),
    5: ("周五", "星期五", "礼拜五", "周5", "星期5", "礼拜5"),
    6: ("周六", "星期六", "礼拜六", "周6", "星期6", "礼拜6"),
    7: (
        "周日", "周天", "星期日", "星期天", "礼拜日", "礼拜天",
        "周7", "星期7", "礼拜7",
    ),
}
_WEEKDAY_SELECTION_LABELS = {
    1: "周一", 2: "周二", 3: "周三", 4: "周四",
    5: "周五", 6: "周六", 7: "周日",
}
_ALL_TARGET_SELECTION_MARKERS = (
    "全部", "所有", "都改", "都调整", "都设置", "都应用", "每个都",
)
_EXACT_ALL_TARGET_SELECTIONS = {
    "全部", "所有", "全都", "都改", "全部都改", "全部调整",
    "两个都改", "两个都调整", "这些都改", "每个都改",
}
_ORDINAL_SELECTIONS = {
    "第一个": 0,
    "第一项": 0,
    "第1个": 0,
    "第1项": 0,
    "第二个": 1,
    "第二项": 1,
    "第2个": 1,
    "第2项": 1,
    "第三个": 2,
    "第三项": 2,
    "第3个": 2,
    "第3项": 2,
}
_STRUCTURED_CLARIFICATION_TTL = timedelta(hours=24)


def _structured_clarification_is_current(
    pending_clarification: dict[str, Any] | None,
) -> bool:
    if not pending_clarification:
        return False
    expires_at = pending_clarification.get("expires_at")
    if not isinstance(expires_at, str) or not expires_at:
        return False
    try:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed > datetime.now(timezone.utc)


def _resolve_plan_target_selection(
    message: str,
    pending_clarification: dict[str, Any] | None,
) -> IntentResolverOutcome | None:
    """Resolve only a server-generated plan occurrence choice.

    This is a constrained state-machine continuation, not general-language
    intent routing. Candidate ids and the original changes must both come from
    the previously persisted server clarification state.
    """
    if not pending_clarification:
        return None
    context = pending_clarification.get("clarification_context")
    if not isinstance(context, dict) or context.get("clarification_type") != (
        "plan_exercise_occurrence"
    ):
        return None
    inherited = pending_clarification_to_resolution(pending_clarification)
    if (
        inherited is None
        or inherited.intent_domain != "workout_plan"
        or inherited.request_kind != "mutation"
    ):
        return None
    raw_candidates = context.get("candidates")
    if not isinstance(raw_candidates, list) or not raw_candidates:
        return None
    candidates: list[dict[str, Any]] = []
    for raw in raw_candidates[:7]:
        if not isinstance(raw, dict):
            return None
        item_key = raw.get("item_key")
        day = raw.get("day_of_week")
        name = raw.get("exercise_name")
        if (
            not isinstance(item_key, str)
            or not item_key.startswith("planned:")
            or isinstance(day, bool)
            or not isinstance(day, int)
            or day not in _WEEKDAY_SELECTION_LABELS
            or not isinstance(name, str)
            or not name.strip()
        ):
            return None
        candidates.append({
            "item_key": item_key,
            "day_of_week": day,
            "exercise_name": name.strip(),
        })

    ambiguous_reference = context.get("target_reference")
    if not isinstance(ambiguous_reference, str) or not ambiguous_reference:
        return None
    normalized = re.sub(r"[\s。！？!?，,、；;：:]", "", message.lower())
    if not normalized or len(normalized) > 120:
        return None
    selected: list[dict[str, Any]] = []
    candidate_names = {
        re.sub(r"\s", "", candidate["exercise_name"].lower())
        for candidate in candidates
    }
    all_choice = (
        normalized in _EXACT_ALL_TARGET_SELECTIONS
        or any(marker in normalized for marker in _ALL_TARGET_SELECTION_MARKERS)
        and (
            any(name in normalized for name in candidate_names)
            or any(pointer in normalized for pointer in ("这两个", "这些", "每个"))
        )
    )
    if all_choice:
        selected = candidates
    else:
        selected_days = {
            day
            for day, aliases in _WEEKDAY_SELECTION_ALIASES.items()
            if any(alias in normalized for alias in aliases)
        }
        selection_remainder = normalized
        for aliases in _WEEKDAY_SELECTION_ALIASES.values():
            for alias in aliases:
                selection_remainder = selection_remainder.replace(alias, "")
        for name in candidate_names:
            selection_remainder = selection_remainder.replace(name, "")
        for filler in (
            "的", "那个", "这个", "一个", "和", "与", "及", "都修改",
            "都调整", "都设置", "都改", "修改", "调整", "设置", "选择",
            "训练日", "动作", "改", "选", "要", "就", "吧",
        ):
            selection_remainder = selection_remainder.replace(filler, "")
        if selected_days and not selection_remainder:
            selected = [
                candidate for candidate in candidates
                if candidate["day_of_week"] in selected_days
            ]
        else:
            ordinal = next(
                (
                    index
                    for marker, index in _ORDINAL_SELECTIONS.items()
                    if marker in normalized
                ),
                None,
            )
            if ordinal is not None and ordinal < len(candidates):
                selected = [candidates[ordinal]]
    if not selected:
        return None

    expanded_changes: list[ChangeRequest] = []
    replaced = False
    for change in inherited.change_requests:
        if (
            change.resource == "workout_plan"
            and change.target_reference == ambiguous_reference
        ):
            replaced = True
            expanded_changes.extend(
                change.model_copy(update={"target_reference": candidate["item_key"]})
                for candidate in selected
            )
        else:
            expanded_changes.append(change)
    if not replaced or len(expanded_changes) > 12:
        return None
    selection_text = "、".join(
        f"{_WEEKDAY_SELECTION_LABELS[item['day_of_week']]}的{item['exercise_name']}"
        for item in selected
    )
    resolution = inherited.model_copy(update={
        "change_requests": expanded_changes,
        "resolved_query": f"{inherited.resolved_query}；目标位置：{selection_text}"[:4000],
        "missing_slots": [],
        "clarification_required": False,
        "clarification_question": None,
        "confidence": min(max(inherited.confidence, 0.9), 0.99),
    })
    return IntentResolverOutcome(
        resolution=resolution,
        source="rules",
        fallback_reason="persisted_plan_target_selection",
    )


def _plan_target_choice_card(
    context: dict[str, Any] | None,
    *,
    origin_run_id: str,
) -> dict[str, Any] | None:
    if not isinstance(context, dict) or context.get("clarification_type") != (
        "plan_exercise_occurrence"
    ):
        return None
    raw_candidates = context.get("candidates")
    if not isinstance(raw_candidates, list) or len(raw_candidates) < 2:
        return None
    options: list[dict[str, Any]] = []
    all_ids: list[str] = []
    for raw in raw_candidates[:7]:
        if not isinstance(raw, dict):
            return None
        item_key = raw.get("item_key")
        day = raw.get("day_of_week")
        name = raw.get("exercise_name")
        if (
            not isinstance(item_key, str)
            or not item_key.startswith("planned:")
            or not isinstance(day, int)
            or day not in _WEEKDAY_SELECTION_LABELS
            or not isinstance(name, str)
            or not name.strip()
        ):
            return None
        all_ids.append(item_key)
        options.append({
            "label": f"{_WEEKDAY_SELECTION_LABELS[day]}的{name.strip()}",
            "message": f"选择{_WEEKDAY_SELECTION_LABELS[day]}的{name.strip()}",
            "action": {
                "action": "select_plan_occurrences",
                "origin_run_id": origin_run_id,
                "choice_ids": [item_key],
            },
        })
    options.append({
        "label": "全部匹配动作",
        "message": "修改全部匹配动作",
        "action": {
            "action": "select_plan_occurrences",
            "origin_run_id": origin_run_id,
            "choice_ids": all_ids,
        },
    })
    return {
        "type": "clarification_choices",
        "data": {
            "title": "请选择要修改的动作",
            "options": options,
        },
    }


def _resolve_structured_plan_target_selection(
    action: AgentClarificationActionRequest,
    pending_clarification: dict[str, Any] | None,
) -> IntentResolverOutcome | None:
    if not _structured_clarification_is_current(pending_clarification):
        return None
    if pending_clarification.get("origin_run_id") != action.origin_run_id:
        return None
    context = pending_clarification.get("clarification_context")
    if not isinstance(context, dict) or context.get("clarification_type") != (
        "plan_exercise_occurrence"
    ):
        return None
    inherited = pending_clarification_to_resolution(pending_clarification)
    if (
        inherited is None
        or inherited.intent_domain != "workout_plan"
        or inherited.request_kind != "mutation"
    ):
        return None
    raw_candidates = context.get("candidates")
    if not isinstance(raw_candidates, list):
        return None
    candidates = {
        str(item.get("item_key")): item
        for item in raw_candidates
        if isinstance(item, dict)
        and isinstance(item.get("item_key"), str)
        and str(item["item_key"]).startswith("planned:")
    }
    choice_ids = list(dict.fromkeys(action.choice_ids))
    if len(choice_ids) != len(action.choice_ids) or any(
        item_key not in candidates for item_key in choice_ids
    ):
        return None
    ambiguous_reference = context.get("target_reference")
    if not isinstance(ambiguous_reference, str) or not ambiguous_reference:
        return None

    expanded_changes: list[ChangeRequest] = []
    replaced = False
    for change in inherited.change_requests:
        if (
            change.resource == "workout_plan"
            and change.target_reference == ambiguous_reference
        ):
            replaced = True
            expanded_changes.extend(
                change.model_copy(update={"target_reference": item_key})
                for item_key in choice_ids
            )
        else:
            expanded_changes.append(change)
    if not replaced or len(expanded_changes) > 12:
        return None
    labels = [
        f"{_WEEKDAY_SELECTION_LABELS[int(candidates[item_key]['day_of_week'])]}"
        f"的{candidates[item_key]['exercise_name']}"
        for item_key in choice_ids
    ]
    resolution = inherited.model_copy(update={
        "change_requests": expanded_changes,
        "resolved_query": f"{inherited.resolved_query}；目标位置：{'、'.join(labels)}"[:4000],
        "missing_slots": [],
        "clarification_required": False,
        "clarification_question": None,
        "confidence": 1.0,
    })
    return IntentResolverOutcome(
        resolution=resolution,
        source="rules",
        fallback_reason="structured_plan_target_selection",
    )


HIGH_RISK_REPLY = (
    "你描述的情况可能属于需要优先处理的健康警示。请立即停止训练，不要继续加量或硬撑；"
    "如果正在出现胸痛、呼吸困难、晕厥或严重急性疼痛，请及时联系急救或尽快就医。"
    "我不能在小程序里替代医生诊断。"
)


async def _conversation_action_state(
    db: AsyncSession,
    *,
    user_id: str,
    conversation_id: str,
) -> dict[str, int]:
    """Return non-sensitive state needed to disambiguate state-machine verbs."""
    moment = datetime.now(timezone.utc)
    active_artifact_count = await db.scalar(
        select(func.count(AgentArtifact.id)).where(
            AgentArtifact.user_id == user_id,
            AgentArtifact.conversation_id == conversation_id,
            AgentArtifact.artifact_type == "daily_meal_plan_v1",
            AgentArtifact.status == "active",
            AgentArtifact.expires_at > moment,
        )
    )
    pending_proposal_count = await db.scalar(
        select(func.count(AgentProposal.id)).where(
            AgentProposal.user_id == user_id,
            AgentProposal.conversation_id == conversation_id,
            AgentProposal.status == "pending_confirmation",
            or_(
                AgentProposal.expires_at.is_(None),
                AgentProposal.expires_at > moment,
            ),
        )
    )
    return {
        "active_daily_meal_artifact_count": int(active_artifact_count or 0),
        "pending_proposal_count": int(pending_proposal_count or 0),
    }


def _structured_artifact_action_outcome(
    action: AgentArtifactActionRequest,
) -> IntentResolverOutcome:
    """Translate a trusted card action into the existing mutation contract."""
    resolution = IntentResolution(
        primary_intent="nutrition_today_query",
        intent_domain="nutrition",
        request_kind="mutation",
        requested_effect="create",
        change_requests=[ChangeRequest(
            resource="nutrition",
            operation="create",
            field_path="daily_meal_plan.save",
            target_reference=action.artifact_id,
            value={
                "expected_version": action.expected_version,
                "payload_fingerprint": action.payload_fingerprint,
            },
        )],
        requested_output="answer",
        resolved_query="将指定的全天饮食方案保存为待确认提案",
        subtasks=["校验指定全天饮食方案", "生成待确认提案"],
        risk_level="low",
        confidence=1.0,
    )
    return IntentResolverOutcome(
        resolution=resolution,
        source="rules",
        fallback_reason="structured_artifact_action",
    )


async def _create_structured_mutation_proposal(
    db: AsyncSession,
    *,
    run: AgentRun,
    conversation: AgentConversation,
    resolution: IntentResolution,
) -> AgentProposalReference:
    changes = list(resolution.change_requests)
    common = {
        "db": db,
        "user_id": run.user_id,
        "conversation_id": conversation.id,
        "run_id": run.id,
        "changes": changes,
    }
    if resolution.intent_domain == "workout_plan":
        if resolution.requested_effect == "create":
            reference = await create_agent_plan_creation_proposal(
                enabled=settings.AGENT_PLAN_MANAGEMENT_PROPOSALS_ENABLED,
                **common,
            )
        elif resolution.requested_effect in {"update", "delete"}:
            deletes_entire_plan = (
                resolution.requested_effect == "delete"
                and all(
                    change.operation == "delete"
                    and change.field_path in {None, "plan", "workout_plan"}
                    for change in changes
                )
            )
            reference = await create_agent_plan_management_proposal(
                enabled=settings.AGENT_PLAN_MANAGEMENT_PROPOSALS_ENABLED,
                effect="delete" if deletes_entire_plan else "update",
                **common,
            )
        else:
            raise PlanProposalError(
                "proposal_change_unsupported", "这项计划操作暂不支持", status_code=422
            )
    elif resolution.intent_domain in {"profile", "health"}:
        if any(
            change.operation == "create"
            and change.field_path in {
                "weight_log.weight_kg", "profile.weight_kg", "weight_kg"
            }
            for change in changes
        ):
            reference = await create_agent_weight_proposal(
                enabled=settings.AGENT_WEIGHT_PROPOSALS_ENABLED,
                **common,
            )
        else:
            reference = await create_agent_profile_update_proposal(
                enabled=settings.AGENT_PROFILE_PROPOSALS_ENABLED,
                **common,
            )
    elif resolution.intent_domain == "nutrition":
        if resolution.requested_effect == "create":
            saves_daily_artifact = any(
                change.resource == "nutrition"
                and change.operation == "create"
                and change.field_path == "daily_meal_plan.save"
                for change in changes
            )
            if saves_daily_artifact:
                save_change = next(
                    change
                    for change in changes
                    if change.resource == "nutrition"
                    and change.operation == "create"
                    and change.field_path == "daily_meal_plan.save"
                )
                action_data = (
                    save_change.value
                    if isinstance(save_change.value, dict)
                    else {}
                )
                artifact_id = (
                    save_change.target_reference
                    if save_change.target_reference
                    != "latest_active_artifact_in_conversation"
                    else None
                )
                reference = await create_agent_daily_meal_proposal(
                    db=db,
                    enabled=settings.AGENT_NUTRITION_PROPOSALS_ENABLED,
                    user_id=run.user_id,
                    conversation_id=conversation.id,
                    run_id=run.id,
                    artifact_id=artifact_id,
                    expected_artifact_version=action_data.get(
                        "expected_version"
                    ),
                    expected_payload_fingerprint=action_data.get(
                        "payload_fingerprint"
                    ),
                )
            else:
                reference = await create_agent_meal_create_proposal(
                    enabled=settings.AGENT_NUTRITION_PROPOSALS_ENABLED,
                    **common,
                )
        elif resolution.requested_effect == "delete":
            reference = await create_agent_meal_delete_proposal(
                enabled=settings.AGENT_NUTRITION_PROPOSALS_ENABLED,
                **common,
            )
        else:
            raise PlanProposalError(
                "proposal_change_unsupported",
                "暂不支持修改既有餐次；可以新增或删除整条饮食记录",
                status_code=422,
            )
    else:
        raise PlanProposalError(
            "proposal_change_unsupported",
            "已识别写入请求，但该领域目前不支持写入",
            status_code=422,
        )
    return AgentProposalReference.model_validate(reference.model_dump(mode="json"))


from app.services.agent_query_reports import report_for_resolution, execute_query_report


async def _load_history(
    db: AsyncSession,
    *,
    conversation_id: str,
    before_run: AgentRun,
) -> list[dict[str, str]]:
    if conversation_id != before_run.conversation_id:
        return []
    history_run = aliased(AgentRun)
    filters = [
        AgentMessage.conversation_id == conversation_id,
        AgentMessage.role.in_(("user", "assistant")),
        or_(
            and_(
                AgentMessage.run_id.is_(None),
                AgentMessage.created_at <= before_run.queued_at,
            ),
            history_run.queued_at < before_run.queued_at,
            and_(
                history_run.queued_at == before_run.queued_at,
                history_run.id < before_run.id,
            ),
        ),
    ]
    verified_read = select(AgentToolCall.id).where(
        AgentToolCall.run_id == history_run.id,
        AgentToolCall.tool_name == "workout.get_progress",
        AgentToolCall.status == "completed",
        AgentToolCall.result_data["total_sessions"].as_integer().is_not(None),
    ).exists().correlate(history_run)
    rows = list((await db.execute(
        select(AgentMessage, history_run.id, history_run.primary_intent,
               history_run.resolved_query, history_run.request_kind, history_run.requested_effect,
               history_run.risk_level, history_run.status, history_run.clarification_required,
               history_run.tool_allowlist, verified_read)
        .outerjoin(history_run, and_(
            AgentMessage.run_id == history_run.id,
            history_run.user_id == before_run.user_id,
            history_run.conversation_id == conversation_id,
        ))
        .where(*filters)
        .order_by(AgentMessage.created_at.desc())
        .limit(settings.AGENT_MAX_HISTORY_MESSAGES)
    )).all())
    rows.reverse()
    history = []
    for item, run_id, primary, query, kind, effect, risk, status, clarify, allowlist, success in rows:
        entry = {"role": item.role, "content": item.content}
        if (item.role == "assistant" and status == "completed" and not clarify and success
                and kind in {"query", "assessment"} and effect == "read" and risk == "low"
                and primary == "workout_progress_query"
                and allowlist == ["workout.get_progress"]):
            entry["query_context"] = {
                "source_run_id": run_id, "primary_intent": primary, "resolved_query": query,
                "request_kind": kind, "requested_effect": effect, "risk_level": risk,
                "successful_query": True,
            }
        history.append(entry)
    return history


async def invoke_langchain_agent(
    db: AsyncSession,
    *,
    user_id: str,
    history: list[dict[str, str]],
    user_message: str,
    tool_allowlist: list[str],
    resolved_query: str | None = None,
    subtasks: list[str] | None = None,
    shadow_session: ToolRegistryShadowSession | None = None,
    resolution: IntentResolution | None = None,
) -> dict[str, Any]:
    tools = build_read_tools(
        db,
        user_id=user_id,
        allowlist=tool_allowlist,
    )
    if shadow_session is not None:
        shadow_session.record_constructed_tools(tools, tool_allowlist)
    report = report_for_resolution(resolution, tool_allowlist) if resolution else None
    if report is not None:
        return await execute_query_report(report, tools)
    model = _build_model()
    agent = create_agent(
        model=model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        name="fitness_agent_v1",
    )
    execution_message = user_message
    normalized_query = (resolved_query or "").strip()
    normalized_subtasks = [item.strip() for item in (subtasks or []) if item.strip()]
    if normalized_query and (
        normalized_query != user_message.strip() or normalized_subtasks
    ):
        execution_message = (
            f"用户原始表达：{user_message}\n"
            f"经服务端对话理解层消解后的请求：{normalized_query}\n"
        )
        if normalized_subtasks:
            execution_message += (
                "本轮需要覆盖的语义目标："
                f"{'；'.join(normalized_subtasks)}\n"
            )
        execution_message += "请围绕消解后的请求完成回答，不要扩展到无关目标。"
    return await agent.ainvoke(
        {"messages": [*[{"role": item["role"], "content": item["content"]} for item in history], {"role": "user", "content": execution_message}]},
        config={"recursion_limit": settings.AGENT_RECURSION_LIMIT},
    )


async def execute_agent_run(
    db: AsyncSession,
    *,
    run: AgentRun,
    conversation: AgentConversation,
    user_message: str,
    artifact_action: dict[str, Any] | None = None,
    clarification_action: dict[str, Any] | None = None,
    expected_attempt_count: int | None = None,
) -> AgentRuntimeResult:
    started = time.perf_counter()
    execution_trace: AgentExecutionTrace | None = None
    shadow_session: ToolRegistryShadowSession | None = None
    try:
        existing = await db.scalar(
            select(AgentMessage).where(
                AgentMessage.run_id == run.id,
                AgentMessage.role == "assistant",
            )
        )
        if existing is not None:
            cards = existing.content_data.get("cards", [])
            proposal_reference = _proposal_reference_from_data(
                existing.content_data.get("proposal")
            )
            artifact_reference_value = _artifact_reference_from_data(
                existing.content_data.get("artifact")
            )
            if run.execution_trace:
                execution_trace = AgentExecutionTrace.model_validate(
                    run.execution_trace
                )
            await _lock_run_ownership(
                db,
                run_id=run.id,
                expected_attempt_count=expected_attempt_count,
            )
            run.status = "completed"
            run.completed_at = run.completed_at or datetime.now(timezone.utc)
            run.lease_expires_at = None
            await db.commit()
            return AgentRuntimeResult(
                reply=existing.content,
                run_id=run.id,
                cards=cards if isinstance(cards, list) else [],
                execution_trace=execution_trace,
                proposal=proposal_reference,
                artifact=artifact_reference_value,
            )

        shadow_session = create_registry_shadow_session(
            run_id=run.id,
            enabled=settings.AGENT_TOOL_REGISTRY_SHADOW_ENABLED,
            sample_rate=settings.AGENT_TOOL_REGISTRY_SHADOW_SAMPLE_RATE,
        )
        history = await _load_history(
            db,
            conversation_id=conversation.id,
            before_run=run,
        )
        pending_clarification_state = (
            dict(conversation.pending_clarification)
            if conversation.pending_clarification
            else None
        )
        conversation_action_state = await _conversation_action_state(
            db,
            user_id=run.user_id,
            conversation_id=conversation.id,
        )
        structured_choice_invalid = False
        if clarification_action is not None:
            parsed_action = AgentClarificationActionRequest.model_validate(
                clarification_action
            )
            intent_outcome = _resolve_structured_plan_target_selection(
                parsed_action,
                pending_clarification_state,
            )
            if intent_outcome is None:
                structured_choice_invalid = True
                inherited = pending_clarification_to_resolution(
                    pending_clarification_state or {}
                )
                intent_outcome = IntentResolverOutcome(
                    resolution=(inherited or IntentResolution(
                        primary_intent="plan_query",
                        intent_domain="workout_plan",
                        request_kind="query",
                        requested_effect="read",
                        resolved_query="重新选择要修改的计划动作",
                        risk_level="low",
                        confidence=1.0,
                    )),
                    source="rules",
                    fallback_reason="structured_clarification_invalid",
                )
        elif artifact_action is not None:
            intent_outcome = _structured_artifact_action_outcome(
                AgentArtifactActionRequest.model_validate(artifact_action)
            )
        else:
            intent_outcome = _resolve_plan_target_selection(
                user_message,
                pending_clarification_state,
            )
            if intent_outcome is None:
                intent_outcome = await resolve_intent_with_fallback(
                    user_message,
                    context_messages=history,
                    pending_clarification=pending_clarification_state,
                    conversation_action_state=conversation_action_state,
                )
        resolution = intent_outcome.resolution
        legacy_explicit_command = parse_explicit_plan_adjustment_command(
            user_message
        )
        legacy_tool_allowlist = route_tools(resolution)
        if shadow_session is not None:
            shadow_session.record_route(resolution, legacy_tool_allowlist)
        enforcement = apply_optional_registry_read_enforcement(
            resolution=resolution,
            legacy_tool_ids=legacy_tool_allowlist,
            enabled=settings.AGENT_TOOL_REGISTRY_ENFORCE_READS_ENABLED,
            run_id=run.id,
        )
        tool_allowlist = list(enforcement.tool_allowlist)
        execution_trace = build_initial_execution_trace(
            resolution,
            tool_allowlist,
            intent_outcome,
            proposal_creation_enabled=(
                settings.AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED
                or settings.AGENT_PLAN_MANAGEMENT_PROPOSALS_ENABLED
                or settings.AGENT_PROFILE_PROPOSALS_ENABLED
                or settings.AGENT_WEIGHT_PROPOSALS_ENABLED
                or settings.AGENT_NUTRITION_PROPOSALS_ENABLED
            ),
        )
        run.status = "running"
        run.primary_intent = resolution.primary_intent
        run.intent_domain = resolution.intent_domain or "general"
        run.request_kind = resolution.request_kind
        run.requested_effect = resolution.requested_effect
        run.change_requests = [
            item.model_dump(mode="json") for item in resolution.change_requests
        ]
        run.evidence_requirements = list(resolution.evidence_requirements)
        run.requested_output = resolution.requested_output
        run.resolved_query = resolution.resolved_query
        run.references = [item.model_dump() for item in resolution.references]
        run.expanded_intents = resolution.expanded_intents
        run.subtasks = resolution.subtasks
        run.missing_slots = resolution.missing_slots
        run.tool_allowlist = tool_allowlist
        run.execution_mode = execution_trace.execution_mode
        run.execution_trace = execution_trace.model_dump(mode="json")
        run.risk_level = resolution.risk_level
        run.clarification_required = resolution.clarification_required
        run.clarification_question = resolution.clarification_question
        run.understanding_version = "v6"
        run.intent_source = intent_outcome.source
        run.intent_confidence = resolution.confidence
        run.intent_attempt_count = intent_outcome.attempt_count
        run.intent_fallback_reason = intent_outcome.fallback_reason
        run.intent_error_category = intent_outcome.error_category
        run.model_name = settings.AGENT_MODEL

        if resolution.risk_level == "high":
            conversation.pending_clarification = {}
        elif resolution.clarification_required:
            conversation.pending_clarification = {
                "origin_run_id": run.id,
                "original_message": user_message,
                "resolved_query": resolution.resolved_query,
                "references": [
                    item.model_dump() for item in resolution.references
                ],
                "primary_intent": resolution.primary_intent,
                "intent_domain": resolution.intent_domain,
                "request_kind": resolution.request_kind,
                "requested_effect": resolution.requested_effect,
                "change_requests": [
                    item.model_dump(mode="json")
                    for item in resolution.change_requests
                ],
                "evidence_requirements": list(resolution.evidence_requirements),
                "requested_output": resolution.requested_output,
                "expanded_intents": resolution.expanded_intents,
                "subtasks": resolution.subtasks,
                "missing_slots": resolution.missing_slots,
                "clarification_question": resolution.clarification_question,
                "risk_level": resolution.risk_level,
                "confidence": resolution.confidence,
                "understanding_version": "v6",
            }
        else:
            conversation.pending_clarification = {}
        await _lock_run_ownership(
            db,
            run_id=run.id,
            expected_attempt_count=expected_attempt_count,
        )
        await db.commit()

        async def complete_semantic_short_circuit(
            reply: str,
            *,
            terminal_action: str = "answer",
            termination_reason: str,
            content_data: dict[str, Any] | None = None,
            missing_slots: list[str] | None = None,
            clarification_context: dict[str, Any] | None = None,
        ) -> AgentRuntimeResult:
            nonlocal execution_trace
            execution_trace = terminate_execution_trace(
                execution_trace,
                terminal_action=terminal_action,
                termination_reason=termination_reason,
            ).model_copy(update={
                "mode_reasons": [
                    *execution_trace.mode_reasons,
                    termination_reason,
                ][-8:],
            })
            execution_trace = _finalize_registry_shadow_trace(
                execution_trace,
                shadow_session,
            )
            await _lock_run_ownership(
                db,
                run_id=run.id,
                expected_attempt_count=expected_attempt_count,
            )
            db.add(AgentMessage(
                conversation_id=conversation.id,
                run_id=run.id,
                role="assistant",
                content=reply,
                content_data=content_data or {},
            ))
            now = datetime.now(timezone.utc)
            run.status = "completed"
            run.completed_at = now
            run.duration_ms = round((time.perf_counter() - started) * 1000)
            run.lease_expires_at = None
            run.execution_trace = execution_trace.model_dump(mode="json")
            if terminal_action == "clarify":
                slots = missing_slots or ["要处理的具体对象"]
                run.clarification_required = True
                run.clarification_question = reply
                run.missing_slots = slots
                conversation.pending_clarification = {
                    "origin_run_id": run.id,
                    "original_message": user_message,
                    "resolved_query": resolution.resolved_query,
                    "primary_intent": resolution.primary_intent,
                    "intent_domain": resolution.intent_domain,
                    "request_kind": resolution.request_kind,
                    "requested_effect": resolution.requested_effect,
                    "change_requests": [
                        item.model_dump(mode="json")
                        for item in resolution.change_requests
                    ],
                    "evidence_requirements": list(resolution.evidence_requirements),
                    "requested_output": resolution.requested_output,
                    "missing_slots": slots,
                    "clarification_question": reply,
                    "risk_level": resolution.risk_level,
                    "confidence": resolution.confidence,
                    "understanding_version": "v6",
                }
                if clarification_context is not None:
                    conversation.pending_clarification[
                        "clarification_context"
                    ] = clarification_context
                    conversation.pending_clarification["expires_at"] = (
                        now + _STRUCTURED_CLARIFICATION_TTL
                    ).isoformat()
            else:
                conversation.pending_clarification = {}
            conversation.summary = _conversation_summary(reply)
            conversation.updated_at = now
            await db.commit()
            short_proposal = _proposal_reference_from_data(
                (content_data or {}).get("proposal")
            )
            short_artifact = _artifact_reference_from_data(
                (content_data or {}).get("artifact")
            )
            short_cards = (content_data or {}).get("cards", [])
            return AgentRuntimeResult(
                reply=reply,
                run_id=run.id,
                cards=short_cards if isinstance(short_cards, list) else [],
                execution_trace=execution_trace,
                proposal=short_proposal,
                artifact=short_artifact,
            )

        if structured_choice_invalid:
            context = (
                pending_clarification_state.get("clarification_context")
                if pending_clarification_state
                else None
            )
            card = (
                _plan_target_choice_card(
                    context if isinstance(context, dict) else None,
                    origin_run_id=str(
                        pending_clarification_state.get("origin_run_id")
                    ),
                )
                if _structured_clarification_is_current(
                    pending_clarification_state
                )
                else None
            )
            if card is None:
                return await complete_semantic_short_circuit(
                    "这个澄清选项已经失效，请重新发起计划修改请求。当前数据未作修改。",
                    termination_reason="structured_clarification_expired",
                )
            return await complete_semantic_short_circuit(
                "这个选项无法验证，请从下方候选项中重新选择。当前数据未作修改。",
                terminal_action="clarify",
                termination_reason="structured_clarification_invalid",
                content_data={"cards": [card]},
                missing_slots=["要修改的训练日"],
                clarification_context=context,
            )

        if intent_outcome.understanding_failed:
            reply = INTENT_UNAVAILABLE_REPLY
            run.error_code = "intent_understanding_unavailable"
            run.error_message = reply
            return await complete_semantic_short_circuit(
                reply,
                termination_reason="intent_understanding_unavailable",
            )

        write_structure_unavailable = (
            resolution.request_kind == "mutation"
            and intent_outcome.source == "rules"
            and intent_outcome.fallback_reason in _UNTRUSTED_WRITE_FALLBACK_REASONS
        )
        persisted_partial_mutation = (
            bool(pending_clarification_state)
            and pending_clarification_state.get("understanding_version") in {"v4", "v5", "v6"}
            and pending_clarification_state.get("request_kind") == "mutation"
            and bool(pending_clarification_state.get("change_requests"))
            and bool(resolution.change_requests)
            and resolution.clarification_required
        )
        if write_structure_unavailable and not persisted_partial_mutation:
            reply = WRITE_UNAVAILABLE_REPLY
            run.error_code = "intent_structure_unavailable"
            run.error_message = reply
            return await complete_semantic_short_circuit(
                reply,
                termination_reason="intent_structure_unavailable",
            )

        domain_mutation_path = (
            resolution.request_kind == "mutation"
            and (
                resolution.intent_domain != "workout_plan"
                or resolution.requested_effect in {"create", "delete"}
                or settings.AGENT_PLAN_MANAGEMENT_PROPOSALS_ENABLED
            )
        )
        high_risk_health_record = (
            resolution.risk_level == "high"
            and resolution.request_kind == "mutation"
            and resolution.intent_domain == "health"
        )
        if (
            domain_mutation_path
            and not resolution.clarification_required
            and (resolution.risk_level != "high" or high_risk_health_record)
        ):
            try:
                proposal_reference = await _create_structured_mutation_proposal(
                    db,
                    run=run,
                    conversation=conversation,
                    resolution=resolution,
                )
            except PlanProposalError as exc:
                prefix = f"{HIGH_RISK_REPLY}\n\n" if high_risk_health_record else ""
                clarification_context = (
                    exc.details
                    if exc.code == "proposal_target_ambiguous"
                    and exc.details.get("clarification_type")
                    == "plan_exercise_occurrence"
                    else None
                )
                target_slot = (
                    f"“{clarification_context['target_reference']}”的训练日"
                    if clarification_context is not None
                    else None
                )
                return await complete_semantic_short_circuit(
                    f"{prefix}{exc.message}。当前数据未作修改。",
                    terminal_action=(
                        "safe_stop" if high_risk_health_record else "clarify"
                        if exc.status_code == 422 else "answer"
                    ),
                    termination_reason=exc.code,
                    missing_slots=(
                        [target_slot]
                        if target_slot is not None
                        else _mutation_missing_slots(resolution)
                        if exc.status_code == 422 else None
                    ),
                    clarification_context=clarification_context,
                    content_data=(
                        {"cards": [choice_card]}
                        if (
                            clarification_context is not None
                            and (choice_card := _plan_target_choice_card(
                                clarification_context,
                                origin_run_id=run.id,
                            )) is not None
                        )
                        else None
                    ),
                )
            proposal_data = proposal_reference.model_dump(mode="json")
            reply = (
                f"{HIGH_RISK_REPLY}\n\n我也已把你明确要求记录的健康资料整理成待确认提案；"
                "当前档案尚未修改，请核对后确认。"
                if high_risk_health_record
                else PROPOSAL_PENDING_REPLY
            )
            return await complete_semantic_short_circuit(
                reply,
                terminal_action="safe_stop" if high_risk_health_record else "proposal",
                termination_reason="structured_mutation_proposal_created",
                content_data={"proposal": proposal_data},
            )

        if resolution.risk_level == "high" or resolution.clarification_required:
            reply = (
                HIGH_RISK_REPLY
                if resolution.risk_level == "high"
                else _clarification_reply(resolution)
            )
            execution_trace = terminate_execution_trace(
                execution_trace,
                terminal_action=(
                    "safe_stop"
                    if resolution.risk_level == "high"
                    else "clarify"
                ),
                termination_reason=(
                    "health_red_flag"
                    if resolution.risk_level == "high"
                    else "clarification_required"
                ),
            )
            execution_trace = _finalize_registry_shadow_trace(
                execution_trace,
                shadow_session,
            )
            await _lock_run_ownership(
                db,
                run_id=run.id,
                expected_attempt_count=expected_attempt_count,
            )
            db.add(AgentMessage(
                conversation_id=conversation.id,
                run_id=run.id,
                role="assistant",
                content=reply,
                content_data={
                    "guardrail": (
                        "health_red_flag"
                        if resolution.risk_level == "high"
                        else "clarification_required"
                    ),
                    "missing_slots": resolution.missing_slots,
                },
            ))
            now = datetime.now(timezone.utc)
            run.status = "completed"
            run.completed_at = now
            run.duration_ms = round((time.perf_counter() - started) * 1000)
            run.lease_expires_at = None
            run.execution_trace = execution_trace.model_dump(mode="json")
            conversation.summary = _conversation_summary(reply)
            conversation.updated_at = now
            await db.commit()
            return AgentRuntimeResult(
                reply=reply,
                run_id=run.id,
                execution_trace=execution_trace,
            )

        if (
            resolution.request_kind == "generation"
            and resolution.requested_output == "daily_meal_plan"
        ):
            generation_message = user_message
            if (
                pending_clarification_state
                and pending_clarification_state.get("request_kind") == "generation"
            ):
                generation_message = (
                    f"原始生成目标：{pending_clarification_state.get('resolved_query') or ''}\n"
                    f"上一轮缺少：{'、'.join(pending_clarification_state.get('missing_slots') or [])}\n"
                    f"用户本轮补充：{user_message}"
                )[:3000]
            try:
                generated = await generate_daily_meal_artifact(
                    db,
                    user_id=run.user_id,
                    conversation_id=conversation.id,
                    run_id=run.id,
                    user_message=generation_message,
                    revise_latest=(
                        "这份方案" in user_message
                        or "刚才的方案" in user_message
                        or "上个方案" in user_message
                    ),
                )
            except DailyMealPlanError as exc:
                _persist_daily_meal_diagnostics(
                    db,
                    run_id=run.id,
                    evidence_audits=tuple(exc.evidence_audits),
                    generation_attempts=tuple(exc.generation_attempts),
                    optimization_attempts=tuple(exc.optimization_attempts),
                )
                reply = f"{exc.message}。本次没有写入任何饮食记录。"
                terminal_action = "clarify" if exc.missing_slots else "answer"
                return await complete_semantic_short_circuit(
                    reply,
                    terminal_action=terminal_action,
                    termination_reason=exc.code,
                    missing_slots=exc.missing_slots or None,
                )
            _persist_daily_meal_diagnostics(
                db,
                run_id=run.id,
                evidence_audits=generated.audits,
                generation_attempts=generated.generation_attempts,
                optimization_attempts=generated.optimization_attempts,
            )
            artifact_data = artifact_reference(generated.artifact)
            return await complete_semantic_short_circuit(
                generated.reply,
                termination_reason="daily_meal_artifact_created",
                content_data={
                    "artifact": artifact_data,
                    "cards": [generated.card],
                },
            )

        if resolution.request_kind == "proposal_decision":
            decision_values = {
                str(change.value)
                for change in resolution.change_requests
                if change.field_path == "proposal.status"
                and change.value in {"confirm", "reject"}
            }
            if len(decision_values) != 1:
                return await complete_semantic_short_circuit(
                    "请明确告诉我是确认还是拒绝当前待确认提案。",
                    terminal_action="clarify",
                    termination_reason="proposal_decision_action_ambiguous",
                    missing_slots=["提案决策动作"],
                )
            action = decision_values.pop()
            # The model, normalized query and persisted clarification are not
            # proof of consent. Both sync chat and queued runs must authorize
            # against this turn's original message before any proposal handler.
            if parse_explicit_proposal_decision(user_message) != action:
                return await complete_semantic_short_circuit(
                    PROPOSAL_DECISION_UNCLEAR_REPLY,
                    terminal_action="clarify",
                    termination_reason="proposal_decision_not_explicit",
                    missing_slots=["明确的提案确认或拒绝"],
                )
            pending = list((await db.execute(
                select(AgentProposal)
                .where(
                    AgentProposal.user_id == run.user_id,
                    AgentProposal.conversation_id == conversation.id,
                    AgentProposal.status == "pending_confirmation",
                )
                .order_by(AgentProposal.created_at.desc())
                .limit(2)
            )).scalars().all())
            if len(pending) != 1:
                reply = (
                    (
                        "当前会话只有尚未保存的全天饮食方案，还没有待确认提案。"
                        "请先点击方案卡片上的“保存为待确认提案”，再核对并确认。"
                    )
                    if not pending
                    and conversation_action_state[
                        "active_daily_meal_artifact_count"
                    ] == 1
                    else "这个对话里还没有待确认提案。看到“待你确认”卡片后，"
                    "就可以打开详情核对，再决定是否应用。"
                    if not pending
                    else "当前会话有多个待确认提案，请打开提案详情选择要处理的那一个。"
                )
                return await complete_semantic_short_circuit(
                    reply,
                    terminal_action="clarify",
                    termination_reason=(
                        "proposal_decision_candidate_missing"
                        if not pending
                        else "proposal_decision_candidate_ambiguous"
                    ),
                    missing_slots=["要处理的待确认提案"],
                )
            proposal = pending[0]
            decision_request_id = f"agent-chat-decision:{run.id}:{action}"
            now = datetime.now(timezone.utc)
            if proposal.proposal_type == "plan_adjustment_v1":
                request = PlanAdjustmentProposalDecisionRequest(
                    expected_version=proposal.version,
                    client_request_id=decision_request_id,
                )
                decision_result = (
                    await apply_confirmed_plan_adjustment_atomically(
                        db,
                        enabled=settings.AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED,
                        user_id=run.user_id,
                        proposal_id=proposal.id,
                        request=request,
                        now=now,
                    )
                    if action == "confirm"
                    else await decide_plan_adjustment_proposal(
                        db,
                        enabled=settings.AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED,
                        user_id=run.user_id,
                        proposal_id=proposal.id,
                        action="reject",
                        request=request,
                        now=now,
                    )
                )
                if decision_result.error is not None:
                    reply = f"{decision_result.error.message}当前数据未发生额外修改。"
                    status_value = decision_result.error.code
                else:
                    reply = (
                        "已确认并应用调整。"
                        if action == "confirm"
                        else "已拒绝这份调整提案，当前计划保持不变。"
                    )
                    status_value = (
                        decision_result.response.status
                        if decision_result.response is not None
                        else "completed"
                    )
            else:
                generic_request = GenericProposalDecisionRequest(
                    expected_version=proposal.version,
                    client_request_id=decision_request_id,
                )
                try:
                    if proposal.proposal_type in PLAN_MANAGEMENT_TYPES:
                        generic_result = await decide_manual_plan_proposal(
                            db,
                            user_id=run.user_id,
                            proposal_id=proposal.id,
                            action="confirm" if action == "confirm" else "reject",
                            request=generic_request,
                            now=now,
                        )
                    elif proposal.proposal_type in DOMAIN_PROPOSAL_TYPES:
                        generic_result = await decide_agent_domain_proposal(
                            db,
                            user_id=run.user_id,
                            proposal_id=proposal.id,
                            action="confirm" if action == "confirm" else "reject",
                            request=generic_request,
                            now=now,
                        )
                    else:
                        raise PlanProposalError(
                            "proposal_type_unsupported", "这类提案暂不能在对话中处理"
                        )
                    reply = (
                        "已确认并应用提案。"
                        if action == "confirm"
                        else "已拒绝这份提案，当前数据保持不变。"
                    )
                    status_value = generic_result.status
                except PlanProposalError as exc:
                    reply = f"{exc.message}。当前数据未发生额外修改。"
                    status_value = exc.code
            return await complete_semantic_short_circuit(
                reply,
                termination_reason="proposal_decision_completed",
                content_data={
                    "proposal_decision": {
                        "proposal_id": proposal.id,
                        "action": action,
                        "status": status_value,
                    }
                },
            )

        if resolution.request_kind == "mutation":
            plan_mutation_supported = (
                resolution.intent_domain == "workout_plan"
                and resolution.requested_effect == "update"
                and bool(resolution.change_requests)
                and all(
                    change.resource == "workout_plan"
                    and change.operation == "update"
                    and change.field_path in _SUPPORTED_PLAN_MUTATION_FIELDS
                    and change.preserve_unspecified
                    for change in resolution.change_requests
                )
            )
            if not plan_mutation_supported:
                return await complete_semantic_short_circuit(
                    "我识别到了写入请求，但这类操作目前还不能执行，当前数据未作修改。",
                    termination_reason="mutation_capability_unsupported",
                )
            if not settings.AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED:
                return await complete_semantic_short_circuit(
                    "训练计划调整提案功能暂时未开启，当前计划未作修改。",
                    termination_reason="proposal_feature_disabled",
                )
            if (
                not settings.AGENT_PLANNED_EXECUTION_ENABLED
                or tool_allowlist != ["plan.get_active"]
            ):
                return await complete_semantic_short_circuit(
                    "训练计划调整所需的安全读取链路暂时不可用，当前计划未作修改。",
                    termination_reason="mutation_read_route_unavailable",
                )

        if (
            execution_trace.execution_mode == "planned"
            and settings.AGENT_PLANNED_EXECUTION_ENABLED
        ):
            async def invoke_parallel_read_tool(
                tool_id: str,
                arguments: dict[str, Any],
            ) -> Any:
                # SQLAlchemy AsyncSession is not safe for concurrent tasks.
                # Every Planner-owned parallel read therefore gets an isolated,
                # short-lived session while identity remains server-owned.
                async with AsyncSessionLocal() as parallel_db:
                    parallel_tools = build_read_tools(
                        parallel_db,
                        user_id=run.user_id,
                        allowlist=[tool_id],
                    )
                    return await parallel_tools[0].ainvoke(arguments)

            async def persist_planned_event(
                trace: AgentExecutionTrace,
                audit: ToolAuditEvent | None,
            ) -> None:
                nonlocal execution_trace
                execution_trace = trace
                await _lock_run_ownership(
                    db,
                    run_id=run.id,
                    expected_attempt_count=expected_attempt_count,
                )
                run.execution_trace = trace.model_dump(mode="json")
                if audit is not None:
                    existing_audit = await db.scalar(
                        select(AgentToolCall.id).where(
                            AgentToolCall.run_id == run.id,
                            AgentToolCall.call_id == audit.call_id,
                        )
                    )
                    if existing_audit is None:
                        db.add(AgentToolCall(
                            run_id=run.id,
                            call_id=audit.call_id,
                            tool_name=audit.tool_id,
                            arguments_data=audit.arguments,
                            result_data=audit.result_summary,
                            status=audit.status,
                            error_code=audit.error_code,
                            duration_ms=audit.duration_ms,
                        ))
                await db.commit()

            planned_result = await execute_planned_agent(
                db=db,
                user_id=run.user_id,
                run_id=run.id,
                model=_build_model(
                    temperature=0,
                    max_tokens=settings.AGENT_PLANNING_MAX_TOKENS,
                ),
                goal=resolution.resolved_query,
                subtasks=resolution.subtasks,
                tool_allowlist=tool_allowlist,
                initial_trace=execution_trace,
                summarize_observation=_audit_result_summary,
                event_sink=persist_planned_event,
                parallel_tool_invoker=invoke_parallel_read_tool,
                shadow_session=shadow_session,
                proposal_creation_enabled=(
                    settings.AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED
                    and resolution.intent_domain == "workout_plan"
                ),
                force_adjustment_proposal=(
                    resolution.request_kind == "mutation"
                    and resolution.intent_domain == "workout_plan"
                ),
            )
            execution_trace = planned_result.execution_trace
            execution_trace = _finalize_registry_shadow_trace(
                execution_trace,
                shadow_session,
            )
            reply = planned_result.reply
            cards = planned_result.cards
            run.input_tokens = planned_result.input_tokens
            run.output_tokens = planned_result.output_tokens

            if execution_trace.terminal_action == "clarify":
                run.clarification_required = True
                run.clarification_question = reply
                run.missing_slots = planned_result.missing_slots
                conversation.pending_clarification = {
                    "origin_run_id": run.id,
                    "original_message": user_message,
                    "resolved_query": resolution.resolved_query,
                    "references": [
                        item.model_dump() for item in resolution.references
                    ],
                    "primary_intent": resolution.primary_intent,
                    "intent_domain": resolution.intent_domain,
                    "request_kind": resolution.request_kind,
                    "requested_effect": resolution.requested_effect,
                    "change_requests": [
                        item.model_dump(mode="json")
                        for item in resolution.change_requests
                    ],
                    "expanded_intents": resolution.expanded_intents,
                    "subtasks": resolution.subtasks,
                    "missing_slots": planned_result.missing_slots,
                    "clarification_question": reply,
                    "risk_level": resolution.risk_level,
                    "confidence": resolution.confidence,
                    "understanding_version": "v6",
                }
            elif execution_trace.terminal_action == "safe_stop":
                run.risk_level = "high"
                conversation.pending_clarification = {}

            proposal_reference: AgentProposalReference | None = None
            runtime_proposal = None
            if settings.AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED:
                runtime_proposal = build_runtime_plan_adjustment_proposal(
                    feature_enabled=True,
                    run_owned=True,
                    selected_outcome=(
                        execution_trace.finalization_contract.selected_outcome
                        if execution_trace.finalization_contract is not None
                        and execution_trace.finalization_contract.selected_outcome
                        is not None
                        else ""
                    ),
                    terminal_action=execution_trace.terminal_action or "",
                    intent_allows_adjustment=(
                        execution_trace.finalization_contract is not None
                        and "adjustment_proposal"
                        in execution_trace.finalization_contract.allowed_outcomes
                    ),
                    risk_level=run.risk_level,
                    clarification_required=run.clarification_required,
                    observations=planned_result.proposal_observations,
                    proposal_draft=planned_result.proposal_draft,
                    change_requests=(
                        resolution.change_requests
                        if resolution.request_kind == "mutation"
                        else None
                    ),
                    created_at=datetime.now(timezone.utc),
                )
                execution_trace = attach_proposal_creation_decision(
                    execution_trace,
                    runtime_proposal.decision,
                )
            await _lock_run_ownership(
                db,
                run_id=run.id,
                expected_attempt_count=expected_attempt_count,
            )
            if runtime_proposal is not None and runtime_proposal.built is not None:
                try:
                    persisted = await persist_optional_plan_adjustment_proposal(
                        db,
                        enabled=(
                            settings.AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED
                        ),
                        user_id=run.user_id,
                        conversation_id=conversation.id,
                        run_id=run.id,
                        expected_attempt_count=(
                            expected_attempt_count
                            if expected_attempt_count is not None
                            else run.attempt_count
                        ),
                        built=runtime_proposal.built,
                    )
                except PlanAdjustmentProposalPersistenceRejected as exc:
                    execution_trace = mark_proposal_persistence_rejected(
                        execution_trace,
                        reason_code=exc.reason_code,
                    )
                    raise
                except Exception:
                    execution_trace = mark_proposal_persistence_failed(
                        execution_trace
                    )
                    raise
                if persisted.proposal is not None:
                    execution_trace = mark_proposal_persisted(
                        execution_trace,
                        created=persisted.created,
                    )
                    proposal_reference = _proposal_reference_from_model(
                        persisted.proposal
                    )
                elif persisted.reason_code is not None:
                    execution_trace = mark_proposal_persistence_rejected(
                        execution_trace,
                        reason_code=persisted.reason_code,
                    )
                else:  # pragma: no cover - persistence result invariant
                    execution_trace = mark_proposal_persistence_failed(
                        execution_trace
                    )
                    raise RuntimeError(
                        "proposal persistence returned no proposal or reason"
                    )
            if (
                proposal_reference is not None
                and legacy_explicit_command is not None
            ):
                reply = _explicit_proposal_created_reply(
                    before_weeks=legacy_explicit_command.expected_duration_weeks,
                    after_weeks=legacy_explicit_command.target_duration_weeks,
                )
            elif (
                proposal_reference is not None
                and resolution.request_kind == "mutation"
                and runtime_proposal is not None
                and runtime_proposal.built is not None
            ):
                reply = _mutation_proposal_created_reply(
                    runtime_proposal.built
                )
            elif (
                resolution.request_kind == "mutation"
                and runtime_proposal is not None
                and runtime_proposal.reply
            ):
                reply = runtime_proposal.reply
            reply, execution_trace = _normalize_unpersisted_proposal_result(
                reply=reply,
                execution_trace=execution_trace,
                proposal_reference=proposal_reference,
                proposal_expected=(
                    resolution.request_kind == "mutation"
                ),
                intent_domain=resolution.intent_domain,
                request_kind=resolution.request_kind,
            )
            message_content_data: dict[str, Any] = {}
            if cards:
                message_content_data["cards"] = cards
            if proposal_reference is not None:
                message_content_data["proposal"] = (
                    proposal_reference.model_dump(mode="json")
                )
            db.add(AgentMessage(
                conversation_id=conversation.id,
                run_id=run.id,
                role="assistant",
                content=reply,
                content_data=message_content_data,
            ))
            now = datetime.now(timezone.utc)
            run.status = "completed"
            run.completed_at = now
            run.duration_ms = round((time.perf_counter() - started) * 1000)
            run.lease_expires_at = None
            run.execution_trace = execution_trace.model_dump(mode="json")
            conversation.summary = _conversation_summary(reply)
            conversation.updated_at = now
            await db.commit()
            _log_proposal_creation_diagnostic(
                run_id=run.id,
                execution_trace=execution_trace,
            )
            return AgentRuntimeResult(
                reply=reply,
                run_id=run.id,
                cards=cards,
                execution_trace=execution_trace,
                proposal=proposal_reference,
            )

        direct_started = time.perf_counter()
        try:
            result = await invoke_langchain_agent(
                db,
                user_id=run.user_id,
                history=history,
                user_message=user_message,
                tool_allowlist=tool_allowlist,
                resolved_query=resolution.resolved_query,
                subtasks=resolution.subtasks,
                shadow_session=shadow_session,
                resolution=resolution,
            )
        except Exception as exc:
            execution_trace = add_stage_timing(
                execution_trace,
                stage="direct_agent",
                source="model",
                status="error",
                latency_ms=round(
                    (time.perf_counter() - direct_started) * 1000
                ),
                error_category=safe_error_category(exc),
            )
            raise
        execution_trace = add_stage_timing(
            execution_trace,
            stage="direct_agent",
            source="controller" if result.get("response_mode", "").startswith("verified_") else "model",
            status="success",
            latency_ms=round(
                (time.perf_counter() - direct_started) * 1000
            ),
        )
        execution_trace = complete_execution_trace(
            execution_trace,
            result,
            summarize_observation=_audit_result_summary,
        )
        execution_trace = _finalize_registry_shadow_trace(
            execution_trace,
            shadow_session,
        )
        reply, cards = _extract_agent_output(result)
        reply, execution_trace = _normalize_unpersisted_proposal_result(
            reply=reply,
            execution_trace=execution_trace,
            proposal_reference=None,
            proposal_expected=(
                resolution.request_kind == "mutation"
            ),
            intent_domain=resolution.intent_domain,
            request_kind=resolution.request_kind,
        )
        await _lock_run_ownership(
            db,
            run_id=run.id,
            expected_attempt_count=expected_attempt_count,
        )
        db.add(AgentMessage(
            conversation_id=conversation.id,
            run_id=run.id,
            role="assistant",
            content=reply,
            content_data={"cards": cards} if cards else {},
        ))
        for record in _tool_call_records(result, run.id):
            db.add(record)
        run.input_tokens, run.output_tokens = _usage_totals(result)
        now = datetime.now(timezone.utc)
        run.status = "completed"
        run.completed_at = now
        run.duration_ms = round((time.perf_counter() - started) * 1000)
        run.lease_expires_at = None
        run.execution_trace = execution_trace.model_dump(mode="json")
        conversation.summary = _conversation_summary(reply)
        conversation.updated_at = now
        await db.commit()
        return AgentRuntimeResult(
            reply=reply,
            run_id=run.id,
            cards=cards,
            execution_trace=execution_trace,
        )
    except AgentRunOwnershipLost:
        await db.rollback()
        raise
    except AIServiceError as exc:
        if execution_trace is not None:
            execution_trace = terminate_execution_trace(
                execution_trace,
                terminal_action="failed",
                termination_reason="ai_service_error",
                failed=True,
            )
            execution_trace = _finalize_registry_shadow_trace(
                execution_trace,
                shadow_session,
            )
        await _mark_owned_run_failed(
            db,
            run_id=run.id,
            expected_attempt_count=expected_attempt_count,
            error_code="ai_service_error",
            duration_ms=round((time.perf_counter() - started) * 1000),
            execution_trace=execution_trace,
        )
        raise
    except Exception as exc:
        logger.exception("Agent run failed: run_id=%s", run.id)
        if execution_trace is not None:
            execution_trace = terminate_execution_trace(
                execution_trace,
                terminal_action="failed",
                termination_reason="agent_runtime_error",
                failed=True,
            )
            execution_trace = _finalize_registry_shadow_trace(
                execution_trace,
                shadow_session,
            )
        await _mark_owned_run_failed(
            db,
            run_id=run.id,
            expected_attempt_count=expected_attempt_count,
            error_code="agent_runtime_error",
            duration_ms=round((time.perf_counter() - started) * 1000),
            execution_trace=execution_trace,
        )
        raise AIServiceError("Agent 暂时无法完成请求，请稍后重试") from exc


async def run_agent_chat(
    db: AsyncSession,
    *,
    user_id: str,
    conversation: AgentConversation,
    user_message: str,
    artifact_action: dict[str, Any] | None = None,
    clarification_action: dict[str, Any] | None = None,
) -> AgentRuntimeResult:
    """Compatibility path for existing HTTP clients and focused tests.

    Production miniapp traffic uses the durable queue endpoint. Keeping this
    synchronous wrapper avoids breaking older clients during the rollout.
    """
    now = datetime.now(timezone.utc)
    run = AgentRun(
        conversation_id=conversation.id,
        user_id=user_id,
        status="running",
        model_name=settings.AGENT_MODEL,
        processing_started_at=now,
        attempt_count=1,
    )
    db.add(run)
    await db.flush()
    db.add(AgentMessage(
        conversation_id=conversation.id,
        run_id=run.id,
        role="user",
        content=user_message,
        content_data=(
            {
                **(
                    {"artifact_action": artifact_action}
                    if artifact_action is not None
                    else {}
                ),
                **(
                    {"clarification_action": clarification_action}
                    if clarification_action is not None
                    else {}
                ),
            }
        ),
    ))
    await db.commit()
    return await execute_agent_run(
        db,
        run=run,
        conversation=conversation,
        user_message=user_message,
        artifact_action=artifact_action,
        clarification_action=clarification_action,
    )
