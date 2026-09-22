# Path: tools/build_laya_dataset.py
# Purpose: Build a seed-split Laya JSONL dataset from disposable TrainingStore evidence.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from training.laya_dataset import iter_cases_from_store, write_cases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", action="append", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--split", choices=("train", "calibration", "held_out", "test"))
    parser.add_argument("--derive-seed-splits", action="store_true", help="derive stable train/calibration/held-out/test splits from scenario seeds")
    parser.add_argument(
        "--step-level", action="store_true",
        help="emit one provenance-linked case per staged or typed workflow decision",
    )
    args = parser.parse_args()
    quality = {"rows": 0, "emitted": 0, "excluded": 0, "exclusions": {}}

    def cases_from_databases():
        for database in args.database:
            if not database.exists():
                quality.setdefault("missing_databases", []).append(str(database))
                continue
            yield from iter_cases_from_store(
                database,
                split=args.split,
                derive_seed_splits=args.derive_seed_splits,
                step_level=args.step_level,
                quality_report=quality,
            )

    cases = cases_from_databases()
    count = write_cases(cases, args.output)
    quality["written"] = count
    print(f"wrote {count} Laya cases to {args.output}")
    print(json.dumps({"dataset_quality": quality}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
