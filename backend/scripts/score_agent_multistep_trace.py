from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas.agent_trace import AgentExecutionTrace  # noqa: E402
from evals.multistep_schema import (  # noqa: E402
    DEFAULT_DATASET_PATH,
    load_multistep_dataset,
)
from evals.multistep_scorer import score_runtime_execution_trace  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Score a persisted Agent execution trace against one case"
    )
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--trace-file", type=Path, required=True)
    parser.add_argument("--case-file", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def _trace_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("trace file must contain a JSON object")
    nested = payload.get("execution_trace")
    if isinstance(nested, dict):
        return nested
    return payload


def main() -> int:
    args = parse_args()
    dataset = load_multistep_dataset(args.case_file)
    case = next(
        (item for item in dataset.cases if item.id == args.case_id),
        None,
    )
    if case is None:
        print(f"Unknown case id: {args.case_id}", file=sys.stderr)
        return 2

    raw_payload = json.loads(args.trace_file.read_text(encoding="utf-8"))
    trace = AgentExecutionTrace.model_validate(_trace_payload(raw_payload))
    score = score_runtime_execution_trace(case, trace)
    print(json.dumps(score.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 1 if args.strict and not score.deterministic_pass else 0


if __name__ == "__main__":
    raise SystemExit(main())
