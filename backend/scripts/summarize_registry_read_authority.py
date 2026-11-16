from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.agent_tool_registry_read_authority_observation import (  # noqa: E402
    registry_read_authority_gate_failures,
    summarize_registry_read_authority_lines,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Strictly summarize Registry read-authority container logs"
    )
    parser.add_argument("input", help="UTF-8 container log file")
    parser.add_argument(
        "--expected-runs",
        required=True,
        help="Observation runner JSON report containing run IDs",
    )
    parser.add_argument("--min-enforced-runs", type=int, default=30)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def _expected_run_ids(path: str) -> list[str]:
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        run_id
        for item in report.get("runs", [])
        if isinstance((run_id := item.get("run_id")), str) and run_id
    ]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.min_enforced_runs < 1:
        print("min enforced runs must be positive", file=sys.stderr)
        return 2
    try:
        lines = Path(args.input).read_text(encoding="utf-8").splitlines()
        expected_run_ids = _expected_run_ids(args.expected_runs)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"cannot read observation input: {type(exc).__name__}", file=sys.stderr)
        return 2

    summary = summarize_registry_read_authority_lines(lines)
    failures = registry_read_authority_gate_failures(
        summary,
        min_enforced_runs=args.min_enforced_runs,
        expected_run_ids=expected_run_ids,
    )
    report = {
        **summary,
        "gate": {
            "passed": not failures,
            "failures": failures,
            "min_enforced_runs": args.min_enforced_runs,
            "expected_run_count": len(expected_run_ids),
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if args.strict and failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
