#!/usr/bin/env python3
# Path: tools/laya_evidence.py | Purpose: Read-only milestone evidence for Laya job records: ordered reach, held at end, recorded predicate version, baselined C6.
"""Turn one Laya job record into milestone evidence that cannot be misread.

The runner's sticky ``strict_reached`` latch is unordered and its C6 consumer delta can
include a machine's lifetime craft counter from an earlier recipe (the C72 and C86c
artifact). This module reads the samples the runner already records and reports, per
milestone:

- ``reached_any``: the latch, as recorded (``strict_reached``, else ``accepted_ever``).
- ``reached_ordered``: reached and its prerequisite chain (C2 -> C3 -> C4 -> C5/C6) reached too.
  Order in time is not required: plastic before the stone rollout is a real path (61 of
  C81-C87's completed runs). C1 is not a prerequisite: C2 requires the starters to be gone,
  so C1 is transient and unsampled at restart origins.
- ``ordered_tick``: when the whole chain up to this milestone had been reached.
- ``held_at_end``: the predicate's status in the final sample (True, False or None).
- ``first_tick``: the latch tick, or for C6 the baselined pass tick.
- ``predicate_version``: from the job's own samples, never from git ancestry.

C6 baseline: a consumer key first seen after the start sample is baselined at its first
value. Rows record deltas per consumer recipe, not per machine, so a second relabelled
machine joining an existing key is not caught; that needs the world journal.

It is read-only and imports nothing from the runtime, so it is safe beside live cohorts.
Usage: ``python tools/laya_evidence.py data/laya-experiments/<cohort> [...]``.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

MILESTONES = ("C0", "C1", "C2", "C3", "C4", "C5", "C6")
PREREQUISITE = {"C3": "C2", "C4": "C3", "C5": "C4", "C6": "C4"}


def _samples(job: dict) -> list[dict]:
    return [s for s in job.get("strict_current") or () if isinstance(s, dict)]


def _evidence(sample: dict, milestone: str) -> dict:
    return (sample.get("predicate_evidence") or {}).get(milestone) or {}


def baselined_c6_tick(job: dict) -> int | None:
    """First sample tick where C6 passes once late-appearing consumer keys are baselined.

    Returns None when no sample carries per-consumer C6 evidence (older rows or c6-v1).
    """
    baseline: dict[str, float] = {}
    seen_evidence = False
    for index, sample in enumerate(_samples(job)):
        evidence = _evidence(sample, "C6").get("evidence") or {}
        deltas = evidence.get("consumer_deltas")
        if not isinstance(deltas, dict):
            continue
        seen_evidence = True
        for key, value in deltas.items():
            if key not in baseline:
                baseline[key] = float(value or 0) if index else 0.0
        consumed = any(float(value or 0) > baseline[key] for key, value in deltas.items())
        produced = (evidence.get("checks") or {}).get("advanced_circuits_newly_produced")
        if consumed and produced:
            return sample.get("tick")
    return None if seen_evidence else _latched(job).get("C6")


def _latched(job: dict) -> dict[str, int | None]:
    reached = job.get("strict_reached")
    if isinstance(reached, dict) and reached:
        return {m: tick for m, tick in reached.items() if m in MILESTONES}
    return {m: None for m in job.get("accepted_ever") or () if m in MILESTONES}


def milestone_evidence(job: dict) -> dict[str, dict]:
    """Per-milestone evidence for one job record from ``job-status.json``."""
    latched = _latched(job)
    samples = _samples(job)
    held = job.get("strict_current_at_end") or (samples[-1].get("statuses") if samples else None) or {}
    c6_has_samples = any(isinstance((_evidence(s, "C6").get("evidence") or {}).get("consumer_deltas"), dict)
                         for s in samples)
    result: dict[str, dict] = {}
    for milestone in MILESTONES:
        version = next((_evidence(s, milestone).get("predicate_version") for s in reversed(samples)
                        if _evidence(s, milestone).get("predicate_version")), None)
        version = version or job.get(f"{milestone.lower()}_predicate_version")
        tick = baselined_c6_tick(job) if milestone == "C6" and c6_has_samples else latched.get(milestone)
        reached = milestone in latched and not (milestone == "C6" and c6_has_samples and tick is None)
        prerequisite = result.get(PREREQUISITE.get(milestone, ""))
        ordered = reached and (prerequisite is None or prerequisite["reached_ordered"])
        chain_ticks = [t for t in (tick, prerequisite and prerequisite["ordered_tick"]) if t is not None]
        result[milestone] = {"reached_any": milestone in latched, "reached_ordered": bool(ordered),
                             "held_at_end": held.get(milestone), "first_tick": tick,
                             "ordered_tick": max(chain_ticks) if ordered and chain_ticks else None,
                             "predicate_version": version}
    return result


def arm_summary(jobs: list[dict]) -> dict[str, dict]:
    """Per-arm counts over completed jobs: latch versus ordered versus held at end."""
    arms: dict[str, dict] = {}
    for job in jobs:
        arm = arms.setdefault(str(job.get("arm")), {"planned": 0, "completed": 0, "versions": collections.Counter(),
                                                     **{k: collections.Counter() for k in ("any", "ordered", "held")}})
        arm["planned"] += 1
        if job.get("status") != "completed":
            continue
        arm["completed"] += 1
        for milestone, row in milestone_evidence(job).items():
            arm["any"][milestone] += row["reached_any"]
            arm["ordered"][milestone] += row["reached_ordered"]
            arm["held"][milestone] += row["held_at_end"] is True
            if row["predicate_version"] and milestone in ("C1", "C5", "C6"):
                arm["versions"][row["predicate_version"]] += 1
    return arms


def _table(cohort: Path) -> str:
    ledger = json.loads((cohort / "job-status.json").read_text())
    jobs = ledger.get("jobs") if isinstance(ledger, dict) else ledger
    lines = [f"## {cohort.name}", "", "Cells are latch / ordered / held at end, over completed jobs. "
             "C6 ordered uses the baselined consumer.", "",
             "| Arm | Done/planned | C2 | C3 | C4 | C5 | C6 | Predicates seen |", "|---|---|---|---|---|---|---|---|"]
    for name, arm in sorted(arm_summary(jobs or []).items()):
        cells = [f"{arm['any'][m]} / {arm['ordered'][m]} / {arm['held'][m]}" for m in ("C2", "C3", "C4", "C5", "C6")]
        versions = ", ".join(sorted(arm["versions"])) or "?"
        lines.append(f"| {name} | {arm['completed']}/{arm['planned']} | " + " | ".join(cells) + f" | {versions} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cohorts", nargs="+", type=Path, help="Cohort directories containing job-status.json")
    args = parser.parse_args(argv)
    print("\n".join(_table(cohort) for cohort in args.cohorts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
