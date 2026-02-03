# Path: planners/city_planner/ghost_projection_phase.py
# Purpose: Convert build intents into a ghost-only plan without geometry.

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


def generate_ghost_plan(
    build_intent: dict,
    build_intent_schema_path: Optional[Path] = None,
    ghost_plan_schema_path: Optional[Path] = None,
) -> GhostPlan:
    repo_root = Path(__file__).resolve().parents[2]
    if build_intent_schema_path is None:
        build_intent_schema_path = repo_root / "schemas" / "build_intent.schema.json"
    if ghost_plan_schema_path is None:
        ghost_plan_schema_path = repo_root / "schemas" / "ghost_plan.schema.json"

    _validate_payload(build_intent, build_intent_schema_path, "Build intent")

    ghosts: List[dict] = []
    for intent in build_intent.get("intents", []):
        kind = intent.get("kind")
        block_type = intent.get("block_type")
        count = int(intent.get("count", 0))
        interfaces = intent.get("interfaces", [])
        capacity_class = intent.get("capacity_class")

        if kind != "block_placeholder":
            raise ValueError(f"Unsupported build intent kind: {kind}")

        prototype = _placeholder_prototype(block_type)
        for _ in range(count):
            tags: Dict[str, str] = {
                "block_type": str(block_type),
                "kind": str(kind),
            }
            if interfaces:
                tags["interfaces"] = ",".join(interfaces)
            if capacity_class:
                tags["capacity_class"] = str(capacity_class)

            ghosts.append({"prototype": prototype, "tags": tags})

    plan = GhostPlan(ghosts=ghosts)
    _validate_payload(plan.to_dict(), ghost_plan_schema_path, "Ghost plan")
    return plan
