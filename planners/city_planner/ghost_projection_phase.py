# Path: planners/city_planner/ghost_projection_phase.py
# Purpose: Convert build intents into a phase-aware ghost-only plan without geometry.
#
# Example input:
#   build_intent = {
#     "intents": [
#       {"kind": "block_placeholder", "block_type": "circuits", "count": 3},
#       {"kind": "block_placeholder", "block_type": "smelting", "count": 2}
#     ]
#   }
#   progress_state = {
#     "ultimate_capacity": 5,
#     "current_capacity": 2,
#     "committed_capacity": 2,
#     "active_phase_capacity": 50,
#     "completed_phases": [50],
#     "rationale": "..."
#   }
#   capacity_phasing = {
#     "desired_active_capacity": 4,
#     "previous_active_capacity": 2,
#     "reason": "early-stage",
#     "next_allowed_actions": ["ghost_expand", "ghost_upgrade"]
#   }
#
# Example output:
#   {
#     "ghosts": [
#       {
#         "prototype": "assembling-machine-1",
#         "tags": {
#           "block": "circuits",
#           "block_type": "circuits",
#           "kind": "block_placeholder",
#           "phase": "capacity_phase",
#           "capacity_slice": "2->4"
#         }
#       }
#     ]
#   }

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from jsonschema import Draft7Validator


@dataclass(frozen=True)
class GhostPlan:
    ghosts: List[dict]

    def to_dict(self) -> dict:
        return {"ghosts": list(self.ghosts)}


def _load_schema(schema_path: Path) -> dict:
    with schema_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _validate_payload(payload: dict, schema_path: Path, label: str) -> None:
    schema = _load_schema(schema_path)
    validator = Draft7Validator(schema)
    errors = list(validator.iter_errors(payload))
    if errors:
        messages = []
        for error in errors:
            path = "/".join(str(part) for part in error.path) if error.path else "<root>"
            messages.append(f"- {path}: {error.message}")
        raise ValueError(f"{label} validation FAILED:\n" + "\n".join(messages))


def _placeholder_prototype(block_type: str) -> str:
    mapping = {
        "circuits": "assembling-machine-1",
        "smelting": "stone-furnace",
        "science": "lab",
    }
    return mapping.get(block_type, "assembling-machine-1")


def _derive_delta_capacity(build_intent: dict, progress_state: dict, capacity_phasing: dict) -> int:
    ultimate_capacity = int(progress_state.get("ultimate_capacity", 0))
    active_phase = int(progress_state.get("active_phase_capacity", 0))

    previous = int(capacity_phasing.get("previous_active_capacity", 0))
    desired = int(capacity_phasing.get("desired_active_capacity", 0))

    if desired < previous:
        raise ValueError("Capacity phasing must be monotonic")
    if desired > ultimate_capacity:
        raise ValueError("Desired capacity exceeds ultimate capacity")
    if previous != active_phase:
        raise ValueError("Capacity phasing previous_active_capacity must match ProgressState active_phase_capacity")

    remaining = ultimate_capacity - previous
    delta = desired - previous
    if delta > remaining:
        raise ValueError("Delta capacity exceeds remaining capacity")

    return delta


def generate_ghost_plan(
    build_intent: dict,
    progress_state: dict,
    capacity_phasing: dict,
    build_intent_schema_path: Optional[Path] = None,
    progress_schema_path: Optional[Path] = None,
    capacity_phasing_schema_path: Optional[Path] = None,
    ghost_plan_schema_path: Optional[Path] = None,
) -> GhostPlan:
    repo_root = Path(__file__).resolve().parents[2]
    if build_intent_schema_path is None:
        build_intent_schema_path = repo_root / "schemas" / "build_intent.schema.json"
    if progress_schema_path is None:
        progress_schema_path = repo_root / "schemas" / "progress_state.schema.json"
    if capacity_phasing_schema_path is None:
        capacity_phasing_schema_path = repo_root / "schemas" / "capacity_phasing.schema.json"
    if ghost_plan_schema_path is None:
        ghost_plan_schema_path = repo_root / "schemas" / "ghost_plan.schema.json"

    _validate_payload(build_intent, build_intent_schema_path, "Build intent")
    _validate_payload(progress_state, progress_schema_path, "ProgressState")
    _validate_payload(capacity_phasing, capacity_phasing_schema_path, "CapacityPhasing")

    delta_capacity = _derive_delta_capacity(build_intent, progress_state, capacity_phasing)
    previous = int(capacity_phasing.get("previous_active_capacity", 0))
    desired = int(capacity_phasing.get("desired_active_capacity", 0))

    ghosts: List[dict] = []
    remaining = delta_capacity
    for intent in build_intent.get("intents", []):
        kind = intent.get("kind")
        block_type = intent.get("block_type")
        count = int(intent.get("count", 0))
        interfaces = intent.get("interfaces", [])
        capacity_class = intent.get("capacity_class")

        if kind != "block_placeholder":
            raise ValueError(f"Unsupported build intent kind: {kind}")

        prototype = _placeholder_prototype(block_type)
        to_emit = min(count, remaining)
        for _ in range(to_emit):
            tags: Dict[str, str] = {
                "block": str(block_type),
                "block_type": str(block_type),
                "kind": str(kind),
                "phase": "capacity_phase",
                "capacity_slice": f"{previous}->{desired}",
            }
            if interfaces:
                tags["interfaces"] = ",".join(interfaces)
            if capacity_class:
                tags["capacity_class"] = str(capacity_class)

            ghosts.append({"prototype": prototype, "tags": tags})
        remaining -= to_emit
        if remaining == 0:
            break

    if remaining != 0:
        raise ValueError("Delta capacity could not be allocated from BuildIntent")

    plan = GhostPlan(ghosts=ghosts)
    _validate_payload(plan.to_dict(), ghost_plan_schema_path, "Ghost plan")
    return plan
