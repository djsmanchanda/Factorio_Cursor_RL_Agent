#!/usr/bin/env python3
# Path: tools/laya_checkpoint_regression.py | Purpose: Offline catalog and matched checkpoint regression queue CLI.
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.checkpoint_catalog import _safe_relative
from training.laya_checkpoint_bundle import validate
from training.laya_outcome_export import export, export_journal
from training.laya_segment_queue import Queue

REGRESSION = ROOT / "data/laya-regression"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("catalog", "validate", "plan", "enqueue", "report", "export", "dispatch"))
    parser.add_argument("--generation")
    parser.add_argument("--spec", help="Spec filename under data/laya-regression/specs")
    parser.add_argument("--suite")
    parser.add_argument("--journal", help="Native decision-states JSONL (outcome supplied by --spec)")
    args = parser.parse_args(argv)
    catalog = REGRESSION / "catalog"
    if args.action == "catalog":
        generations = []
        for path in sorted(catalog.iterdir()) if catalog.exists() else []:
            if path.is_dir() and not path.name.startswith("."):
                try:
                    item = validate(catalog, path.name)
                    generations.append({"generation": path.name, "checkpoint": item["checkpoint"], "status": "valid"})
                except ValueError as error:
                    generations.append({"generation": path.name, "status": "incompatible", "reason": str(error)})
        print(json.dumps(generations, indent=2))
        return 0
    if args.action == "validate":
        if not args.generation:
            parser.error("--generation required")
        print(json.dumps(validate(catalog, args.generation), indent=2))
        return 0
    if args.action == "dispatch":
        parser.error("live dispatch unavailable: global admission and runner restore hooks are not integrated")
    if args.action == "export":
        if not args.spec:
            parser.error("--spec required for joined input")
        data = json.loads(_safe_relative(REGRESSION / "specs", args.spec).read_text())
        if args.journal:
            journal = _safe_relative(REGRESSION, args.journal)
            records = [json.loads(line) for line in journal.read_text().splitlines()]
            rows = export_journal(records, data)
        else:
            rows = export(data["choices"], data["receipts"], data["outcomes"])
        print(json.dumps(rows, indent=2))
        return 0
    if args.action in ("plan", "enqueue"):
        if not args.spec:
            parser.error("--spec required")
        spec = json.loads(_safe_relative(REGRESSION / "specs", args.spec).read_text())
        if args.action == "plan":
            queue = Queue(":memory:", catalog)
            queue.enqueue(spec)
            print(json.dumps(queue.report(spec["suite_id"]), indent=2))
            queue.close()
            return 0
        REGRESSION.mkdir(parents=True, exist_ok=True)
        queue = Queue(REGRESSION / "queue.sqlite3", catalog)
        queue.enqueue(spec)
        print(json.dumps(queue.report(spec["suite_id"]), indent=2))
        queue.close()
        return 0
    if not args.suite:
        parser.error("--suite required")
    if not (REGRESSION / "queue.sqlite3").is_file():
        parser.error("queue does not exist")
    queue = Queue(REGRESSION / "queue.sqlite3", catalog)
    print(json.dumps(queue.report(args.suite), indent=2))
    queue.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
