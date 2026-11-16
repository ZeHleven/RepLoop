import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.agent_planning import (
    FinalizationDecision,
    MicroPlan,
    MicroPlanDraft,
    MicroPlanStep,
    ModelInvocationMetrics,
    PlannedToolAction,
    ProposalFinalizationDecision,
)
from app.services.agent_planner import (
    ModelPlanningPolicy,
    PlanningModelError,
    compact_planner_tool_catalog,
)
from app.services.ai_client import AIServiceError


class FakeStructuredRunnable:
    def __init__(self, parsed):
        self.parsed = parsed

    async def ainvoke(self, _messages):
        self.messages = _messages
        output = (
            self.parsed.model_dump(mode="json")
            if hasattr(self.parsed, "model_dump") else self.parsed
        )
        return {
            "parsed": self.parsed,
            "raw": SimpleNamespace(
                content=json.dumps(output, ensure_ascii=False),
                response_metadata={"finish_reason": "stop"},
                usage_metadata={
                    "input_tokens": 120,
                    "output_tokens": 40,
                },
            ),
        }


class FakeStructuredModel:
    def __init__(self, parsed):
        self.parsed = parsed
        self.schema = None

    def with_structured_output(
        self,
        schema,
        *,
        method,
        include_raw,
    ):
        self.schema = schema
        assert method == "json_mode"
        assert include_raw is True
        parsed = self.parsed
        if schema is MicroPlanDraft and isinstance(parsed, MicroPlan):
            parsed = MicroPlanDraft(steps=parsed.steps)
        self.runnable = FakeStructuredRunnable(parsed)
        return self.runnable


class FakeParsingError(Exception):
    def errors(self):
        return [{
            "type": "literal_error",
            "loc": ("steps", 0, "execution_strategy"),
            "input": "must-not-leak",
        }]


class FakeInvalidStructuredRunnable:
    async def ainvoke(self, _messages):
        return {"parsed": None, "parsing_error": FakeParsingError()}


class FakeInvalidStructuredModel:
    def with_structured_output(self, *_args, **_kwargs):
        return FakeInvalidStructuredRunnable()


def _plan(tool_id: str) -> MicroPlan:
    return MicroPlan(
        goal="模型提供的目标会被服务端目标覆盖",
        steps=[
            MicroPlanStep(
                objective="取得第一项证据",
                candidate_tools=[tool_id],
                execution_strategy="direct",
                success_signal="获得观察",
            ),
            MicroPlanStep(
                objective="根据观察完成判断",
                candidate_tools=[tool_id],
                execution_strategy="bounded_react",
                success_signal="证据足够",
            ),
        ],
    )


@pytest.mark.asyncio
async def test_model_planner_uses_structured_contract_and_tracks_usage():
    model = FakeStructuredModel(_plan("workout.get_progress"))
    policy = ModelPlanningPolicy(model)

    plan = await policy.create_plan(
        goal="检查最近训练趋势",
        subtasks=["查询进度", "判断趋势"],
        tool_catalog=[{
            "tool_id": "workout.get_progress",
            "description": "读取训练进度",
            "parameters": {},
        }],
    )

    assert model.schema is MicroPlanDraft
    assert plan.goal == "检查最近训练趋势"
    assert plan.steps[1].execution_strategy == "bounded_react"
    assert policy.input_tokens == 120
    assert policy.output_tokens == 40
    planner_input = model.runnable.messages[1]["content"]
    assert '"request": "检查最近训练趋势"' in planner_input
    assert '"semantic_goals"' in planner_input
    assert '"tools"' in planner_input
    assert '"tool_catalog"' not in planner_input


def test_planner_catalog_removes_display_only_json_schema_fields():
    compact = compact_planner_tool_catalog([{
        "tool_id": "workout.get_progress",
        "description": "读取最近若干周训练进度。 仅用于趋势。",
        "parameters": {
            "title": "WorkoutProgressArguments",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "weeks": {
                    "title": "Weeks",
                    "type": "integer",
                    "default": 8,
                    "minimum": 1,
                    "maximum": 52,
                    "examples": [4],
                },
            },
        },
    }])

    assert compact == [{
        "tool_id": "workout.get_progress",
        "purpose": "读取最近若干周训练进度。 仅用于趋势。",
        "arguments": {
            "properties": {
                "weeks": {
                    "type": "integer",
                    "default": 8,
                    "minimum": 1,
                    "maximum": 52,
                },
            },
            "required": [],
        },
    }]


@pytest.mark.asyncio
async def test_model_planner_accepts_one_step_lightweight_plan():
    plan = MicroPlan(
        goal="检查活动训练后按观察选择路径",
        steps=[MicroPlanStep(
            objective="判断继续活动训练还是读取下一练",
            candidate_tools=[
                "workout.get_active_session",
                "workout.get_next",
            ],
            execution_strategy="bounded_react",
            success_signal="已根据活动训练是否存在给出真实依据",
        )],
    )
    policy = ModelPlanningPolicy(FakeStructuredModel(plan))

    result = await policy.create_plan(
        goal="昨天没做完，现在接着练还是开始下一练",
        subtasks=["检查活动训练", "必要时读取下一练"],
        tool_catalog=[
            {
                "tool_id": "workout.get_active_session",
                "description": "读取活动训练",
                "parameters": {},
            },
            {
                "tool_id": "workout.get_next",
                "description": "读取下一练",
                "parameters": {},
            },
        ],
    )

    assert len(result.steps) == 1
    assert result.steps[0].execution_strategy == "bounded_react"


@pytest.mark.asyncio
async def test_model_planner_accepts_explicit_parallel_read_actions():
    tool_ids = ["profile.get_summary", "plan.get_active"]
    plan = MicroPlan(
        goal="结合资料和计划判断适配度",
        steps=[MicroPlanStep(
            objective="并行取得资料和计划",
            candidate_tools=tool_ids,
            execution_strategy="parallel_read",
            completion_policy="after_all_observations",
            planned_actions=[
                PlannedToolAction(tool_id=item, arguments={})
                for item in tool_ids
            ],
            success_signal="两项独立观察均已返回",
        )],
    )
    policy = ModelPlanningPolicy(FakeStructuredModel(plan))

    result = await policy.create_plan(
        goal="结合资料和计划判断适配度",
        subtasks=["读取资料", "读取计划"],
        tool_catalog=[
            {"tool_id": item, "description": item, "parameters": {}}
            for item in tool_ids
        ],
    )

    assert result.steps[0].execution_strategy == "parallel_read"
    assert [
        item.tool_id for item in result.steps[0].planned_actions
    ] == tool_ids


@pytest.mark.asyncio
async def test_finalizer_maps_semantic_outcome_to_terminal_action():
    model = FakeStructuredModel(FinalizationDecision(
        outcome="adjustment_proposal",
        reply="建议降频；这是待确认提案，尚未执行。",
    ))
    policy = ModelPlanningPolicy(model)

    response = await policy.finalize(
        goal="判断计划是否需要调整",
        steps=[],
        observations=[],
        allowed_outcomes=[
            "adjustment_proposal",
            "no_change_needed",
            "insufficient_evidence",
        ],
    )

    assert model.schema is FinalizationDecision
    assert response.outcome == "adjustment_proposal"
    assert response.terminal_action == "proposal"
    assert response.invocation_metrics == ModelInvocationMetrics(
        input_chars=response.invocation_metrics.input_chars,
        output_chars=response.invocation_metrics.output_chars,
        input_tokens=120,
        output_tokens=40,
        finish_reason="stop",
    )


@pytest.mark.asyncio
async def test_enabled_proposal_finalizer_uses_opt_in_draft_schema():
    draft = {
        "proposal_type": "plan_adjustment_v1",
        "changes": [{
            "change_type": "adjust_exercise_target",
            "stable_display_key": "day-1-order-0",
            "before": {"sets": 4},
            "after": {"sets": 3},
            "reason": "降低单次训练量。",
            "safety_priority": False,
        }],
        "rationale": ["提高连续完成概率。"],
        "safety_notes": [],
    }
    model = FakeStructuredModel(ProposalFinalizationDecision(
        outcome="adjustment_proposal",
        reply="建议减少一组；这是待确认提案。",
        proposal_draft=draft,
    ))
    policy = ModelPlanningPolicy(model)

    response = await policy.finalize(
        goal="判断计划是否需要调整",
        steps=[],
        observations=[],
        allowed_outcomes=["adjustment_proposal", "insufficient_evidence"],
        proposal_creation_enabled=True,
    )

    assert model.schema is ProposalFinalizationDecision
    assert response.proposal_draft == draft
    assert "proposal_draft" in model.runnable.messages[0]["content"]


@pytest.mark.asyncio
async def test_finalizer_compacts_duplicate_orchestration_input():
    model = FakeStructuredModel(FinalizationDecision(
        outcome="informational_answer",
        reply="已综合证据回答。",
    ))
    policy = ModelPlanningPolicy(model)
    duplicate_observation = {
        "step_id": "step_1",
        "call_id": "private-invocation-id",
        "tool_id": "profile.get_summary",
        "status": "success",
        "result": {"training_days_per_week": 3},
    }

    await policy.finalize(
        goal="结合资料回答",
        steps=[{
            "id": "step_1",
            "objective": "取得资料",
            "candidate_tools": ["profile.get_summary"],
            "execution_strategy": "direct",
            "completion_policy": "after_successful_observation",
            "planned_actions": [],
            "success_signal": "资料已返回",
            "status": "completed",
            "summary": "资料读取成功",
        }],
        observations=[duplicate_observation, duplicate_observation],
        allowed_outcomes=["informational_answer", "insufficient_evidence"],
    )

    content = model.runnable.messages[1]["content"]
    payload = json.loads(
        content.removeprefix("输入：").removesuffix("\n请输出 JSON。")
    )
    assert payload["step_results"] == [{
        "id": "step_1",
        "objective": "取得资料",
        "status": "completed",
        "summary": "资料读取成功",
    }]
    assert payload["tool_observations"] == [{
        "step_id": "step_1",
        "tool_id": "profile.get_summary",
        "status": "success",
        "result": {"training_days_per_week": 3},
    }]
    assert "private-invocation-id" not in content
    assert "candidate_tools" not in content


@pytest.mark.asyncio
async def test_finalizer_preserves_large_observation_without_field_loss():
    model = FakeStructuredModel(FinalizationDecision(
        outcome="informational_answer",
        reply="已综合证据回答。",
    ))
    policy = ModelPlanningPolicy(model)

    await policy.finalize(
        goal="总结长历史",
        steps=[],
        observations=[{
            "step_id": "step_1",
            "call_id": "long-result-call",
            "tool_id": "workout.list_history",
            "status": "success",
            "result": {
                "first_marker": "START",
                "records": "x" * 3000,
                "z_last_marker": "END",
            },
        }],
        allowed_outcomes=["informational_answer", "insufficient_evidence"],
    )

    content = model.runnable.messages[1]["content"]
    payload = json.loads(
        content.removeprefix("输入：").removesuffix("\n请输出 JSON。")
    )
    result = payload["tool_observations"][0]["result"]
    assert result["first_marker"] == "START"
    assert result["z_last_marker"] == "END"
    assert len(result["records"]) == 3000
    assert "truncated" not in result


@pytest.mark.asyncio
async def test_finalizer_rejects_outcome_outside_contract():
    policy = ModelPlanningPolicy(FakeStructuredModel(FinalizationDecision(
        outcome="adjustment_proposal",
        reply="未经允许的提案。",
    )))

    with pytest.raises(PlanningModelError) as captured:
        await policy.finalize(
            goal="查询下一练",
            steps=[],
            observations=[],
            allowed_outcomes=[
                "informational_answer",
                "insufficient_evidence",
            ],
        )

    assert captured.value.stage == "finalizer"
    assert captured.value.category == "finalization_outcome_not_allowed"


def test_automatic_completion_is_rejected_for_bounded_react():
    with pytest.raises(
        ValidationError,
        match="automatic completion is only valid for direct steps",
    ):
        MicroPlanStep(
            objective="按观察选择替代证据",
            candidate_tools=[
                "workout.get_progress",
                "workout.list_history",
            ],
            execution_strategy="bounded_react",
            completion_policy="after_successful_observation",
            success_signal="获得聚合进度或历史替代证据",
        )


def test_parallel_read_requires_explicit_matching_actions():
    with pytest.raises(
        ValidationError,
        match="planned actions must match candidate tools",
    ):
        MicroPlanStep(
            objective="并行读取资料和计划",
            candidate_tools=["profile.get_summary", "plan.get_active"],
            execution_strategy="parallel_read",
            completion_policy="after_all_observations",
            planned_actions=[
                PlannedToolAction(
                    tool_id="profile.get_summary",
                    arguments={},
                ),
                PlannedToolAction(
                    tool_id="workout.get_progress",
                    arguments={"weeks": 4},
                ),
            ],
            success_signal="两项观察均已返回",
        )


def test_non_parallel_step_rejects_planned_actions():
    with pytest.raises(
        ValidationError,
        match="only valid for parallel_read",
    ):
        MicroPlanStep(
            objective="读取资料",
            candidate_tools=["profile.get_summary"],
            execution_strategy="direct",
            planned_actions=[PlannedToolAction(
                tool_id="profile.get_summary",
                arguments={},
            )],
            success_signal="资料已返回",
        )


@pytest.mark.asyncio
async def test_model_planner_rejects_tool_outside_server_allowlist():
    policy = ModelPlanningPolicy(FakeStructuredModel(_plan("admin.write")))

    with pytest.raises(AIServiceError, match="未授权工具"):
        await policy.create_plan(
            goal="检查最近训练趋势",
            subtasks=["查询进度", "判断趋势"],
            tool_catalog=[{
                "tool_id": "workout.get_progress",
                "description": "读取训练进度",
                "parameters": {},
            }],
        )


@pytest.mark.asyncio
async def test_model_planner_exposes_safe_stage_and_schema_path():
    policy = ModelPlanningPolicy(FakeInvalidStructuredModel())

    with pytest.raises(PlanningModelError) as captured:
        await policy.create_plan(
            goal="检查最近训练趋势",
            subtasks=["查询进度", "判断趋势"],
            tool_catalog=[{
                "tool_id": "workout.get_progress",
                "description": "读取训练进度",
                "parameters": {},
            }],
        )

    assert captured.value.stage == "planner"
    assert captured.value.category == (
        "FakeParsingError>FakeParsingError:"
        "literal_error@steps.0.execution_strategy"
    )
    assert "must-not-leak" not in captured.value.category
