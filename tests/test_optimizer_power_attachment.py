# Path: tests/test_optimizer_power_attachment.py | Purpose: Compose and verify powered oil ghost candidates using pinned prototype facts.
import copy
from dataclasses import replace
import hashlib
import json

import pytest

from core.optimizer_artifact_binding import bind_artifact_to_construction_plan
from core.optimizer_construction_state import ConstructionScope
from orchestrator.stage_services import _power_bridge_hops
from planners.oil_block_artifact import build_oil_block_artifact
from planners.optimizer_power_attachment import compose_optimizer_power_attachment
from planners.plan_validation import actions, entity_footprint_tiles
from tests.test_fluid_topology import MODS, catalog_bytes, snapshot
from tests.test_oil_block_artifact import oil_arguments


@pytest.fixture(scope="module")
def oil_case(oil_arguments):
    artifact = build_oil_block_artifact(**oil_arguments)["artifact"]
    bound = bind_artifact_to_construction_plan(artifact,
        scope=ConstructionScope("synthetic", "a" * 64, "nauvis", "player"), project_id="oil",
        transaction_id="a" * 32, throughput_output="petroleum-gas", throughput_target=195,
        throughput_unit="fluid/second")
    report = json.loads(catalog_bytes())
    report["force_state"] = {"name": "player", "research_progress": 0, "research_queue": [],
                             "research_enabled": True, "bonuses": {}}
    for entity in report["entities"]:
        if entity["name"] == "medium-electric-pole":
            entity["electric_pole"] = {"quality": "normal", "max_wire_distance": 9, "supply_area_distance": 3.5}
        if entity["name"] in {"chemical-plant", "oil-refinery"}:
            entity["energy"] = {"source": "electric"}
    blocked = set().union(*(entity_footprint_tiles(action) for action in actions(artifact["plan"])))
    spans = [((70.5, 1.5), (59.5, 1.5)), ((5.5, 7.5), (3.5, 61.5)), ((3.5, 65.5), (3.5, 121.5))]
    extra = []
    for start, end in spans:
        for x, y in _power_bridge_hops(start, end, 9, blocked):
            extra.append({"action_type": "place_ghost", "entity": "medium-electric-pole", "position": {"x": x, "y": y}})
            blocked.add((int(x // 1), int(y // 1)))
    anchor = {"entity": "medium-electric-pole", "unit_number": 999, "position": {"x": 70.5, "y": 1.5}}
    return bound, report, anchor, extra


def capture(report):
    raw = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    return {"catalog_bytes": raw, "expected_sha256": hashlib.sha256(raw).hexdigest(),
            "surface": "nauvis", "force": "player", "active_mods": MODS}


def compose(case, *, report=None, anchor=None, extra=None):
    bound, default, default_anchor, default_extra = case
    return compose_optimizer_power_attachment(bound, placement_capture=capture(report or default),
        anchor=default_anchor if anchor is None else anchor, pole_actions=default_extra if extra is None else extra)


def test_real_oil_artifact_gets_connected_poles_and_full_new_bill(oil_case, tmp_path):
    from core.fluid_topology import load_fluid_prototype_catalog
    from orchestrator.material_reservations import MaterialReservationLedger
    from orchestrator.optimizer_reservations import reserve_optimizer_artifact
    from tools.optimizer_fluid_topology import check_bound_fluid_topology
    bound, report, anchor, extra = oil_case
    result = compose(oil_case)
    artifact = result["artifact"]
    assert len(extra) == 13 and result["potential_pole_connections"] == 57
    assert result["center_covered_electric_consumers"] == 22 and result["center_coverage_unverified"] == []
    assert result["dispatch_authorized"] is False
    assert artifact["declared_item_bill"]["medium-electric-pole"] == bound.artifact["declared_item_bill"]["medium-electric-pole"] + 13
    context = artifact["planning_context"]["data"]
    assert context["kind"] == "bounded-oil-block-v1" and context["power_attachment"]["anchor"] == anchor
    assert context["power_attachment"]["input_artifact_sha256"] == bound.artifact_sha256
    assert context["fluid_topology_intent"] == bound.artifact["planning_context"]["data"]["fluid_topology_intent"]
    attached = bind_artifact_to_construction_plan(artifact, scope=bound.plan.binding.scope,
        project_id="powered-oil", transaction_id="b" * 32, throughput_output="petroleum-gas",
        throughput_target=195, throughput_unit="fluid/second")
    assert attached.binding_sha256 != bound.binding_sha256
    ledger = MaterialReservationLedger(tmp_path / "script-output", episode_id="power-test", surface="nauvis", force="player")
    short = reserve_optimizer_artifact(attached, ledger, episode_id="power-test", stock=dict(bound.plan.required_materials))
    assert short.state == "supply_wait" and short.reserved["medium-electric-pole"] == 44
    funded = reserve_optimizer_artifact(attached, ledger, episode_id="power-test", stock=dict(attached.plan.required_materials))
    assert funded.state == "ready" and funded.reserved["medium-electric-pole"] == 57
    cap = capture(report)
    catalog = load_fluid_prototype_catalog(cap["catalog_bytes"], expected_sha256=cap["expected_sha256"],
                                           surface="nauvis", force="player", active_mods=MODS)
    fluid = check_bound_fluid_topology(attached, catalog=catalog, snapshot=snapshot(), reference_tick=1010,
                                       max_snapshot_age_ticks=60)
    assert fluid["conflicts"] == [] and fluid["underground_pairs"] == 92
    assert "automatic wiring" in artifact["unknowns"]["power_attachment"]


def test_center_outside_supply_is_reported_without_inventing_footprint_failure(oil_case):
    report = copy.deepcopy(oil_case[1])
    next(entity for entity in report["entities"] if entity["name"] == "medium-electric-pole")["electric_pole"]["supply_area_distance"] = 0
    result = compose(oil_case, report=report)
    assert result["center_covered_electric_consumers"] == 0
    assert len(result["center_coverage_unverified"]) == 22
    assert result["dispatch_authorized"] is False


@pytest.mark.parametrize("failure", ["no_reach", "unknown_energy", "mapping", "anchor", "overlap", "disconnected", "binding"])
def test_power_attachment_rejects_unknown_or_inconsistent_inputs(oil_case, failure):
    bound, original, anchor, extra = oil_case
    report, anchor, extra = copy.deepcopy(original), copy.deepcopy(anchor), copy.deepcopy(extra)
    pole = next(entity for entity in report["entities"] if entity["name"] == "medium-electric-pole")
    if failure == "no_reach": pole.pop("electric_pole")
    elif failure == "unknown_energy": next(entity for entity in report["entities"] if entity["name"] == "chemical-plant").pop("energy")
    elif failure == "mapping": pole["placement"]["items"][0]["name"] = "other-pole-item"
    elif failure == "anchor": anchor["position"] = {"x": 5.5, "y": 1.5}
    elif failure == "overlap": extra[0]["position"] = {"x": 5.5, "y": 1.5}
    elif failure == "disconnected": extra.clear()
    else: bound = replace(bound, canonical_binding=bound.canonical_binding + b" ")
    with pytest.raises(ValueError):
        compose_optimizer_power_attachment(bound, placement_capture=capture(report), anchor=anchor, pole_actions=extra)
