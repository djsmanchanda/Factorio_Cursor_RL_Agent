# Path: training/laya_acceptance.py
# Purpose: Preserve current checkpoint checks beside monotonic reached milestones.

from __future__ import annotations

import copy

VERSION = "laya-acceptance-v1"


def acceptance_packet(observation, evaluator, *, baseline, history, scope):
    """Pure export of existing predicates; no new success or scheduling rule."""
    statuses, results = {}, {}
    for definition in evaluator.milestones:
        latched = evaluator.evaluate(definition.checkpoint_id, observation.snapshot,
                                     baseline=baseline, history=history)
        # The monotonic evaluator returns only a reached marker after a pass.
        # A fresh evaluation retains today's conditions without retracting it.
        current = definition.evaluate(observation.snapshot, baseline, history)
        statuses[definition.checkpoint_id] = latched.status.value
        results[definition.checkpoint_id] = {**current.as_mapping(), "latched_status": latched.status.value}
    return {"version": VERSION, "scope": dict(scope), "observation": copy.deepcopy(observation.as_mapping()),
            "machine_counter_units": {"kind": "scaled_machine_progress", "completed_craft_scale": 1000,
                                      "note": "machine_health combines completed crafts * 1000 with fractional progress; these are not item delivery counts"},
            "statuses": statuses, "results": results}


def summary_details(packet, path):
    """Index checks compactly; full raw evidence stays in the immutable sample."""
    snapshot = packet["observation"]["snapshot"]
    return {"version": VERSION, "path": str(path), "tick": snapshot.get("tick"),
            "tick_end": snapshot.get("tick_end"), "results": {
                key: {"predicate_version": value["predicate_version"], "status": value["status"],
                      "latched_status": value["latched_status"], "checks": value["evidence"].get("checks", {}),
                      "missing": value["missing"]}
                for key, value in packet["results"].items()}}


def condition_counts(rows, checkpoint="C1"):
    """Latest measured conditions; legacy/missing samples remain not sampled."""
    observations = [(row.get("summary", {}).get("acceptance_details", {}).get("results", {}).get(checkpoint, {}))
                    for row in rows]
    names = sorted({name for result in observations for name in result.get("checks", {})})
    counts = {name: dict(passed=0, failed=0, unknown=0, not_sampled=0) for name in names}
    for result in observations:
        checks = result.get("checks", {})
        for name in names:
            value = checks.get(name)
            state = ("not_sampled" if name not in checks else "passed" if value is True else
                     "failed" if value is False else "unknown")
            counts[name][state] += 1
    return {"conditions": counts, "predicate_versions": sorted({r["predicate_version"] for r in observations if r})}
