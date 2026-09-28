#!/usr/bin/env python3
# Path: tools/compare_laya_batches.py
# Purpose: Compare policy batch summaries seed by seed on completion rate and completion ticks.

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path


def load(path: Path) -> dict[int, dict]:
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        rows[int(row["seed"])] = row
    return rows


def cost(row: dict, max_ticks: int, material_weight: float = 0.0, decision_ticks: float = 0.0) -> float:
    # Same objective as counterfactual labels: completion ticks plus priced
    # material and decisions; unfinished episodes cost the full budget.
    ticks = float(row["elapsed_ticks"]) if row.get("status") == "completed" and row.get("elapsed_ticks") else float(max_ticks)
    return (ticks + material_weight * float(row.get("material_cost") or 0.0)
            + decision_ticks * float(row.get("decisions") or 0))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidates", nargs="+", type=Path)
    parser.add_argument("--max-ticks", type=int, default=36_000)
    parser.add_argument("--material-weight", type=float, default=10.0,
                        help="Ticks per material unit, matching the label objective")
    parser.add_argument("--decision-ticks", type=float, default=60.0,
                        help="Ticks per decision, matching the label objective")
    args = parser.parse_args(argv)
    base = load(args.baseline)
    for path in [args.baseline, *args.candidates]:
        rows = load(path)
        seeds = sorted(set(rows) & set(base))
        weights = (args.max_ticks, args.material_weight, args.decision_ticks)
        costs = [cost(rows[seed], *weights) for seed in seeds]
        deltas = [cost(rows[seed], *weights) - cost(base[seed], *weights) for seed in seeds]
        wins = sum(delta < 0 for delta in deltas)
        print(json.dumps({
            "batch": path.stem, "seeds": len(seeds),
            "completed": sum(rows[seed].get("status") == "completed" for seed in seeds),
            "median_cost_ticks": statistics.median(costs) if costs else None,
            "median_decisions": statistics.median(float(rows[seed].get("decisions") or 0) for seed in seeds) if seeds else None,
            "mean_delta_vs_baseline": round(statistics.fmean(deltas), 1) if deltas else None,
            "faster_than_baseline": wins,
        }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
