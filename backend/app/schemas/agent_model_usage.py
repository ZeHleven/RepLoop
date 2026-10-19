"""Versioned provider observations; absent usage is never zero usage."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class ModelHTTPObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sequence: int = Field(ge=1)
    source: Literal["structured", "chat", "agent", "daily_meal"]
    outcome: Literal["pending", "http_success", "http_error", "transport_error", "cancelled"]
    http_status: int | None = Field(default=None, ge=100, le=599)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)


class LegacyAgentModelUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["provider_http_v1"] = "provider_http_v1"
    scope: Literal["worker_attempt"] = "worker_attempt"
    worker_attempt: int = Field(ge=0)
    # Earlier reclaimed attempts may have died before recording their costs.
    prior_attempts_unobserved: bool
    observation_complete: bool
    http_requests: int = Field(ge=0)
    http_successes: int = Field(ge=0)
    http_errors: int = Field(ge=0)
    transport_errors: int = Field(ge=0)
    pending_or_cancelled: int = Field(ge=0)
    usage_known_requests: int = Field(ge=0)
    known_input_tokens: int = Field(ge=0)
    known_output_tokens: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    calls: list[ModelHTTPObservation] = Field(default_factory=list)


class ModelHTTPResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    http_status: int = Field(ge=100, le=599)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)


class ModelClientObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sequence: int = Field(ge=1)
    source: Literal["structured", "chat", "agent", "daily_meal"]
    outcome: Literal["pending", "http_success", "http_error", "transport_error", "local_error", "client_error", "cancelled"]
    transport_boundary_observed: bool = False
    transport_attempts: int = Field(default=0, ge=0)
    transport_errors: int = Field(default=0, ge=0)
    responses: list[ModelHTTPResponse] = Field(default_factory=list)
    # Last-response convenience fields; totals always use all responses.
    http_status: int | None = Field(default=None, ge=100, le=599)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)


class AgentModelUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["transport_boundary_v2"] = "transport_boundary_v2"
    scope: Literal["worker_attempt"] = "worker_attempt"
    worker_attempt: int = Field(ge=0)
    prior_attempts_unobserved: bool
    observation_complete: bool
    client_attempts: int = Field(ge=0)
    transport_attempts: int = Field(ge=0)
    # Transport invocation or response confirms an attempt, NOT provider receipt.
    http_requests: int = Field(ge=0)
    http_responses: int = Field(ge=0)
    http_successes: int = Field(ge=0)
    http_errors: int = Field(ge=0)
    transport_errors: int = Field(ge=0)
    local_errors: int = Field(ge=0)
    unclassified_client_errors: int = Field(ge=0)
    pending_or_cancelled: int = Field(ge=0)
    usage_known_requests: int = Field(ge=0)
    known_input_tokens: int = Field(ge=0)
    known_output_tokens: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    calls: list[ModelClientObservation] = Field(default_factory=list)


AgentModelUsagePayload = Annotated[AgentModelUsage | LegacyAgentModelUsage, Field(discriminator="version")]
