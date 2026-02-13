# Path: core/ghost_slice_planner.py
# Purpose: Build deterministic, read-only ghost emission slices from allocation and target telemetry.

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

from jsonschema import Draft7Validator


@dataclass(frozen=True)
class GhostSlice:
    target_block: str
    target_recipe: str
    ghost_count: int
    capacity_slice: int
    rationale: str

    def to_dict(self) -> dict:
        return {
            "target_block": self.target_block,
            "target_recipe": self.target_recipe,
            "ghost_count": int(self.ghost_count),
            "capacity_slice": int(self.capacity_slice),
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


def _intent_id(intent: dict, index: int) -> str:
    kind = str(intent.get("kind", ""))
    block_type = str(intent.get("block_type", ""))
    capacity_class = str(intent.get("capacity_class", ""))
    return f"{kind}:{block_type}:{capacity_class}:{index:06d}"


def _intent_matches_recipe(intent: dict, target_recipe: str) -> bool:
    if str(intent.get("block_type", "")) == target_recipe:
        return True
    if str(intent.get("capacity_class", "")) == target_recipe:
        return True
    interfaces = intent.get("interfaces", [])
    if type(interfaces) is list:
        return any(str(item) == target_recipe for item in interfaces)
    return False


def sort_intents_for_slice(build_intent: dict, target_recipe: str) -> List[Tuple[str, dict]]:
    intents = build_intent.get("intents")
    if type(intents) is not list:
        raise ValueError("BuildIntent must include intents array")

    ranked: List[Tuple[int, str, dict]] = []
    for index, intent in enumerate(intents):
        if type(intent) is not dict:
            raise ValueError("BuildIntent intents must be objects")
        intent_id = _intent_id(intent, index)
        matched = _intent_matches_recipe(intent, target_recipe)
        # Prefer recipe matches first, then stable deterministic intent_id order.
        ranked.append((0 if matched else 1, intent_id, intent))

    ranked.sort(key=lambda item: (item[0], item[1]))
    return [(intent_id, intent) for _, intent_id, intent in ranked]


def derive_ghost_slice(
    build_intent: dict,
    progress_state: dict,
    capacity_allocation: dict,
    expansion_target: dict,
    schema_path: Path | None = None,
) -> GhostSlice:
    if type(build_intent) is not dict:
        raise ValueError("build_intent must be an object")
    if type(progress_state) is not dict:
        raise ValueError("progress_state must be an object")
    if type(capacity_allocation) is not dict:
        raise ValueError("capacity_allocation must be an object")
    if type(expansion_target) is not dict:
        raise ValueError("expansion_target must be an object")

    if "active_phase_capacity" not in progress_state:
        raise ValueError("progress_state must include active_phase_capacity")
    if "allocated_now" not in capacity_allocation:
        raise ValueError("capacity_allocation must include allocated_now")
    if "target_block" not in expansion_target or "target_recipe" not in expansion_target:
        raise ValueError("expansion_target must include target_block and target_recipe")

    target_block = str(expansion_target["target_block"])
    target_recipe = str(expansion_target["target_recipe"])
    if target_block == "" or target_recipe == "":
        raise ValueError("expansion_target fields must be non-empty strings")

    allocated_now = int(capacity_allocation["allocated_now"])
    ghost_count = max(0, allocated_now)
    capacity_slice = ghost_count

    if ghost_count <= 0:
        rationale = "No immediate allocation available; emitting empty ghost slice."
    else:
        ordered = sort_intents_for_slice(build_intent, target_recipe)
        rationale = (
            f"Ghost slice derived from allocated_now={ghost_count}; intents prioritized by recipe "
            f"match to '{target_recipe}' and stable synthetic intent id ordering."
        )
        if len(ordered) == 0:
            rationale = "No intents available; emitting empty ghost slice."
            ghost_count = 0
            capacity_slice = 0

    slice_obj = GhostSlice(
        target_block=target_block,
        target_recipe=target_recipe,
        ghost_count=ghost_count,
        capacity_slice=capacity_slice,
        rationale=rationale,
    )

    if schema_path is None:
        repo_root = Path(__file__).resolve().parents[1]
        schema_path = repo_root / "schemas" / "ghost_slice.schema.json"
    _validate_schema(slice_obj.to_dict(), schema_path, "GhostSlice")
    return slice_obj
