# Path: core/capacity_allocator.py
# Purpose: Deterministically allocate conservative phase capacity to an expansion target.

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft7Validator


@dataclass(frozen=True)
class CapacityAllocation:
    target_block: str
    target_recipe: str
    phase_capacity: int
    allocated_now: int
    reserved_for_later: int
    rationale: str

    def to_dict(self) -> dict:
        return {
            "target_block": self.target_block,
            "target_recipe": self.target_recipe,
            "phase_capacity": int(self.phase_capacity),
            "allocated_now": int(self.allocated_now),
            "reserved_for_later": int(self.reserved_for_later),
            "rationale": self.rationale,
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
            loc = "/".join(str(part) for part in error.path) if error.path else "<root>"
            messages.append(f"- {loc}: {error.message}")
        raise ValueError(f"{label} validation FAILED:\n" + "\n".join(messages))


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


def allocate_phase_capacity(
    expansion_target: dict,
    progress_state: dict,
    capacity_phasing: dict,
    throughput_stress_index: float,
    schema_path: Path | None = None,
) -> CapacityAllocation:
    required_target = {"target_block", "target_recipe"}
    missing_target = sorted(required_target.difference(expansion_target.keys()))
    if missing_target:
        raise ValueError(f"ExpansionTarget missing required fields for allocation: {missing_target}")

    required_progress = {"active_phase_capacity", "committed_capacity"}
    missing_progress = sorted(required_progress.difference(progress_state.keys()))
    if missing_progress:
        raise ValueError(f"ProgressState missing required fields for allocation: {missing_progress}")

    required_phasing = {"desired_active_capacity", "previous_active_capacity", "reason", "next_allowed_actions"}
    missing_phasing = sorted(required_phasing.difference(capacity_phasing.keys()))
    if missing_phasing:
        raise ValueError(f"CapacityPhasing missing required fields for allocation: {missing_phasing}")

    if not isinstance(throughput_stress_index, (int, float)):
        raise ValueError("throughput_stress_index must be numeric for allocation")
    stress = _clamp(float(throughput_stress_index), 0.0, 1.0)

    target_block = expansion_target["target_block"]
    target_recipe = expansion_target["target_recipe"]
    if not isinstance(target_block, str) or target_block == "":
        raise ValueError("ExpansionTarget.target_block must be a non-empty string")
    if not isinstance(target_recipe, str) or target_recipe == "":
        raise ValueError("ExpansionTarget.target_recipe must be a non-empty string")

    phase_capacity = int(progress_state["active_phase_capacity"])
    committed_capacity = int(progress_state["committed_capacity"])
    if phase_capacity < 0 or committed_capacity < 0:
        raise ValueError("capacity fields must be non-negative for allocation")

    headroom = phase_capacity - committed_capacity

    if headroom <= 0:
        allocated_now = 0
        reserved_for_later = 0
        rationale = "No remaining phase headroom; allocation deferred."
    else:
        base_fraction = _clamp(0.25 + (0.5 * stress), 0.25, 0.75)
        allocated_now = int(math.floor(float(headroom) * base_fraction))
        reserved_for_later = int(headroom - allocated_now)
        if allocated_now < 0:
            allocated_now = 0
        if reserved_for_later < 0:
            reserved_for_later = 0
        rationale = (
            f"Headroom={headroom}; conservative base_fraction={base_fraction:.3f} "
            f"from throughput_stress_index={stress:.3f}."
        )

    allocation = CapacityAllocation(
        target_block=target_block,
        target_recipe=target_recipe,
        phase_capacity=max(0, phase_capacity),
        allocated_now=max(0, int(allocated_now)),
        reserved_for_later=max(0, int(reserved_for_later)),
        rationale=rationale,
    )

    if schema_path is None:
        repo_root = Path(__file__).resolve().parents[1]
        schema_path = repo_root / "schemas" / "capacity_allocation.schema.json"
    _validate_schema(allocation.to_dict(), schema_path, "CapacityAllocation")
    return allocation
