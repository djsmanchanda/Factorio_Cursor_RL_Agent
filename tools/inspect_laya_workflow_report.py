# Path: tools/inspect_laya_workflow_report.py | Purpose: Inspect measured branch and supply evidence from one disposable lab report.

from __future__ import annotations

import argparse
import json
from pathlib import Path

from training.laya_lab_workflow import observe_lab_workflow
from training.laya_work_graph import project_work_graph


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Project a disposable training report into Laya workflow evidence")
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--branches", required=True, type=Path)
    args = parser.parse_args(argv)
    report = json.loads(args.report.read_text(encoding="utf-8"))
    branches = json.loads(args.branches.read_text(encoding="utf-8"))
    graph, supply = observe_lab_workflow(report, branches)
    print(json.dumps({"work_graph": project_work_graph(graph), "supply_evidence": supply},
                     sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
