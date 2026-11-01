# Path: tools/optimizer_power_topology.py | Purpose: Compare a bound optimizer power anchor with a same-query pole snapshot.
"""Observation-only checks; potential reach does not establish live wiring or power."""

from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path

from jsonschema import Draft7Validator

from core.optimizer_artifact_binding import BoundArtifactPlan, bind_artifact_to_construction_plan
from core.runtime_placement_mapping import mapping_from_runtime_catalog
from planners.plan_validation import actions

ROOT = Path(__file__).resolve().parents[1]


def _position(value):
    if (not isinstance(value, dict) or set(value) != {"x", "y"}
            or any(type(number) not in (int, float) or abs(number) > 1_000_000 or not math.isfinite(number)
                   for number in value.values())):
        raise ValueError("invalid pole position")
    return tuple(Fraction(str(value[axis])) for axis in ("x", "y"))


def _distance(value):
    if type(value) not in (int, float) or abs(value) > 1_000_000 or not math.isfinite(value) or value < 0:
        raise ValueError("invalid pole distance")
    return Fraction(str(value))


def diagnose_bound_power(bound, *, catalog_bytes, catalog_sha256, snapshot, reference_tick):
    """Check pinned anchor identity and potential links in a caller-supplied snapshot.

    A consistent result is not evidence of actual wires, working generation,
    capacity, construction ownership, authenticated runtime or permission to dispatch.
    """
    if not isinstance(bound, BoundArtifactPlan) or not isinstance(catalog_bytes, bytes):
        raise ValueError("bound artifact and catalog bytes are required")
    if not isinstance(catalog_sha256, str) or hashlib.sha256(catalog_bytes).hexdigest() != catalog_sha256:
        raise ValueError("power catalog hash mismatch")
    plan, binding = bound.plan, bound.plan.binding
    rebound = bind_artifact_to_construction_plan(bound.artifact, scope=binding.scope,
        project_id=binding.project_id, transaction_id=binding.transaction_id,
        throughput_output=plan.throughput_output, throughput_target=plan.throughput_target,
        throughput_unit=plan.throughput_unit, expected_artifact_sha256=bound.artifact_sha256,
        expected_plan_hash=bound.binding_sha256)
    if rebound.plan != plan or rebound.canonical_binding != bound.canonical_binding:
        raise ValueError("power artifact binding is inconsistent")
    context = bound.artifact.get("planning_context", {}).get("data", {}).get("power_attachment")
    if (not isinstance(context, dict) or context.get("version") != "optimizer-power-attachment-v1"
            or context.get("catalog_sha256") != catalog_sha256 or not isinstance(context.get("anchor"), dict)):
        raise ValueError("bound artifact lacks the pinned power attachment")
    anchor = context["anchor"]
    if (set(anchor) != {"entity", "position", "unit_number"} or not isinstance(anchor["entity"], str)
            or not anchor["entity"] or type(anchor["unit_number"]) is not int or anchor["unit_number"] <= 0):
        raise ValueError("bound power anchor is malformed")
    expected_position = _position(anchor["position"])
    result = {"status": "unverified", "binding": binding.to_dict(), "catalog_sha256": catalog_sha256,
              "snapshot_tick": None, "anchor_grid": None, "generator_type_present": None,
              "planned_poles": 0, "same_grid_candidates": 0, "potential_other_grid_links": [],
              "reasons": [], "dispatch_authorized": False,
              "non_claims": ["catalog, snapshot and runtime origin are not authenticated",
                             "only same-force built poles are scanned; ghosts and other forces are not evaluated",
                             "potential wire reach does not prove that the engine will create a connection",
                             "generator-type presence is not measured generation, available capacity or delivered power",
                             "planned wiring, coverage, bot construction and commissioning remain unverified"]}
    def unavailable(reason):
        result["reasons"].append(reason)
        return result
    if (type(reference_tick) is not int or reference_tick < 0 or not isinstance(snapshot, dict)
            or set(snapshot) != {"version", "tick", "surface", "force", "mods", "complete", "poles"}
            or snapshot.get("version") != "optimizer-power-snapshot-v1"
            or type(snapshot.get("tick")) is not int or snapshot["tick"] < 0
            or (snapshot.get("surface"), snapshot.get("force")) != (binding.scope.surface, binding.scope.force)
            or type(snapshot.get("complete")) is not bool or not isinstance(snapshot.get("mods"), dict)):
        return unavailable("snapshot_invalid")
    result["snapshot_tick"] = snapshot["tick"]
    if snapshot["tick"] != reference_tick:
        return unavailable("snapshot_tick_mismatch")
    if not snapshot["complete"]:
        return unavailable("snapshot_incomplete")
    poles = [] if snapshot["poles"] == {} else snapshot["poles"]
    if not isinstance(poles, list) or len(poles) > 5000:
        return unavailable("snapshot_poles_invalid")
    names = {action["entity"] for action in actions(bound.artifact["plan"])} | {anchor["entity"]}
    mapping_from_runtime_catalog(catalog_bytes, expected_sha256=catalog_sha256, entity_names=names,
        surface=binding.scope.surface, force=binding.scope.force, active_mods=snapshot["mods"])
    catalog = json.loads(catalog_bytes)
    schema = json.loads((ROOT / "schemas/runtime_catalog.schema.json").read_text())
    if (not Draft7Validator(schema).is_valid(catalog)
            or catalog["force_state"]["name"] != binding.scope.force):
        raise ValueError("power catalog schema or force mismatch")
    prototypes = {entity["name"]: entity for entity in catalog["entities"]}
    anchor_prototype = prototypes[anchor["entity"]]
    spec = anchor_prototype.get("electric_pole")
    if anchor_prototype["type"] != "electric-pole" or not isinstance(spec, dict) or spec.get("quality") != "normal":
        return unavailable("anchor_prototype_unavailable")
    try:
        expected_wire, expected_supply = _distance(spec["max_wire_distance"]), _distance(spec["supply_area_distance"])
        planned = []
        for action in actions(bound.artifact["plan"]):
            proto = prototypes[action["entity"]]
            if proto["type"] == "electric-pole":
                pole_spec = proto.get("electric_pole")
                if not isinstance(pole_spec, dict) or pole_spec.get("quality") != "normal":
                    return unavailable("planned_pole_prototype_unavailable")
                planned.append((_position(action["position"]), _distance(pole_spec["max_wire_distance"])))
        result["planned_poles"] = len(planned)
        if not planned:
            return unavailable("planned_poles_unavailable")
        if len(planned) * len(poles) > 2_000_000:
            return unavailable("comparison_limit_exceeded")
        normalized = []
        units = set()
        for row in poles:
            if (not isinstance(row, dict) or not isinstance(row.get("name"), str) or not row["name"]
                    or not isinstance(row.get("grid"), str) or not row["grid"]
                    or type(row.get("generated")) is not bool):
                return unavailable("snapshot_poles_invalid")
            pos, wire, supply = _position(row.get("position")), _distance(row.get("wire")), _distance(row.get("supply"))
            unit = row.get("unit_number")
            if unit is not None:
                if type(unit) is not int or unit <= 0 or unit in units:
                    return unavailable("snapshot_poles_invalid")
                units.add(unit)
            quality = row.get("quality")
            if quality is not None and (not isinstance(quality, str) or not quality):
                return unavailable("snapshot_poles_invalid")
            normalized.append((row, pos, wire, supply, unit, quality))
    except (KeyError, TypeError, ValueError, OverflowError):
        return unavailable("snapshot_or_prototype_invalid")
    match = [item for item in normalized if item[4] == anchor["unit_number"]]
    if not match:
        return unavailable("anchor_not_observed")
    row, pos, wire, supply, _unit, quality = match[0]
    if row["name"] != anchor["entity"] or pos != expected_position:
        return unavailable("anchor_identity_mismatch")
    if quality != "normal":
        return unavailable("anchor_quality_unverified")
    if wire != expected_wire or supply != expected_supply:
        return unavailable("anchor_prototype_mismatch")
    if row["grid"] == "nil":
        return unavailable("anchor_network_unavailable")
    result["anchor_grid"] = row["grid"]
    result["generator_type_present"] = row["generated"]
    for other, other_pos, other_wire, _supply, unit, quality in normalized:
        if unit == anchor["unit_number"]:
            continue
        if quality is None:
            if "neighbor_quality_unverified" not in result["reasons"]:
                result["reasons"].append("neighbor_quality_unverified")
            continue
        if not any((point[0] - other_pos[0]) ** 2 + (point[1] - other_pos[1]) ** 2
                   <= min(reach, other_wire) ** 2 for point, reach in planned):
            continue
        if other["grid"] == "nil":
            if "neighbor_network_unavailable" not in result["reasons"]:
                result["reasons"].append("neighbor_network_unavailable")
        elif other["grid"] == row["grid"]:
            result["same_grid_candidates"] += 1
        else:
            result["potential_other_grid_links"].append({"name": other["name"], "position": other["position"],
                                                          "unit_number": unit, "grid": other["grid"]})
    if result["potential_other_grid_links"]:
        result["status"] = "potential_conflict"
    elif not result["reasons"]:
        result["status"] = "consistent"
    return result


def add_candidate_power_diagnostics(bound, *, episode_id, network_anchor, fluid_capture,
                                    adapter, facts, candidates, response, validations):
    """Attach bounded diagnostics after rechecking the candidate and held ledger."""
    from collections.abc import Mapping
    from orchestrator.material_reservations import MaterialReservationError, MaterialReservationLedger
    from training.laya_saved_world import SavedWorldError
    from tools.optimizer_saved_world import (_annotate_candidate, _stock_observation,
                                              optimizer_observation_candidate)

    if not isinstance(validations, Mapping):
        return validations
    output = dict(validations)
    for candidate in candidates:
        if not isinstance(candidate, Mapping) or candidate.get("optimizer_binding") != bound.plan.binding.to_dict():
            continue
        option_id = candidate.get("option_id")
        entry = output.get(option_id)
        if not isinstance(entry, Mapping):
            continue
        summary = {"status": "unavailable", "reason": "diagnostic_inputs_invalid_or_stale",
                   "dispatch_authorized": False}
        try:
            ledger = MaterialReservationLedger(adapter.root / "server/script-output", episode_id=episode_id,
                                               surface=adapter.surface, force=adapter.force)
            expected = optimizer_observation_candidate(adapter, bound, ledger, episode_id=episode_id,
                                                        network_anchor=network_anchor)
            expected = _annotate_candidate(expected, _stock_observation(facts, bound, ledger, network_anchor), bound, ledger)
            if candidate != expected:
                summary["reason"] = "candidate_or_funding_changed"
            else:
                report = diagnose_bound_power(bound, catalog_bytes=fluid_capture["json"].encode("utf-8"),
                    catalog_sha256=fluid_capture["sha256"], snapshot=entry.get("power_topology_snapshot"),
                    reference_tick=response.get("tick"))
                summary = {key: report[key] for key in ("status", "catalog_sha256", "snapshot_tick", "anchor_grid",
                    "generator_type_present", "planned_poles", "same_grid_candidates", "reasons", "dispatch_authorized")}
                summary["potential_other_grid_count"] = len(report["potential_other_grid_links"])
                summary["potential_other_grid_links"] = report["potential_other_grid_links"][:16]
        except (ValueError, TypeError, KeyError, OSError, OverflowError, MaterialReservationError, SavedWorldError):
            pass
        summary["evidence_limit"] = ("Same-force built poles only; actual wires, generation and power capacity remain unverified.")
        output[option_id] = {**entry, "power_topology_diagnostic": summary}
    return output
