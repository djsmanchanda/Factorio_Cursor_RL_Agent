# Path: tools/build_laya_dataset.py
# Purpose: Build a seed-split Laya JSONL dataset from disposable TrainingStore evidence and pre-built cases.

from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from training.laya_dataset import (
    DEFAULT_DATASET_DIR,
    file_sha256,
    iter_cases_from_jsonl,
    iter_cases_from_store,
    write_cases,
    write_cases_content_addressed,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", action="append", default=[], type=Path)
    parser.add_argument(
        "--cases-jsonl", action="append", default=[], type=Path,
        help="pre-built Laya cases (e.g. exploration journals with soft gold targets); "
             "validated, deduplicated by state hash, and split by scenario seed",
    )
    parser.add_argument(
        "--output", type=Path,
        help=f"defaults to {DEFAULT_DATASET_DIR.relative_to(REPO_ROOT)}/<sha-prefix>.jsonl "
             "and repoints latest.jsonl there",
    )
    parser.add_argument("--split", choices=("train", "calibration", "held_out", "test"))
    parser.add_argument("--derive-seed-splits", action="store_true", help="derive stable train/calibration/held-out/test splits from scenario seeds")
    parser.add_argument(
        "--step-level", action="store_true",
        help="emit one provenance-linked case per staged or typed workflow decision",
    )
    args = parser.parse_args()
    if not args.database and not args.cases_jsonl:
        parser.error("provide at least one --database or --cases-jsonl input")
    quality = {"rows": 0, "emitted": 0, "excluded": 0, "exclusions": {}}
    jsonl_quality = {"rows": 0, "emitted": 0, "excluded": 0, "exclusions": {}}

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

    def cases_from_jsonl():
        seen: set[str] = set()
        for path in args.cases_jsonl:
            if not path.is_file():
                jsonl_quality.setdefault("missing_inputs", []).append(str(path))
                continue
            yield from iter_cases_from_jsonl(
                path, split=args.split, quality_report=jsonl_quality, seen_state_hashes=seen,
            )

    cases = itertools.chain(cases_from_databases(), cases_from_jsonl())
    if args.output is None:
        output, digest, count = write_cases_content_addressed(cases)
    else:
        output = args.output
        count = write_cases(cases, output)
        digest = file_sha256(output)
    quality["written"] = count
    manifest = {
        "output": str(output),
        "sha256": digest,
        "written": count,
        "inputs": {
            "databases": [str(path) for path in args.database],
            "cases_jsonl": [str(path) for path in args.cases_jsonl],
            "split": args.split,
            "derive_seed_splits": args.derive_seed_splits,
            "step_level": args.step_level,
        },
        "dataset_quality": quality,
        "cases_jsonl_quality": jsonl_quality,
    }
    # The manifest records exactly which inputs produced these bytes.
    Path(str(output) + ".manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {count} Laya cases to {output}")
    print(json.dumps({"dataset_quality": quality, "cases_jsonl_quality": jsonl_quality, "output": str(output), "sha256": digest}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
