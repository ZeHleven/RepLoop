from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.schemas.agent_plan_adjustment_proposal import (
    PlanAdjustmentProposalPayload,
)
from app.services.agent_intent import ChangeRequest, ExplicitPlanAdjustmentCommand
from app.services.agent_plan_adjustment_proposals import (
    PlanAdjustmentProposalCreationRejected,
    PlanAdjustmentProposalPayloadRejected,
    build_runtime_plan_adjustment_proposal,
    build_validated_plan_adjustment_proposal,
    canonical_plan_adjustment_proposal_payload_data,
    evaluate_plan_adjustment_proposal_creation,
    plan_adjustment_proposal_payload_fingerprint,
)


_CASES_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "agent_plan_adjustment_proposal_contract_cases.json"
)
_CREATED_AT = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)


def _canonical_payloads() -> dict[str, dict]:
    fixture = json.loads(_CASES_PATH.read_text(encoding="utf-8"))
    return fixture["canonical_payloads"]


def _eligible_decision(*, ttl_hours: int | None = None):
    return evaluate_plan_adjustment_proposal_creation(
        feature_enabled=True,
        run_owned=True,
        selected_outcome="adjustment_proposal",
        terminal_action="proposal",
        intent_allows_adjustment=True,
        risk_level="low",
        clarification_required=False,
        evidence_state="complete",
        draft_state="valid",
        proposal_type="plan_adjustment_v1",
        requested_ttl_hours=ttl_hours,
    )


@pytest.mark.parametrize(
    ("overrides", "reason_code"),
    [
        ({"risk_level": "unknown"}, "health_red_flag"),
        ({"evidence_state": "unknown"}, "supporting_evidence_missing"),
        ({"draft_state": "unknown"}, "proposal_draft_invalid"),
        ({"requested_ttl_hours": 0}, "proposal_ttl_out_of_range"),
        ({"requested_ttl_hours": True}, "proposal_ttl_out_of_range"),
    ],
)
def test_creation_gate_fails_closed_for_unknown_or_invalid_facts(
    overrides,
    reason_code,
):
    facts = {
        "feature_enabled": True,
        "run_owned": True,
        "selected_outcome": "adjustment_proposal",
        "terminal_action": "proposal",
        "intent_allows_adjustment": True,
        "risk_level": "low",
        "clarification_required": False,
        "evidence_state": "complete",
        "draft_state": "valid",
        "proposal_type": "plan_adjustment_v1",
        "requested_ttl_hours": None,
    }
    facts.update(overrides)

    decision = evaluate_plan_adjustment_proposal_creation(**facts)

    assert decision.eligible is False
    assert decision.reason_code == reason_code


def _build(payload: dict, *, ttl_hours: int | None = None):
    return build_validated_plan_adjustment_proposal(
        decision=_eligible_decision(ttl_hours=ttl_hours),
        payload_data=payload,
        expected_base_plan_id=payload["target"]["base_plan_id"],
        expected_base_plan_fingerprint=payload["target"][
            "base_plan_fingerprint"
        ],
        created_at=_CREATED_AT,
    )


def test_builder_normalizes_both_canonical_payloads_without_mutating_inputs():
    for original in _canonical_payloads().values():
        payload = copy.deepcopy(original)
        built = _build(payload)

        assert payload == original
        assert built.initial_status == "pending_confirmation"
        assert built.ttl_hours == 24
        assert built.expires_at == _CREATED_AT + timedelta(hours=24)
        assert canonical_plan_adjustment_proposal_payload_data(
            built.payload
        ) == original
        assert built.payload_fingerprint == (
            plan_adjustment_proposal_payload_fingerprint(built.payload)
        )


def test_builder_fingerprint_is_sha256_of_stable_canonical_json():
    original = _canonical_payloads()["adherence"]
    reordered = dict(reversed(list(original.items())))
    first = _build(original, ttl_hours=72)
    second = _build(reordered, ttl_hours=72)
    canonical_json = json.dumps(
        canonical_plan_adjustment_proposal_payload_data(first.payload),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )

    assert first.payload_fingerprint == second.payload_fingerprint
    assert first.payload_fingerprint == hashlib.sha256(
        canonical_json.encode("utf-8")
    ).hexdigest()
    assert first.expires_at == _CREATED_AT + timedelta(hours=72)


def test_rejected_gate_short_circuits_before_payload_validation():
    decision = evaluate_plan_adjustment_proposal_creation(
        feature_enabled=False,
        run_owned=True,
        selected_outcome="adjustment_proposal",
        terminal_action="proposal",
        intent_allows_adjustment=True,
        risk_level="low",
        clarification_required=False,
        evidence_state="complete",
        draft_state="valid",
        proposal_type="plan_adjustment_v1",
        requested_ttl_hours=None,
    )

    with pytest.raises(
        PlanAdjustmentProposalCreationRejected,
        match="feature_disabled",
    ) as raised:
        build_validated_plan_adjustment_proposal(
            decision=decision,
            payload_data={"raw_result": "must-not-be-processed"},
            expected_base_plan_id="ignored",
            expected_base_plan_fingerprint="ignored",
            created_at=_CREATED_AT,
        )

    assert raised.value.reason_code == "feature_disabled"


def test_builder_rejects_private_or_mismatched_payloads_with_stable_codes():
    private_payload = copy.deepcopy(_canonical_payloads()["adherence"])
    private_payload["user_id"] = "private-user-value"

    with pytest.raises(PlanAdjustmentProposalPayloadRejected) as private:
        _build(private_payload)
    assert private.value.error_codes == ("forbidden_field",)
    assert "private-user-value" not in str(private.value)

    payload = _canonical_payloads()["adherence"]
    with pytest.raises(PlanAdjustmentProposalPayloadRejected) as mismatch:
        build_validated_plan_adjustment_proposal(
            decision=_eligible_decision(),
            payload_data=payload,
            expected_base_plan_id="another-active-plan",
            expected_base_plan_fingerprint=payload["target"][
                "base_plan_fingerprint"
            ],
            created_at=_CREATED_AT,
        )
    assert mismatch.value.error_codes == ("invalid_target",)


@pytest.mark.parametrize(
    ("mutation", "reason_code"),
    [
        ("missing_plan", "plan_evidence_missing"),
        ("missing_support", "supporting_evidence_missing"),
        ("unknown_tool", "supporting_evidence_missing"),
        ("future_evidence", "supporting_evidence_missing"),
        ("duplicate_evidence", "proposal_draft_invalid"),
    ],
)
def test_builder_rechecks_actual_evidence_after_gate(
    mutation,
    reason_code,
):
    payload = copy.deepcopy(_canonical_payloads()["adherence"])
    if mutation == "missing_plan":
        payload["evidence"] = payload["evidence"][1:]
    elif mutation == "missing_support":
        payload["evidence"] = payload["evidence"][:1]
    elif mutation == "unknown_tool":
        payload["evidence"][1]["tool_id"] = "unknown.get_private"
    elif mutation == "future_evidence":
        payload["evidence"][1]["observed_at"] = (
            _CREATED_AT + timedelta(seconds=1)
        ).isoformat()
    else:
        payload["evidence"].append(copy.deepcopy(payload["evidence"][1]))

    with pytest.raises(
        PlanAdjustmentProposalCreationRejected,
        match=reason_code,
    ) as raised:
        _build(payload)
    assert raised.value.reason_code == reason_code


def test_builder_accepts_an_already_validated_immutable_payload():
    raw = _canonical_payloads()["health"]
    validated = PlanAdjustmentProposalPayload.model_validate(raw)
    built = build_validated_plan_adjustment_proposal(
        decision=_eligible_decision(),
        payload_data=validated,
        expected_base_plan_id=validated.target.base_plan_id,
        expected_base_plan_fingerprint=(
            validated.target.base_plan_fingerprint
        ),
        created_at=_CREATED_AT,
    )

    assert built.payload is validated


def _runtime_observations() -> list[dict]:
    before = copy.deepcopy(_canonical_payloads()["adherence"]["before"])
    plan = {
        "id": "runtime-active-plan",
        "name": before["name"],
        "goal": before["goal"],
        "duration_weeks": before["duration_weeks"],
        "days_per_week": before["days_per_week"],
        "exercises": [{
            key: value
            for key, value in exercise.items()
            if key != "slot_key"
        } for exercise in before["exercises"]],
    }
    return [
        {
            "tool_id": "plan.get_active",
            "status": "success",
            "result": {"found": True, "plan": plan},
        },
        {
            "tool_id": "workout.get_progress",
            "status": "success",
            "result": {"weeks": 4, "total_sessions": 1},
        },
    ]


def _runtime_draft() -> dict:
    payload = _canonical_payloads()["adherence"]
    return {
        "proposal_type": "plan_adjustment_v1",
        "changes": copy.deepcopy(payload["changes"]),
        "rationale": copy.deepcopy(payload["rationale"]),
        "safety_notes": copy.deepcopy(payload["safety_notes"]),
        "requested_ttl_hours": 24,
    }


def test_runtime_builder_derives_full_snapshots_target_and_evidence():
    result = build_runtime_plan_adjustment_proposal(
        feature_enabled=True,
        run_owned=True,
        selected_outcome="adjustment_proposal",
        terminal_action="proposal",
        intent_allows_adjustment=True,
        risk_level="low",
        clarification_required=False,
        observations=_runtime_observations(),
        proposal_draft=_runtime_draft(),
        created_at=_CREATED_AT,
    )

    assert result.decision.eligible is True
    assert result.built is not None
    payload = result.built.payload
    assert payload.target.base_plan_id == "runtime-active-plan"
    assert payload.before.exercises[0].sets == 4
    assert payload.after.exercises[0].sets == 3
    assert payload.after.exercises[0].rest_seconds == 150
    assert payload.after.exercises[0].recommended_weight_kg == 57.5
    assert {item.tool_id for item in payload.evidence} == {
        "plan.get_active",
        "workout.get_progress",
    }


def test_runtime_builder_accepts_authoritative_plan_duration_extension():
    draft = {
        "proposal_type": "plan_adjustment_v1",
        "changes": [{
            "change_type": "update_plan_schedule",
            "stable_display_key": "plan-schedule",
            "before": {"duration_weeks": 4},
            "after": {"duration_weeks": 6},
            "reason": "延长计划周期以降低每周推进压力。",
            "safety_priority": False,
        }],
        "rationale": ["近期完成率偏低，先给计划留出更多适应时间。"],
        "safety_notes": [],
        "requested_ttl_hours": 24,
    }

    result = build_runtime_plan_adjustment_proposal(
        feature_enabled=True,
        run_owned=True,
        selected_outcome="adjustment_proposal",
        terminal_action="proposal",
        intent_allows_adjustment=True,
        risk_level="low",
        clarification_required=False,
        observations=_runtime_observations(),
        proposal_draft=draft,
        created_at=_CREATED_AT,
    )

    assert result.decision.eligible is True
    assert result.built is not None
    assert result.built.payload.before.duration_weeks == 4
    assert result.built.payload.after.duration_weeks == 6
    assert result.built.payload.before.days_per_week == (
        result.built.payload.after.days_per_week
    )


def test_explicit_duration_command_accepts_plan_only_and_owns_exact_diff():
    observations = _runtime_observations()[:1]
    untrusted_extra_change = _runtime_draft()
    command = ExplicitPlanAdjustmentCommand(
        expected_duration_weeks=4,
        target_duration_weeks=6,
    )

    result = build_runtime_plan_adjustment_proposal(
        feature_enabled=True,
        run_owned=True,
        selected_outcome="adjustment_proposal",
        terminal_action="proposal",
        intent_allows_adjustment=True,
        risk_level="low",
        clarification_required=False,
        observations=observations,
        proposal_draft=untrusted_extra_change,
        explicit_adjustment_command=command,
        created_at=_CREATED_AT,
    )

    assert result.decision.eligible is True
    assert result.built is not None
    payload = result.built.payload
    assert payload.before.duration_weeks == 4
    assert payload.after.duration_weeks == 6
    assert payload.before.days_per_week == payload.after.days_per_week
    assert payload.before.exercises == payload.after.exercises
    assert len(payload.changes) == 1
    assert payload.changes[0].change_type == "update_plan_schedule"
    assert payload.changes[0].before.model_fields_set == {"duration_weeks"}
    assert {item.tool_id for item in payload.evidence} == {
        "plan.get_active"
    }


def test_structured_frequency_mutation_builds_four_to_three_proposal_from_plan_only():
    observations = _profile_frequency_runtime_observations()
    plan_only = [
        item for item in observations if item["tool_id"] == "plan.get_active"
    ]

    result = build_runtime_plan_adjustment_proposal(
        feature_enabled=True,
        run_owned=True,
        selected_outcome="adjustment_proposal",
        terminal_action="proposal",
        intent_allows_adjustment=True,
        risk_level="low",
        clarification_required=False,
        observations=plan_only,
        proposal_draft=None,
        change_requests=[ChangeRequest(
            resource="workout_plan",
            operation="update",
            field_path="schedule.days_per_week",
            value=3,
        )],
        created_at=_CREATED_AT,
    )

    assert result.decision.eligible is True
    assert result.built is not None
    assert result.built.payload.before.days_per_week == 4
    assert result.built.payload.after.days_per_week == 3
    assert {item.tool_id for item in result.built.payload.evidence} == {
        "plan.get_active"
    }
    assert len(result.built.payload.after.exercises) == 3


def test_structured_frequency_mutation_rejects_more_than_one_day_change():
    observations = _profile_frequency_runtime_observations()
    plan_only = [
        item for item in observations if item["tool_id"] == "plan.get_active"
    ]

    result = build_runtime_plan_adjustment_proposal(
        feature_enabled=True,
        run_owned=True,
        selected_outcome="adjustment_proposal",
        terminal_action="proposal",
        intent_allows_adjustment=True,
        risk_level="low",
        clarification_required=False,
        observations=plan_only,
        proposal_draft=None,
        change_requests=[ChangeRequest(
            resource="workout_plan",
            operation="update",
            field_path="schedule.days_per_week",
            value=2,
        )],
        created_at=_CREATED_AT,
    )

    assert result.built is None
    assert result.decision.reason_code == (
        "proposal_frequency_restructure_unsupported"
    )
    assert result.reply is not None
    assert "不能直接调整为2天" in result.reply


def test_structured_exercise_mutation_resolves_one_named_target():
    observations = _runtime_observations()[:1]
    exercise = observations[0]["result"]["plan"]["exercises"][0]

    result = build_runtime_plan_adjustment_proposal(
        feature_enabled=True,
        run_owned=True,
        selected_outcome="adjustment_proposal",
        terminal_action="proposal",
        intent_allows_adjustment=True,
        risk_level="low",
        clarification_required=False,
        observations=observations,
        proposal_draft=None,
        change_requests=[ChangeRequest(
            resource="workout_plan",
            operation="update",
            field_path="exercise.sets",
            target_reference=exercise["exercise_name"],
            value=3,
        )],
        created_at=_CREATED_AT,
    )

    assert result.decision.eligible is True
    assert result.built is not None
    change = result.built.payload.changes[0]
    assert change.change_type == "adjust_exercise_target"
    assert change.before.sets == 4
    assert change.after.sets == 3


@pytest.mark.parametrize(
    ("field_path", "target_value", "attribute", "expected_value"),
    [
        ("exercise.reps", "10～12", "reps", "10-12"),
        ("exercise.rest_seconds", 90, "rest_seconds", 90),
        (
            "exercise.recommended_weight_kg",
            42.5,
            "recommended_weight_kg",
            42.5,
        ),
    ],
)
def test_structured_exercise_mutation_compiles_each_supported_target(
    field_path,
    target_value,
    attribute,
    expected_value,
):
    observations = _runtime_observations()[:1]
    exercise_name = observations[0]["result"]["plan"]["exercises"][0][
        "exercise_name"
    ]

    result = build_runtime_plan_adjustment_proposal(
        feature_enabled=True,
        run_owned=True,
        selected_outcome="adjustment_proposal",
        terminal_action="proposal",
        intent_allows_adjustment=True,
        risk_level="low",
        clarification_required=False,
        observations=observations,
        proposal_draft=None,
        change_requests=[ChangeRequest(
            resource="workout_plan",
            operation="update",
            field_path=field_path,
            target_reference=exercise_name,
            value=target_value,
        )],
        created_at=_CREATED_AT,
    )

    assert result.decision.eligible is True
    assert result.built is not None
    change = result.built.payload.changes[0]
    assert getattr(change.after, attribute) == expected_value
    assert len(result.built.payload.evidence) == 1


def test_structured_duration_mutation_compiles_from_authoritative_plan():
    result = build_runtime_plan_adjustment_proposal(
        feature_enabled=True,
        run_owned=True,
        selected_outcome="adjustment_proposal",
        terminal_action="proposal",
        intent_allows_adjustment=True,
        risk_level="low",
        clarification_required=False,
        observations=_runtime_observations()[:1],
        proposal_draft=None,
        change_requests=[ChangeRequest(
            resource="workout_plan",
            operation="update",
            field_path="schedule.duration_weeks",
            value=6,
        )],
        created_at=_CREATED_AT,
    )

    assert result.decision.eligible is True
    assert result.built is not None
    assert result.built.payload.before.duration_weeks == 4
    assert result.built.payload.after.duration_weeks == 6


@pytest.mark.parametrize(
    ("change", "reason_code"),
    [
        (
            ChangeRequest(
                resource="workout_plan",
                operation="update",
                field_path="schedule.duration_weeks",
                value=4,
            ),
            "proposal_no_change",
        ),
        (
            ChangeRequest(
                resource="workout_plan",
                operation="update",
                field_path="schedule.duration_weeks",
                value=13,
            ),
            "proposal_target_mismatch",
        ),
        (
            ChangeRequest(
                resource="workout_plan",
                operation="update",
                field_path="schedule.duration_weeks",
                value=None,
            ),
            "proposal_target_value_required",
        ),
        (
            ChangeRequest(
                resource="workout_plan",
                operation="create",
                field_path="exercises",
                value={"exercise_name": "弓步蹲"},
            ),
            "proposal_operation_unsupported",
        ),
    ],
)
def test_structured_mutation_rejections_are_explicit_and_write_free(
    change,
    reason_code,
):
    result = build_runtime_plan_adjustment_proposal(
        feature_enabled=True,
        run_owned=True,
        selected_outcome="adjustment_proposal",
        terminal_action="proposal",
        intent_allows_adjustment=True,
        risk_level="low",
        clarification_required=False,
        observations=_runtime_observations()[:1],
        proposal_draft=None,
        change_requests=[change],
        created_at=_CREATED_AT,
    )

    assert result.built is None
    assert result.decision.reason_code == reason_code
    assert result.reply is not None


def test_structured_exercise_mutation_requires_a_unique_plan_match():
    observations = _runtime_observations()[:1]
    plan = observations[0]["result"]["plan"]
    duplicate = copy.deepcopy(plan["exercises"][0])
    duplicate["day_of_week"] = 2
    plan["days_per_week"] = 2
    plan["exercises"].append(duplicate)

    result = build_runtime_plan_adjustment_proposal(
        feature_enabled=True,
        run_owned=True,
        selected_outcome="adjustment_proposal",
        terminal_action="proposal",
        intent_allows_adjustment=True,
        risk_level="low",
        clarification_required=False,
        observations=observations,
        proposal_draft=None,
        change_requests=[ChangeRequest(
            resource="workout_plan",
            operation="update",
            field_path="exercise.sets",
            target_reference=duplicate["exercise_name"],
            value=3,
        )],
        created_at=_CREATED_AT,
    )

    assert result.built is None
    assert result.decision.reason_code == "proposal_target_ambiguous"
    assert result.reply is not None
    assert "没有唯一匹配" in result.reply


def test_evidence_derived_adjustment_still_rejects_plan_only_observation():
    result = build_runtime_plan_adjustment_proposal(
        feature_enabled=True,
        run_owned=True,
        selected_outcome="adjustment_proposal",
        terminal_action="proposal",
        intent_allows_adjustment=True,
        risk_level="low",
        clarification_required=False,
        observations=_runtime_observations()[:1],
        proposal_draft=_runtime_draft(),
        created_at=_CREATED_AT,
    )

    assert result.built is None
    assert result.decision.reason_code == "supporting_evidence_missing"


def test_explicit_duration_command_rejects_stale_user_baseline():
    result = build_runtime_plan_adjustment_proposal(
        feature_enabled=True,
        run_owned=True,
        selected_outcome="adjustment_proposal",
        terminal_action="proposal",
        intent_allows_adjustment=True,
        risk_level="low",
        clarification_required=False,
        observations=_runtime_observations()[:1],
        proposal_draft=None,
        explicit_adjustment_command=ExplicitPlanAdjustmentCommand(
            expected_duration_weeks=6,
            target_duration_weeks=8,
        ),
        created_at=_CREATED_AT,
    )

    assert result.built is None
    assert result.decision.reason_code == "proposal_target_mismatch"


def test_explicit_duration_command_is_not_inferred_frequency_workaround():
    observations = _runtime_observations()
    plan = observations[0]["result"]["plan"]
    exercise = copy.deepcopy(plan["exercises"][0])
    plan["days_per_week"] = 4
    plan["exercises"] = [
        {
            **copy.deepcopy(exercise),
            "day_of_week": day_of_week,
            "order_index": 0,
        }
        for day_of_week in (1, 2, 4, 6)
    ]
    observations.insert(0, {
        "tool_id": "profile.get_summary",
        "status": "success",
        "result": {
            "found": True,
            "training_days_per_week": 3,
        },
    })

    result = build_runtime_plan_adjustment_proposal(
        feature_enabled=True,
        run_owned=True,
        selected_outcome="adjustment_proposal",
        terminal_action="proposal",
        intent_allows_adjustment=True,
        risk_level="low",
        clarification_required=False,
        observations=observations,
        proposal_draft=None,
        explicit_adjustment_command=ExplicitPlanAdjustmentCommand(
            expected_duration_weeks=4,
            target_duration_weeks=6,
        ),
        created_at=_CREATED_AT,
    )

    assert result.decision.eligible is True
    assert result.built is not None
    assert result.built.payload.before.days_per_week == 4
    assert result.built.payload.after.days_per_week == 4


def _profile_frequency_runtime_observations() -> list[dict]:
    observations = _runtime_observations()
    plan = observations[0]["result"]["plan"]
    exercise = copy.deepcopy(plan["exercises"][0])
    plan["duration_weeks"] = 8
    plan["days_per_week"] = 4
    plan["exercises"] = [
        {
            **copy.deepcopy(exercise),
            "day_of_week": day_of_week,
            "order_index": 0,
        }
        for day_of_week in (1, 2, 4, 6)
    ]
    observations.insert(0, {
        "tool_id": "profile.get_summary",
        "status": "success",
        "result": {
            "found": True,
            "training_days_per_week": 3,
        },
    })
    return observations


def test_runtime_builder_normalizes_duration_workaround_to_profile_frequency():
    observations = _profile_frequency_runtime_observations()
    draft = {
        "proposal_type": "plan_adjustment_v1",
        "changes": [{
            "change_type": "update_plan_schedule",
            "stable_display_key": "plan-schedule",
            "before": {"duration_weeks": 8},
            "after": {"duration_weeks": 10},
            "reason": "延长周期，使每周实际训练约三次。",
            "safety_priority": False,
        }],
        "rationale": ["个人资料目标为每周三天，当前计划为每周四天。"],
        "safety_notes": [],
        "requested_ttl_hours": 24,
    }

    result = build_runtime_plan_adjustment_proposal(
        feature_enabled=True,
        run_owned=True,
        selected_outcome="adjustment_proposal",
        terminal_action="proposal",
        intent_allows_adjustment=True,
        risk_level="low",
        clarification_required=False,
        observations=observations,
        proposal_draft=draft,
        created_at=_CREATED_AT,
    )

    assert result.decision.eligible is True
    assert result.built is not None
    payload = result.built.payload
    assert payload.before.duration_weeks == payload.after.duration_weeks == 8
    assert payload.before.days_per_week == 4
    assert payload.after.days_per_week == 3
    assert {item.day_of_week for item in payload.after.exercises} == {1, 2, 4}
    assert len(payload.before.exercises) == 4
    assert len(payload.after.exercises) == 3
    assert len(payload.changes) == 1
    change = payload.changes[0]
    assert change.change_type == "update_plan_schedule"
    assert change.before.model_dump(exclude_unset=True) == {
        "days_per_week": 4,
    }
    assert change.after.model_dump(exclude_unset=True) == {
        "days_per_week": 3,
    }


def test_runtime_builder_accepts_server_validated_frequency_draft():
    observations = _profile_frequency_runtime_observations()
    draft = {
        "proposal_type": "plan_adjustment_v1",
        "changes": [{
            "change_type": "update_plan_schedule",
            "stable_display_key": "plan-schedule",
            "before": {"days_per_week": 4},
            "after": {"days_per_week": 3},
            "reason": "按个人目标频率减少一个训练日。",
            "safety_priority": False,
        }],
        "rationale": ["当前四天计划完成率偏低，个人目标为每周三天。"],
        "safety_notes": [],
        "requested_ttl_hours": 24,
    }

    result = build_runtime_plan_adjustment_proposal(
        feature_enabled=True,
        run_owned=True,
        selected_outcome="adjustment_proposal",
        terminal_action="proposal",
        intent_allows_adjustment=True,
        risk_level="low",
        clarification_required=False,
        observations=observations,
        proposal_draft=draft,
        created_at=_CREATED_AT,
    )

    assert result.decision.eligible is True
    assert result.built is not None
    assert result.built.payload.after.days_per_week == 3
    assert {
        item.day_of_week for item in result.built.payload.after.exercises
    } == {1, 2, 4}


@pytest.mark.parametrize("invalid_evidence", ["profile", "adherence"])
def test_runtime_builder_rejects_unproven_frequency_reduction(
    invalid_evidence,
):
    observations = _profile_frequency_runtime_observations()
    if invalid_evidence == "profile":
        observations[0]["result"]["training_days_per_week"] = 2
    else:
        observations[-1]["result"]["total_sessions"] = 9
    draft = {
        "proposal_type": "plan_adjustment_v1",
        "changes": [{
            "change_type": "update_plan_schedule",
            "stable_display_key": "plan-schedule",
            "before": {"days_per_week": 4},
            "after": {"days_per_week": 3},
            "reason": "按个人目标频率减少一个训练日。",
            "safety_priority": False,
        }],
        "rationale": ["尝试调整训练频率。"],
        "safety_notes": [],
        "requested_ttl_hours": 24,
    }

    result = build_runtime_plan_adjustment_proposal(
        feature_enabled=True,
        run_owned=True,
        selected_outcome="adjustment_proposal",
        terminal_action="proposal",
        intent_allows_adjustment=True,
        risk_level="low",
        clarification_required=False,
        observations=observations,
        proposal_draft=draft,
        created_at=_CREATED_AT,
    )

    assert result.built is None
    assert result.decision.reason_code == "proposal_target_mismatch"


@pytest.mark.parametrize(
    ("failure_stage", "expected_reason"),
    [
        ("missing", "proposal_draft_missing"),
        ("schema", "proposal_draft_schema_invalid"),
        ("target", "proposal_target_mismatch"),
        ("candidate", "proposal_candidate_build_invalid"),
    ],
)
def test_runtime_builder_reports_privacy_safe_stage_specific_reasons(
    failure_stage,
    expected_reason,
):
    observations = _runtime_observations()
    draft = _runtime_draft()
    if failure_stage == "missing":
        draft = None
    elif failure_stage == "schema":
        draft["changes"] = []
    elif failure_stage == "target":
        draft["changes"][0]["stable_display_key"] = "private-unknown-slot"
    else:
        del observations[0]["result"]["plan"]["exercises"][0][
            "exercise_name"
        ]

    result = build_runtime_plan_adjustment_proposal(
        feature_enabled=True,
        run_owned=True,
        selected_outcome="adjustment_proposal",
        terminal_action="proposal",
        intent_allows_adjustment=True,
        risk_level="low",
        clarification_required=False,
        observations=observations,
        proposal_draft=draft,
        created_at=_CREATED_AT,
    )

    assert result.built is None
    assert result.decision.reason_code == expected_reason
    assert "private-unknown-slot" not in str(result.decision)


def test_runtime_builder_does_not_require_draft_for_nonproposal_outcome():
    result = build_runtime_plan_adjustment_proposal(
        feature_enabled=True,
        run_owned=True,
        selected_outcome="informational_answer",
        terminal_action="answer",
        intent_allows_adjustment=True,
        risk_level="low",
        clarification_required=False,
        observations=_runtime_observations(),
        proposal_draft=None,
        created_at=_CREATED_AT,
    )

    assert result.built is None
    assert result.decision.reason_code == "outcome_not_adjustment_proposal"


@pytest.mark.parametrize("unsafe_change", ["replacement", "frequency"])
def test_runtime_builder_rejects_changes_outside_the_first_cohort(
    unsafe_change,
):
    draft = _runtime_draft()
    if unsafe_change == "replacement":
        draft["changes"] = copy.deepcopy(
            _canonical_payloads()["health"]["changes"]
        )
    else:
        draft["changes"] = [{
            "change_type": "update_plan_schedule",
            "stable_display_key": "plan-schedule",
            "before": {"days_per_week": 1},
            "after": {"days_per_week": 2},
            "reason": "增加计划频率。",
            "safety_priority": False,
        }]

    result = build_runtime_plan_adjustment_proposal(
        feature_enabled=True,
        run_owned=True,
        selected_outcome="adjustment_proposal",
        terminal_action="proposal",
        intent_allows_adjustment=True,
        risk_level="low",
        clarification_required=False,
        observations=_runtime_observations(),
        proposal_draft=draft,
        created_at=_CREATED_AT,
    )

    assert result.built is None
    assert result.decision.reason_code == "proposal_target_mismatch"
