# Path: tools/compare_laya_workflow.py
# Purpose: Review matched Laya/incumbent execution evidence without switching workflows.

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from training.laya_comparison import LayaComparisonError, compare_matched_runs


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True, help="JSON array of incumbent execution records")
    parser.add_argument("--challenger", type=Path, required=True, help="JSON array of Laya execution records")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        report = compare_matched_runs(*[json.loads(path.read_text()) for path in (args.suite, args.baseline, args.challenger)])
    except (OSError, ValueError, TypeError, KeyError) as error:
        report = {"status": "blocked", "reason": str(error), "incumbent_retained": True, "eligible_for_real_nauvis": False}
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as output:
            output.write(payload)
    print(payload, end="")
    return 0 if report["status"] == "candidate_for_review" else 2


if __name__ == "__main__":
    raise SystemExit(main())
