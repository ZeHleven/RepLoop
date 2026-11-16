from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.multistep_schema import (  # noqa: E402
    DEFAULT_DATASET_PATH,
    load_multistep_dataset,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the outcome-based multi-step Agent eval dataset"
    )
    parser.add_argument("--case-file", type=Path, default=DEFAULT_DATASET_PATH)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    dataset = load_multistep_dataset(args.case_file)
    mode_counts = Counter(
        case.expected.execution_mode for case in dataset.cases
    )
    terminal_counts = Counter(
        case.expected.terminal_action for case in dataset.cases
    )
    report = {
        "schema_version": dataset.schema_version,
        "case_count": len(dataset.cases),
        "scenario_group_count": len({
            case.scenario_group for case in dataset.cases
        }),
        "execution_modes": dict(sorted(mode_counts.items())),
        "terminal_actions": dict(sorted(terminal_counts.items())),
        "case_ids": [case.id for case in dataset.cases],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
