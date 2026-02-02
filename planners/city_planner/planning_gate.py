# Path: planners/city_planner/planning_gate.py
# Purpose: Determine whether planning can proceed based on capability resolution.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class PlanningDecision:
    status: str
    reason: str
    required_actions: List[str] = field(default_factory=list)
    notes: str | None = None


def _required_actions_for_missing(missing: List[str]) -> List[str]:
    mapping = {
        "city_grid": "define_city_grid",
    }
    actions = []
    for item in missing:
        action = mapping.get(item)
        if action:
            actions.append(action)
    return actions


def decide_planning(resolution: dict) -> PlanningDecision:
    intent = resolution.get("intent")
    missing = list(resolution.get("missing_prerequisites", []))
    blocked = list(resolution.get("blocked", []))

    if missing or blocked:
        required_actions = _required_actions_for_missing(missing)
        reason = missing[0] if missing else "capability_blocked"
        return PlanningDecision(
            status="blocked",
            reason=reason,
            required_actions=required_actions,
            notes=f"Intent '{intent}' cannot proceed without prerequisites",
        )

    return PlanningDecision(
        status="ready",
        reason="capabilities_available",
        required_actions=[],
        notes=f"Intent '{intent}' has required capabilities",
    )
