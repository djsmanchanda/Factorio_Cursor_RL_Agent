#!/usr/bin/env python3
# Path: tools/trackb_pair_report.py | Purpose: Read frozen cohort job-status seed by seed and report strict-milestone arms plus paired contrasts and a 2x2 difference-in-differences.
"""Paired report for Laya cohort job-status files.

Reads one cohort (two or more arms) and prints, per arm, the terminal accounting
and the strict milestone counts (ever and held at end), then the paired per-seed
contrasts for two arms. With two cohorts it prints the two contrasts and their
difference of differences (2x2 cells sharing seeds).

Counting rules follow the repo convention: every planned job is accounted for;
unexercised jobs (never started, or no strict rows) are reported separately and
excluded from a paired test rather than counted as a loss. C4 is exposure
sensitive, so decision volume is printed alongside and the contrast is also
reported split at the pooled median decision count.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from math import comb
from pathlib import Path

MILESTONES = [f"C{i}" for i in range(10)]


def read(path: Path) -> dict:
    return json.loads(path.read_text())


def exact_mcnemar(wins: int, losses: int) -> float:
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    tail = sum(comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def arm_rows(jobs: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for job in jobs:
        grouped.setdefault(job["arm"], []).append(job)
    return grouped


def strict_ever(job: dict, milestone: str) -> bool | None:
    """Ever-passed from the runner's summary, the sidecar rows, or the final status."""
    reached = job.get("strict_reached")
    if isinstance(reached, dict) and milestone in reached:
        return True
    rows = [r for r in job.get("strict_current") or [] if isinstance(r, dict)]
    if any(r.get("statuses", {}).get(milestone) is True for r in rows):
        return True
    if job.get("status") != "completed":
        return None
    final = job.get("strict_current_at_end") or {}
    if milestone in final:
        return bool(final.get(milestone))
    return None


def held_at_end(job: dict, milestone: str) -> bool | None:
    final = job.get("strict_current_at_end")
    if not isinstance(final, dict) or milestone not in final:
        return None
    return bool(final[milestone])


def summarize(name: str, jobs: list[dict], milestones: list[str]) -> dict:
    status = Counter(job.get("status") for job in jobs)
    print(f"\n== {name}: {len(jobs)} planned jobs")
    print("   status:", dict(sorted(status.items(), key=lambda kv: (-kv[1], kv[0]))))
    for arm, rows in sorted(arm_rows(jobs).items()):
        terminal = [j for j in rows if j.get("status") == "completed"]
        decisions = [j.get("decisions") for j in terminal if isinstance(j.get("decisions"), int)]
        walls = [j.get("server_seconds") for j in terminal if isinstance(j.get("server_seconds"), (int, float))]
        print(f"   [{arm}] n={len(rows)} completed={len(terminal)}"
              + (f" median_decisions={statistics.median(decisions):g}" if decisions else "")
              + (f" median_server_s={statistics.median(walls):g}" if walls else ""))
        for milestone in milestones:
            ever = [j for j in rows if strict_ever(j, milestone) is True]
            held = [j for j in terminal if held_at_end(j, milestone) is True]
            false_held = [j for j in terminal if held_at_end(j, milestone) is False]
            unknown = len(terminal) - len(held) - len(false_held)
            print(f"      {milestone}: ever={len(ever)}/{len(rows)}  held={len(held)}"
                  f" (false={len(false_held)} unknown={unknown})")
    return {"status": status}


def pair(left_arm: str, right_arm: str, jobs: list[dict], milestone: str) -> dict:
    by_seed: dict[int, dict[str, dict]] = {}
    for job in jobs:
        by_seed.setdefault(job["seed"], {})[job["arm"]] = job
    wins = losses = ties = unexercised = 0
    pairs = []
    for seed, arms in sorted(by_seed.items()):
        left, right = arms.get(left_arm), arms.get(right_arm)
        if left is None or right is None:
            unexercised += 1
            continue
        lv, rv = strict_ever(left, milestone), strict_ever(right, milestone)
        if lv is None or rv is None:
            unexercised += 1
            continue
        pairs.append((seed, lv, rv))
        if lv and not rv:
            wins += 1
        elif rv and not lv:
            losses += 1
        else:
            ties += 1
    p = exact_mcnemar(wins, losses)
    print(f"\n-- paired {milestone}: {right_arm} vs {left_arm}")
    print(f"   wins={wins} losses={losses} ties={ties} unexercised={unexercised} exact McNemar p={p:.3f}")
    return {"wins": wins, "losses": losses, "ties": ties, "unexercised": unexercised, "p": p,
            "rate_left": sum(1 for _, l, _ in pairs if l) / max(1, len(pairs)),
            "rate_right": sum(1 for _, _, r in pairs if r) / max(1, len(pairs))}


def exposure_split(left_arm: str, right_arm: str, jobs: list[dict], milestone: str) -> None:
    by_seed: dict[int, dict[str, dict]] = {}
    for job in jobs:
        by_seed.setdefault(job["seed"], {})[job["arm"]] = job
    paired = [(arms[left_arm], arms[right_arm]) for arms in by_seed.values()
              if left_arm in arms and right_arm in arms
              and arms[left_arm].get("status") == "completed" and arms[right_arm].get("status") == "completed"]
    volumes = [(l.get("decisions") or 0) + (r.get("decisions") or 0) for l, r in paired]
    if not volumes:
        return
    median = statistics.median(volumes)
    print(f"   exposure: pooled median decisions={median:g}")
    for label, keep in (("above", lambda v: v >= median), ("below", lambda v: v < median)):
        subset = [pair_ for pair_, v in zip(paired, volumes) if keep(v)]
        l = sum(1 for a, b in subset if strict_ever(a, milestone) is True)
        r = sum(1 for a, b in subset if strict_ever(b, milestone) is True)
        print(f"      {label} median: {left_arm} {l}/{len(subset)}  {right_arm} {r}/{len(subset)}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cohort", type=Path, nargs="+", help="cohort roots (1 for arms, 2 for a 2x2)")
    parser.add_argument("--left", default="reference", help="control arm label")
    parser.add_argument("--right", default="b", help="treatment arm label")
    parser.add_argument("--milestones", default="C4", help="comma separated strict milestones")
    args = parser.parse_args(argv)
    milestones = [m.strip() for m in args.milestones.split(",") if m.strip()]
    results = {}
    for root in args.cohort:
        name = read(root / "regression.json")["name"]
        jobs = read(root / "job-status.json")["jobs"]
        summarize(name, jobs, milestones)
        contrast = pair(args.left, args.right, jobs, milestones[0])
        exposure_split(args.left, args.right, jobs, milestones[0])
        results[name] = contrast
    if len(results) == 2:
        (n1, c1), (n2, c2) = results.items()
        diff = (c2["rate_right"] - c2["rate_left"]) - (c1["rate_right"] - c1["rate_left"])
        print(f"\n== 2x2 difference-in-differences ({milestones[0]})")
        print(f"   {n1}: right-left={c1['rate_right'] - c1['rate_left']:+.3f}")
        print(f"   {n2}: right-left={c2['rate_right'] - c2['rate_left']:+.3f}")
        print(f"   DiD={diff:+.3f}  (a single cell is not adoption evidence)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
