"""Pure construction boundary for validated plan-adjustment proposals."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from pydantic import ValidationError

from app.schemas.agent_plan_adjustment_proposal import (
    PlanAdjustmentProposalCreationDecision,
    PlanAdjustmentProposalCreationReasonCode,
    PlanAdjustmentProposalDraft,
    PlanAdjustmentProposalEvidence,
    PlanAdjustmentPlanSnapshot,
    PlanAdjustmentProposalPayload,
    PlanAdjustmentProposalPayloadErrorCode,
    PlanAdjustmentExerciseReplacementChange,
    PlanAdjustmentExerciseTargetChange,
    PlanAdjustmentScheduleChange,
    ValidatedPlanAdjustmentProposal,
    plan_adjustment_proposal_payload_error_codes,
)
from app.services.agent_intent import ChangeRequest, ExplicitPlanAdjustmentCommand
from app.services.agent_trace import observation_fingerprint


PLAN_ADJUSTMENT_SUPPORTING_EVIDENCE_TOOL_IDS = frozenset({
    "profile.get_summary",
    "health.get_screening_summary",
    "workout.list_history",
    "workout.get_progress",
})


class PlanAdjustmentProposalCreationRejected(ValueError):
    def __init__(
        self,
        reason_code: PlanAdjustmentProposalCreationReasonCode,
    ) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class PlanAdjustmentProposalPayloadRejected(ValueError):
    def __init__(
        self,
        error_codes: tuple[PlanAdjustmentProposalPayloadErrorCode, ...],
    ) -> None:
        self.error_codes = error_codes
        super().__init__(",".join(error_codes))


@dataclass(frozen=True)
class RuntimePlanAdjustmentProposalBuildResult:
    decision: PlanAdjustmentProposalCreationDecision
    built: ValidatedPlanAdjustmentProposal | None = None
    reply: str | None = None


class PlanMutationCompilationRejected(ValueError):
    def __init__(
        self,
        reason_code: PlanAdjustmentProposalCreationReasonCode,
        reply: str,
    ) -> None:
        self.reason_code = reason_code
        self.reply = reply
        super().__init__(reason_code)


def _rejected(
    reason_code: PlanAdjustmentProposalCreationReasonCode,
) -> PlanAdjustmentProposalCreationDecision:
    return PlanAdjustmentProposalCreationDecision(
        eligible=False,
        reason_code=reason_code,
    )


def evaluate_plan_adjustment_proposal_creation(
    *,
    feature_enabled: bool,
    run_owned: bool,
    selected_outcome: str,
    terminal_action: str,
    intent_allows_adjustment: bool,
    risk_level: str,
    clarification_required: bool,
    evidence_state: str,
    draft_state: str,
    proposal_type: str,
    requested_ttl_hours: int | None,
) -> PlanAdjustmentProposalCreationDecision:
    """Apply the fixed fail-closed creation gate without runtime settings."""

    if not feature_enabled:
        return _rejected("feature_disabled")
    if not run_owned:
        return _rejected("run_ownership_lost")
    if risk_level not in {"low", "medium", "high"} or risk_level == "high":
        return _rejected("health_red_flag")
    if clarification_required:
        return _rejected("clarification_required")
    if selected_outcome != "adjustment_proposal":
        return _rejected("outcome_not_adjustment_proposal")
    if terminal_action != "proposal":
        return _rejected("terminal_action_not_proposal")
    if not intent_allows_adjustment:
        return _rejected("intent_not_adjustment")
    if evidence_state != "complete":
        reason_by_state: dict[
            str,
            PlanAdjustmentProposalCreationReasonCode,
        ] = {
            "plan_missing": "plan_evidence_missing",
            "supporting_missing": "supporting_evidence_missing",
            "deadline_insufficient": "deadline_evidence_insufficient",
        }
        return _rejected(
            reason_by_state.get(
                evidence_state,
                "supporting_evidence_missing",
            )
        )
    if draft_state != "valid":
        return _rejected(
            "proposal_target_ambiguous"
            if draft_state == "ambiguous_target"
            else "proposal_draft_invalid"
        )
    if proposal_type != "plan_adjustment_v1":
        return _rejected("proposal_type_not_allowed")

    ttl_hours = 24 if requested_ttl_hours is None else requested_ttl_hours
    if (
        isinstance(ttl_hours, bool)
        or not isinstance(ttl_hours, int)
        or not 1 <= ttl_hours <= 72
    ):
        return _rejected("proposal_ttl_out_of_range")
    return PlanAdjustmentProposalCreationDecision(
        eligible=True,
        initial_status="pending_confirmation",
        ttl_hours=ttl_hours,
    )


def canonical_plan_adjustment_proposal_payload_data(
    payload: PlanAdjustmentProposalPayload,
) -> dict[str, Any]:
    return payload.model_dump(mode="json", exclude_unset=True)


def plan_adjustment_proposal_payload_fingerprint(
    payload: PlanAdjustmentProposalPayload,
) -> str:
    canonical = json.dumps(
        canonical_plan_adjustment_proposal_payload_data(payload),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_evidence(
    payload: PlanAdjustmentProposalPayload,
    *,
    created_at: datetime,
    supporting_evidence_required: bool = True,
) -> None:
    evidence_tool_ids = {item.tool_id for item in payload.evidence}
    if "plan.get_active" not in evidence_tool_ids:
        raise PlanAdjustmentProposalCreationRejected(
            "plan_evidence_missing"
        )
    if supporting_evidence_required and not (
        evidence_tool_ids & PLAN_ADJUSTMENT_SUPPORTING_EVIDENCE_TOOL_IDS
    ):
        raise PlanAdjustmentProposalCreationRejected(
            "supporting_evidence_missing"
        )
    if not evidence_tool_ids <= (
        PLAN_ADJUSTMENT_SUPPORTING_EVIDENCE_TOOL_IDS | {"plan.get_active"}
    ):
        raise PlanAdjustmentProposalCreationRejected(
            "supporting_evidence_missing"
        )
    evidence_keys = [
        (item.tool_id, item.result_fingerprint) for item in payload.evidence
    ]
    if len(evidence_keys) != len(set(evidence_keys)):
        raise PlanAdjustmentProposalCreationRejected(
            "proposal_draft_invalid"
        )
    if any(item.observed_at > created_at for item in payload.evidence):
        raise PlanAdjustmentProposalCreationRejected(
            "supporting_evidence_missing"
        )


def build_validated_plan_adjustment_proposal(
    *,
    decision: PlanAdjustmentProposalCreationDecision,
    payload_data: Mapping[str, Any] | PlanAdjustmentProposalPayload,
    expected_base_plan_id: str,
    expected_base_plan_fingerprint: str,
    created_at: datetime,
    supporting_evidence_required: bool = True,
) -> ValidatedPlanAdjustmentProposal:
    """Build an immutable proposal result without persistence or side effects."""

    if not decision.eligible:
        if decision.reason_code is None:  # pragma: no cover - schema invariant
            raise ValueError("rejected proposal decision has no reason code")
        raise PlanAdjustmentProposalCreationRejected(decision.reason_code)
    if created_at.tzinfo is None:
        raise ValueError("proposal created_at must include a timezone")

    if isinstance(payload_data, PlanAdjustmentProposalPayload):
        payload = payload_data
    else:
        candidate = dict(payload_data)
        error_codes = plan_adjustment_proposal_payload_error_codes(candidate)
        if error_codes:
            raise PlanAdjustmentProposalPayloadRejected(error_codes)
        try:
            payload = PlanAdjustmentProposalPayload.model_validate(candidate)
        except ValidationError as exc:  # pragma: no cover - classifier parity
            raise PlanAdjustmentProposalPayloadRejected(
                ("forbidden_field",)
            ) from exc

    if (
        payload.target.base_plan_id != expected_base_plan_id
        or payload.target.base_plan_fingerprint
        != expected_base_plan_fingerprint
    ):
        raise PlanAdjustmentProposalPayloadRejected(("invalid_target",))
    _validate_evidence(
        payload,
        created_at=created_at,
        supporting_evidence_required=supporting_evidence_required,
    )

    if (  # pragma: no cover - schema invariant
        decision.ttl_hours is None or decision.initial_status is None
    ):
        raise ValueError("eligible proposal decision is incomplete")
    return ValidatedPlanAdjustmentProposal(
        initial_status=decision.initial_status,
        ttl_hours=decision.ttl_hours,
        created_at=created_at,
        expires_at=created_at + timedelta(hours=decision.ttl_hours),
        payload=payload,
        payload_fingerprint=plan_adjustment_proposal_payload_fingerprint(
            payload
        ),
    )


def _active_plan_observation(
    observations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for observation in reversed(observations):
        result = observation.get("result")
        if (
            observation.get("tool_id") == "plan.get_active"
            and observation.get("status") == "success"
            and isinstance(result, dict)
            and result.get("found") is True
            and isinstance(result.get("plan"), dict)
        ):
            return observation
    return None


def _runtime_evidence_state(
    observations: list[dict[str, Any]],
    *,
    supporting_evidence_required: bool = True,
) -> str:
    if _active_plan_observation(observations) is None:
        return "plan_missing"
    if supporting_evidence_required and not any(
        observation.get("tool_id")
        in PLAN_ADJUSTMENT_SUPPORTING_EVIDENCE_TOOL_IDS
        and observation.get("status") == "success"
        and isinstance(observation.get("result"), dict)
        for observation in observations
    ):
        return "supporting_missing"
    return "complete"


def _explicit_duration_proposal_draft(
    command: ExplicitPlanAdjustmentCommand,
) -> PlanAdjustmentProposalDraft:
    before = command.expected_duration_weeks
    after = command.target_duration_weeks
    return PlanAdjustmentProposalDraft.model_validate({
        "proposal_type": "plan_adjustment_v1",
        "changes": [{
            "change_type": "update_plan_schedule",
            "stable_display_key": "plan-schedule",
            "before": {"duration_weeks": before},
            "after": {"duration_weeks": after},
            "reason": (
                f"按用户明确指令将计划周期从{before}周调整为{after}周，"
                "其他内容保持不变。"
            ),
            "safety_priority": False,
        }],
        "rationale": [
            f"用户明确要求将当前计划周期从{before}周调整为{after}周。"
        ],
        "safety_notes": [],
        "requested_ttl_hours": 24,
    })


_SUPPORTED_MUTATION_FIELDS = frozenset({
    "schedule.duration_weeks",
    "schedule.days_per_week",
    "exercise.sets",
    "exercise.reps",
    "exercise.rest_seconds",
    "exercise.recommended_weight_kg",
})


def _normalized_exercise_name(value: str) -> str:
    return "".join(value.lower().split())


def _resolve_exercise_target(
    before: PlanAdjustmentPlanSnapshot,
    reference: str | None,
) -> Any:
    if not reference:
        raise PlanMutationCompilationRejected(
            "proposal_target_value_required",
            "请说明要调整哪个动作，我再为你生成待确认提案。",
        )
    normalized_reference = _normalized_exercise_name(reference)
    exact = [
        exercise for exercise in before.exercises
        if _normalized_exercise_name(exercise.exercise_name)
        == normalized_reference
    ]
    candidates = exact or [
        exercise for exercise in before.exercises
        if normalized_reference in _normalized_exercise_name(exercise.exercise_name)
        or _normalized_exercise_name(exercise.exercise_name)
        in normalized_reference
    ]
    if len(candidates) != 1:
        raise PlanMutationCompilationRejected(
            "proposal_target_ambiguous",
            (
                f"当前计划中没有唯一匹配“{reference}”的动作，"
                "请提供计划里的完整动作名称。"
            ),
        )
    return candidates[0]


def _require_mutation_value(change: ChangeRequest) -> Any:
    if change.value is None:
        raise PlanMutationCompilationRejected(
            "proposal_target_value_required",
            "请补充计划调整后的具体目标值，我再为你生成待确认提案。",
        )
    return change.value


def _integer_mutation_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _structured_plan_mutation_draft(
    change_requests: list[ChangeRequest],
    *,
    before: PlanAdjustmentPlanSnapshot,
) -> PlanAdjustmentProposalDraft:
    if not change_requests:
        raise PlanMutationCompilationRejected(
            "proposal_target_value_required",
            "请说明希望修改训练计划的哪个项目和目标值。",
        )
    if any(
        change.resource != "workout_plan"
        or change.operation != "update"
        or change.field_path not in _SUPPORTED_MUTATION_FIELDS
        or not change.preserve_unspecified
        for change in change_requests
    ):
        raise PlanMutationCompilationRejected(
            "proposal_operation_unsupported",
            "我识别到了写入请求，但这类训练计划变更目前还不能执行，当前计划未作修改。",
        )

    semantic_targets = [
        (
            change.field_path,
            _normalized_exercise_name(change.target_reference or ""),
        )
        for change in change_requests
    ]
    if len(semantic_targets) != len(set(semantic_targets)):
        raise PlanMutationCompilationRejected(
            "proposal_target_ambiguous",
            "同一个计划字段出现了多个目标值，请只保留一个明确目标。",
        )

    frequency_changes = [
        change for change in change_requests
        if change.field_path == "schedule.days_per_week"
    ]
    if frequency_changes and len(change_requests) != 1:
        raise PlanMutationCompilationRejected(
            "proposal_operation_unsupported",
            "调整每周训练频率暂时不能和其他变更合并，请单独发起频率调整。",
        )

    draft_changes: list[dict[str, Any]] = []
    rationale: list[str] = []
    safety_notes: list[str] = []
    schedule_before: dict[str, Any] = {}
    schedule_after: dict[str, Any] = {}
    exercise_updates: dict[str, dict[str, Any]] = {}

    for change in change_requests:
        field_path = change.field_path
        value = _require_mutation_value(change)
        if field_path == "schedule.duration_weeks":
            integer_value = _integer_mutation_value(value)
            if integer_value is None or not 2 <= integer_value <= 12:
                raise PlanMutationCompilationRejected(
                    "proposal_target_mismatch",
                    "计划周期需要是 2 到 12 周之间的整数，当前计划未作修改。",
                )
            value = integer_value
            if value == before.duration_weeks:
                raise PlanMutationCompilationRejected(
                    "proposal_no_change",
                    f"当前计划周期已经是{value}周，无需生成调整提案。",
                )
            schedule_before["duration_weeks"] = before.duration_weeks
            schedule_after["duration_weeks"] = value
            rationale.append(
                f"用户明确要求将计划周期从{before.duration_weeks}周调整为{value}周。"
            )
            continue
        if field_path == "schedule.days_per_week":
            integer_value = _integer_mutation_value(value)
            if integer_value is None or not 1 <= integer_value <= 7:
                raise PlanMutationCompilationRejected(
                    "proposal_target_mismatch",
                    "每周训练天数需要是 1 到 7 之间的整数，当前计划未作修改。",
                )
            value = integer_value
            if value == before.days_per_week:
                raise PlanMutationCompilationRejected(
                    "proposal_no_change",
                    f"当前计划已经是每周{value}天，无需生成调整提案。",
                )
            if value != before.days_per_week - 1:
                raise PlanMutationCompilationRejected(
                    "proposal_frequency_restructure_unsupported",
                    (
                        f"当前只支持把每周训练频率从{before.days_per_week}天"
                        f"减少为{before.days_per_week - 1}天；"
                        f"不能直接调整为{value}天，当前计划未作修改。"
                    ),
                )
            removed_day = _deterministic_frequency_reduction_day(before)
            schedule_before["days_per_week"] = before.days_per_week
            schedule_after["days_per_week"] = value
            rationale.append(
                f"用户明确要求将每周训练频率从{before.days_per_week}天调整为{value}天。"
            )
            safety_notes.append(
                f"将按总组数最少原则移除周{removed_day}训练日；确认前请核对完整计划。"
            )
            continue

        exercise = _resolve_exercise_target(before, change.target_reference)
        field_name = str(field_path).split(".", 1)[1]
        before_value = getattr(exercise, field_name)
        normalized_value = value
        if field_name in {"sets", "rest_seconds"}:
            integer_value = _integer_mutation_value(value)
            if integer_value is None:
                raise PlanMutationCompilationRejected(
                    "proposal_target_mismatch",
                    "动作组数和休息秒数需要使用整数，当前计划未作修改。",
                )
            normalized_value = integer_value
        elif field_name == "recommended_weight_kg":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise PlanMutationCompilationRejected(
                    "proposal_target_mismatch",
                    "动作重量需要使用有效数字，当前计划未作修改。",
                )
            normalized_value = float(value)
        elif field_name == "reps":
            if isinstance(value, int) and not isinstance(value, bool):
                normalized_value = str(value)
            elif isinstance(value, str) and value.strip():
                normalized_value = re.sub(
                    r"[~～至到]",
                    "-",
                    value.strip(),
                )
            else:
                raise PlanMutationCompilationRejected(
                    "proposal_target_mismatch",
                    "动作次数需要是明确的次数或次数范围，当前计划未作修改。",
                )
        if normalized_value == before_value:
            raise PlanMutationCompilationRejected(
                "proposal_no_change",
                f"{exercise.exercise_name}的{field_name}已经是目标值，无需生成调整提案。",
            )
        update = exercise_updates.setdefault(exercise.slot_key, {
            "exercise": exercise,
            "before": {},
            "after": {},
        })
        if field_name in update["after"]:
            raise PlanMutationCompilationRejected(
                "proposal_target_ambiguous",
                "同一个动作字段出现了多个目标值，请只保留一个明确目标。",
            )
        update["before"][field_name] = before_value
        update["after"][field_name] = normalized_value
        rationale.append(
            f"用户明确要求调整{exercise.exercise_name}的{field_name}。"
        )

    if schedule_before:
        draft_changes.append({
            "change_type": "update_plan_schedule",
            "stable_display_key": "plan-schedule",
            "before": schedule_before,
            "after": schedule_after,
            "reason": "按用户明确请求调整训练计划日程，未指定内容保持不变。",
            "safety_priority": False,
        })
    for slot_key, update in exercise_updates.items():
        draft_changes.append({
            "change_type": "adjust_exercise_target",
            "stable_display_key": slot_key,
            "before": update["before"],
            "after": update["after"],
            "reason": (
                f"按用户明确请求调整{update['exercise'].exercise_name}的训练目标。"
            ),
            "safety_priority": False,
        })
    try:
        return PlanAdjustmentProposalDraft.model_validate({
            "proposal_type": "plan_adjustment_v1",
            "changes": draft_changes,
            "rationale": rationale,
            "safety_notes": safety_notes,
            "requested_ttl_hours": 24,
        })
    except ValidationError as exc:
        raise PlanMutationCompilationRejected(
            "proposal_target_mismatch",
            "目标值超出训练计划允许范围，当前计划未作修改。",
        ) from exc


def _plan_snapshot_from_observation(
    observation: dict[str, Any],
) -> tuple[str, PlanAdjustmentPlanSnapshot]:
    result = observation["result"]
    plan = result["plan"]
    plan_id = plan.get("id")
    if not isinstance(plan_id, str) or not plan_id:
        raise ValueError("active plan observation has no stable plan id")
    exercises = plan.get("exercises")
    if not isinstance(exercises, list) or not exercises:
        raise ValueError("active plan observation has no exercises")

    normalized_exercises: list[dict[str, Any]] = []
    for item in exercises:
        if not isinstance(item, dict):
            raise ValueError("active plan exercise is not an object")
        day_of_week = item.get("day_of_week")
        order_index = item.get("order_index")
        exercise_name = item.get("exercise_name")
        if (
            not isinstance(day_of_week, int)
            or isinstance(day_of_week, bool)
            or not isinstance(order_index, int)
            or isinstance(order_index, bool)
            or not isinstance(exercise_name, str)
            or not exercise_name
        ):
            raise ValueError("active plan exercise identity is incomplete")
        normalized_exercises.append({
            "slot_key": f"day-{day_of_week}-order-{order_index}",
            "exercise_id": item.get("exercise_id"),
            "exercise_name": exercise_name,
            "day_of_week": day_of_week,
            "sets": item.get("sets"),
            "reps": item.get("reps"),
            "rest_seconds": item.get("rest_seconds"),
            "recommended_weight_kg": item.get("recommended_weight_kg"),
            "order_index": order_index,
        })

    snapshot = PlanAdjustmentPlanSnapshot.model_validate({
        "name": plan.get("name"),
        "goal": plan.get("goal"),
        "duration_weeks": plan.get("duration_weeks"),
        "days_per_week": plan.get("days_per_week"),
        "exercises": normalized_exercises,
    })
    return plan_id, snapshot


def plan_adjustment_plan_snapshot_fingerprint(
    snapshot: PlanAdjustmentPlanSnapshot,
) -> str:
    canonical = json.dumps(
        snapshot.model_dump(mode="json"),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _deterministic_frequency_reduction_day(
    snapshot: PlanAdjustmentPlanSnapshot,
) -> int:
    day_loads: dict[int, int] = {}
    for exercise in snapshot.exercises:
        day_loads[exercise.day_of_week] = (
            day_loads.get(exercise.day_of_week, 0) + exercise.sets
        )
    if len(day_loads) != snapshot.days_per_week:
        raise ValueError("active plan schedule is inconsistent")
    return min(
        day_loads,
        key=lambda day: (day_loads[day], -day),
    )


def apply_plan_adjustment_proposal_draft(
    before: PlanAdjustmentPlanSnapshot,
    draft: PlanAdjustmentProposalDraft,
) -> PlanAdjustmentPlanSnapshot:
    after_data = deepcopy(before.model_dump(mode="python"))
    exercises_by_slot = {
        item["slot_key"]: item for item in after_data["exercises"]
    }
    seen_changes: set[tuple[str, str]] = set()
    frequency_changes = [
        change
        for change in draft.changes
        if (
            isinstance(change, PlanAdjustmentScheduleChange)
            and "days_per_week" in change.before.model_fields_set
        )
    ]
    if frequency_changes and (
        len(frequency_changes) != 1 or len(draft.changes) != 1
    ):
        raise ValueError(
            "schedule frequency reduction must be the only proposal change"
        )

    for change in draft.changes:
        change_key = (change.change_type, change.stable_display_key)
        if change_key in seen_changes:
            raise ValueError("proposal draft repeats a change target")
        seen_changes.add(change_key)

        if isinstance(change, PlanAdjustmentExerciseReplacementChange):
            raise ValueError("exercise replacement is outside the first cohort")
        if isinstance(change, PlanAdjustmentScheduleChange):
            changed_fields = change.before.model_fields_set
            if "days_per_week" in changed_fields:
                if changed_fields != {"days_per_week"}:
                    raise ValueError(
                        "schedule frequency reduction cannot mix fields"
                    )
                before_days = change.before.days_per_week
                after_days = change.after.days_per_week
                if (
                    before_days != after_data["days_per_week"]
                    or after_days != before_days - 1
                ):
                    raise ValueError(
                        "schedule frequency reduction must lower one day"
                    )
                removed_day = _deterministic_frequency_reduction_day(before)
                after_data["exercises"] = [
                    exercise
                    for exercise in after_data["exercises"]
                    if exercise["day_of_week"] != removed_day
                ]
                after_data["days_per_week"] = after_days
                after_data["name"] = (
                    after_data["name"]
                    .replace(f"{before_days}日", f"{after_days}日")
                    .replace(f"{before_days}天", f"{after_days}天")
                )
                continue
            for field_name in changed_fields:
                if after_data[field_name] != getattr(change.before, field_name):
                    raise ValueError("schedule draft does not match active plan")
                after_data[field_name] = getattr(change.after, field_name)
            continue
        if not isinstance(change, PlanAdjustmentExerciseTargetChange):
            raise ValueError("proposal draft change type is unsupported")

        exercise = exercises_by_slot.get(change.stable_display_key)
        if exercise is None:
            raise ValueError("proposal draft target is not in active plan")
        for field_name in change.before.model_fields_set:
            if exercise[field_name] != getattr(change.before, field_name):
                raise ValueError("proposal draft before value is stale")
            exercise[field_name] = getattr(change.after, field_name)

    after = PlanAdjustmentPlanSnapshot.model_validate(after_data)
    if after == before:
        raise ValueError("proposal draft has no effect")
    return after


def _runtime_evidence(
    observations: list[dict[str, Any]],
    *,
    observed_at: datetime,
) -> tuple[PlanAdjustmentProposalEvidence, ...]:
    evidence: list[PlanAdjustmentProposalEvidence] = []
    seen: set[tuple[str, str]] = set()
    for observation in observations:
        tool_id = observation.get("tool_id")
        result = observation.get("result")
        if (
            observation.get("status") != "success"
            or not isinstance(tool_id, str)
            or tool_id
            not in (
                PLAN_ADJUSTMENT_SUPPORTING_EVIDENCE_TOOL_IDS
                | {"plan.get_active"}
            )
            or not isinstance(result, dict)
        ):
            continue
        result_fingerprint = observation_fingerprint(result)
        evidence_key = (tool_id, result_fingerprint)
        if evidence_key in seen:
            continue
        seen.add(evidence_key)
        evidence.append(PlanAdjustmentProposalEvidence(
            tool_id=tool_id,
            result_fingerprint=result_fingerprint,
            observed_at=observed_at,
        ))
    return tuple(evidence)


def _profile_training_days_per_week(
    observations: list[dict[str, Any]],
) -> int | None:
    for observation in reversed(observations):
        if (
            observation.get("tool_id") != "profile.get_summary"
            or observation.get("status") != "success"
        ):
            continue
        result = observation.get("result")
        if not isinstance(result, dict) or result.get("found") is not True:
            continue
        value = result.get("training_days_per_week")
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and 1 <= value <= 7
        ):
            return value
    return None


def _is_unsupported_frequency_workaround(
    *,
    before: PlanAdjustmentPlanSnapshot,
    draft: PlanAdjustmentProposalDraft,
    observations: list[dict[str, Any]],
) -> bool:
    profile_days = _profile_training_days_per_week(observations)
    if profile_days is None or profile_days >= before.days_per_week:
        return False
    return bool(draft.changes) and all(
        isinstance(change, PlanAdjustmentScheduleChange)
        and change.before.model_fields_set == {"duration_weeks"}
        for change in draft.changes
    )


def _contains_frequency_change(
    draft: PlanAdjustmentProposalDraft,
) -> bool:
    return any(
        isinstance(change, PlanAdjustmentScheduleChange)
        and "days_per_week" in change.before.model_fields_set
        for change in draft.changes
    )


def _is_frequency_only_draft(
    draft: PlanAdjustmentProposalDraft,
) -> bool:
    return (
        len(draft.changes) == 1
        and isinstance(draft.changes[0], PlanAdjustmentScheduleChange)
        and draft.changes[0].before.model_fields_set == {"days_per_week"}
    )


def _has_clear_low_adherence(
    *,
    before: PlanAdjustmentPlanSnapshot,
    observations: list[dict[str, Any]],
) -> bool:
    for observation in reversed(observations):
        if (
            observation.get("tool_id") != "workout.get_progress"
            or observation.get("status") != "success"
        ):
            continue
        result = observation.get("result")
        if not isinstance(result, dict):
            continue
        weeks = result.get("weeks")
        total_sessions = result.get("total_sessions")
        if (
            not isinstance(weeks, int)
            or isinstance(weeks, bool)
            or weeks < 1
            or not isinstance(total_sessions, int)
            or isinstance(total_sessions, bool)
            or total_sessions < 0
        ):
            continue
        expected_sessions = weeks * before.days_per_week
        return (
            expected_sessions >= 4
            and total_sessions / expected_sessions <= 0.5
        )
    return False


def _profile_aligned_frequency_draft(
    *,
    before: PlanAdjustmentPlanSnapshot,
    observations: list[dict[str, Any]],
    requested_ttl_hours: int | None,
) -> PlanAdjustmentProposalDraft | None:
    profile_days = _profile_training_days_per_week(observations)
    if (
        profile_days is None
        or profile_days != before.days_per_week - 1
        or not _has_clear_low_adherence(
            before=before,
            observations=observations,
        )
    ):
        return None

    try:
        removed_day = _deterministic_frequency_reduction_day(before)
    except ValueError:
        return None
    return PlanAdjustmentProposalDraft.model_validate({
        "proposal_type": "plan_adjustment_v1",
        "changes": [{
            "change_type": "update_plan_schedule",
            "stable_display_key": "plan-schedule",
            "before": {"days_per_week": before.days_per_week},
            "after": {"days_per_week": profile_days},
            "reason": (
                f"个人训练目标为每周{profile_days}天，当前计划为每周"
                f"{before.days_per_week}天且近期完成率偏低；按计划总组数"
                f"最少原则移除周{removed_day}训练日。"
            ),
            "safety_priority": False,
        }],
        "rationale": [
            f"个人训练资料目标为每周{profile_days}天，低于当前计划的"
            f"每周{before.days_per_week}天。",
            "近期实际完成率不超过计划频率的一半，先减少一个训练日以降低执行压力。",
        ],
        "safety_notes": [
            "本提案会移除一个完整训练日；确认前请核对完整调整后计划。"
        ],
        "requested_ttl_hours": requested_ttl_hours,
    })


def build_runtime_plan_adjustment_proposal(
    *,
    feature_enabled: bool,
    run_owned: bool,
    selected_outcome: str,
    terminal_action: str,
    intent_allows_adjustment: bool,
    risk_level: str,
    clarification_required: bool,
    observations: list[dict[str, Any]],
    proposal_draft: Mapping[str, Any] | None,
    explicit_adjustment_command: ExplicitPlanAdjustmentCommand | None = None,
    change_requests: list[ChangeRequest] | None = None,
    created_at: datetime,
) -> RuntimePlanAdjustmentProposalBuildResult:
    """Build a server-owned full payload from one compact Finalizer draft."""

    parsed_draft: PlanAdjustmentProposalDraft | None = None
    expects_draft = (
        feature_enabled
        and selected_outcome == "adjustment_proposal"
        and terminal_action == "proposal"
    )
    draft_rejection_reason: PlanAdjustmentProposalCreationReasonCode | None = None
    rejection_reply: str | None = None
    if feature_enabled and explicit_adjustment_command is not None:
        parsed_draft = _explicit_duration_proposal_draft(
            explicit_adjustment_command
        )
    elif feature_enabled and change_requests:
        plan_observation = _active_plan_observation(observations)
        if plan_observation is not None:
            try:
                _, mutation_before = _plan_snapshot_from_observation(
                    plan_observation
                )
                parsed_draft = _structured_plan_mutation_draft(
                    change_requests,
                    before=mutation_before,
                )
            except PlanMutationCompilationRejected as exc:
                draft_rejection_reason = exc.reason_code
                rejection_reply = exc.reply
            except (KeyError, ValidationError, TypeError, ValueError):
                draft_rejection_reason = "proposal_candidate_build_invalid"
                rejection_reply = (
                    "当前训练计划数据不完整，无法安全生成调整提案，计划未作修改。"
                )
    elif feature_enabled and isinstance(proposal_draft, Mapping):
        try:
            parsed_draft = PlanAdjustmentProposalDraft.model_validate(
                dict(proposal_draft)
            )
        except ValidationError:
            parsed_draft = None
            if expects_draft:
                draft_rejection_reason = "proposal_draft_schema_invalid"
    elif expects_draft:
        draft_rejection_reason = "proposal_draft_missing"

    proposal_type = (
        parsed_draft.proposal_type if parsed_draft is not None else ""
    )
    requested_ttl_hours = (
        parsed_draft.requested_ttl_hours
        if parsed_draft is not None
        else None
    )
    decision = evaluate_plan_adjustment_proposal_creation(
        feature_enabled=feature_enabled,
        run_owned=run_owned,
        selected_outcome=selected_outcome,
        terminal_action=terminal_action,
        intent_allows_adjustment=intent_allows_adjustment,
        risk_level=risk_level,
        clarification_required=clarification_required,
        evidence_state=_runtime_evidence_state(
            observations,
            supporting_evidence_required=(
                explicit_adjustment_command is None and not change_requests
            ),
        ),
        draft_state="valid" if parsed_draft is not None else "invalid",
        proposal_type=proposal_type,
        requested_ttl_hours=requested_ttl_hours,
    )
    if not decision.eligible:
        if (
            decision.reason_code == "proposal_draft_invalid"
            and draft_rejection_reason is not None
        ):
            decision = _rejected(draft_rejection_reason)
        elif draft_rejection_reason is not None:
            decision = _rejected(draft_rejection_reason)
        return RuntimePlanAdjustmentProposalBuildResult(
            decision=decision,
            reply=rejection_reply,
        )
    if parsed_draft is None:  # pragma: no cover - gate/draft parity
        return RuntimePlanAdjustmentProposalBuildResult(
            decision=_rejected("proposal_draft_invalid")
        )

    plan_observation = _active_plan_observation(observations)
    if plan_observation is None:  # pragma: no cover - decision parity
        return RuntimePlanAdjustmentProposalBuildResult(
            decision=_rejected("plan_evidence_missing")
        )
    try:
        plan_id, before = _plan_snapshot_from_observation(plan_observation)
    except (KeyError, ValidationError, TypeError, ValueError):
        return RuntimePlanAdjustmentProposalBuildResult(
            decision=_rejected("proposal_candidate_build_invalid")
        )
    if explicit_adjustment_command is None and not change_requests:
        is_frequency_workaround = _is_unsupported_frequency_workaround(
            before=before,
            draft=parsed_draft,
            observations=observations,
        )
        contains_frequency_change = _contains_frequency_change(parsed_draft)
        if contains_frequency_change and not _is_frequency_only_draft(
            parsed_draft
        ):
            return RuntimePlanAdjustmentProposalBuildResult(
                decision=_rejected("proposal_target_mismatch")
            )
        if is_frequency_workaround or contains_frequency_change:
            normalized_draft = _profile_aligned_frequency_draft(
                before=before,
                observations=observations,
                requested_ttl_hours=parsed_draft.requested_ttl_hours,
            )
            if normalized_draft is None:
                return RuntimePlanAdjustmentProposalBuildResult(
                    decision=_rejected(
                        "proposal_frequency_restructure_unsupported"
                        if is_frequency_workaround
                        else "proposal_target_mismatch"
                    )
                )
            parsed_draft = normalized_draft
    try:
        after = apply_plan_adjustment_proposal_draft(before, parsed_draft)
    except (ValidationError, TypeError, ValueError):
        return RuntimePlanAdjustmentProposalBuildResult(
            decision=_rejected("proposal_target_mismatch")
        )
    try:
        base_fingerprint = plan_adjustment_plan_snapshot_fingerprint(before)
        evidence = _runtime_evidence(
            observations,
            observed_at=created_at,
        )
        payload = PlanAdjustmentProposalPayload(
            schema_version="1.0.0",
            proposal_type=parsed_draft.proposal_type,
            target={
                "resource_type": "workout_plan",
                "base_plan_id": plan_id,
                "base_plan_fingerprint": base_fingerprint,
            },
            before=before,
            after=after,
            changes=parsed_draft.changes,
            evidence=evidence,
            rationale=parsed_draft.rationale,
            safety_notes=parsed_draft.safety_notes,
        )
        built = build_validated_plan_adjustment_proposal(
            decision=decision,
            payload_data=payload,
            expected_base_plan_id=plan_id,
            expected_base_plan_fingerprint=base_fingerprint,
            created_at=created_at,
            supporting_evidence_required=(
                explicit_adjustment_command is None and not change_requests
            ),
        )
    except (
        PlanAdjustmentProposalCreationRejected,
        PlanAdjustmentProposalPayloadRejected,
        ValidationError,
        TypeError,
        ValueError,
    ):
        return RuntimePlanAdjustmentProposalBuildResult(
            decision=_rejected("proposal_candidate_build_invalid")
        )
    return RuntimePlanAdjustmentProposalBuildResult(
        decision=decision,
        built=built,
    )
