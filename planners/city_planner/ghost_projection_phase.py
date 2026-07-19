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
from core.block_prototypes import placeholder_prototype
from core.ghost_slice_planner import derive_ghost_slice, sort_intents_for_slice
from core.sandbox_zoning import derive_sandbox_zones
from core.zone_fill_tracker import derive_zone_fill


@dataclass(frozen=True)
class GhostPlan:
    ghosts: List[dict]
    metadata: Optional[dict] = None

    def to_dict(self) -> dict:
        payload = {"ghosts": list(self.ghosts)}
        if self.metadata is not None:
            payload["metadata"] = self.metadata
        return payload


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
    return placeholder_prototype(block_type)


def _derive_delta_capacity(build_intent: dict, progress_state: dict, capacity_phasing: dict) -> int:
    ultimate_capacity = int(progress_state.get("ultimate_capacity", 0))
    active_phase = int(progress_state.get("active_phase_capacity", 0))
    current = int(progress_state.get("current_capacity", 0))
    committed = int(progress_state.get("committed_capacity", 0))

    previous = int(capacity_phasing.get("previous_active_capacity", 0))
    desired = int(capacity_phasing.get("desired_active_capacity", 0))

    if desired < previous:
        raise ValueError("Capacity phasing must be monotonic")
    if desired > ultimate_capacity:
        raise ValueError("Desired capacity exceeds ultimate capacity")
    if previous != active_phase:
        raise ValueError("Capacity phasing previous_active_capacity must match ProgressState active_phase_capacity")

    # Delta fills the desired phase: everything already built or already
    # projected as ghosts counts against it. The old formula (desired -
    # previous) only produced ghosts at phase jumps, which auto-derived
    # state can never trigger, making the pipeline inert.
    committed_effective = max(current, committed)
    delta = min(desired, ultimate_capacity) - committed_effective
    return max(0, delta)


def generate_ghost_plan(
    build_intent: dict,
    progress_state: dict,
    capacity_phasing: dict,
    capacity_allocation: Optional[dict] = None,
    expansion_target: Optional[dict] = None,
    existing_by_block: Optional[Dict[str, int]] = None,
    build_intent_schema_path: Optional[Path] = None,
    progress_schema_path: Optional[Path] = None,
    capacity_phasing_schema_path: Optional[Path] = None,
    ghost_slice_schema_path: Optional[Path] = None,
    ghost_plan_schema_path: Optional[Path] = None,
) -> GhostPlan:
    repo_root = Path(__file__).resolve().parents[2]
    if build_intent_schema_path is None:
        build_intent_schema_path = repo_root / "schemas" / "build_intent.schema.json"
    if progress_schema_path is None:
        progress_schema_path = repo_root / "schemas" / "progress_state.schema.json"
    if capacity_phasing_schema_path is None:
        capacity_phasing_schema_path = repo_root / "schemas" / "capacity_phasing.schema.json"
    if ghost_slice_schema_path is None:
        ghost_slice_schema_path = repo_root / "schemas" / "ghost_slice.schema.json"
    if ghost_plan_schema_path is None:
        ghost_plan_schema_path = repo_root / "schemas" / "ghost_plan.schema.json"

    _validate_payload(build_intent, build_intent_schema_path, "Build intent")
    _validate_payload(progress_state, progress_schema_path, "ProgressState")
    _validate_payload(capacity_phasing, capacity_phasing_schema_path, "CapacityPhasing")

    delta_capacity = _derive_delta_capacity(build_intent, progress_state, capacity_phasing)
    previous = int(capacity_phasing.get("previous_active_capacity", 0))
    desired = int(capacity_phasing.get("desired_active_capacity", 0))
    default_target = {
        "target_block": str(build_intent.get("intents", [{}])[0].get("block_type", "default")) if build_intent.get("intents") else "default",
        "target_recipe": str(build_intent.get("intents", [{}])[0].get("block_type", "default")) if build_intent.get("intents") else "default",
    }
    if expansion_target is None:
        expansion_target = default_target
    if capacity_allocation is None:
        capacity_allocation = {
            "target_block": expansion_target["target_block"],
            "target_recipe": expansion_target["target_recipe"],
            "phase_capacity": int(progress_state.get("active_phase_capacity", 0)),
            "allocated_now": int(max(0, delta_capacity)),
            "reserved_for_later": 0,
            "rationale": "Fallback allocation derived from delta capacity.",
        }

    ghost_slice = derive_ghost_slice(
        build_intent=build_intent,
        progress_state=progress_state,
        capacity_allocation=capacity_allocation,
        expansion_target=expansion_target,
        schema_path=ghost_slice_schema_path,
    )

    ghosts: List[dict] = []
    ghosts_present_by_block: Dict[str, int] = {}
    remaining = min(int(delta_capacity), int(ghost_slice.ghost_count))
    ordered_intents = sort_intents_for_slice(build_intent, ghost_slice.target_recipe)
    block_ids = [str(intent.get("block_type", "")) for _, intent in ordered_intents if str(intent.get("block_type", "")) != ""]
    zones = derive_sandbox_zones(block_ids)
    existing_by_block = dict(existing_by_block or {})
    for _, intent in ordered_intents:
        kind = intent.get("kind")
        block_type = intent.get("block_type")
        count = int(intent.get("count", 0))
        interfaces = intent.get("interfaces", [])
        capacity_class = intent.get("capacity_class")

        if kind != "block_placeholder":
            raise ValueError(f"Unsupported build intent kind: {kind}")

        prototype = _placeholder_prototype(block_type)
        # Blocks already satisfied (built or pending) must not be re-projected,
        # and new ghosts continue the zone grid after the occupied cells.
        already = int(existing_by_block.get(str(block_type), 0))
        to_emit = min(max(0, count - already), remaining)
        zone = zones.get(str(block_type))
        if zone is None:
            raise ValueError(f"Missing sandbox zone for block: {block_type}")
        for offset in range(to_emit):
            tags: Dict[str, str] = {
                "block": str(block_type),
                "block_type": str(block_type),
                "kind": str(kind),
                "phase": "capacity_phase",
                "capacity_slice": str(ghost_slice.capacity_slice),
                "zone_block_id": str(zone.block_id),
                "zone_origin_x": str(zone.origin_x),
                "zone_origin_y": str(zone.origin_y),
                "zone_stride_x": str(zone.stride_x),
                "zone_stride_y": str(zone.stride_y),
                "zone_index": str(already + offset),
            }
            if interfaces:
                tags["interfaces"] = ",".join(interfaces)
            if capacity_class:
                tags["capacity_class"] = str(capacity_class)

            ghosts.append({"prototype": prototype, "tags": tags})
            ghosts_present_by_block[str(block_type)] = int(ghosts_present_by_block.get(str(block_type), 0)) + 1
        remaining -= to_emit
        if remaining == 0:
            break

    if remaining > 0:
        raise ValueError("Delta capacity could not be allocated from BuildIntent")

    zone_fill = derive_zone_fill(zones=zones, ghosts_present_by_block=ghosts_present_by_block)
    metadata = {"zone_fill": [item.to_dict() for item in zone_fill]}
    plan = GhostPlan(ghosts=ghosts, metadata=metadata)
    # Preserve existing GhostPlan schema validation over the ghosts payload.
    _validate_payload({"ghosts": list(plan.ghosts)}, ghost_plan_schema_path, "Ghost plan")
    return plan
