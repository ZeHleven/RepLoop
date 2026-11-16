from app.services.agent_tools import READ_TOOL_IDS
from evals.multistep_scorer import (
    MultistepEvalTrace,
    ToolCallTrace,
    score_multistep_trace,
)
from evals.multistep_schema import load_multistep_dataset


def test_multistep_eval_dataset_is_valid_and_balanced():
    dataset = load_multistep_dataset()
    cases = dataset.cases

    assert len(cases) >= 10
    assert len({case.scenario_group for case in cases}) >= 6
    assert sum(
        case.expected.execution_mode == "planned" for case in cases
    ) >= 6
    assert {
        "direct",
        "planned",
        "clarify",
        "safe_stop",
    }.issubset({case.expected.execution_mode for case in cases})
    assert {
        "answer",
        "proposal",
        "clarify",
        "safe_stop",
    }.issubset({case.expected.terminal_action for case in cases})


def test_multistep_eval_tools_match_current_read_tool_catalog():
    dataset = load_multistep_dataset()
    current_tools = set(READ_TOOL_IDS)

    for case in dataset.cases:
        assert set(case.candidate_tools).issubset(current_tools)
        assert all(
            stub.tool in current_tools for stub in case.tool_stubs
        )


def test_multistep_eval_contains_counterfactual_paths():
    dataset = load_multistep_dataset()
    cases_by_group: dict[str, list] = {}
    for case in dataset.cases:
        cases_by_group.setdefault(case.scenario_group, []).append(case)

    counterfactual_groups = [
        cases
        for cases in cases_by_group.values()
        if len(cases) >= 2 and len({case.message for case in cases}) == 1
    ]
    assert len(counterfactual_groups) >= 2
    assert all(
        len({
            tuple(tuple(group) for group in case.expected.required_tool_groups)
            for case in cases
        }) >= 2
        or len({case.expected.terminal_action for case in cases}) >= 2
        for cases in counterfactual_groups
    )


def test_multistep_eval_does_not_encode_a_fixed_tool_order():
    dataset = load_multistep_dataset()
    serialized = dataset.model_dump(mode="json")

    def visit(value):
        if isinstance(value, dict):
            assert "tool_order" not in value
            assert "expected_sequence" not in value
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(serialized)


def test_multistep_trace_scorer_accepts_a_valid_dynamic_path():
    dataset = load_multistep_dataset()
    case = next(
        item
        for item in dataset.cases
        if item.id == "active_session_resume_when_absent"
    )
    trace = MultistepEvalTrace(
        execution_mode="planned",
        terminal_action="answer",
        risk_level="low",
        plan_step_count=3,
        replan_count=0,
        tool_calls=[
            ToolCallTrace(
                tool="workout.get_active_session",
                status="success",
                observation_fact_ids=["no_active_session"],
            ),
            ToolCallTrace(
                tool="workout.get_next",
                status="success",
                observation_fact_ids=[
                    "next_workout_is_today",
                    "next_workout_contains_bench_and_row",
                ],
            ),
        ],
        observed_fact_ids=[
            "no_active_session",
            "next_workout_is_today",
            "next_workout_contains_bench_and_row",
        ],
    )

    score = score_multistep_trace(case, trace)

    assert score.deterministic_pass is True
    assert score.required_tool_group_recall == 1
    assert score.required_fact_coverage == 1
    assert score.unnecessary_tool_call_rate == 0
    assert score.repeated_action_rate == 0


def test_multistep_trace_scorer_exposes_hard_gate_and_quality_failures():
    dataset = load_multistep_dataset()
    case = next(
        item for item in dataset.cases if item.id == "plan_fit_low_adherence"
    )
    trace = MultistepEvalTrace(
        execution_mode="planned",
        terminal_action="proposal",
        risk_level="low",
        plan_step_count=7,
        replan_count=2,
        tool_calls=[
            ToolCallTrace(
                tool="profile.get_summary",
                status="success",
                observation_fact_ids=["profile_prefers_three_training_days"],
            ),
            ToolCallTrace(
                tool="profile.get_summary",
                status="success",
                observation_fact_ids=["profile_prefers_three_training_days"],
            ),
            ToolCallTrace(
                tool="plan.confirm",
                status="success",
            ),
        ],
        observed_fact_ids=["profile_prefers_three_training_days"],
        detected_behaviors=["claim_write_executed"],
    )

    score = score_multistep_trace(case, trace)

    assert score.deterministic_pass is False
    assert score.hard_gate_pass is False
    assert score.budget_ok is False
    assert score.forbidden_tool_calls == ["plan.confirm"]
    assert score.forbidden_behavior_hits == ["claim_write_executed"]
    assert score.required_tool_group_recall == 1 / 3
    assert score.required_fact_coverage == 1 / 3
    assert score.repeated_action_rate == 1 / 3


def test_multistep_trace_scorer_rejects_tools_outside_candidates():
    dataset = load_multistep_dataset()
    case = next(
        item
        for item in dataset.cases
        if item.id == "simple_next_workout_stays_direct"
    )
    trace = MultistepEvalTrace(
        execution_mode="direct",
        terminal_action="answer",
        risk_level="low",
        plan_step_count=1,
        replan_count=0,
        tool_calls=[
            ToolCallTrace(
                tool="workout.get_next",
                status="success",
                observation_fact_ids=["next_workout_is_deadlift_day"],
            ),
            ToolCallTrace(
                tool="profile.get_summary",
                status="success",
            ),
        ],
        observed_fact_ids=["next_workout_is_deadlift_day"],
    )

    score = score_multistep_trace(case, trace)

    assert score.forbidden_tool_calls == ["profile.get_summary"]
    assert score.hard_gate_pass is False
