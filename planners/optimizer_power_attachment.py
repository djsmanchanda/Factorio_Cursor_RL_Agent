# Path: planners/optimizer_power_attachment.py | Purpose: Compose explicitly chosen ghost poles with a bound optimizer artifact.
"""Catalog-backed potential power geometry; no live connection or capacity claim."""

from collections.abc import Mapping, Sequence
import copy
from fractions import Fraction
import json
import math
from pathlib import Path

from jsonschema import Draft7Validator

from core.optimizer_artifact_binding import BoundArtifactPlan, bind_artifact_to_construction_plan
from core.runtime_placement_mapping import mapping_from_runtime_catalog
from planners.ghost_line_artifact import derive_ghost_artifact_from_runtime_catalog
from planners.plan_validation import actions


_CONSUMER_TYPES = frozenset({"assembling-machine", "furnace", "mining-drill", "pump", "inserter",
                             "lab", "beacon", "roboport", "radar", "rocket-silo"})


def _point(position):
    if (not isinstance(position, dict) or set(position) != {"x", "y"}
            or any(type(value) not in (int, float) or abs(value) > 1_000_000 or not math.isfinite(value)
                   for value in position.values())):
        raise ValueError("power geometry requires finite bounded positions")
    return tuple(Fraction(str(position[key])) for key in ("x", "y"))


def _dimensions(prototype, action):
    width, height = prototype.get("tile_width"), prototype.get("tile_height")
    if type(width) is not int or type(height) is not int or not 1 <= width <= 128 or not 1 <= height <= 128:
        raise ValueError("prototype footprint is unavailable")
    direction = action.get("direction", "north")
    if direction not in {"north", "east", "south", "west"}:
        raise ValueError("power geometry requires cardinal directions")
    return (height, width) if direction in {"east", "west"} else (width, height)


def _tiles(prototype, action):
    x, y = _point(action["position"])
    width, height = _dimensions(prototype, action)
    left, top = x - Fraction(width, 2), y - Fraction(height, 2)
    if left.denominator != 1 or top.denominator != 1:
        raise ValueError("prototype placement is not aligned to its tile footprint")
    return {(int(left) + dx, int(top) + dy) for dx in range(width) for dy in range(height)}


def _pole(prototype, action):
    values = prototype.get("electric_pole")
    if prototype.get("type") != "electric-pole" or not isinstance(values, dict) or values.get("quality") != "normal":
        raise ValueError("normal-quality pole reach is unavailable")
    reach, supply = values.get("max_wire_distance"), values.get("supply_area_distance")
    if any(type(value) not in (int, float) or abs(value) > 1_000_000 or not math.isfinite(value) or value < 0
           for value in (reach, supply)):
        raise ValueError("pole reach is invalid")
    return {"action": action, "position": _point(action["position"]),
            "wire": Fraction(str(reach)), "supply": Fraction(str(supply))}


def compose_optimizer_power_attachment(bound, *, placement_capture, anchor, pole_actions):
    """Attach caller-selected pole ghosts to a bound artifact and rebuild its bill.

    ``anchor`` identifies an existing pole asserted by the caller. All prototype
    ranges and footprints come from the pinned catalog. The resulting graph
    only establishes that wiring and coverage are geometrically possible; it
    does not establish engine auto-wiring, network power, capacity or ownership.
    """
    if not isinstance(bound, BoundArtifactPlan):
        raise ValueError("bound optimizer artifact is required")
    plan, binding = bound.plan, bound.plan.binding
    rebound = bind_artifact_to_construction_plan(
        bound.artifact, scope=binding.scope, project_id=binding.project_id, transaction_id=binding.transaction_id,
        throughput_output=plan.throughput_output, throughput_target=plan.throughput_target,
        throughput_unit=plan.throughput_unit, expected_artifact_sha256=bound.artifact_sha256,
        expected_plan_hash=bound.binding_sha256)
    if rebound.plan != plan or rebound.canonical_binding != bound.canonical_binding:
        raise ValueError("optimizer artifact binding is inconsistent")
    if (not isinstance(placement_capture, Mapping)
            or set(placement_capture) != {"catalog_bytes", "expected_sha256", "surface", "force", "active_mods"}
            or (placement_capture.get("surface"), placement_capture.get("force")) != (binding.scope.surface, binding.scope.force)
            or not isinstance(anchor, dict) or set(anchor) != {"entity", "position", "unit_number"}
            or not isinstance(anchor["entity"], str) or not anchor["entity"]
            or type(anchor["unit_number"]) is not int or anchor["unit_number"] <= 0
            or not isinstance(pole_actions, Sequence) or isinstance(pole_actions, (str, bytes))
            or len(pole_actions) > 4096):
        raise ValueError("capture, anchor and explicit pole actions are required")
    _point(anchor["position"])
    new = copy.deepcopy(list(pole_actions))
    if any(not isinstance(action, dict) or set(action) != {"action_type", "entity", "position"}
           or action.get("action_type") != "place_ghost"
           or not isinstance(action.get("entity"), str) or not action["entity"] for action in new):
        raise ValueError("power additions must be plain pole ghost actions")
    original = list(actions(bound.artifact["plan"]))
    if any(action.get("action_type") != "place_ghost" for action in original):
        raise ValueError("power attachment requires entity ghosts only")
    names = {action["entity"] for action in original + new} | {anchor["entity"]}
    mapping = mapping_from_runtime_catalog(placement_capture["catalog_bytes"],
        expected_sha256=placement_capture["expected_sha256"], entity_names=names,
        surface=placement_capture["surface"], force=placement_capture["force"], active_mods=placement_capture["active_mods"])
    old_mapping = bound.artifact["item_mapping"]["entities"]
    if any(old_mapping.get(action["entity"]) != mapping["entities"][action["entity"]] for action in original):
        raise ValueError("runtime placement mapping differs from the bound artifact")
    report = json.loads(placement_capture["catalog_bytes"])
    schema = json.loads((Path(__file__).resolve().parents[1] / "schemas/runtime_catalog.schema.json").read_text())
    if not Draft7Validator(schema).is_valid(report) or report["force_state"]["name"] != binding.scope.force:
        raise ValueError("runtime catalog does not match its schema")
    prototypes = {item["name"]: item for item in report["entities"]}
    occupied = set()
    pole_tiles = set()
    planned_poles, powered = [], []
    for action in original:
        prototype = prototypes[action["entity"]]
        tiles = _tiles(prototype, action)
        if prototype["type"] == "electric-pole":
            if occupied & tiles:
                raise ValueError("planned pole overlaps another planned entity")
            pole_tiles.update(tiles)
            planned_poles.append(_pole(prototype, action))
        elif pole_tiles & tiles:
            raise ValueError("planned entity overlaps a planned pole")
        elif prototype["type"] in _CONSUMER_TYPES:
            energy = prototype.get("energy")
            if not isinstance(energy, dict) or not isinstance(energy.get("source"), str):
                raise ValueError("consumer energy source is unavailable")
            if energy["source"] == "electric":
                powered.append((prototype, action))
        occupied.update(tiles)
    for action in new:
        prototype = prototypes[action["entity"]]
        pole = _pole(prototype, action)
        tiles = _tiles(prototype, action)
        if occupied & tiles:
            raise ValueError("added pole overlaps the bound plan or another added pole")
        occupied.update(tiles)
        planned_poles.append(pole)
    anchor_action = {"entity": anchor["entity"], "position": anchor["position"]}
    root = _pole(prototypes[anchor["entity"]], anchor_action)
    if occupied & _tiles(prototypes[anchor["entity"]], anchor_action):
        raise ValueError("existing anchor overlaps a planned entity")
    if not planned_poles or not powered:
        raise ValueError("planned poles and electric consumers are required")
    connected = [root]
    remaining = list(planned_poles)
    while remaining:
        reached = []
        for pole in remaining:
            x, y = pole["position"]
            if any((x - other["position"][0]) ** 2 + (y - other["position"][1]) ** 2
                   <= min(pole["wire"], other["wire"]) ** 2 for other in connected):
                reached.append(pole)
        if not reached:
            raise ValueError("planned pole graph cannot reach the chosen anchor")
        connected.extend(reached)
        remaining = [pole for pole in remaining if pole not in reached]
    uncovered = []
    for prototype, action in powered:
        x, y = _point(action["position"])
        # The center-inside test is a conservative sufficient geometry check;
        # a center outside can still have an overlapping footprint, so report it
        # as unverified rather than rejecting a potentially valid candidate.
        if not any(abs(x - pole["position"][0]) < pole["supply"]
                   and abs(y - pole["position"][1]) < pole["supply"] for pole in connected):
            uncovered.append({"entity": action["entity"], "position": copy.deepcopy(action["position"])})
    combined = {"phases": copy.deepcopy(bound.artifact["plan"]["phases"])}
    if new:
        combined["phases"].append({"name": "optimizer-power-attachment", "actions": new})
    artifact = derive_ghost_artifact_from_runtime_catalog(combined, placement_capture=placement_capture)
    context = copy.deepcopy(bound.artifact.get("planning_context"))
    if not isinstance(context, dict) or not isinstance(context.get("data"), dict):
        raise ValueError("optimizer artifact requires a planning context")
    if "power_attachment" in context["data"]:
        raise ValueError("artifact already has a power attachment")
    context["data"]["power_attachment"] = {"version": "optimizer-power-attachment-v1",
        "anchor": copy.deepcopy(anchor), "added_poles": new, "catalog_sha256": placement_capture["expected_sha256"].lower(),
        "input_artifact_sha256": bound.artifact_sha256, "input_plan_sha256": bound.binding_sha256,
        "planned_pole_count": len(planned_poles), "electric_consumer_count": len(powered),
        "center_coverage_unverified": uncovered}
    artifact["planning_context"] = context
    artifact["pending_checks"] = list(dict.fromkeys(bound.artifact["pending_checks"] + ["power_attachment"]))
    artifact["unknowns"].update(copy.deepcopy(bound.artifact["unknowns"]))
    artifact["unknowns"]["power_attachment"] = (
        "Anchor, automatic wiring, current network generation, capacity and power delivery are unverified.")
    return {"artifact": artifact, "dispatch_authorized": False,
            "potential_pole_connections": len(planned_poles),
            "center_covered_electric_consumers": len(powered) - len(uncovered),
            "center_coverage_unverified": uncovered,
            "non_claims": ["catalog and anchor are caller assertions", "potential reach does not prove automatic wires",
                           "no current network generation, available power, bot coverage or runtime authority is established"]}
