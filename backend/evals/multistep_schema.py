from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ExecutionMode = Literal["direct", "planned", "clarify", "safe_stop"]
TerminalAction = Literal[
    "answer",
    "proposal",
    "clarify",
    "safe_stop",
    "failed",
]
RiskLevel = Literal["low", "medium", "high"]
ForbiddenBehavior = Literal[
    "call_forbidden_tool",
    "claim_write_executed",
    "fabricate_user_data",
    "make_medical_diagnosis",
    "recommend_training_through_red_flag",
    "recommend_load_increase_with_pain",
    "hide_capability_gap",
]


class EvalMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ToolErrorFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=80)
    retryable: bool = False


class ToolStub(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1, max_length=100)
    result: dict[str, Any] | None = None
    error: ToolErrorFixture | None = None
    facts: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_result_or_error(self) -> ToolStub:
        if (self.result is None) == (self.error is None):
            raise ValueError("tool stub must define exactly one of result or error")
        if self.error is not None and self.facts:
            raise ValueError("failed tool stubs cannot provide facts")
        return self


class ExpectedOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_mode: ExecutionMode
    terminal_action: TerminalAction
    risk_level: RiskLevel
    required_tool_groups: list[list[str]] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "Each inner group is an acceptable alternative evidence source; "
            "at least one tool from every group must be called."
        ),
    )
    optional_tools: list[str] = Field(default_factory=list, max_length=8)
    forbidden_tools: list[str] = Field(default_factory=list, max_length=20)
    required_facts: list[str] = Field(default_factory=list, max_length=30)
    response_requirements: list[str] = Field(default_factory=list, max_length=20)
    forbidden_behaviors: list[ForbiddenBehavior] = Field(
        default_factory=list,
        max_length=10,
    )
    max_plan_steps: int = Field(default=1, ge=0, le=6)
    max_tool_calls: int = Field(default=0, ge=0, le=6)
    max_replans: int = Field(default=0, ge=0, le=1)
    require_three_action_parallel_fast_path: bool = False

    @model_validator(mode="after")
    def validate_mode_budget(self) -> ExpectedOutcome:
        if any(not group for group in self.required_tool_groups):
            raise ValueError("required tool groups cannot be empty")
        required_tools = {
            tool for group in self.required_tool_groups for tool in group
        }
        if required_tools & set(self.forbidden_tools):
            raise ValueError("required and forbidden tools overlap")
        if set(self.optional_tools) & set(self.forbidden_tools):
            raise ValueError("optional and forbidden tools overlap")
        if self.execution_mode in {"clarify", "safe_stop"}:
            if self.required_tool_groups or self.max_tool_calls != 0:
                raise ValueError(
                    "clarify and safe-stop controls cannot require tool calls"
                )
        if self.execution_mode == "direct" and self.max_replans != 0:
            raise ValueError("direct cases cannot allow replanning")
        if self.execution_mode == "planned" and self.max_plan_steps < 2:
            raise ValueError("planned cases require at least two plan steps")
        if self.require_three_action_parallel_fast_path:
            if self.execution_mode != "planned":
                raise ValueError(
                    "three-action fast path requires planned execution"
                )
            if (
                len(self.required_tool_groups) != 3
                or any(
                    len(group) != 1 for group in self.required_tool_groups
                )
            ):
                raise ValueError(
                    "three-action fast path requires three singleton evidence groups"
                )
        return self


class MultistepEvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9_]+$", max_length=100)
    scenario_group: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=160)
    business_goal: str = Field(min_length=1, max_length=500)
    message: str = Field(min_length=1, max_length=4000)
    context_messages: list[EvalMessage] = Field(default_factory=list, max_length=12)
    initial_facts: list[str] = Field(default_factory=list, max_length=20)
    candidate_tools: list[str] = Field(default_factory=list, max_length=8)
    tool_stubs: list[ToolStub] = Field(default_factory=list, max_length=10)
    expected: ExpectedOutcome

    @model_validator(mode="after")
    def validate_case_contract(self) -> MultistepEvalCase:
        if len(self.candidate_tools) != len(set(self.candidate_tools)):
            raise ValueError("candidate tools must be unique")
        stub_tools = {stub.tool for stub in self.tool_stubs}
        if not stub_tools.issubset(set(self.candidate_tools)):
            raise ValueError("every tool stub must be present in candidate_tools")
        expected_tools = {
            tool
            for group in self.expected.required_tool_groups
            for tool in group
        } | set(self.expected.optional_tools)
        if not expected_tools.issubset(set(self.candidate_tools)):
            raise ValueError(
                "required and optional tools must be present in candidate_tools"
            )
        if not expected_tools.issubset(stub_tools):
            raise ValueError("required and optional tools need deterministic stubs")
        available_facts = set(self.initial_facts)
        for stub in self.tool_stubs:
            available_facts.update(stub.facts)
        if not set(self.expected.required_facts).issubset(available_facts):
            raise ValueError("required facts must be provided by state or tool stubs")
        return self


class MultistepEvalDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    description: str = Field(min_length=1, max_length=1000)
    cases: list[MultistepEvalCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dataset(self) -> MultistepEvalDataset:
        ids = [case.id for case in self.cases]
        duplicates = [
            case_id for case_id, count in Counter(ids).items() if count > 1
        ]
        if duplicates:
            raise ValueError(f"duplicate case ids: {duplicates}")
        return self


DEFAULT_DATASET_PATH = Path(__file__).with_name("agent_multistep_cases.json")


def load_multistep_dataset(
    path: Path = DEFAULT_DATASET_PATH,
) -> MultistepEvalDataset:
    return MultistepEvalDataset.model_validate_json(
        path.read_text(encoding="utf-8")
    )
