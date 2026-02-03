# Path: planners/city_planner/intent_router.py
# Purpose: Route validated intents to required planning capabilities.

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List

from jsonschema import Draft7Validator


@dataclass(frozen=True)
class PlanningRequest:
    intent: str
    required_capabilities: List[str]
    blocked_by: List[str] = field(default_factory=list)
    notes: str | None = None
    scope: str | None = None

    def to_dict(self) -> dict:
        payload = {
            "intent": self.intent,
            "scope": self.scope,
            "required_capabilities": list(self.required_capabilities),
            "blocked_by": list(self.blocked_by),
        }
        if self.notes is not None:
            payload["notes"] = self.notes
        return payload


_INTENT_CAPABILITIES: Dict[str, List[str]] = {
    "reduce_bot_dependency": ["belt_backbone_planning"],
    "prepare_transport_migration": [
        "rail_corridor_planning",
        "station_interface_definition",
        "block_planning",
        "dependency_graph_planning",
    ],
    "increase_transport_capacity": ["rail_corridor_planning", "station_interface_definition"],
    "stabilize_power_margin": ["power_block_planning"],
    "prepare_upgrade_phase": ["upgrade_phase_planning"],
    "delay_expansion": ["expansion_governance"],
    "increase_buffering": ["buffer_block_planning"],
    "rebalance_supply_chain": ["flow_balancing_planning"],
    "investigate_anomaly": ["anomaly_investigation"],
}


_INTENT_BLOCKERS: Dict[str, List[str]] = {
    "prepare_transport_migration": ["no_city_grid"],
    "increase_transport_capacity": ["no_city_grid"],
}


_INTENT_NOTES: Dict[str, str] = {
    "prepare_transport_migration": "Intent requires city-level rail infrastructure",
    "reduce_bot_dependency": "Local belt-based transport may be sufficient",
}


def _to_request(intent: dict) -> PlanningRequest:
    intent_name = intent.get("intent")
    scope = intent.get("scope")
    if intent_name not in _INTENT_CAPABILITIES:
        raise ValueError(f"Unknown intent: {intent_name}")

    required = list(_INTENT_CAPABILITIES.get(intent_name, []))
    blockers = list(_INTENT_BLOCKERS.get(intent_name, []))
    notes = _INTENT_NOTES.get(intent_name)

    return PlanningRequest(
        intent=intent_name,
        required_capabilities=required,
        scope=scope,
        blocked_by=blockers,
        notes=notes,
    )


def _load_schema(schema_path: Path) -> dict:
    with schema_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _validate_request(request: PlanningRequest, schema_path: Path) -> None:
    schema = _load_schema(schema_path)
    validator = Draft7Validator(schema)
    errors = list(validator.iter_errors(request.to_dict()))
    if errors:
        messages = []
        for error in errors:
            path = "/".join(str(part) for part in error.path) if error.path else "<root>"
            messages.append(f"- {path}: {error.message}")
        raise ValueError("Planning request validation FAILED:\n" + "\n".join(messages))


def route_intents(intents: Iterable[dict], schema_path: Path | None = None) -> List[PlanningRequest]:
    requests = [_to_request(intent) for intent in intents]
    if schema_path is None:
        repo_root = Path(__file__).resolve().parents[2]
        schema_path = repo_root / "schemas" / "planning_request.schema.json"

    for request in requests:
        _validate_request(request, schema_path)

    requests.sort(key=lambda request: (request.intent or "", request.required_capabilities))
    return requests
