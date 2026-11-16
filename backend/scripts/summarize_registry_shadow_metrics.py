from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.agent_tool_registry_shadow_observation import (  # noqa: E402
    registry_shadow_observation_gate_failures,
    summarize_registry_shadow_metric_lines,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Summarize Tool Registry shadow metric events from exported "
            "container logs"
        )
    )
    parser.add_argument(
        "input",
        nargs="?",
        default="-",
        help="UTF-8 log file, or - for stdin",
    )
    parser.add_argument("--min-sampled-runs", type=int, default=20)
    parser.add_argument("--max-p95-latency-ms", type=int, default=5)
    parser.add_argument(
        "--min-run-match-rate",
        type=float,
        default=None,
        choices=[item / 100 for item in range(0, 101)],
        help=(
            "Optional terminal run match-rate gate. Leave unset for mixed "
            "traffic where partial lifecycle reports are expected."
        ),
    )
    parser.add_argument(
        "--allow-missing-check-types",
        action="store_true",
        help="Do not fail when the window does not cover all six checks.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 when any observation gate fails.",
    )
    return parser.parse_args(argv)


def _read_lines(path: str) -> list[str]:
    if path == "-":
        return sys.stdin.read().splitlines()
    return Path(path).read_text(encoding="utf-8").splitlines()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.min_sampled_runs < 1 or args.max_p95_latency_ms < 0:
        print(
            "min sampled runs must be positive and latency non-negative",
            file=sys.stderr,
        )
        return 2
    try:
        lines = _read_lines(args.input)
    except OSError as exc:
        print(f"cannot read input log: {type(exc).__name__}", file=sys.stderr)
        return 2

    summary = summarize_registry_shadow_metric_lines(lines)
    failures = registry_shadow_observation_gate_failures(
        summary,
        min_sampled_runs=args.min_sampled_runs,
        max_p95_latency_ms=args.max_p95_latency_ms,
        min_run_match_rate=args.min_run_match_rate,
        require_all_check_types=not args.allow_missing_check_types,
    )
    report = {
        **summary,
        "gate": {
            "passed": not failures,
            "failures": failures,
            "min_sampled_runs": args.min_sampled_runs,
            "max_p95_latency_ms": args.max_p95_latency_ms,
            "min_run_match_rate": args.min_run_match_rate,
            "require_all_check_types": not args.allow_missing_check_types,
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if args.strict and failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
