# Path: tests/test_optimizer_fluid_runner.py | Purpose: Exercise bound fluid diagnostics in candidate observations.
import copy
import json

import pytest

from core.optimizer_artifact_binding import bind_artifact_to_construction_plan
from core.optimizer_construction_state import ConstructionScope
from orchestrator.material_reservations import MaterialReservationLedger
from orchestrator.optimizer_reservations import reserve_optimizer_artifact
from planners.oil_block_artifact import build_oil_block_artifact
from tests.test_fluid_topology import MODS, catalog_bytes, snapshot
from tests.test_oil_block_artifact import oil_arguments
from tests.test_optimizer_saved_world import _network_facts, world
from tests.test_optimizer_demand_observation import inputs
from tools.optimizer_saved_world import load_optimizer_candidate_config, optimizer_candidate_config


@pytest.fixture(scope="module")
def oil_artifact(oil_arguments):
    return build_oil_block_artifact(**oil_arguments)["artifact"]


@pytest.fixture
def configured(world, oil_artifact, tmp_path):
    adapter, original, _ledger = world
    artifact = copy.deepcopy(oil_artifact)
    bound = bind_artifact_to_construction_plan(
        artifact, scope=ConstructionScope(str(adapter.root), original.plan.binding.scope.manifest_digest, "nauvis", "player"),
        project_id="oil", transaction_id="a" * 32, throughput_output="petroleum-gas",
        throughput_target=195, throughput_unit="fluid/second")
    ledger = MaterialReservationLedger(adapter.root / "server/script-output", episode_id="oil-episode", surface="nauvis", force="player")
    reserve_optimizer_artifact(bound, ledger, episode_id="oil-episode", stock=dict(bound.plan.required_materials))
    config = optimizer_candidate_config(bound, episode_id="oil-episode", network_anchor={"x": 3, "y": -1},
                                        fluid_catalog_bytes=catalog_bytes())
    path = tmp_path / "fluid-candidate.json"
    path.write_text(json.dumps(config))
    provider, metadata = load_optimizer_candidate_config(path, adapter)
    facts = _network_facts(bound)
    facts["mods"] = MODS
    board = {"needs": [{"item": "petroleum-gas", "condition": "shortfall"}]}
    candidate = provider(adapter, facts, board)[0]
    response = {"tick": 200, "validations": {candidate["option_id"]: {
        "reason": None, "bill": candidate["bill"], "fluid_topology_snapshot": snapshot(tick=200)}}}
    return adapter, provider, metadata, facts, candidate, response, path


def test_oil_candidate_gets_bound_unverified_topology_without_changing_gate(configured):
    adapter, provider, metadata, facts, candidate, response, _path = configured
    result = provider.validate(adapter, facts, [candidate], response)[candidate["option_id"]]
    diag = result["fluid_topology_diagnostic"]
    assert diag["status"] == "unverified" and diag["conflict_count"] == 0
    assert {entry["code"] for entry in diag["unverified"]} == {"underground_tile_blocking_unverified"}
    assert diag["underground_pairs"] == 92 and diag["dispatch_authorized"] is False
    assert diag["catalog_sha256"] == metadata["fluid_catalog_sha256"]
    assert result["reason"] is None and "fluid_topology_diagnostic" not in response["validations"][candidate["option_id"]]


@pytest.mark.parametrize("mutation", ["tick", "mods", "candidate", "snapshot"])
def test_diagnostic_fails_closed_for_changed_observations(configured, mutation):
    adapter, provider, _metadata, facts, candidate, response, _path = configured
    if mutation == "tick": response["tick"] += 1
    elif mutation == "mods": facts["mods"] = {"base": "different"}
    elif mutation == "candidate": candidate["bill"]["pipe"] += 1
    else: response["validations"][candidate["option_id"]].pop("fluid_topology_snapshot")
    diag = provider.validate(adapter, facts, [candidate], response)[candidate["option_id"]]["fluid_topology_diagnostic"]
    assert diag["status"] in {"unavailable", "unverified"} and diag["dispatch_authorized"] is False
    if mutation == "tick": assert "snapshot_stale" in {row["code"] for row in diag["unverified"]}
    if mutation == "candidate": assert diag["reason"] == "candidate_or_funding_changed"


def test_tampered_catalog_is_rejected_when_loading_config(configured):
    adapter, _provider, _metadata, _facts, _candidate, _response, path = configured
    config = json.loads(path.read_text())
    config["fluid_catalog"]["json"] += " "
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="hash does not match"):
        load_optimizer_candidate_config(path, adapter)


def test_empty_lua_entity_table_is_normalized_for_topology(configured):
    adapter, provider, _metadata, facts, candidate, response, _path = configured
    response["validations"][candidate["option_id"]]["fluid_topology_snapshot"]["entities"] = {}
    diag = provider.validate(adapter, facts, [candidate], response)[candidate["option_id"]]["fluid_topology_diagnostic"]
    assert diag["normalized_empty_entities"] is True
    assert "snapshot_invalid" not in {row["code"] for row in diag["unverified"]}
    assert diag["dispatch_authorized"] is False


@pytest.mark.parametrize("failure", ["ledger", "instance"])
def test_runtime_or_ledger_failure_is_reported_without_opening_gate(configured, monkeypatch, failure):
    from orchestrator.material_reservations import MaterialReservationError
    from training.laya_saved_world import SavedWorldError
    adapter, provider, _metadata, facts, candidate, response, _path = configured
    def fail(*_args, **_kwargs):
        raise MaterialReservationError("stale") if failure == "ledger" else SavedWorldError("instance changed")
    monkeypatch.setattr("tools.optimizer_saved_world.optimizer_observation_candidate", fail)
    diag = provider.validate(adapter, facts, [candidate], response)[candidate["option_id"]]["fluid_topology_diagnostic"]
    assert diag["status"] == "unavailable" and diag["reason"] == "diagnostic_inputs_invalid_or_stale"
    assert diag["dispatch_authorized"] is False


def test_oil_candidate_diagnostic_is_visible_when_plastic_board_has_no_fluid_need(configured):
    from tests.test_laya_clean_runner import facts as clean_facts, validator
    from training.laya_clean_runner import compile_menu
    from training.laya_factory_status import compile_status

    adapter, provider, _metadata, observed, _candidate, response, _path = configured
    facts = {**clean_facts(), **observed}
    facts["ports"][0].update(construction_radius=55, logistic_radius=25)
    facts["networks"]["1"].update(construction_robots=50, available_construction_robots=50, logistic_robots=50)
    board = compile_status(facts, {"plastic-bar": 0.5})
    assert all(need["item"] != "petroleum-gas" for need in board["needs"])
    candidate, = provider(adapter, facts, board)
    assert candidate["option_id"] in response["validations"]
    normal = validator()
    def validate(candidates):
        if candidates[0]["option_id"] == candidate["option_id"]:
            return provider.validate(adapter, facts, candidates, copy.deepcopy(response))
        return normal(candidates)
    menu = compile_menu(facts, board, validate, 2, additional_candidates=[candidate], observe_unlinked=True)
    result, = menu["additional_candidate_results"]
    assert result["need_id"] is None and result["demand_status"] == "not_in_status_board"
    assert result["validation"]["fluid_topology_diagnostic"]["status"] == "unverified"
    assert result["dispatch_reason"] == "external_dispatch_unimplemented"
    assert candidate["option_id"] not in {option["option_id"] for option in menu["options"]}


def test_catalog_demand_links_oil_proposal_to_plastic_goal_in_director_state(configured, inputs):
    from tests.test_laya_clean_runner import facts as clean_facts, validator
    from training.laya_clean_runner import ExplorationPolicy, build_state, compile_menu
    from training.laya_factory_status import compile_status
    from training.laya_mission import phase

    adapter, _provider, _metadata, observed, _candidate, response, path = configured
    _unused_bound, demand_model, _unused_facts = inputs
    config = json.loads(path.read_text())
    config["demand_model"] = demand_model
    path.write_text(json.dumps(config))
    provider, metadata = load_optimizer_candidate_config(path, adapter)
    assert metadata["demand_catalog_sha256"] == demand_model["provenance"]["catalog_sha256"]
    assert {"core/recipe_dag.py", "schemas/recipe_catalog.schema.json"} <= set(metadata["demand_code_sha256"])
    facts = {**clean_facts(), **observed}
    facts["ports"][0].update(construction_radius=55, logistic_radius=25)
    facts["networks"]["1"].update(construction_robots=50, available_construction_robots=50, logistic_robots=50)
    facts["enabled_recipes"] = {"plastic-bar": True, "other": True}
    board = compile_status(facts, {"plastic-bar": .5})
    proposals = provider(adapter, facts, board)
    candidate, = proposals
    annotations = provider.describe(facts, board, proposals)
    assert annotations[candidate["option_id"]]["net_external_input_per_second"] == "5"
    normal = validator()
    def validate(candidates):
        if candidates[0]["option_id"] == candidate["option_id"]:
            return provider.validate(adapter, facts, candidates, copy.deepcopy(response))
        return normal(candidates)
    menu = compile_menu(facts, board, validate, 2, additional_candidates=proposals, observe_unlinked=True,
                        additional_observations=annotations)
    state = build_state(facts, menu, board, phase("C4"), "test", ExplorationPolicy.model_ref, [], None)
    evidence, = state["observation_digest"]["optimizer_demand"]
    assert evidence["status"] == "required_by_selected_recipe_model"
    assert evidence["product"] == "petroleum-gas" and evidence["net_external_input_per_second"] == "5"
    assert evidence["available_supply_per_second"] is None and evidence["dispatch_authorized"] is False
    assert menu["additional_candidate_results"][0]["need_id"] is None
    assert candidate["option_id"] not in {option["option_id"] for option in menu["options"]}


def test_candidate_config_rejects_tampered_demand_catalog(configured, inputs):
    adapter, _provider, _metadata, _facts, _candidate, _response, path = configured
    _bound, demand_model, _facts2 = inputs
    config = json.loads(path.read_text())
    config["demand_model"] = demand_model
    config["demand_model"]["catalog_json"] += " "
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="demand catalog hash"):
        load_optimizer_candidate_config(path, adapter)


def test_pinned_mask_enables_opt_in_terrain_diagnostic_for_oil(configured):
    import hashlib
    from core.underground_tile_blocking import space_age_2_1_21_mask_facts
    from tests.test_underground_tile_blocking import RUN_MODS, OURS, _planned_underground_pair, tile_scan

    adapter, _provider, _metadata, facts, _candidate, _response, path = configured
    config = json.loads(path.read_text())
    report = json.loads(catalog_bytes())
    report["provenance"]["active_mods"] = RUN_MODS
    report["provenance"]["active_mods_count"] = len(RUN_MODS)
    raw_catalog = json.dumps(report).encode()
    config["fluid_catalog"] = {"json": raw_catalog.decode(), "sha256": hashlib.sha256(raw_catalog).hexdigest()}
    raw_mask = space_age_2_1_21_mask_facts(RUN_MODS, attested_mods=OURS)
    config["underground_mask"] = {"json": raw_mask.decode(), "sha256": hashlib.sha256(raw_mask).hexdigest()}
    path.write_text(json.dumps(config))
    provider, metadata = load_optimizer_candidate_config(path, adapter)
    facts["mods"] = RUN_MODS
    board = {"needs": [{"item": "petroleum-gas", "condition": "shortfall"}]}
    candidate, = provider(adapter, facts, board)
    prepared, = provider.prepare_validation([candidate])
    assert prepared["inspect_underground_tiles"] is True
    assert "inspect_underground_tiles" not in candidate
    assert metadata["underground_mask_sha256"] == hashlib.sha256(raw_mask).hexdigest()
    area = (-200, -200, 200, 260)
    snap = snapshot(tick=200, area=area)
    response = {"tick": 200, "validations": {candidate["option_id"]: {"reason": None,
                 "bill": candidate["bill"], "fluid_topology_snapshot": snap}}}
    diag = provider.validate(adapter, facts, [candidate], response)[candidate["option_id"]]["fluid_topology_diagnostic"]
    assert diag["status"] == "unverified"
    assert "tile_scan_absent" in {reason for row in diag["unverified"] for reason in row.get("reasons", [])}
    snap["tiles"] = tile_scan(area=area, tick=200)
    diag = provider.validate(adapter, facts, [candidate], response)[candidate["option_id"]]["fluid_topology_diagnostic"]
    assert diag["status"] == "consistent" and diag["dispatch_authorized"] is False
    assert diag["underground_tile_blocking"]["spans_clear"] == 92
    _, tile = _planned_underground_pair(candidate["plan"])
    snap["tiles"] = tile_scan(area=area, tick=200, overrides={tile: "lava"})
    diag = provider.validate(adapter, facts, [candidate], response)[candidate["option_id"]]["fluid_topology_diagnostic"]
    assert diag["status"] == "rejected" and diag["dispatch_authorized"] is False
    assert any(row["code"] == "underground_span_blocked" for row in diag["conflicts"])


def test_pinned_mask_requires_catalog_and_matching_bytes(configured):
    import hashlib
    from core.underground_tile_blocking import space_age_2_1_21_mask_facts
    from tests.test_underground_tile_blocking import RUN_MODS, OURS
    adapter, _provider, _metadata, _facts, _candidate, _response, path = configured
    config = json.loads(path.read_text())
    raw = space_age_2_1_21_mask_facts(RUN_MODS, attested_mods=OURS)
    config["underground_mask"] = {"json": raw.decode(), "sha256": hashlib.sha256(raw).hexdigest()}
    config["underground_mask"]["json"] += " "
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="mask hash"):
        load_optimizer_candidate_config(path, adapter)
    config["underground_mask"]["json"] = raw.decode()
    del config["fluid_catalog"]
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="configuration is invalid"):
        load_optimizer_candidate_config(path, adapter)


def test_mask_mod_set_mismatch_does_not_fall_back_to_a_clear_verdict(configured):
    import hashlib
    from core.underground_tile_blocking import space_age_2_1_21_mask_facts
    from tests.test_underground_tile_blocking import RUN_MODS, OURS
    adapter, _provider, _metadata, facts, _candidate, response, path = configured
    config = json.loads(path.read_text())
    raw = space_age_2_1_21_mask_facts(RUN_MODS, attested_mods=OURS)
    config["underground_mask"] = {"json": raw.decode(), "sha256": hashlib.sha256(raw).hexdigest()}
    path.write_text(json.dumps(config))
    provider, _ = load_optimizer_candidate_config(path, adapter)
    candidate, = provider(adapter, facts, {"needs": [{"item": "petroleum-gas", "condition": "shortfall"}]})
    result = provider.validate(adapter, facts, [candidate], response)[candidate["option_id"]]["fluid_topology_diagnostic"]
    assert result["status"] == "unavailable" and result["dispatch_authorized"] is False
