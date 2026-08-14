# Path: training/contracts.py
# Purpose: Validate training scenarios and recorded transitions at package boundaries.

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator

from training.canonical import scenario_hash
from training.isolation import assert_training_identity
from training.power import POWER_SOURCE_ENTITIES, POWER_STORAGE_ENTITIES

_ROOT = Path(__file__).resolve().parents[1]
_SCENARIO_SCHEMA = _ROOT / "schemas" / "training_scenario.schema.json"
_TRANSITION_SCHEMA = _ROOT / "schemas" / "training_transition.schema.json"
_SCENARIO_VALIDATOR = Draft7Validator(json.loads(_SCENARIO_SCHEMA.read_text(encoding="utf-8")))
_TRANSITION_VALIDATOR = Draft7Validator(json.loads(_TRANSITION_SCHEMA.read_text(encoding="utf-8")))
_MINING_PRODUCTS = frozenset({
    "iron-ore", "copper-ore", "coal", "stone",
    "iron-plate", "copper-plate", "steel-plate",
})
_FIXTURE_ENTITIES = {
    "item_source": "infinity-chest",
    "item_sink": "infinity-chest",
}


def _validate_finite(value: Any, path: str = "<root>") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{path} must contain only finite numbers")
    if isinstance(value, Mapping):
        for key, nested in value.items():
            _validate_finite(nested, f"{path}/{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, nested in enumerate(value):
            _validate_finite(nested, f"{path}/{index}")


def _validate_schema(payload: Mapping, validator: Draft7Validator, label: str) -> None:
    errors = sorted(
        validator.iter_errors(dict(payload)),
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
    if bounds["x_min"] >= bounds["x_max_exclusive"] or bounds["y_min"] >= bounds["y_max_exclusive"]:
        raise ValueError(f"{label} bounds must have positive width and height")


def _validate_scenario_semantics(payload: Mapping) -> None:
    environment = payload["environment"]
    world = environment["bounds"]
    allowed = payload["constraints"]["allowed_build_area"]
    patch = payload["resource_patch"]["bounds"]
    assert_training_identity(environment["surface_name"], environment["force_name"])
    if payload["scenario_hash"] != scenario_hash(payload):
        raise ValueError("scenario_hash does not match canonical scenario content")
    _ordered_bounds(world, "environment")
    _ordered_bounds(allowed, "allowed build area")
    if (
        allowed["x_min"] < world["x_min"] or allowed["y_min"] < world["y_min"]
        or allowed["x_max_exclusive"] > world["x_max_exclusive"]
        or allowed["y_max_exclusive"] > world["y_max_exclusive"]
    ):
        raise ValueError("allowed build area must remain inside the environment")
    if patch["x1"] > patch["x2"] or patch["y1"] > patch["y2"]:
        raise ValueError("resource patch bounds must be ordered")
    if any(not _contains(world, x, y) for x, y in ((patch["x1"], patch["y1"]), (patch["x2"], patch["y2"]))):
        raise ValueError("resource patch must remain inside the environment bounds")
    _validate_fixtures_and_budget(payload, world)


def _validate_fixtures_and_budget(payload: Mapping, world: Mapping) -> None:
    fixtures = {fixture["id"]: fixture for fixture in payload["fixtures"]}
    if len(fixtures) != len(payload["fixtures"]):
        raise ValueError("fixture ids must be unique")
    if any(not _contains(world, *fixture["position"]) for fixture in fixtures.values()):
        raise ValueError("every fixture must remain inside the environment bounds")
    for fixture in fixtures.values():
        kind, entity = fixture["kind"], fixture["entity"]
        if kind == "power_source" and entity not in POWER_SOURCE_ENTITIES:
            raise ValueError("power_source fixture must use an approved electricity producer")
        if kind == "power_storage" and entity not in POWER_STORAGE_ENTITIES:
            raise ValueError("power_storage fixture must use an accumulator")
        if kind in _FIXTURE_ENTITIES and entity != _FIXTURE_ENTITIES[kind]:
            raise ValueError("fixture kind must use its approved instrumentation entity")
    objective = payload["objective"]
    stages = objective.get("stages") or []
    if stages:
        if stages[0]["target_rate_per_tick"] != objective["target_rate_per_tick"]:
            raise ValueError("objective first stage must match target_rate_per_tick")
        seen_stage_ids: set[str] = set()
        for stage in stages:
            if stage["id"] in seen_stage_ids:
                raise ValueError("objective stage ids must be unique")
            seen_stage_ids.add(stage["id"])
            if not stage["destination_fixture_ids"]:
                raise ValueError("objective stages require at least one destination fixture")
            for fixture_id in stage["destination_fixture_ids"]:
                fixture = fixtures.get(fixture_id)
                if fixture is None or fixture["kind"] != "item_sink":
                    raise ValueError("objective stage destination must identify an item_sink fixture")
        if objective["destination_fixture_id"] not in stages[0]["destination_fixture_ids"]:
            raise ValueError("objective destination must be part of the first stage")
    destination = fixtures.get(payload["objective"]["destination_fixture_id"])
    if destination is None or destination["kind"] != "item_sink":
        raise ValueError("objective destination must identify an item_sink fixture")
    if len(fixtures) > 4:
        raise ValueError("mining delivery scenarios permit at most two item sinks")
    stages = payload["objective"].get("stages")
    if stages is not None:
        stage_ids: set[str] = set()
        for index, stage in enumerate(stages):
            stage_id = stage["id"]
            if stage_id in stage_ids:
                raise ValueError("objective stage ids must be unique")
            stage_ids.add(stage_id)
            destinations = stage["destination_fixture_ids"]
            if any(fixtures.get(fixture_id, {}).get("kind") != "item_sink" for fixture_id in destinations):
                raise ValueError(f"objective stage {index} must identify item_sink fixtures")
        first = stages[0]
        if first["target_rate_per_tick"] != payload["objective"]["target_rate_per_tick"]:
            raise ValueError("first objective stage must match target_rate_per_tick")
        if first["sustain_ticks"] != payload["objective"]["sustain_ticks"]:
            raise ValueError("first objective stage must match sustain_ticks")
        if payload["objective"]["destination_fixture_id"] not in first["destination_fixture_ids"]:
            raise ValueError("legacy destination_fixture_id must be in the first objective stage")
    power_sources = [fixture for fixture in fixtures.values() if fixture["kind"] == "power_source"]
    if not power_sources:
        raise ValueError("mining delivery scenarios require a power_source fixture")
    for fixture in power_sources:
        x, y = fixture["position"]
        if not isinstance(x, int) or isinstance(x, bool) or not isinstance(y, int) or isinstance(y, bool):
            raise ValueError("power_source fixture must use an integral centre")
        if not _contains(world, x - 1, y - 1) or not _contains(world, x + 1, y + 1):
            raise ValueError("power_source 2x2 footprint must remain inside the environment bounds")
    storage = [fixture for fixture in fixtures.values() if fixture["kind"] == "power_storage"]
    if len(storage) > 1:
        raise ValueError("mining delivery scenarios permit at most one power_storage fixture")
    if payload["family"] == "mining_delivery":
        if payload["objective"]["item"] != payload["resource_patch"]["resource"]:
            raise ValueError("objective item must match the resource patch")
    else:
        objective = payload["objective"]
        if objective.get("kind") != "smelt_item_rate":
            raise ValueError("furnace_refining objective must smelt_item_rate")
        if objective.get("input_item") != payload["resource_patch"]["resource"]:
            raise ValueError("furnace input_item must match the ore patch")
        if not any(fixture["kind"] == "item_source" for fixture in payload["fixtures"]):
            raise ValueError("furnace_refining requires an item_source fixture")
    budget = set(payload["construction_budget"])
    if budget & _MINING_PRODUCTS:
        raise ValueError("mining delivery construction budget cannot contain production items")
    if set(payload["constraints"]["allowed_entities"]) != budget:
        raise ValueError("allowed entities must exactly match the construction budget")


def validate_scenario(payload: Mapping) -> None:
    """Reject malformed, mutable, or semantically unsafe scenario contracts."""
    _validate_finite(payload)
    _validate_schema(payload, _SCENARIO_VALIDATOR, "TrainingScenario")
    _validate_scenario_semantics(payload)


def _validate_transition_semantics(payload: Mapping) -> None:
    if payload["ended_tick"] < payload["started_tick"]:
        raise ValueError("ended_tick cannot precede started_tick")
    action_ids = [candidate["action_id"] for candidate in payload["candidates"]]
    if len(action_ids) != len(set(action_ids)):
        raise ValueError("candidate action ids must be unique")
    if payload["chosen_action_id"] not in action_ids:
        raise ValueError("chosen_action_id must identify one candidate")
    for action in payload.get("stage_action_trail", []):
        if action["post_execution_tick"] < action["observed_tick"]:
            raise ValueError("stage action post_execution_tick cannot precede observed_tick")
        if action["selected_candidate"]["action_id"] not in action_ids:
            raise ValueError("stage action candidate must identify one candidate")
    reward = payload["reward"]
    computed = sum(float(value) for name, value in reward.items() if name != "total")
    if abs(computed - float(reward["total"])) > 1e-6:
        raise ValueError("reward total must equal its recorded components")
    result = payload["result"]
    if result["status"] == "completed" and result["failure_kind"] != "none":
        raise ValueError("completed transitions cannot record a failure kind")
    if result["status"] != "completed" and result["failure_kind"] == "none":
        raise ValueError("unsuccessful transitions must classify their failure")


def validate_transition(payload: Mapping) -> None:
    """Reject transition records that cannot safely train a future policy."""
    _validate_finite(payload)
    _validate_schema(payload, _TRANSITION_VALIDATOR, "TrainingTransition")
    _validate_transition_semantics(payload)
