# Path: training/contracts.py
# Purpose: Validate training scenarios and recorded transitions at package boundaries.

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from jsonschema import Draft7Validator

_ROOT = Path(__file__).resolve().parents[1]
_SCENARIO_SCHEMA = _ROOT / "schemas" / "training_scenario.schema.json"
_TRANSITION_SCHEMA = _ROOT / "schemas" / "training_transition.schema.json"
_MINING_PRODUCTS = frozenset({
    "iron-ore", "copper-ore", "coal", "stone",
    "iron-plate", "copper-plate", "steel-plate",
})
_FIXTURE_ENTITIES = {
    "power_source": "electric-energy-interface",
    "item_sink": "infinity-chest",
}


def _validate_schema(payload: Mapping, schema_path: Path, label: str) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft7Validator(schema).iter_errors(dict(payload)),
        key=lambda error: list(error.path),
    )
    if not errors:
        return
    detail = "; ".join(
        f"{'/'.join(map(str, error.path)) or '<root>'}: {error.message}"
        for error in errors
    )
    raise ValueError(f"{label} validation failed: {detail}")


def _contains(bounds: Mapping, x: float, y: float) -> bool:
    return (
        bounds["x_min"] <= x < bounds["x_max_exclusive"]
        and bounds["y_min"] <= y < bounds["y_max_exclusive"]
    )


def _ordered_bounds(bounds: Mapping, label: str) -> None:
    if (
        bounds["x_min"] >= bounds["x_max_exclusive"]
        or bounds["y_min"] >= bounds["y_max_exclusive"]
    ):
        raise ValueError(f"{label} bounds must have positive width and height")


def _validate_scenario_semantics(payload: Mapping) -> None:
    environment = payload["environment"]
    world = environment["bounds"]
    allowed = payload["constraints"]["allowed_build_area"]
    patch = payload["resource_patch"]["bounds"]
    _ordered_bounds(world, "environment")
    _ordered_bounds(allowed, "allowed build area")
    if (
        allowed["x_min"] < world["x_min"]
        or allowed["y_min"] < world["y_min"]
        or allowed["x_max_exclusive"] > world["x_max_exclusive"]
        or allowed["y_max_exclusive"] > world["y_max_exclusive"]
    ):
        raise ValueError("allowed build area must remain inside the environment")
    if patch["x1"] > patch["x2"] or patch["y1"] > patch["y2"]:
        raise ValueError("resource patch bounds must be ordered")
    corners = ((patch["x1"], patch["y1"]), (patch["x2"], patch["y2"]))
    if any(not _contains(world, x, y) for x, y in corners):
        raise ValueError("resource patch must remain inside the environment bounds")
    fixtures = {fixture["id"]: fixture for fixture in payload["fixtures"]}
    if len(fixtures) != len(payload["fixtures"]):
        raise ValueError("fixture ids must be unique")
    if any(not _contains(world, *fixture["position"]) for fixture in fixtures.values()):
        raise ValueError("every fixture must remain inside the environment bounds")
    if any(
        fixture["entity"] != _FIXTURE_ENTITIES[fixture["kind"]]
        for fixture in fixtures.values()
    ):
        raise ValueError("fixture kind must use its approved instrumentation entity")
    destination = fixtures.get(payload["objective"]["destination_fixture_id"])
    if destination is None or destination["kind"] != "item_sink":
        raise ValueError("objective destination must identify an item_sink fixture")
    if not any(fixture["kind"] == "power_source" for fixture in fixtures.values()):
        raise ValueError("mining delivery scenarios require a power_source fixture")
    if payload["objective"]["item"] != payload["resource_patch"]["resource"]:
        raise ValueError("objective item must match the resource patch")
    budget = set(payload["construction_budget"])
    if budget & _MINING_PRODUCTS:
        raise ValueError("mining delivery construction budget cannot contain production items")
    if set(payload["constraints"]["allowed_entities"]) != budget:
        raise ValueError("allowed entities must exactly match the construction budget")


def validate_scenario(payload: Mapping) -> None:
    """Reject malformed or semantically unsafe training scenario contracts."""
    _validate_schema(payload, _SCENARIO_SCHEMA, "TrainingScenario")
    _validate_scenario_semantics(payload)


def _validate_transition_semantics(payload: Mapping) -> None:
    if payload["ended_tick"] < payload["started_tick"]:
        raise ValueError("ended_tick cannot precede started_tick")
    action_ids = [candidate["action_id"] for candidate in payload["candidates"]]
    if len(action_ids) != len(set(action_ids)):
        raise ValueError("candidate action ids must be unique")
    if payload["chosen_action_id"] not in action_ids:
        raise ValueError("chosen_action_id must identify one candidate")
    reward = payload["reward"]
    computed = sum(
        float(value) for name, value in reward.items() if name != "total"
    )
    if abs(computed - float(reward["total"])) > 1e-6:
        raise ValueError("reward total must equal its recorded components")
    result = payload["result"]
    if result["status"] == "completed" and result["failure_kind"] != "none":
        raise ValueError("completed transitions cannot record a failure kind")
    if result["status"] != "completed" and result["failure_kind"] == "none":
        raise ValueError("unsuccessful transitions must classify their failure")


def validate_transition(payload: Mapping) -> None:
    """Reject transition records that cannot safely train a future policy."""
    _validate_schema(payload, _TRANSITION_SCHEMA, "TrainingTransition")
    _validate_transition_semantics(payload)
