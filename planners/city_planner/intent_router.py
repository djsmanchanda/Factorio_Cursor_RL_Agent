# Path: planners/city_planner/intent_router.py
# Purpose: Route validated intents to required planning capabilities.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List


@dataclass(frozen=True)
class PlanningRequest:
    intent: str
    required_capabilities: List[str]
    blocked_by: List[str] = field(default_factory=list)
    notes: str | None = None


_INTENT_CAPABILITIES: Dict[str, List[str]] = {
    "reduce_bot_dependency": ["belt_backbone_planning"],
    "prepare_transport_migration": ["rail_corridor_planning", "station_interface_definition"],
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
    required = list(_INTENT_CAPABILITIES.get(intent_name, []))
    blockers = list(_INTENT_BLOCKERS.get(intent_name, []))
    notes = _INTENT_NOTES.get(intent_name)

    return PlanningRequest(
        intent=intent_name,
        required_capabilities=required,
        blocked_by=blockers,
        notes=notes,
    )


def route_intents(intents: Iterable[dict]) -> List[PlanningRequest]:
    requests = [_to_request(intent) for intent in intents]
    requests.sort(key=lambda request: (request.intent or "", request.required_capabilities))
    return requests
