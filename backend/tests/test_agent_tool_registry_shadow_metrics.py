from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from app.schemas.agent_tool_registry import (
    ToolRegistryShadowCheck,
    ToolRegistryShadowCheckType,
    ToolRegistryShadowMetricSample,
    ToolRegistryShadowMismatchCode,
    ToolRegistryShadowReport,
)
from app.services.agent_tool_registry_shadow_metrics import (
    project_registry_shadow_metrics,
)


_METRIC_CASES_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "agent_tool_registry_shadow_metric_cases.json"
)
_METRIC_NAMES = {
    "agent_tool_registry_shadow_runs_total",
    "agent_tool_registry_shadow_checks_total",
    "agent_tool_registry_shadow_mismatches_total",
    "agent_tool_registry_shadow_errors_total",
    "agent_tool_registry_shadow_latency_ms",
}
_FORBIDDEN_LABELS = {
    "tool_id",
    "run_id",
    "user_id",
    "registry_version",
    "sample_bucket",
    "legacy_fingerprint",
    "registry_fingerprint",
    "skip_reason",
}


def _load_metric_fixture() -> dict[str, Any]:
    return json.loads(_METRIC_CASES_PATH.read_text(encoding="utf-8"))


def _sample(
    name: str,
    kind: str,
    labels: dict[str, str],
    value: int,
) -> dict[str, Any]:
    return {
        "name": name,
        "kind": kind,
        "labels": labels,
        "value": value,
    }


def _declared_samples(
    report: ToolRegistryShadowReport,
    contract: dict[str, Any],
) -> list[dict[str, Any]]:
    samples = [_sample(
        "agent_tool_registry_shadow_runs_total",
        "counter",
        {"status": report.status},
        1,
    )]
    allowed_error_categories = set(contract["allowed_error_categories"])
    unknown_error_category = contract["unknown_error_category"]

    for check in report.checks:
        samples.append(_sample(
            "agent_tool_registry_shadow_checks_total",
            "counter",
            {
                "check_type": check.check_type,
                "status": check.status,
            },
            1,
        ))
        if check.status == "mismatch":
            for code in check.mismatch_codes:
                samples.append(_sample(
                    "agent_tool_registry_shadow_mismatches_total",
                    "counter",
                    {
                        "check_type": check.check_type,
                        "code": code,
                    },
                    1,
                ))
        elif check.status == "error":
            error_category = check.error_category or unknown_error_category
            if error_category not in allowed_error_categories:
                error_category = unknown_error_category
            samples.append(_sample(
                "agent_tool_registry_shadow_errors_total",
                "counter",
                {
                    "check_type": check.check_type,
                    "error_category": error_category,
                },
                1,
            ))

    if report.status != "not_sampled":
        samples.append(_sample(
            "agent_tool_registry_shadow_latency_ms",
            "histogram",
            {},
            report.total_latency_ms,
        ))
    return samples


def test_metric_projection_fixture_declares_a_bounded_contract():
    payload = _load_metric_fixture()

    assert set(payload) == {"fixture_version", "contract", "cases"}
    assert payload["fixture_version"] == "1.0.0"
    contract = payload["contract"]
    assert set(contract) == {
        "projection_inputs",
        "requires_persisted_trace",
        "on_projection_error",
        "error_mismatch_codes_are_not_counted",
        "allowed_error_categories",
        "unknown_error_category",
        "metric_kinds",
        "metric_labels",
    }
    assert contract["projection_inputs"] == ["report"]
    assert contract["requires_persisted_trace"] is False
    assert contract["on_projection_error"] == "drop_metrics"
    assert contract["error_mismatch_codes_are_not_counted"] is True
    assert contract["allowed_error_categories"] == [
        "comparator_internal_error",
        "invalid_shadow_fact",
        "shadow_fact_builder_error",
        "other",
    ]
    assert contract["unknown_error_category"] == "other"
    assert set(contract["metric_kinds"]) == _METRIC_NAMES
    assert set(contract["metric_labels"]) == _METRIC_NAMES
    assert contract["metric_kinds"] == {
        "agent_tool_registry_shadow_runs_total": "counter",
        "agent_tool_registry_shadow_checks_total": "counter",
        "agent_tool_registry_shadow_mismatches_total": "counter",
        "agent_tool_registry_shadow_errors_total": "counter",
        "agent_tool_registry_shadow_latency_ms": "histogram",
    }
    assert contract["metric_labels"] == {
        "agent_tool_registry_shadow_runs_total": ["status"],
        "agent_tool_registry_shadow_checks_total": [
            "check_type",
            "status",
        ],
        "agent_tool_registry_shadow_mismatches_total": [
            "check_type",
            "code",
        ],
        "agent_tool_registry_shadow_errors_total": [
            "check_type",
            "error_category",
        ],
        "agent_tool_registry_shadow_latency_ms": [],
    }


def test_metric_projection_cases_cover_every_report_check_and_status():
    payload = _load_metric_fixture()
    cases = payload["cases"]
    reports = [
        ToolRegistryShadowReport.model_validate(case["report"])
        for case in cases
    ]
    report_statuses = set(get_args(
        ToolRegistryShadowReport.model_fields["status"].annotation
    ))
    check_statuses = set(get_args(
        ToolRegistryShadowCheck.model_fields["status"].annotation
    ))

    assert len({case["case_id"] for case in cases}) == len(cases)
    assert {report.status for report in reports} == report_statuses
    assert {
        check.check_type
        for report in reports
        for check in report.checks
    } == set(get_args(ToolRegistryShadowCheckType))
    assert {
        check.status
        for report in reports
        for check in report.checks
    } == check_statuses


def test_metric_projection_cases_match_declared_sample_semantics():
    payload = _load_metric_fixture()
    contract = payload["contract"]

    for case in payload["cases"]:
        assert set(case) == {"case_id", "report", "expected_samples"}
        report = ToolRegistryShadowReport.model_validate(case["report"])
        assert report.total_latency_ms == sum(
            check.latency_ms for check in report.checks
        )
        assert case["expected_samples"] == _declared_samples(
            report,
            contract,
        )

        latency_samples = [
            sample for sample in case["expected_samples"]
            if sample["name"] == (
                "agent_tool_registry_shadow_latency_ms"
            )
        ]
        if report.status == "not_sampled":
            assert latency_samples == []
        else:
            assert latency_samples == [_sample(
                "agent_tool_registry_shadow_latency_ms",
                "histogram",
                {},
                report.total_latency_ms,
            )]


def test_metric_projection_samples_have_only_low_cardinality_labels():
    payload = _load_metric_fixture()
    contract = payload["contract"]
    stable_check_types = set(get_args(ToolRegistryShadowCheckType))
    stable_mismatch_codes = set(get_args(ToolRegistryShadowMismatchCode))
    stable_error_categories = set(contract["allowed_error_categories"])

    for case in payload["cases"]:
        report = ToolRegistryShadowReport.model_validate(case["report"])
        private_values = {
            report.registry_version,
            str(report.sample_bucket),
            *(
                value
                for check in report.checks
                for value in (
                    check.legacy_fingerprint,
                    check.registry_fingerprint,
                    check.skip_reason,
                    *check.legacy_tool_ids,
                    *check.registry_tool_ids,
                )
                if value is not None
            ),
        }
        for sample in case["expected_samples"]:
            assert set(sample) == {"name", "kind", "labels", "value"}
            name = sample["name"]
            assert name in _METRIC_NAMES
            assert sample["kind"] == contract["metric_kinds"][name]
            assert list(sample["labels"]) == contract["metric_labels"][name]
            assert set(sample["labels"]).isdisjoint(_FORBIDDEN_LABELS)
            assert set(sample["labels"].values()).isdisjoint(private_values)
            assert type(sample["value"]) is int
            assert sample["value"] >= 0
            if sample["kind"] == "counter":
                assert sample["value"] == 1

            labels = sample["labels"]
            if "check_type" in labels:
                assert labels["check_type"] in stable_check_types
            if "code" in labels:
                assert labels["code"] in stable_mismatch_codes
            if "error_category" in labels:
                assert labels["error_category"] in stable_error_categories


def test_metric_projection_contract_is_trace_persistence_independent_and_safe():
    payload = _load_metric_fixture()
    contract = payload["contract"]
    serialized_cases = json.dumps(
        payload["cases"],
        ensure_ascii=False,
        sort_keys=True,
    )

    assert contract["projection_inputs"] == ["report"]
    assert contract["requires_persisted_trace"] is False
    assert contract["on_projection_error"] == "drop_metrics"
    assert "persist_trace" not in serialized_cases
    assert "run_id" not in serialized_cases
    assert "user_id" not in serialized_cases
    assert "private-provider-error-12345" not in json.dumps(
        [
            sample
            for case in payload["cases"]
            for sample in case["expected_samples"]
        ],
        ensure_ascii=False,
        sort_keys=True,
    )


@pytest.mark.parametrize(
    "case",
    _load_metric_fixture()["cases"],
    ids=lambda case: case["case_id"],
)
def test_metric_projector_matches_declared_case(case: dict[str, Any]):
    report = ToolRegistryShadowReport.model_validate(case["report"])
    report_before = report.model_dump(mode="json")

    samples = project_registry_shadow_metrics(report)

    assert [
        sample.model_dump(mode="json") for sample in samples
    ] == case["expected_samples"]
    assert report.model_dump(mode="json") == report_before
    assert project_registry_shadow_metrics(report) == samples


def test_metric_projector_accepts_only_the_validated_report():
    signature = inspect.signature(project_registry_shadow_metrics)

    assert list(signature.parameters) == ["report"]
    assert signature.parameters["report"].default is inspect.Parameter.empty


@pytest.mark.parametrize(
    "sample",
    [
        {
            "name": "agent_tool_registry_shadow_runs_total",
            "kind": "counter",
            "labels": {"status": "private-status"},
            "value": 1,
        },
        {
            "name": "agent_tool_registry_shadow_errors_total",
            "kind": "counter",
            "labels": {
                "check_type": "route_allowlist",
                "error_category": "private-provider-error-12345",
            },
            "value": 1,
        },
        {
            "name": "agent_tool_registry_shadow_runs_total",
            "kind": "counter",
            "labels": {
                "status": "match",
                "run_id": "private-run-id",
            },
            "value": 1,
        },
    ],
    ids=["unknown-status", "unknown-error-category", "extra-label"],
)
def test_metric_sample_rejects_unbounded_labels(sample: dict[str, Any]):
    with pytest.raises(ValidationError):
        ToolRegistryShadowMetricSample.model_validate(sample)
