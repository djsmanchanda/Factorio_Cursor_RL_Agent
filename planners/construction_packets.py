# Path: planners/construction_packets.py | Purpose: Release funded ghost work within observed construction capacity.
from __future__ import annotations

from collections import Counter
from math import floor


def release_packet(phases, targets, network, *, charge_fraction=1.0):
    """One packet, at most one outstanding job per construction robot.

    Existing ghosts already consume the network's budget and material ledger.
    Inspect exact targets on every pass so restarts never resubmit built work.
    Material shortages include future packets, without reserving their items.
    """
    states = {(t["entity"], t["position"]["x"], t["position"]["y"]): t["state"] for t in targets}
    reserved = network.get("reserved") or {}
    available = {item: max(0, int(count) - int(reserved.get(item, 0)))
                 for item, count in (network.get("items") or {}).items()}
    backlog = sum(int(count) for count in reserved.values())
    budget = min(max(0, int(network.get("construction_robots", 0)) - backlog),
                 int(network.get("available_construction_robots", network.get("construction_robots", 0))))
    budget = floor(budget * max(0.0, min(1.0, charge_fraction)))
    total_logistic = int(network.get("logistic_robots", 0))
    if total_logistic and "available_logistic_robots" in network and budget:
        budget = min(budget, max(1, floor(budget * network["available_logistic_robots"] / total_logistic)))
    missing = []
    required = Counter()
    seen = set()
    for phase in phases:
        actions = []
        for action in phase["actions"]:
            key = (action["entity"], action["position"]["x"], action["position"]["y"])
            if key in seen:
                continue
            seen.add(key)
            if key not in states:
                raise ValueError("construction inspection omitted a target")
            if states[key] == "missing":
                actions.append(action)
                required[action["entity"]] += 1
            elif states[key] not in ("built", "ghost"):
                raise ValueError("unknown construction target state")
        if actions:
            missing.append({**phase, "actions": actions})
    short = {item: count - available.get(item, 0) for item, count in required.items()
             if count > available.get(item, 0)}
    for phase in missing:
        selected = []
        for action in phase["actions"]:
            item = action["entity"]
            if len(selected) >= budget:
                break
            if available.get(item, 0) > 0:
                selected.append(action)
                available[item] -= 1
        if selected:
            return [{**phase, "actions": selected}], short
    return [], short
