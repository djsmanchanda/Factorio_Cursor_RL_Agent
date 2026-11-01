# Path: tests/test_optimizer_power_topology.py | Purpose: Check same-tick powered optimizer anchor diagnostics.
import copy

import pytest

from core.optimizer_artifact_binding import bind_artifact_to_construction_plan
from tests.test_oil_block_artifact import oil_arguments
from tests.test_optimizer_power_attachment import capture, compose, oil_case
from tests.test_optimizer_saved_world import _network_facts, world
from tools.optimizer_power_topology import diagnose_bound_power


@pytest.fixture(scope="module")
def powered(oil_case):
    original, report, anchor, _ = oil_case
    artifact = compose(oil_case)["artifact"]
    bound = bind_artifact_to_construction_plan(artifact, scope=original.plan.binding.scope,
        project_id="powered", transaction_id="c" * 32, throughput_output="petroleum-gas",
        throughput_target=195, throughput_unit="fluid/second")
    return bound, capture(report), anchor


def sample(anchor):
    return {"version": "optimizer-power-snapshot-v1", "tick": 200, "surface": "nauvis", "force": "player",
            "mods": {"base": "2.1.21"}, "complete": True,
            "poles": [{"name": anchor["entity"], "position": dict(anchor["position"]),
                       "unit_number": anchor["unit_number"], "quality": "normal", "wire": 9,
                       "supply": 3.5, "grid": "7", "generated": True}]}


def diagnose(powered, snapshot):
    bound, cap, _anchor = powered
    return diagnose_bound_power(bound, catalog_bytes=cap["catalog_bytes"], catalog_sha256=cap["expected_sha256"],
                                snapshot=snapshot, reference_tick=200)


def test_same_tick_observed_anchor_is_still_not_power_or_dispatch_proof(powered):
    snapshot = sample(powered[2])
    result = diagnose(powered, snapshot)
    assert result["status"] == "consistent" and result["anchor_grid"] == "7"
    assert result["generator_type_present"] is True and result["planned_poles"] == 57
    assert result["potential_other_grid_links"] == [] and result["dispatch_authorized"] is False
    assert any("not measured generation" in text for text in result["non_claims"])
    snapshot["poles"][0]["generated"] = False
    assert diagnose(powered, snapshot)["generator_type_present"] is False


@pytest.mark.parametrize("damage,reason", [
    ("tick", "snapshot_tick_mismatch"), ("incomplete", "snapshot_incomplete"),
    ("missing", "anchor_not_observed"), ("unit", "anchor_not_observed"),
    ("position", "anchor_identity_mismatch"), ("quality", "anchor_quality_unverified"),
    ("wire", "anchor_prototype_mismatch"), ("grid", "anchor_network_unavailable"),
])
def test_changed_or_unavailable_anchor_never_becomes_consistent(powered, damage, reason):
    snapshot = sample(powered[2])
    pole = snapshot["poles"][0]
    if damage == "tick": snapshot["tick"] = 201
    elif damage == "incomplete": snapshot["complete"] = False
    elif damage == "missing": snapshot["poles"] = {}
    elif damage == "unit": pole["unit_number"] += 1
    elif damage == "position": pole["position"]["x"] += 1
    elif damage == "quality": pole.pop("quality")
    elif damage == "wire": pole["wire"] = 10
    else: pole["grid"] = "nil"
    result = diagnose(powered, snapshot)
    assert result["status"] == "unverified" and reason in result["reasons"]
    assert result["dispatch_authorized"] is False


def test_nearby_different_grid_is_a_potential_merge_not_an_actual_connection(powered):
    snapshot = sample(powered[2])
    snapshot["poles"].append({"name": "medium-electric-pole", "position": {"x": 5.5, "y": -5.5},
        "unit_number": 11, "quality": "normal", "wire": 9, "supply": 3.5, "grid": "8", "generated": False})
    result = diagnose(powered, snapshot)
    assert result["status"] == "potential_conflict"
    assert result["potential_other_grid_links"][0]["grid"] == "8"
    assert result["dispatch_authorized"] is False
    snapshot["poles"][1]["grid"] = "7"
    assert diagnose(powered, snapshot)["same_grid_candidates"] == 1


def test_unreadable_neighbor_quality_and_mod_drift_fail_closed(powered):
    snapshot = sample(powered[2])
    neighbor = copy.deepcopy(snapshot["poles"][0])
    neighbor.update(unit_number=20, position={"x": 0.5, "y": 0.5})
    neighbor.pop("quality")
    snapshot["poles"].append(neighbor)
    assert "neighbor_quality_unverified" in diagnose(powered, snapshot)["reasons"]
    snapshot["mods"] = {"base": "different"}
    with pytest.raises(ValueError):
        diagnose(powered, snapshot)


def test_runner_journals_power_snapshot_digest_and_director_sees_diagnostic(powered, world, tmp_path):
    import json
    from orchestrator.material_reservations import MaterialReservationLedger
    from orchestrator.optimizer_reservations import reserve_optimizer_artifact
    from tests.test_laya_clean_runner import facts as clean_facts, validator
    from training.laya_clean_runner import ExplorationPolicy, build_state, compile_menu
    from training.laya_factory_status import compile_status
    from training.laya_mission import phase
    from tools.optimizer_saved_world import load_optimizer_candidate_config, optimizer_candidate_config

    offline, cap, anchor = powered
    adapter, original, _ = world
    bound = bind_artifact_to_construction_plan(offline.artifact, scope=original.plan.binding.scope,
        project_id="power-oil", transaction_id="d" * 32, throughput_output="petroleum-gas",
        throughput_target=195, throughput_unit="fluid/second")
    ledger = MaterialReservationLedger(adapter.root / "server/script-output", episode_id="power-episode",
                                       surface="nauvis", force="player")
    reserve_optimizer_artifact(bound, ledger, episode_id="power-episode", stock=dict(bound.plan.required_materials))
    config = optimizer_candidate_config(bound, episode_id="power-episode", network_anchor={"x": 3, "y": -1},
                                        fluid_catalog_bytes=cap["catalog_bytes"])
    path = tmp_path / "power-config.json"
    path.write_text(json.dumps(config))
    provider, metadata = load_optimizer_candidate_config(path, adapter)
    assert "power_diagnostic_code_sha256" in metadata
    facts = {**clean_facts(), **_network_facts(bound), "mods": {"base": "2.1.21"}}
    facts["ports"][0].update(construction_radius=55, logistic_radius=25)
    facts["networks"]["1"].update(construction_robots=50, available_construction_robots=50, logistic_robots=50)
    board = compile_status(facts, {"plastic-bar": .5})
    candidate, = provider(adapter, facts, board)
    prepared, = provider.prepare_validation([candidate])
    assert prepared["inspect_power_topology"] is True and "inspect_power_topology" not in candidate
    response = {"tick": 200, "validations": {candidate["option_id"]: {"reason": None,
        "bill": candidate["bill"], "power_topology_snapshot": sample(anchor)}}}
    normal = validator()
    def validate(candidates):
        if candidates[0]["option_id"] == candidate["option_id"]:
            return provider.validate(adapter, facts, candidates, copy.deepcopy(response))
        return normal(candidates)
    menu = compile_menu(facts, board, validate, 2, additional_candidates=[candidate], observe_unlinked=True)
    entry, = menu["additional_candidate_results"]
    power = entry["validation"]["power_topology_diagnostic"]
    assert power["status"] == "consistent" and power["dispatch_authorized"] is False
    assert set(entry["validation"]["power_topology_snapshot"]) == {"sha256", "tick", "complete", "poles"}
    assert candidate["option_id"] not in {option["option_id"] for option in menu["options"]}
    state = build_state(facts, menu, board, phase("C4"), "test", ExplorationPolicy.model_ref, [], None)
    assert state["observation_digest"]["optimizer_power"][0]["anchor_grid"] == "7"
    assert "actual wires" in state["observation_digest"]["optimizer_power"][0]["evidence_limit"]
    altered = copy.deepcopy(candidate)
    altered["bill"]["pipe"] += 1
    changed = provider.validate(adapter, facts, [altered], copy.deepcopy(response))[candidate["option_id"]]
    assert changed["power_topology_diagnostic"]["reason"] == "candidate_or_funding_changed"
    stale = copy.deepcopy(response)
    stale["tick"] = 201
    diagnostic = provider.validate(adapter, facts, [candidate], stale)[candidate["option_id"]]["power_topology_diagnostic"]
    assert diagnostic["status"] == "unverified" and "snapshot_tick_mismatch" in diagnostic["reasons"]
