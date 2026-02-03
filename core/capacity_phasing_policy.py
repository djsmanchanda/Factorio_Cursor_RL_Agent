# Path: core/capacity_phasing_policy.py
# Purpose: Determine deterministic capacity phasing from ProgressState and BuildIntent.
#
# Example input:
#   progress_state = {
#     "ultimate_capacity": 1000,
#     "current_capacity": 80,
#     "committed_capacity": 120,
#     "active_phase_capacity": 100,
#     "completed_phases": [50],
#     "rationale": "..."
#   }
#   build_intent = {
#     "intents": [{"kind": "block_placeholder", "block_type": "circuits", "count": 3}]
#   }
#
# Example output:
#   {
#     "desired_active_capacity": 250,
#     "previous_active_capacity": 100,
#     "reason": "early-stage",
#     "next_allowed_actions": ["ghost_expand", "ghost_upgrade"]
#   }

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List

from jsonschema import Draft7Validator


PHASE_THRESHOLDS: List[int] = [50, 100, 250, 1000]


@dataclass(frozen=True)
class CapacityPhasing:
    desired_active_capacity: int
    previous_active_capacity: int
    reason: str
    next_allowed_actions: List[str]

    def to_dict(self) -> dict:
        return {
            "desired_active_capacity": self.desired_active_capacity,
            "previous_active_capacity": self.previous_active_capacity,
            "reason": self.reason,
            "next_allowed_actions": list(self.next_allowed_actions),
        }


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _validate_schema(payload: dict, schema_path: Path, label: str) -> None:
    schema = _load_json(schema_path)
    validator = Draft7Validator(schema)
    errors = list(validator.iter_errors(payload))
    if errors:
        messages = []
        for error in errors:
            path = "/".join(str(part) for part in error.path) if error.path else "<root>"
            messages.append(f"- {path}: {error.message}")
        raise ValueError(f"{label} validation FAILED:\n" + "\n".join(messages))


def _select_phase(thresholds: List[int], target: int, ultimate: int) -> int:
    if not thresholds:
        raise ValueError("Phase thresholds must not be empty")
    for phase in thresholds:
        if phase >= target:
            return phase
    return ultimate


def _derive_reason(desired: int, previous: int, ultimate: int) -> str:
    if desired == previous:
        return "resource-limited"
    if desired >= ultimate:
        return "upgrade-ready"
    return "early-stage"


def evaluate_capacity_phasing(
    progress_state: dict,
    build_intent: dict,
    schema_path: Path | None = None,
) -> CapacityPhasing:
    if "active_phase_capacity" not in progress_state:
        raise ValueError("ProgressState must include active_phase_capacity")
    if "current_capacity" not in progress_state:
        raise ValueError("ProgressState must include current_capacity")
    if "committed_capacity" not in progress_state:
        raise ValueError("ProgressState must include committed_capacity")
    if "ultimate_capacity" not in progress_state:
        raise ValueError("ProgressState must include ultimate_capacity")

    previous = int(progress_state["active_phase_capacity"])
    current = int(progress_state["current_capacity"])
    committed = int(progress_state["committed_capacity"])
    ultimate = int(progress_state["ultimate_capacity"])

    if any(value < 0 for value in [previous, current, committed, ultimate]):
        raise ValueError("ProgressState capacities must be non-negative")

    if not build_intent.get("intents"):
        raise ValueError("BuildIntent must include at least one intent")

    target = max(current, committed)
    selected = _select_phase(PHASE_THRESHOLDS, target, ultimate)
    desired = max(previous, selected)

    reason = _derive_reason(desired, previous, ultimate)
    next_allowed_actions = ["ghost_upgrade"] if desired == previous else ["ghost_expand", "ghost_upgrade"]

    phasing = CapacityPhasing(
        desired_active_capacity=desired,
        previous_active_capacity=previous,
        reason=reason,
        next_allowed_actions=next_allowed_actions,
    )

    if schema_path is None:
        repo_root = Path(__file__).resolve().parents[1]
        schema_path = repo_root / "schemas" / "capacity_phasing.schema.json"
    _validate_schema(phasing.to_dict(), schema_path, "CapacityPhasing")

    return phasing
