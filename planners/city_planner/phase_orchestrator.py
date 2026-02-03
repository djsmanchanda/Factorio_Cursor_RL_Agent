# Path: planners/city_planner/phase_orchestrator.py
# Purpose: Execute phase planners in order to produce a planning bundle.

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from jsonschema import Draft7Validator, RefResolver

from planners.city_planner.phases.transport_strategy_phase import evaluate_transport_strategy
from planners.city_planner.phases.interface_definition_phase import evaluate_interface_definition
from planners.city_planner.phases.block_boundary_definition_phase import (
    evaluate_block_boundary_definition,
)
from planners.city_planner.phases.block_topology_planning_phase import (
    evaluate_block_topology_planning,
)


@dataclass(frozen=True)
class PlanningBundle:
    intent: str
    scope: str
    phase_results: List[dict]
    notes: Optional[str] = None

    def to_dict(self) -> dict:
        payload = {
            "intent": self.intent,
            "scope": self.scope,
            "phase_results": list(self.phase_results),
        }
        if self.notes is not None:
            payload["notes"] = self.notes
        return payload


_PHASE_PLANNERS = {
    "transport_strategy_selection": evaluate_transport_strategy,
    "interface_definition": evaluate_interface_definition,
    "block_boundary_definition": evaluate_block_boundary_definition,
    "block_topology_planning": evaluate_block_topology_planning,
}


def _load_schema(schema_path: Path) -> dict:
    with schema_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _validate_phase_result(result: dict, schema_path: Path) -> None:
    schema = _load_schema(schema_path)
    validator = Draft7Validator(schema)
    errors = list(validator.iter_errors(result))
    if errors:
        messages = []
        for error in errors:
            path = "/".join(str(part) for part in error.path) if error.path else "<root>"
            messages.append(f"- {path}: {error.message}")
        raise ValueError("Phase result validation FAILED:\n" + "\n".join(messages))


def _validate_bundle(bundle: PlanningBundle, schema_path: Path) -> None:
    schema = _load_schema(schema_path)
    resolver = RefResolver(base_uri=schema_path.parent.as_uri() + "/", referrer=schema)
    validator = Draft7Validator(schema, resolver=resolver)
    errors = list(validator.iter_errors(bundle.to_dict()))
    if errors:
        messages = []
        for error in errors:
            path = "/".join(str(part) for part in error.path) if error.path else "<root>"
            messages.append(f"- {path}: {error.message}")
        raise ValueError("Planning bundle validation FAILED:\n" + "\n".join(messages))


def build_planning_bundle(
    skeleton: dict,
    capability_resolution: dict,
    metrics: Optional[dict] = None,
    schema_path: Optional[Path] = None,
    phase_schema_path: Optional[Path] = None,
) -> PlanningBundle:
    phases = skeleton.get("phases", [])
    results: List[dict] = []

    for phase_entry in phases:
        phase_name = phase_entry.get("phase")
        planner = _PHASE_PLANNERS.get(phase_name)
        if planner is None:
            raise ValueError(f"No phase planner registered for '{phase_name}'")

        result = planner(phase_entry, capability_resolution, list(results), metrics)
        result_dict = result.to_dict()

        if phase_schema_path is None:
            repo_root = Path(__file__).resolve().parents[2]
            phase_schema_path = repo_root / "schemas" / "phase_result.schema.json"

        _validate_phase_result(result_dict, phase_schema_path)
        results.append(result_dict)

    bundle = PlanningBundle(
        intent=skeleton.get("intent"),
        scope=skeleton.get("scope"),
        phase_results=results,
        notes="Read-only planning bundle; no geometry",
    )

    if schema_path is None:
        repo_root = Path(__file__).resolve().parents[2]
        schema_path = repo_root / "schemas" / "planning_bundle.schema.json"

    _validate_bundle(bundle, schema_path)
    return bundle
