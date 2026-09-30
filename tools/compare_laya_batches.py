#!/usr/bin/env python3
# Path: tools/compare_laya_batches.py
# Purpose: Compare policy batch summaries seed by seed on completion rate and completion ticks.

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path


def load(path: Path) -> dict[int, dict]:
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        seed = row.get("seed")
        if type(seed) is not int or seed < 0 or seed in rows:
            raise ValueError(f"invalid or duplicate seed in {path}: {seed}")
        rows[seed] = row
    if not rows:
        raise ValueError(f"empty batch: {path}")
    return rows


def cost(row: dict, max_ticks: int, material_weight: float = 0.0, decision_ticks: float = 0.0) -> float:
    # Same objective as counterfactual labels: completion ticks plus priced
    # material and decisions; unfinished episodes cost the full budget.
    ticks = float(row["elapsed_ticks"]) if row.get("status") == "completed" else float(max_ticks)
    material, decisions = float(row["material_cost"]), float(row["decisions"])
    if any(not math.isfinite(value) or value < 0 for value in (ticks, material, decisions)):
        raise ValueError("batch metrics must be finite and nonnegative")
    return ticks + material_weight * material + decision_ticks * decisions


def compare(base: dict[int, dict], rows: dict[int, dict], expected: set[int],
            weights: tuple[int, float, float]) -> dict:
    if not expected or set(base) != expected or set(rows) != expected:
        raise ValueError("every expected seed requires exactly one result in both batches")
    required = {"scenario_hash", "request_version", "guideline_hash", "max_len", "max_decisions",
                "max_no_progress", "stepped", "game_speed"}
    for seed in sorted(expected):
        left, right = base[seed].get("comparison_conditions"), rows[seed].get("comparison_conditions")
        if not isinstance(left, dict) or not isinstance(right, dict) or required - left.keys() or required - right.keys():
            raise ValueError(f"missing comparison conditions for seed {seed}; rerun legacy batches")
        if left != right:
            raise ValueError(f"comparison conditions differ for seed {seed}")
        for row in (base[seed], rows[seed]):
            if type(row.get("safety_violations")) is not int or row["safety_violations"] < 0:
                raise ValueError(f"missing safety evidence for seed {seed}")
    seeds = sorted(expected)
    costs = [cost(rows[seed], *weights) for seed in seeds]
    deltas = [cost(rows[seed], *weights) - cost(base[seed], *weights) for seed in seeds]
    completed = [costs[i] for i, seed in enumerate(seeds) if rows[seed]["status"] == "completed"]
    errors = sum(rows[seed]["status"] not in {"completed", "timed_out"} for seed in seeds)
    completion_losses = sum(base[s]["status"] == "completed" and rows[s]["status"] != "completed" for s in seeds)
    safety = sum(rows[s]["safety_violations"] for s in seeds)
    return {
        "seeds": len(seeds), "completed": len(completed), "errors": errors, "safety_violations": safety,
        "completion_wins": sum(base[s]["status"] != "completed" and rows[s]["status"] == "completed" for s in seeds),
        "completion_losses": completion_losses,
        "median_cost_ticks": statistics.median(costs), "mean_cost_ticks": statistics.fmean(costs),
        "p95_cost_ticks": sorted(costs)[math.ceil(0.95 * len(costs)) - 1],
        "mean_completed_cost_ticks": statistics.fmean(completed) if completed else None,
        "median_decisions": statistics.median(float(rows[s]["decisions"]) for s in seeds),
        "mean_delta_vs_baseline": round(statistics.fmean(deltas), 1),
        "lower_cost_pairs": sum(delta < 0 for delta in deltas),
        "completed_lower_cost_pairs": sum(delta < 0 and rows[s]["status"] == "completed"
                                           and base[s]["status"] == "completed" for s, delta in zip(seeds, deltas)),
        "status": "blocked" if errors or completion_losses or safety else "diagnostic_only",
        "eligible_for_real_nauvis": False,
        "limitation": "Lab diagnostic; deployed mod/save hashes and primary-workflow promotion evidence are not established.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidates", nargs="+", type=Path)
    parser.add_argument("--max-ticks", type=int, default=36_000)
    parser.add_argument("--material-weight", type=float, default=10.0,
                        help="Ticks per material unit, matching the label objective")
    parser.add_argument("--decision-ticks", type=float, default=60.0,
                        help="Ticks per decision, matching the label objective")
    parser.add_argument("--seed-start", type=int, help="Explicit frozen cohort start (otherwise use baseline manifest)")
    parser.add_argument("--episodes", type=int, help="Explicit frozen cohort size")
    args = parser.parse_args(argv)
    try:
        if args.max_ticks <= 0 or any(not math.isfinite(v) or v < 0 for v in (args.material_weight, args.decision_ticks)):
            raise ValueError("comparison cost weights require a positive horizon and finite nonnegative prices")
        if (args.seed_start is None) != (args.episodes is None):
            raise ValueError("--seed-start and --episodes must be provided together")
        if args.seed_start is not None:
            if args.seed_start < 0 or args.episodes < 1:
                raise ValueError("invalid expected seed cohort")
            expected = set(range(args.seed_start, args.seed_start + args.episodes))
        else:
            manifest = json.loads(args.baseline.with_suffix(".manifest.json").read_text())
            seeds = manifest["expected_seeds"]
            if not seeds or any(type(s) is not int or s < 0 for s in seeds) or len(set(seeds)) != len(seeds):
                raise ValueError("invalid expected seeds in manifest")
            expected = set(seeds)
        base = load(args.baseline)
        reports = []
        for path in [args.baseline, *args.candidates]:
            rows = load(path)
            reports.append({"batch": path.stem, **compare(base, rows, expected,
                (args.max_ticks, args.material_weight, args.decision_ticks))})
        for report in reports:
            print(json.dumps(report))
        return 2 if any(r["status"] == "blocked" for r in reports) else 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "blocked", "reason": str(error), "eligible_for_real_nauvis": False}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
