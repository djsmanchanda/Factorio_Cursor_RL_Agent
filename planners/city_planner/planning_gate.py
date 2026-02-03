# Path: planners/city_planner/planning_gate.py
# Purpose: Determine whether planning can proceed based on capability resolution.

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from jsonschema import Draft7Validator


@dataclass(frozen=True)
class PlanningDecision:
    status: str
    reason: str
    required_actions: List[str] = field(default_factory=list)
    notes: str | None = None

    def to_dict(self) -> dict:
        payload = {
            "status": self.status,
            "reason": self.reason,
            "required_actions": list(self.required_actions),
        }
        if self.notes is not None:
            payload["notes"] = self.notes
        return payload


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


def _load_schema(schema_path: Path) -> dict:
    with schema_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _validate_decision(decision: PlanningDecision, schema_path: Path) -> None:
    schema = _load_schema(schema_path)
    validator = Draft7Validator(schema)
    errors = list(validator.iter_errors(decision.to_dict()))
    if errors:
        messages = []
        for error in errors:
            path = "/".join(str(part) for part in error.path) if error.path else "<root>"
            messages.append(f"- {path}: {error.message}")
        raise ValueError("Planning gate validation FAILED:\n" + "\n".join(messages))


def decide_planning(resolution: dict, schema_path: Path | None = None) -> PlanningDecision:
    intent = resolution.get("intent")
    missing = list(resolution.get("missing_prerequisites", []))
    blocked = list(resolution.get("blocked", []))

    if missing or blocked:
        required_actions = _required_actions_for_missing(missing)
        reason = missing[0] if missing else "capability_blocked"
        decision = PlanningDecision(
            status="blocked",
            reason=reason,
            required_actions=required_actions,
            notes=f"Intent '{intent}' cannot proceed without prerequisites",
        )
    else:
        decision = PlanningDecision(
            status="ready",
            reason="capabilities_available",
            required_actions=[],
            notes=f"Intent '{intent}' has required capabilities",
        )

    if schema_path is None:
        repo_root = Path(__file__).resolve().parents[2]
        schema_path = repo_root / "schemas" / "planning_gate.schema.json"

    _validate_decision(decision, schema_path)
    return decision
