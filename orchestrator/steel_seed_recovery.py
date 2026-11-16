# Path: orchestrator/steel_seed_recovery.py | Purpose: Refeed an exhausted owned steel seed without replacing production or emptying providers.
from __future__ import annotations

import hashlib
import json
import math
import os

from orchestrator import live_base, operation_facts
from orchestrator.stage_services import StuckError
from orchestrator.stage_transport import _plan_belt_transport, _through_belt_source
from orchestrator.work_state import WorkStateSignal
from planners.belt_bridge import DIRECTION_VECTORS
from planners.plan_validation import entity_footprint_tiles, validate_build_plan, validate_no_collisions
from planners.steel_bootstrap import (
    mined_seed_interfaces, steel_seed_intakes, steel_seed_source_taps, steel_seed_through_handoff,
)


class SteelSeedRecoveryWait(RuntimeError):
    """A missing or conflicting fact cannot authorize source recovery."""


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _seed_hash(plan):
    return _hash({key: value for key, value in plan.items() if key != "source_recovery"})


def save_seed(path, plan):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(plan, sort_keys=True, allow_nan=False))
    os.replace(temporary, path)


def _units(health):
    units = [row.get("unit") for row in health["entities"]]
    if any(type(unit) is not int or unit <= 0 for unit in units) or len(set(units)) != len(units):
        raise SteelSeedRecoveryWait("steel seed identities are unknown")
    return units


def _row(health, action):
    return next(row for row in health["entities"] if row["expected"] == {
        "name": action["entity"], "position": action["position"]})


def _count(row, key, item):
    if row.get("reads", {}).get(key) != "value":
        raise SteelSeedRecoveryWait(f"steel seed {key} read is unknown")
    contents = row[key]
    if contents == {}:
        contents = []  # Factorio serializes an empty native table as {}.
    if not isinstance(contents, list) or any(
        not isinstance(stack, dict) or not isinstance(stack.get("name"), str)
        or type(stack.get("count")) is not int or stack["count"] < 0 for stack in contents
    ):
        raise SteelSeedRecoveryWait(f"steel seed {key} is malformed")
    return sum(stack["count"] for stack in contents if stack["name"] == item)


def _baseline(health, interfaces):
    furnace = _row(health, interfaces["furnace"])
    if (furnace.get("reads", {}).get("lifetime_crafts") != "value"
            or type(furnace.get("lifetime_crafts")) is not int or furnace["lifetime_crafts"] < 0):
        raise SteelSeedRecoveryWait("steel seed craft counter is unknown")
    return {"tick": health["tick"], "crafts": furnace["lifetime_crafts"],
            "provider_steel": _count(_row(health, interfaces["provider"]), "contents", "steel-plate")}


def _valid_sample(value):
    return (isinstance(value, dict) and set(value) == {"tick", "crafts", "provider_steel"}
            and all(type(count) is int and count >= 0 for count in value.values()))


def _source_health(client, surface, force, action, *, expected_unit=None, allow_empty=False):
    health = live_base.plan_entity_health(client, surface, force,
        {"phases": [{"name": "iron_source_identity", "actions": [action]}]})
    row = health["entities"][0]
    if not row["found"] or row.get("type") != "transport-belt":
        raise SteelSeedRecoveryWait("iron output terminal is missing")
    unit = _units(health)[0]
    if expected_unit is not None and unit != expected_unit:
        raise SteelSeedRecoveryWait("iron output terminal identity changed")
    cardinal = {0: "north", 4: "east", 8: "south", 12: "west"}.get(row.get("direction"))
    if cardinal != action["direction"]:
        raise SteelSeedRecoveryWait("iron output terminal heading changed")
    iron = _count(row, "belt_contents", "iron-plate")
    total = sum(stack["count"] for stack in row["belt_contents"]) if row["belt_contents"] != {} else 0
    if total != iron or (not allow_empty and not iron):
        raise SteelSeedRecoveryWait("iron output terminal has no verified pure iron supply")
    return unit


def _terminal(client, surface, force, provider):
    tail = _through_belt_source(client, surface, "iron-plate", provider)
    for _ in range(64):
        if tail is None:
            break
        entity = live_base.entity_at(client, surface, tail)
        if not entity or entity.get("type") != "transport-belt" or entity.get("force") != force:
            break
        direction = live_base.transport_belt_direction_at(client, surface, tail)
        if direction not in DIRECTION_VECTORS:
            break
        dx, dy = DIRECTION_VECTORS[direction]
        following = (tail[0] + dx, tail[1] + dy)
        next_entity = live_base.entity_at(client, surface, following)
        if next_entity is None:
            action = {"action_type": "place_ghost", "entity": entity["name"],
                      "position": {"x": tail[0], "y": tail[1]}, "direction": direction}
            unit = _source_health(client, surface, force, action)
            return tail, action, unit
        tail = following
    raise SteelSeedRecoveryWait("steel seed waits for an unused verified iron-output terminal")


def prepare_seed_refeed(client, surface, force, plan, provider, *, reserved_tiles=()):
    """Observe viability, then return one durable additive transport transaction.

    Ownership comes from the episode seed plan; native unit identities bind a
    persisted repair to those same entities on retries. No historical recipe
    fallback, new furnace, requester, provider, removal or gate change is used.
    """
    interfaces = mined_seed_interfaces(plan)
    if interfaces is None:
        return None
    if plan.get("surface") != surface or plan.get("force") != force:
        raise SteelSeedRecoveryWait("steel seed plan scope is unknown")
    health = live_base.plan_entity_health(client, surface, force, plan)
    if not all(row["found"] for row in health["entities"]):
        if "source_recovery" in plan:
            raise SteelSeedRecoveryWait("owned steel seed recovery target disappeared")
        return None  # Original, unfinished construction still owns this pass.
    units = _units(health)
    drill = _row(health, interfaces["drill"])
    reads = drill["reads"]
    resources = drill.get("resources_in_mining_area")
    if (reads.get("resources_in_mining_area") != "value" or reads.get("mining_area") != "value"
            or not isinstance(resources, list) and resources != {}):
        raise SteelSeedRecoveryWait("steel seed mining-area census is unknown")
    exhausted = (drill["status"] == "no_minable_resources" and resources in ([], {})
                 and reads.get("mining_target") == "absent")
    operation_facts.record("steel_seed_source_viability", tick=health["tick"], surface=surface,
                          force=force, units=units, state="exhausted" if exhausted else "not_exhausted",
                          observation=health)
    record = plan.get("source_recovery")
    if record is None and not exhausted:
        return None
    for action, recipe in ((interfaces["furnace"], "steel-plate"),):
        row = _row(health, action)
        if (row["reads"].get("recipe") not in ("value", "absent")
                or row.get("recipe") not in (None, recipe)):
            raise SteelSeedRecoveryWait("owned steel furnace recipe is unknown or changed")
    if record is not None:
        if (not isinstance(record, dict) or type(record.get("version")) is not int or record["version"] != 1
                or record.get("seed_sha256") != _seed_hash(plan) or record.get("units") != units
                or record.get("plan_sha256") != _hash(record.get("plan"))
                or type(record.get("source_unit")) is not int or record["source_unit"] <= 0
                or type(record.get("delivery_observed")) is not bool
                or not _valid_sample(record.get("baseline"))
                or "submission" not in record or "built_baseline" not in record
                or any(record[key] is not None and not _valid_sample(record[key]) for key in ("submission", "built_baseline"))
                or (record["built_baseline"] is not None and record["submission"] is None)
                or (record["delivery_observed"] and record["built_baseline"] is None)):
            raise SteelSeedRecoveryWait("steel seed recovery ownership or plan changed")
        repair = record["plan"]
        validate_build_plan(repair)
        validate_no_collisions([("seed", plan), ("refeed", repair)])
        if (repair.get("surface") != surface or repair.get("force") != force
                or any(a.get("action_type") != "place_ghost" or a.get("entity") not in {
                    "inserter", "transport-belt", "underground-belt"}
                    for phase in repair["phases"] for a in phase["actions"])):
            raise SteelSeedRecoveryWait("steel seed recovery must remain additive in its owned scope")
        _source_health(client, surface, force, record["source"],
                       expected_unit=record["source_unit"], allow_empty=True)
        return record
    tail, source, source_unit = _terminal(client, surface, force, provider)
    centre = interfaces["furnace"]["position"]
    blocked = set(reserved_tiles)
    for phase in plan["phases"]:
        for action in phase["actions"]:
            blocked.update(entity_footprint_tiles(action))
    blocked.update(steel_seed_through_handoff(tail, source["direction"]))
    nearby = live_base.occupied_tiles(client, surface,
        (min(centre["x"], tail[0]) - 5, min(centre["y"], tail[1]) - 5),
        (max(centre["x"], tail[0]) + 5, max(centre["y"], tail[1]) + 5)) | blocked
    candidates = []
    for tap, tap_heading, tap_actions in steel_seed_source_taps(tail, source["direction"]):
        tap_tiles = {tile for action in tap_actions for tile in entity_footprint_tiles(action)}
        if tap_tiles & nearby:
            continue
        for endpoint, heading, inserter in steel_seed_intakes(plan):
            endpoint_tile = (math.floor(endpoint[0]), math.floor(endpoint[1]))
            if (entity_footprint_tiles(inserter) | {endpoint_tile}) & (nearby | tap_tiles):
                continue
            try:
                actions, _, _ = _plan_belt_transport(client, surface, force, "iron-plate", tap, endpoint,
                    reuse_existing=False, max_belt_route_tiles=256,
                    additional_blocked=blocked | tap_tiles | set(entity_footprint_tiles(inserter)),
                    destination_is_belt=True, destination_belt_direction=heading,
                    planned_belt_source=tap, through_flow_direction=tap_heading,
                    required_belt_type="transport-belt", defer_required_tier_affordability=True)
                # The shared belt-to-belt bridge joins existing endpoints.
                # Both endpoints here are new: explicitly own and price them.
                pickup = {"action_type": "place_ghost", "entity": "transport-belt",
                          "position": {"x": endpoint[0], "y": endpoint[1]}, "direction": heading}
                actions = [a for a in actions if a.get("position") not in (tap_actions[1]["position"], pickup["position"])]
                repair = {"surface": surface, "force": force, "phases": [{"name": "steel_seed_iron_refeed",
                    "actions": [*tap_actions, *actions, pickup, inserter]}]}
                if any(a.get("action_type") != "place_ghost" for a in actions):
                    raise ValueError("seed recovery must be additive")
                validate_build_plan(repair)
                validate_no_collisions([("seed", plan), ("refeed", repair)])
                candidates.append(repair)
            except (StuckError, ValueError):
                continue
            except WorkStateSignal as error:
                if error.code != "direct_belt_approach_wait":
                    raise
    if not candidates:
        raise SteelSeedRecoveryWait("steel seed has no legal powered belt intake from its iron source")
    repair = min(candidates, key=lambda p: len(p["phases"][0]["actions"]))
    record = {"version": 1, "seed_sha256": _seed_hash(plan), "units": units,
              "source": source, "source_unit": source_unit, "plan": repair,
              "plan_sha256": _hash(repair), "baseline": _baseline(health, interfaces),
              "submission": None, "built_baseline": None, "delivery_observed": False}
    plan["source_recovery"] = record
    operation_facts.record("steel_seed_refeed_plan", **record)
    return record


def update_seed_refeed(plan, health):
    record = plan.get("source_recovery")
    if record is None:
        return
    if _units(health) != record["units"]:
        raise SteelSeedRecoveryWait("steel seed recovery identities changed")
    observed = _baseline(health, mined_seed_interfaces(plan))
    baseline = record.get("built_baseline")
    if record.get("submission") is None or baseline is None:
        operation_facts.record("steel_seed_refeed_progress", state="awaiting_submission_or_construction",
                              observed=observed, delivery_observed=False)
        return
    if observed["tick"] < baseline["tick"] or observed["crafts"] < baseline["crafts"]:
        raise SteelSeedRecoveryWait("steel seed recovery clock or craft counter reset")
    delivered = observed["crafts"] > baseline["crafts"] and observed["provider_steel"] > baseline["provider_steel"]
    record["latest"] = observed
    record["delivery_observed"] = record["delivery_observed"] or delivered
    operation_facts.record("steel_seed_refeed_progress", baseline=baseline, observed=observed,
                          delivery_observed=record["delivery_observed"], units=record["units"])


def note_seed_refeed_submission(client, surface, force, plan):
    record = plan["source_recovery"]
    if record["submission"] is None:
        health = live_base.plan_entity_health(client, surface, force, plan)
        if _units(health) != record["units"]:
            raise SteelSeedRecoveryWait("steel seed submission identities changed")
        record["submission"] = _baseline(health, mined_seed_interfaces(plan))
        operation_facts.record("steel_seed_refeed_submission", observed=record["submission"],
                              plan_sha256=record["plan_sha256"])


def observe_seed_refeed(client, surface, force, plan):
    record = plan["source_recovery"]
    if record["submission"] is None:
        return
    status = live_base.plan_construction_status(client, surface, force, record["plan"])
    operation_facts.record("steel_seed_refeed_construction", status=status, plan_sha256=record["plan_sha256"])
    if not status["required"] or status["remaining"] or status["installed"] != status["required"]:
        return
    health = live_base.plan_entity_health(client, surface, force, plan)
    if _units(health) != record["units"]:
        raise SteelSeedRecoveryWait("steel seed construction identities changed")
    observed = _baseline(health, mined_seed_interfaces(plan))
    if (observed["tick"] < record["submission"]["tick"]
            or observed["crafts"] < record["submission"]["crafts"]):
        raise SteelSeedRecoveryWait("steel seed recovery clock or craft counter reset after submission")
    if record["built_baseline"] is None:
        record["built_baseline"] = observed
        operation_facts.record("steel_seed_refeed_built_baseline", observed=record["built_baseline"])
    update_seed_refeed(plan, health)
