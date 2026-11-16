from __future__ import annotations

from typing import Any, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator


ToolIntentName = Literal[
    "profile_query",
    "health_query",
    "plan_query",
    "next_workout_query",
    "active_workout_query",
    "workout_history_query",
    "workout_progress_query",
    "weight_history_query",
    "nutrition_today_query",
    "nutrition_history_query",
    "food_search_query",
    "daily_meal_generation",
]


class ToolArgumentFieldContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(
        pattern=r"^[a-z][a-z0-9_]*$",
        min_length=1,
        max_length=80,
    )
    json_type: Literal[
        "integer",
        "number",
        "string",
        "boolean",
        "array",
        "object",
    ]
    required: bool = False
    has_default: bool = False
    default: Any = None
    minimum: int | float | None = None
    maximum: int | float | None = None

    @model_validator(mode="after")
    def validate_default_presence(self) -> Self:
        if not self.has_default and self.default is not None:
            raise ValueError("fields without defaults cannot declare a value")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("argument minimum cannot exceed maximum")
        return self


class ToolArgumentContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_ref: str = Field(min_length=1, max_length=120)
    default_arguments: dict[str, Any] = Field(default_factory=dict)
    additional_properties: bool = False
    identity_source: Literal["server_context"] = "server_context"
    fields: tuple[ToolArgumentFieldContract, ...] = Field(
        default=(),
        max_length=30,
    )

    @model_validator(mode="after")
    def validate_declared_defaults(self) -> Self:
        field_names = [item.name for item in self.fields]
        if len(field_names) != len(set(field_names)):
            raise ValueError("argument field names must be unique")
        declared_defaults = {
            item.name: item.default
            for item in self.fields
            if item.has_default
        }
        if declared_defaults != self.default_arguments:
            raise ValueError(
                "default arguments must match declared field defaults"
            )
        return self


class ToolObservationContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    current_shape: Literal["legacy_mapping", "normalized_envelope"]
    output_schema_ref: str | None = Field(default=None, max_length=120)
    strict_output_schema: bool = False
    missing_data_signals: tuple[
        Literal[
            "found_false",
            "reason_code",
            "count_zero",
            "empty_aggregate",
        ],
        ...,
    ] = ()
    found_false_is_success: bool = False
    empty_collection_is_success: bool = False
    tool_errors_raise: bool = True


class ToolFreshnessContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reuse_scope: Literal["none", "run", "conversation", "user"]
    max_age_seconds: int = Field(ge=0, le=86400)
    invalidation_events: tuple[str, ...] = Field(default=(), max_length=12)

    @model_validator(mode="after")
    def validate_reuse_window(self) -> Self:
        if self.reuse_scope == "none" and self.max_age_seconds != 0:
            raise ValueError("non-reusable tools must have a zero freshness window")
        if self.reuse_scope != "none" and self.max_age_seconds == 0:
            raise ValueError("reusable tools must have a positive freshness window")
        return self


class ToolAuditContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    argument_storage: Literal["normalized"] = "normalized"
    result_storage: Literal["summary_and_fingerprint"] = (
        "summary_and_fingerprint"
    )
    identity_logged: bool = False
    sensitive_result_fields: tuple[str, ...] = Field(
        default=(),
        max_length=20,
    )


class ToolRegistryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_id: str = Field(
        pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$",
        max_length=100,
    )
    contract_version: str = Field(
        pattern=r"^\d+\.\d+\.\d+$",
        max_length=20,
    )
    langchain_name: str = Field(
        pattern=r"^[a-z][a-z0-9_]*$",
        max_length=100,
    )
    title: str = Field(min_length=1, max_length=80)
    mode: Literal["read", "proposal", "execute"]
    availability: Literal["active", "hidden", "planned"]
    side_effects: Literal["none", "proposal_only", "writes_data"]
    risk_level: Literal["low", "medium", "high"]
    data_sensitivity: Literal["standard", "personal", "health_sensitive"]
    supported_intents: tuple[ToolIntentName, ...] = Field(
        min_length=1,
        max_length=8,
    )
    use_cases: tuple[str, ...] = Field(min_length=1, max_length=8)
    exclusions: tuple[str, ...] = Field(default=(), max_length=8)
    data_sources: tuple[str, ...] = Field(min_length=1, max_length=8)
    parallel_safe: bool
    arguments: ToolArgumentContract
    observation: ToolObservationContract
    freshness: ToolFreshnessContract
    audit: ToolAuditContract

    @model_validator(mode="after")
    def validate_permission_boundary(self) -> Self:
        if self.parallel_safe and (
            self.mode != "read" or self.side_effects != "none"
        ):
            raise ValueError("only side-effect-free read tools may be parallel safe")
        if self.mode == "read" and self.side_effects != "none":
            raise ValueError("read tools must declare no side effects")
        return self


class ConditionalEvidenceContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    group_id: str = Field(
        pattern=r"^[a-z][a-z0-9_]*$",
        max_length=80,
    )
    primary_tool_id: str = Field(min_length=1, max_length=100)
    fallback_tool_id: str = Field(min_length=1, max_length=100)
    fallback_trigger: Literal["on_error", "on_not_found"]
    fallback_arguments: dict[str, Any] = Field(default_factory=dict)
    speculative_parallel_allowed: bool = False

    @model_validator(mode="after")
    def validate_direction(self) -> Self:
        if self.primary_tool_id == self.fallback_tool_id:
            raise ValueError("primary and fallback tools must be different")
        if self.speculative_parallel_allowed:
            raise ValueError("conditional evidence tools cannot be prefetched")
        return self


class ToolRegistryV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    registry_version: str = Field(min_length=1, max_length=40)
    status: Literal["design_only", "shadow", "active"]
    max_routed_tools: int = Field(ge=1, le=8)
    tools: tuple[ToolRegistryEntry, ...] = Field(min_length=1, max_length=100)
    conditional_evidence: tuple[ConditionalEvidenceContract, ...] = Field(
        default=(),
        max_length=30,
    )

    @model_validator(mode="after")
    def validate_registry_references(self) -> Self:
        tool_ids = [item.tool_id for item in self.tools]
        if len(tool_ids) != len(set(tool_ids)):
            raise ValueError("tool ids must be unique")
        langchain_names = [item.langchain_name for item in self.tools]
        if len(langchain_names) != len(set(langchain_names)):
            raise ValueError("LangChain tool names must be unique")
        group_ids = [item.group_id for item in self.conditional_evidence]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("conditional evidence group ids must be unique")

        known_tool_ids = set(tool_ids)
        for group in self.conditional_evidence:
            if not {
                group.primary_tool_id,
                group.fallback_tool_id,
            }.issubset(known_tool_ids):
                raise ValueError(
                    "conditional evidence groups must reference registered tools"
                )
        return self


ToolRegistryReadAuthoritySurface = Literal[
    "route_allowlist",
    "constructed_tools",
    "argument_schema",
    "parallel_policy",
    "conditional_evidence",
]


class ToolRegistryReadEnforcementContract(BaseModel):
    """Conservative, reversible transition from shadow to read authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: str = Field(
        pattern=r"^\d+\.\d+\.\d+$",
        max_length=20,
    )
    from_mode: Literal["shadow"] = "shadow"
    to_mode: Literal["enforce"] = "enforce"
    feature_flag: Literal[
        "AGENT_TOOL_REGISTRY_ENFORCE_READS_ENABLED"
    ] = "AGENT_TOOL_REGISTRY_ENFORCE_READS_ENABLED"
    enabled_by_default: Literal[False] = False
    scope: Literal["read_only"] = "read_only"
    tool_ids: tuple[str, ...] = Field(min_length=1, max_length=100)
    authority_surfaces: tuple[ToolRegistryReadAuthoritySurface, ...] = Field(
        min_length=1,
        max_length=5,
    )
    effective_allowlist_policy: Literal[
        "legacy_registry_intersection"
    ] = "legacy_registry_intersection"
    fallback_on_registry_error: Literal["legacy_read_runtime"] = (
        "legacy_read_runtime"
    )
    shadow_observation_during_enforce: bool = True
    rollback_mode: Literal["legacy"] = "legacy"
    requires_data_migration: Literal[False] = False

    @model_validator(mode="after")
    def validate_initial_read_scope(self) -> Self:
        if len(self.tool_ids) != len(set(self.tool_ids)):
            raise ValueError("enforced tool ids must be unique")
        if any(not tool_id for tool_id in self.tool_ids):
            raise ValueError("enforced tool ids cannot be empty")
        if len(self.authority_surfaces) != len(set(self.authority_surfaces)):
            raise ValueError("authority surfaces must be unique")
        return self


ToolRegistryReadAuthorityReasonCode = Literal[
    "permission_expansion",
    "outside_enforce_cohort",
    "unregistered_tool",
    "inactive_tool",
    "non_read_tool",
    "side_effecting_tool",
    "registry_internal_error",
]


class ToolRegistryReadAuthorityEntryFact(BaseModel):
    """Minimal privacy-safe Registry fact consumed by the pure selector."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_id: str = Field(
        pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$",
        max_length=100,
    )
    availability: Literal["active", "hidden", "planned"]
    mode: Literal["read", "proposal", "execute"]
    side_effects: Literal["none", "proposal_only", "writes_data"]


class ToolRegistryReadAuthorityDecision(BaseModel):
    """Deterministic read-authority decision without user or tool payloads."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    authority_mode: Literal["enforce", "legacy_fallback"]
    effective_tool_ids: tuple[str, ...] = Field(default=(), max_length=100)
    denied_tool_ids: tuple[str, ...] = Field(default=(), max_length=100)
    reason_codes: tuple[ToolRegistryReadAuthorityReasonCode, ...] = Field(
        default=(),
        max_length=7,
    )

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        if len(self.effective_tool_ids) != len(set(self.effective_tool_ids)):
            raise ValueError("effective tool ids must be unique")
        if len(self.denied_tool_ids) != len(set(self.denied_tool_ids)):
            raise ValueError("denied tool ids must be unique")
        if set(self.effective_tool_ids) & set(self.denied_tool_ids):
            raise ValueError("effective and denied tool ids must be disjoint")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("read authority reason codes must be unique")
        if self.authority_mode == "legacy_fallback":
            if self.denied_tool_ids:
                raise ValueError("legacy fallback cannot deny legacy tools")
            if self.reason_codes != ("registry_internal_error",):
                raise ValueError(
                    "legacy fallback requires registry_internal_error"
                )
        elif "registry_internal_error" in self.reason_codes:
            raise ValueError(
                "enforce decisions cannot contain registry_internal_error"
            )
        return self


ToolRegistryShadowCheckType = Literal[
    "route_allowlist",
    "constructed_tools",
    "argument_schema",
    "parallel_policy",
    "conditional_evidence",
    "observation_semantics",
]

ToolRegistryShadowMismatchCode = Literal[
    "permission_expansion",
    "registered_tool_missing",
    "unexpected_tool",
    "tool_order_mismatch",
    "langchain_name_mismatch",
    "argument_schema_mismatch",
    "default_argument_mismatch",
    "parallel_policy_mismatch",
    "conditional_evidence_mismatch",
    "observation_semantics_mismatch",
    "shadow_internal_error",
]


class ToolRegistryShadowCheck(BaseModel):
    """Privacy-safe result of one deterministic v1/v2 comparison."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    check_type: ToolRegistryShadowCheckType
    status: Literal["match", "mismatch", "skipped", "error"]
    mismatch_codes: tuple[ToolRegistryShadowMismatchCode, ...] = Field(
        default=(),
        max_length=12,
    )
    legacy_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    registry_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    legacy_tool_ids: tuple[str, ...] = Field(default=(), max_length=30)
    registry_tool_ids: tuple[str, ...] = Field(default=(), max_length=30)
    latency_ms: int = Field(default=0, ge=0)
    skip_reason: str | None = Field(default=None, max_length=120)
    error_category: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def validate_status_payload(self) -> Self:
        if self.status == "mismatch" and not self.mismatch_codes:
            raise ValueError("mismatch checks require a stable mismatch code")
        if self.status not in ("mismatch", "error") and self.mismatch_codes:
            raise ValueError("non-mismatch checks cannot carry mismatch codes")
        if self.status == "skipped" and not self.skip_reason:
            raise ValueError("skipped checks require a reason")
        if self.status != "skipped" and self.skip_reason is not None:
            raise ValueError("only skipped checks may carry a skip reason")
        if self.status == "error" and not self.error_category:
            raise ValueError("error checks require an error category")
        if self.status != "error" and self.error_category is not None:
            raise ValueError("only error checks may carry an error category")
        return self


class ToolRegistryShadowReport(BaseModel):
    """Run-local shadow report; never participates in an Agent decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    registry_version: str = Field(min_length=1, max_length=40)
    mode: Literal["shadow"] = "shadow"
    status: Literal["not_sampled", "match", "mismatch", "partial", "error"]
    sample_bucket: int = Field(ge=0, le=9999)
    checks: tuple[ToolRegistryShadowCheck, ...] = Field(
        default=(),
        max_length=20,
    )
    total_latency_ms: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_report_status(self) -> Self:
        check_statuses = {item.status for item in self.checks}
        if self.status == "not_sampled" and self.checks:
            raise ValueError("non-sampled reports cannot contain checks")
        if self.status == "match" and (
            not self.checks or check_statuses != {"match"}
        ):
            raise ValueError("match reports require only matching checks")
        if self.status == "mismatch" and "mismatch" not in check_statuses:
            raise ValueError("mismatch reports require a mismatching check")
        if self.status == "partial" and "skipped" not in check_statuses:
            raise ValueError("partial reports require a skipped check")
        if self.status == "error" and "error" not in check_statuses:
            raise ValueError("error reports require an error check")
        return self


ToolRegistryShadowMetricName = Literal[
    "agent_tool_registry_shadow_runs_total",
    "agent_tool_registry_shadow_checks_total",
    "agent_tool_registry_shadow_mismatches_total",
    "agent_tool_registry_shadow_errors_total",
    "agent_tool_registry_shadow_latency_ms",
]
ToolRegistryShadowMetricKind = Literal["counter", "histogram"]
ToolRegistryShadowMetricErrorCategory = Literal[
    "comparator_internal_error",
    "invalid_shadow_fact",
    "shadow_fact_builder_error",
    "other",
]


_SHADOW_METRIC_CONTRACT: dict[
    ToolRegistryShadowMetricName,
    tuple[ToolRegistryShadowMetricKind, tuple[str, ...]],
] = {
    "agent_tool_registry_shadow_runs_total": (
        "counter",
        ("status",),
    ),
    "agent_tool_registry_shadow_checks_total": (
        "counter",
        ("check_type", "status"),
    ),
    "agent_tool_registry_shadow_mismatches_total": (
        "counter",
        ("check_type", "code"),
    ),
    "agent_tool_registry_shadow_errors_total": (
        "counter",
        ("check_type", "error_category"),
    ),
    "agent_tool_registry_shadow_latency_ms": (
        "histogram",
        (),
    ),
}


class ToolRegistryShadowMetricSample(BaseModel):
    """One bounded, privacy-safe sample projected from a shadow report."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: ToolRegistryShadowMetricName
    kind: ToolRegistryShadowMetricKind
    labels: dict[str, str] = Field(default_factory=dict, max_length=2)
    value: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_metric_contract(self) -> Self:
        expected_kind, expected_labels = _SHADOW_METRIC_CONTRACT[self.name]
        if self.kind != expected_kind:
            raise ValueError("shadow metric kind does not match its name")
        if tuple(self.labels) != expected_labels:
            raise ValueError("shadow metric labels do not match its name")
        if self.kind == "counter" and self.value != 1:
            raise ValueError("shadow counter samples must increment by one")

        allowed_label_values: dict[str, set[str]] = {
            "check_type": set(get_args(ToolRegistryShadowCheckType)),
            "code": set(get_args(ToolRegistryShadowMismatchCode)),
            "error_category": set(get_args(
                ToolRegistryShadowMetricErrorCategory
            )),
        }
        for label_name, allowed_values in allowed_label_values.items():
            value = self.labels.get(label_name)
            if value is not None and value not in allowed_values:
                raise ValueError(
                    "shadow metric label value is not in its bounded domain"
                )

        status = self.labels.get("status")
        if status is not None:
            allowed_statuses = (
                {"not_sampled", "match", "mismatch", "partial", "error"}
                if self.name == "agent_tool_registry_shadow_runs_total"
                else {"match", "mismatch", "skipped", "error"}
            )
            if status not in allowed_statuses:
                raise ValueError(
                    "shadow metric status is not in its bounded domain"
                )
        return self
