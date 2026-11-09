# Path: tools/import_laya_coach_data.py
# Purpose: Merge labelled Factorio coach records into a versioned Laya dataset.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from training.laya_workflow import iter_coach_cases


def _parse(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coach-file", action="append", required=True, type=Path)
    parser.add_argument("--base-cases", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--split", choices=("train", "calibration", "held_out", "test"), default="train")
    parser.add_argument("--report", type=Path)
    return parser.parse_args(argv)


def _read_base(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    if not path.is_file():
        raise SystemExit(f"base dataset does not exist: {path}")
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    rows = _read_base(args.base_cases)
    imported = 0
    skipped: list[dict[str, Any]] = []
    for source in args.coach_file:
        if not source.is_file():
            skipped.append({"file": str(source), "reason": "file does not exist"})
            continue
        for case in iter_coach_cases(source, split=args.split):
            if case.get("_skipped"):
                skipped.append({"file": str(source), **case})
                continue
            rows.append(case)
            imported += 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
    report = {
        "status": "ready" if not skipped else "partial",
        "base_cases": len(rows) - imported,
        "imported_cases": imported,
        "skipped_records": len(skipped),
        "skipped": skipped[:100],
        "output": str(args.output),
        "split": args.split,
    }
    report_path = args.report or args.output.with_suffix(args.output.suffix + ".report.json")
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
