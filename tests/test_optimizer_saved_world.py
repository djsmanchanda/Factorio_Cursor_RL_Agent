# Path: tests/test_optimizer_saved_world.py | Purpose: Bind a selectable optimizer block to an isolated saved-world executor.
import copy
import hashlib
from fractions import Fraction

import pytest

from core.optimizer_artifact_binding import bind_artifact_to_construction_plan
from core.optimizer_construction_state import ConstructionScope
from orchestrator.material_reservations import MaterialReservationError, MaterialReservationLedger
from orchestrator.optimizer_reservations import reserve_optimizer_artifact
from tests.test_optimizer_artifact_binding import _artifact
from tests.test_laya_saved_world import facts
from tools import optimizer_saved_world as integration
from training.laya_saved_world import SavedWorldAdapter, SavedWorldError, decision_state


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    manifest = b'{"runtime":"disposable_saved_world","production_authority":false}'
    (root / "manifest.json").write_bytes(manifest)
    adapter = object.__new__(SavedWorldAdapter)
    adapter.root = root
    monkeypatch.setattr(integration, "owned_root", lambda path: path.resolve())
    monkeypatch.setattr(integration, "verify_instance", lambda _root, **_kwargs: {"runtime": "disposable_saved_world"})
    scope = ConstructionScope(str(root), hashlib.sha256(manifest).hexdigest(), "nauvis", "player")
    bound = bind_artifact_to_construction_plan(
        _artifact(), scope=scope, project_id="iron-block", transaction_id="a" * 32,
        throughput_output="iron-plate", throughput_target=Fraction(1, 2), throughput_unit="items/second")
    ledger = MaterialReservationLedger(root / "server/script-output", episode_id="episode-1", surface="nauvis", force="player")
    reserve_optimizer_artifact(bound, ledger, episode_id="episode-1", stock=dict(bound.plan.required_materials))
    return adapter, bound, ledger


def offer(world):
    adapter, bound, ledger = world
    return integration.optimizer_candidate(adapter, bound, ledger, episode_id="episode-1", network_anchor={"x": 3, "y": -1})


def assert_same_build_with_stock_observation(candidate, original):
    assert candidate["option_id"] != original["option_id"]
    assert candidate["option_id"].startswith("optimizer.")
    assert {k: v for k, v in candidate.items() if k not in {"option_id", "stock_observation"}} == {
        k: v for k, v in original.items() if k != "option_id"}


def test_bound_plan_can_be_inspected_and_only_selected_unchanged(world):
    adapter, bound, ledger = world
    candidate = offer(world)
    assert candidate["bill"] == dict(bound.plan.required_materials)
    assert candidate["plan"]["phases"] == bound.artifact["plan"]["phases"]
    assert (candidate["plan"]["surface"], candidate["plan"]["force"], candidate["plan"]["atomic"]) == ("nauvis", "player", True)
    assert candidate["funding_context"] == {"episode_id": "episode-1", "ledger_revision": ledger.revision}
    state = decision_state(facts(), [candidate], "test", {"model_id": "test", "model_hash": "sha256:" + "0" * 64}, "Produce plates")
    assert state["options"][0]["option_id"] == candidate["option_id"]
    # The strict wrapper re-observes stock, then hands the exact candidate and a bound verifier to the adapter.
    adapter.query = lambda *_args, **_kwargs: _network_facts(bound)
    sent = {}
    adapter.execute = lambda selected, presented, ticks, check_verifier=None: sent.update(
        selected=selected, presented=presented, ticks=ticks, verifier=check_verifier) or {"status": "observed"}
    assert integration.execute_optimizer_candidate(adapter, bound, ledger, episode_id="episode-1",
        network_anchor={"x": 3, "y": -1}, candidate=candidate, selected=candidate["option_id"],
        measure_ticks=600) == {"status": "observed"}
    assert sent["presented"] == [candidate] and sent["ticks"] == 600
    assert isinstance(sent["verifier"], integration.OptimizerCheckVerifier)
    altered = copy.deepcopy(candidate)
    altered["plan"]["phases"][0]["actions"][0]["position"]["x"] += 1
    with pytest.raises(ValueError, match="differs"):
        integration.execute_optimizer_candidate(adapter, bound, ledger, episode_id="episode-1",
            network_anchor={"x": 3, "y": -1}, candidate=altered, selected=candidate["option_id"])


def test_changed_manifest_or_ledger_cannot_offer(world):
    adapter, bound, ledger = world
    (adapter.root / "manifest.json").write_text("changed")
    with pytest.raises(ValueError, match="does not match"):
        offer(world)
    (adapter.root / "manifest.json").write_bytes(b'{"runtime":"disposable_saved_world","production_authority":false}')
    fresh = MaterialReservationLedger(adapter.root / "server/script-output", episode_id="episode-1", surface="nauvis", force="player")
    fresh.declare("competing", {"iron-plate": 1}, {"iron-plate": 1})
    with pytest.raises(MaterialReservationError, match="changed"):
        offer(world)


def test_candidate_identity_binds_episode_and_persisted_ledger_revision(world):
    adapter, bound, ledger = world
    first = offer(world)
    stock = {**dict(bound.plan.required_materials), "extra-item": 1}
    ledger.declare("another-project", {"extra-item": 1}, stock)
    second = offer(world)
    assert first["option_id"] != second["option_id"]
    assert second["funding_context"]["ledger_revision"] == ledger.revision
    other = MaterialReservationLedger(adapter.root / "server/script-output", episode_id="episode-2",
                                      surface="nauvis", force="player")
    reserve_optimizer_artifact(bound, other, episode_id="episode-2", stock=dict(bound.plan.required_materials))
    third = integration.optimizer_candidate(adapter, bound, other, episode_id="episode-2", network_anchor={"x": 3, "y": -1})
    assert first["option_id"] != third["option_id"]
    adapter.execute = lambda *_args: pytest.fail("stale funding reached executor")
    with pytest.raises(ValueError, match="differs"):
        integration.execute_optimizer_candidate(adapter, bound, ledger, episode_id="episode-1",
            network_anchor={"x": 3, "y": -1}, candidate=first, selected=first["option_id"])


def test_unresolved_transaction_prevents_optimizer_submission(world):
    adapter, bound, ledger = world
    candidate = offer(world)
    evidence = adapter.root / "evidence"; evidence.mkdir()
    (evidence / "transaction-old.json").write_text('{"status":"construction_pending"}')
    adapter.execute = lambda *_args, **_kwargs: pytest.fail("unexpected execution")
    with pytest.raises(ValueError, match="unresolved"):
        integration.execute_optimizer_candidate(adapter, bound, ledger, episode_id="episode-1",
            network_anchor={"x": 3, "y": -1}, candidate=candidate, selected=candidate["option_id"])


def test_optimizer_candidate_is_pruned_until_runtime_checks_exist(world, monkeypatch):
    adapter, _bound, _ledger = world
    candidate = offer(world)
    monkeypatch.setattr("training.laya_saved_world.compile_candidates", lambda _facts, _seed: [])
    adapter.query = lambda _candidates: {"validations": {candidate["option_id"]: {"bill": candidate["bill"]}}}
    options, _ = adapter.options(facts(), additional_candidates=[candidate])
    assert candidate not in options
    adapter.query = lambda _candidates: {"validations": {candidate["option_id"]: {"bill": {"different": 1}}}}
    options, evidence = adapter.options(facts(), additional_candidates=[candidate])
    assert candidate not in options
    assert evidence["validations"][candidate["option_id"]]["reason"] == "required_check_unimplemented:fluid_purity"
    adapter.query = lambda _candidates: {"validations": {candidate["option_id"]: {"reason": "underground_world_competitor"}}}
    options, evidence = adapter.options(facts(), additional_candidates=[candidate])
    assert candidate not in options
    assert evidence["validations"][candidate["option_id"]] == {
        "reason": "underground_world_competitor", "required_check_reason": "required_check_unimplemented:fluid_purity"}
    with pytest.raises(SavedWorldError, match="unique"):
        adapter.options(facts(), additional_candidates=[candidate, candidate])


def test_optimizer_checks_cannot_be_omitted_or_claimed_by_a_response(world):
    adapter, _bound, _ledger = world
    candidate = offer(world)
    adapter.query = lambda *_args, **_kwargs: pytest.fail("unverified plan reached live queries")
    for required in ([], ["fluid_purity"], ["fluid_purity", "safe_infrastructure_attachment"]):
        candidate["required_checks"] = required
        candidate["checks"] = {name: True for name in required}
        with pytest.raises(SavedWorldError, match="required validation"):
            adapter.execute(candidate["option_id"], [candidate])


def test_relabelled_external_candidate_cannot_enter_menu_or_execute(world, monkeypatch):
    adapter, _bound, _ledger = world
    candidate = offer(world)
    candidate["family"] = "expand"
    candidate.pop("required_checks")
    monkeypatch.setattr("training.laya_saved_world.compile_candidates", lambda _facts, _seed: [])
    adapter.query = lambda _candidates: {"validations": {candidate["option_id"]: {"bill": candidate["bill"]}}}
    options, evidence = adapter.options(facts(), additional_candidates=[candidate])
    assert candidate not in options
    assert evidence["validations"][candidate["option_id"]]["reason"] == "optimizer_required_checks_missing"
    with pytest.raises(SavedWorldError, match="required validation"):
        adapter.execute(candidate["option_id"], [candidate])
    candidate["option_id"] = "expand.fake"
    candidate.pop("optimizer_binding")
    candidate.pop("optimizer_artifact_sha256")
    with pytest.raises(SavedWorldError, match="required validation"):
        adapter.execute(candidate["option_id"], [candidate])
    candidate.pop("funding_context")
    options, evidence = adapter.options(facts(), additional_candidates=[candidate])
    assert candidate not in options
    assert evidence["validations"][candidate["option_id"]]["reason"] == "optimizer_required_checks_missing"


def test_bound_provider_reloads_funding_and_exposes_blocked_candidate_to_clean_menu(world):
    from tests.test_laya_clean_runner import facts as clean_facts, validator
    from training.laya_clean_runner import compile_menu
    from training.laya_factory_status import compile_status

    adapter, bound, ledger = world
    provider = integration.optimizer_candidate_provider(bound, episode_id="episode-1", network_anchor={"x": 3, "y": -1})
    board = compile_status(clean_facts(), {"iron-plate": 0.5})
    candidate = provider(adapter, clean_facts(), board)[0]
    assert_same_build_with_stock_observation(candidate, offer(world))
    assert candidate["stock_observation"] == {"status": "unavailable", "reason": "invalid_or_missing_snapshot"}
    seen = []
    validate = validator()
    def capture(candidates):
        seen.extend(candidates)
        if candidates[0]["option_id"] == candidate["option_id"]:
            assert candidates[0] == candidate and candidates[0] is not candidate
            return {candidate["option_id"]: {"reason": "underground_world_competitor", "observed_tick": 100,
                                              "underground_competitors": {"conflicts": 1}}}
        return validate(candidates)
    menu = compile_menu(clean_facts(), board, capture, 3, additional_candidates=[candidate])
    assert candidate["option_id"] in {c["option_id"] for c in seen}
    assert candidate["option_id"] not in {c["option_id"] for c in menu["options"]}
    assert menu["additional_candidate_results"] == [{"option_id": candidate["option_id"], "need_id": "item:iron-plate",
        "reason": "underground_world_competitor", "required_check_reason": "required_check_unimplemented:fluid_purity",
        "dispatch_reason": "external_dispatch_unimplemented", "validation": {"reason": "underground_world_competitor",
            "observed_tick": 100, "underground_competitors": {"conflicts": 1}}, "funding_context": candidate["funding_context"],
        "stock_observation": candidate["stock_observation"],
        "optimizer_binding": candidate["optimizer_binding"], "optimizer_artifact_sha256": candidate["optimizer_artifact_sha256"],
        "bill": candidate["bill"]}]
    assert menu["blocked"]["item:iron-plate"] == {"underground_world_competitor": 1}
    ledger.declare("another-project", {"extra-item": 1}, {**dict(bound.plan.required_materials), "extra-item": 1})
    assert provider(adapter, clean_facts(), board)[0]["option_id"] != candidate["option_id"]
    assert provider(adapter, clean_facts(), {"needs": [{"item": "iron-plate", "condition": "satisfied"}]}) == []


def test_candidate_configuration_rebinds_to_the_persisted_disposable_ledger(world, tmp_path):
    import json
    adapter, bound, ledger = world
    config = integration.optimizer_candidate_config(bound, episode_id="episode-1", network_anchor={"x": 3, "y": -1})
    path = tmp_path / "candidate.json"
    raw = json.dumps(config, indent=2).encode()
    path.write_bytes(raw)
    provider, metadata = integration.load_optimizer_candidate_config(path, adapter)
    board = {"needs": [{"item": "iron-plate", "condition": "shortfall"}]}
    candidate = provider(adapter, {}, board)[0]
    assert_same_build_with_stock_observation(candidate, offer(world))
    assert candidate["stock_observation"] == {"status": "unavailable", "reason": "invalid_or_missing_snapshot"}
    assert metadata["config_sha256"] == hashlib.sha256(raw).hexdigest()
    assert metadata["provider_code_sha256"] == integration.file_hash(integration.__file__)
    assert metadata["config_schema_sha256"] == integration.file_hash(integration._CONFIG_SCHEMA)
    assert metadata["plan_sha256"] == bound.binding_sha256
    assert metadata["funding_context"] == {"episode_id": "episode-1", "ledger_revision": ledger.revision}
    assert metadata["dispatch_authorized"] is False


@pytest.mark.parametrize("mutation", ["scope", "manifest", "artifact", "artifact_body", "plan", "episode", "episode_other", "project",
                                      "transaction", "throughput", "anchor", "extra", "duplicate"])
def test_candidate_configuration_rejects_tampering_before_provider_creation(world, tmp_path, mutation):
    import json
    adapter, bound, _ledger = world
    config = integration.optimizer_candidate_config(bound, episode_id="episode-1", network_anchor={"x": 3, "y": -1})
    if mutation == "scope": config["scope"]["instance_id"] += "-other"
    elif mutation == "manifest": config["scope"]["manifest_digest"] = "0" * 64
    elif mutation == "artifact": config["artifact_sha256"] = "0" * 64
    elif mutation == "artifact_body": config["artifact"]["plan"]["phases"][0]["actions"][0]["position"]["x"] += 1
    elif mutation == "plan": config["plan_sha256"] = "0" * 64
    elif mutation == "episode": config["episode_id"] = "../other"
    elif mutation == "episode_other": config["episode_id"] = "episode-2"
    elif mutation == "project": config["project_id"] = "other-project"
    elif mutation == "transaction": config["transaction_id"] = "b" * 32
    elif mutation == "throughput": config["throughput"]["target"] = "3/2"
    elif mutation == "anchor": config["network_anchor"]["x"] = float("nan")
    elif mutation == "extra": config["dispatch_authorized"] = True
    raw = json.dumps(config)
    if mutation == "duplicate": raw = raw[:-1] + ', "episode_id": "episode-1"}'
    path = tmp_path / "candidate.json"
    path.write_text(raw)
    with pytest.raises((ValueError, MaterialReservationError)):
        integration.load_optimizer_candidate_config(path, adapter)


def test_partial_reservation_exposes_all_shortfalls_without_authorizing_construction(world, tmp_path):
    import json
    from tests.test_laya_clean_runner import facts as clean_facts, validator
    from training.laya_clean_runner import compile_menu
    from training.laya_factory_status import compile_status

    adapter, bound, ledger = world
    config = integration.optimizer_candidate_config(bound, episode_id="episode-1", network_anchor={"x": 3, "y": -1})
    required = dict(bound.plan.required_materials)
    stock = dict(required)
    missing_items = list(stock)[:2]
    assert len(missing_items) == 2
    for item in missing_items:
        stock[item] = 0
    reserve_optimizer_artifact(bound, ledger, episode_id="episode-1", stock=stock)
    before = ledger.path.read_bytes()
    from orchestrator.optimizer_layout_preview import prepare_optimizer_layout_preview, prepare_optimizer_observation_preview
    preview = prepare_optimizer_observation_preview(bound, ledger, episode_id="episode-1")
    assert preview["authorization"]["approved_actions"] == [] and preview["execution_authorized"] is False
    assert preview["funding"]["shortfalls"] == {item: required[item] for item in missing_items}
    with pytest.raises(MaterialReservationError, match="fully funded"):
        prepare_optimizer_layout_preview(bound, ledger, episode_id="episode-1")
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(config))
    provider, metadata = integration.load_optimizer_candidate_config(path, adapter)
    board = compile_status(clean_facts(), {"iron-plate": 0.5})
    candidate = provider(adapter, clean_facts(), board)[0]
    funding = candidate["funding_status"]
    assert funding["state"] == "supply_wait"
    assert funding["shortfalls"] == {item: required[item] for item in missing_items}
    assert metadata["funding_status"] == funding and metadata["dispatch_authorized"] is False
    assert ledger.path.read_bytes() == before
    with pytest.raises(MaterialReservationError, match="fully funded"):
        integration.execute_optimizer_candidate(adapter, bound, ledger, episode_id="episode-1",
            network_anchor={"x": 3, "y": -1}, candidate=candidate, selected=candidate["option_id"])
    normal = validator()
    def validate(candidates):
        if candidates[0]["option_id"] == candidate["option_id"]:
            return {candidate["option_id"]: {"bill": candidate["bill"], "observed_tick": 100}}
        return normal(candidates)
    menu = compile_menu(clean_facts(), board, validate, 3, additional_candidates=[candidate])
    result = menu["additional_candidate_results"][0]
    assert result["reason"] == "construction_reservation_shortfall"
    assert result["funding_status"] == funding and all(menu["shortfalls"][item] == 1 for item in missing_items)
    assert candidate["option_id"] not in {option["option_id"] for option in menu["options"]}
    def conflict(candidates):
        if candidates[0]["option_id"] == candidate["option_id"]:
            return {candidate["option_id"]: {"reason": "item_shortfall:" + missing_items[0]}}
        return normal(candidates)
    menu = compile_menu(clean_facts(), board, conflict, 3, additional_candidates=[candidate])
    assert menu["additional_candidate_results"][0]["reason"] == "item_shortfall:" + missing_items[0]
    assert all(menu["shortfalls"][item] == 1 for item in missing_items)
    assert ledger.path.read_bytes() == before
    reserve_optimizer_artifact(bound, ledger, episode_id="episode-1", stock=dict(bound.plan.required_materials))
    funded = provider(adapter, clean_facts(), board)[0]
    assert_same_build_with_stock_observation(funded, offer(world))
    assert funded["stock_observation"] == {"status": "unavailable", "reason": "invalid_or_missing_snapshot"}
    assert funded["option_id"] != candidate["option_id"]
    ledger.mark_constructing("optimizer:iron-block", dict(bound.plan.required_materials))
    with pytest.raises(MaterialReservationError, match="consistent held reservation"):
        provider(adapter, clean_facts(), board)


def _network_facts(bound):
    return {"version": "saved-world-v1", "tick": 200, "surface": "nauvis", "force": "player",
            "ports": [{"position": {"x": 3, "y": -1}, "network": "1", "energy": 100}],
            "networks": {"1": {"items": dict(bound.plan.required_materials), "reserved": {}}}}


def test_network_stock_observation_separates_ghosts_and_other_held_projects(world):
    from tests.test_laya_clean_runner import facts as clean_facts, validator
    from training.laya_clean_runner import compile_menu
    from training.laya_factory_status import compile_status

    adapter, bound, ledger = world
    required = dict(bound.plan.required_materials)
    item, other_item = list(required)[:2]
    pool = {name: count + 4 for name, count in required.items()}
    ledger.declare("competing-project", {item: 2}, pool, priority=100)
    before = ledger.path.read_bytes()
    facts = _network_facts(bound)
    facts["networks"]["1"]["items"][item] = float(required[item] + 3)
    facts["networks"]["1"]["items"][other_item] = required[other_item] - 1
    facts["networks"]["1"]["reserved"][item] = 1
    provider = integration.optimizer_candidate_provider(bound, episode_id="episode-1", network_anchor={"x": 3, "y": -1})
    board = {"needs": [{"item": "iron-plate", "condition": "shortfall"}]}
    candidate = provider(adapter, facts, board)[0]
    observation = candidate["stock_observation"]
    assert observation["status"] == "available" and observation["tick"] == 200 and observation["network"] == "1"
    assert observation["ledger_revision"] == ledger.revision
    assert observation["items"][item] == {"required": required[item], "observed_inventory": required[item] + 3,
        "ghost_claims": 1, "competing_ledger_holds": 2, "project_ledger_hold": required[item],
        "available_after_claims": required[item], "shortfall_after_claims": 0}
    assert observation["items"][other_item]["shortfall_after_claims"] == 1
    normal = validator()
    def validate(candidates):
        if candidates[0]["option_id"] == candidate["option_id"]:
            return {candidate["option_id"]: {"bill": candidate["bill"], "observed_tick": 200}}
        return normal(candidates)
    clean_board = compile_status(clean_facts(), {"iron-plate": 0.5})
    menu = compile_menu(clean_facts(), clean_board, validate, 3, additional_candidates=[candidate])
    assert menu["additional_candidate_results"][0]["stock_observation"] == observation
    assert other_item not in menu["shortfalls"]
    assert candidate["option_id"] not in {option["option_id"] for option in menu["options"]}
    adapter.query = lambda *_args, **_kwargs: facts
    adapter.execute = lambda *_args, **_kwargs: pytest.fail("unpayable bill reached executor")
    # The option-time shortfall persists, so the fresh comparison refuses it at execution.
    with pytest.raises(SavedWorldError, match="stock_shortfall_at_execution:" + other_item):
        integration.execute_optimizer_candidate(adapter, bound, ledger, episode_id="episode-1",
            network_anchor={"x": 3, "y": -1}, candidate=candidate, selected=candidate["option_id"])
    assert ledger.path.read_bytes() == before


@pytest.mark.parametrize("mutation,reason", [
    ("scope", "invalid_or_missing_snapshot"), ("tick", "invalid_or_missing_snapshot"),
    ("position", "invalid_port_position"), ("missing_port", "anchor_missing_or_ambiguous"),
    ("duplicate_port", "anchor_missing_or_ambiguous"), ("unpowered", "anchor_unpowered_or_unknown"),
    ("missing_network", "network_unavailable"), ("bad_count", "invalid_network_stock"),
    ("fractional_count", "invalid_network_stock"), ("nonfinite_count", "invalid_network_stock"),
])
def test_network_stock_observation_keeps_unknowns_explicit(world, mutation, reason):
    _adapter, bound, ledger = world
    facts = _network_facts(bound)
    if mutation == "scope": facts["surface"] = "other"
    elif mutation == "tick": facts["tick"] = True
    elif mutation == "position": facts["ports"][0]["position"]["x"] = True
    elif mutation == "missing_port": facts["ports"] = []
    elif mutation == "duplicate_port": facts["ports"].append(copy.deepcopy(facts["ports"][0]))
    elif mutation == "unpowered": facts["ports"][0]["energy"] = 0
    elif mutation == "missing_network": facts["networks"] = {}
    elif mutation == "bad_count": facts["networks"]["1"]["items"]["pipe"] = True
    elif mutation == "fractional_count": facts["networks"]["1"]["items"]["pipe"] = 1.5
    elif mutation == "nonfinite_count": facts["networks"]["1"]["items"]["pipe"] = float("inf")
    before = ledger.path.read_bytes()
    result = integration._stock_observation(facts, bound, ledger, {"x": 3, "y": -1})
    assert result == {"status": "unavailable", "reason": reason}
    assert ledger.path.read_bytes() == before


def test_observation_identity_changes_with_snapshot_but_not_bound_geometry(world):
    adapter, bound, ledger = world
    provider = integration.optimizer_candidate_provider(bound, episode_id="episode-1", network_anchor={"x": 3, "y": -1})
    board = {"needs": [{"item": "iron-plate", "condition": "shortfall"}]}
    first_facts = _network_facts(bound)
    second_facts = copy.deepcopy(first_facts)
    second_facts["tick"] += 1
    first = provider(adapter, first_facts, board)[0]
    second = provider(adapter, second_facts, board)[0]
    assert first["option_id"] != second["option_id"]
    assert_same_build_with_stock_observation(first, offer(world))
    assert_same_build_with_stock_observation(second, offer(world))
    adapter.query = lambda *_args, **_kwargs: pytest.fail("changed candidate reached live queries")
    with pytest.raises(ValueError, match="differs"):
        integration.execute_optimizer_candidate(adapter, bound, ledger, episode_id="episode-1",
            network_anchor={"x": 3, "y": -1}, candidate=second, selected=first["option_id"])


@pytest.mark.parametrize("mutation", ["tick", "arithmetic", "competing", "own_hold", "ledger_revision",
                                      "missing_item", "extra", "boolean"])
def test_annotated_candidate_rejects_changed_or_inconsistent_observations(world, mutation):
    adapter, bound, ledger = world
    provider = integration.optimizer_candidate_provider(bound, episode_id="episode-1", network_anchor={"x": 3, "y": -1})
    candidate = provider(adapter, _network_facts(bound), {"needs": [{"item": "iron-plate", "condition": "shortfall"}]})[0]
    selected = candidate["option_id"]
    changed = copy.deepcopy(candidate)
    observation = changed["stock_observation"]
    item = next(iter(observation["items"]))
    row = observation["items"][item]
    if mutation == "tick": observation["tick"] += 1
    elif mutation == "arithmetic": row["available_after_claims"] += 1
    elif mutation == "competing": row["competing_ledger_holds"] += 1
    elif mutation == "own_hold": row["project_ledger_hold"] += 1
    elif mutation == "ledger_revision": observation["ledger_revision"] += 1
    elif mutation == "missing_item": observation["items"].pop(item)
    elif mutation == "extra": observation["authorized"] = True
    else: row["ghost_claims"] = True
    adapter.query = lambda *_args, **_kwargs: pytest.fail("tampered candidate reached live queries")
    with pytest.raises(ValueError):
        integration.execute_optimizer_candidate(adapter, bound, ledger, episode_id="episode-1",
            network_anchor={"x": 3, "y": -1}, candidate=changed, selected=selected)


def test_annotated_candidate_rejects_changed_ledger_before_queries(world):
    adapter, bound, ledger = world
    provider = integration.optimizer_candidate_provider(bound, episode_id="episode-1", network_anchor={"x": 3, "y": -1})
    candidate = provider(adapter, _network_facts(bound), {"needs": [{"item": "iron-plate", "condition": "shortfall"}]})[0]
    fresh = MaterialReservationLedger(adapter.root / "server/script-output", episode_id="episode-1", surface="nauvis", force="player")
    fresh.declare("other", {"extra": 1}, {**dict(bound.plan.required_materials), "extra": 1})
    adapter.query = lambda *_args, **_kwargs: pytest.fail("stale ledger reached live queries")
    with pytest.raises(MaterialReservationError, match="changed"):
        integration.execute_optimizer_candidate(adapter, bound, ledger, episode_id="episode-1",
            network_anchor={"x": 3, "y": -1}, candidate=candidate, selected=candidate["option_id"])


def _execute_with_stock(world, facts):
    adapter, bound, ledger = world
    provider = integration.optimizer_candidate_provider(bound, episode_id="episode-1", network_anchor={"x": 3, "y": -1})
    candidate = provider(adapter, _network_facts(bound), {"needs": [{"item": "iron-plate", "condition": "shortfall"}]})[0]
    adapter.query = lambda *_args, **_kwargs: facts
    adapter.execute = lambda *_args, **_kwargs: pytest.fail("unrevalidated stock reached executor")
    return integration.execute_optimizer_candidate(adapter, bound, ledger, episode_id="episode-1",
        network_anchor={"x": 3, "y": -1}, candidate=candidate, selected=candidate["option_id"])


def test_execution_revalidates_stock_on_a_fresh_snapshot(world):
    _adapter, bound, _ledger = world
    shorted = _network_facts(bound)
    item = next(iter(dict(bound.plan.required_materials)))
    shorted["networks"]["1"]["items"][item] -= 1
    with pytest.raises(SavedWorldError, match="stock_shortfall_at_execution:" + item):
        _execute_with_stock(world, shorted)
    claimed = _network_facts(bound)
    claimed["networks"]["1"]["reserved"][item] = 1
    with pytest.raises(SavedWorldError, match="stock_shortfall_at_execution"):
        _execute_with_stock(world, claimed)
    moved = _network_facts(bound)
    moved["ports"][0]["network"] = "2"; moved["networks"] = {"2": moved["networks"]["1"]}
    with pytest.raises(SavedWorldError, match="stock_network_changed"):
        _execute_with_stock(world, moved)
    stale = _network_facts(bound); stale["tick"] = 100
    with pytest.raises(SavedWorldError, match="stock_observation_regressed"):
        _execute_with_stock(world, stale)
    missing = _network_facts(bound); missing["ports"] = []
    with pytest.raises(SavedWorldError, match="stock_revalidation_unavailable:anchor_missing_or_ambiguous"):
        _execute_with_stock(world, missing)


def test_competing_hold_made_after_option_time_blocks_execution(world):
    adapter, bound, ledger = world
    provider = integration.optimizer_candidate_provider(bound, episode_id="episode-1", network_anchor={"x": 3, "y": -1})
    candidate = provider(adapter, _network_facts(bound), {"needs": [{"item": "iron-plate", "condition": "shortfall"}]})[0]
    # A ledger change is caught by the candidate rebuild before any world query.
    fresh = MaterialReservationLedger(adapter.root / "server/script-output", episode_id="episode-1", surface="nauvis", force="player")
    fresh.declare("rival", {"extra": 1}, {**dict(bound.plan.required_materials), "extra": 1})
    adapter.query = lambda *_args, **_kwargs: pytest.fail("changed holds reached live queries")
    with pytest.raises(MaterialReservationError, match="changed"):
        integration.execute_optimizer_candidate(adapter, bound, ledger, episode_id="episode-1",
            network_anchor={"x": 3, "y": -1}, candidate=candidate, selected=candidate["option_id"])


def test_bound_provider_keeps_unlinked_proposal_observable(world):
    adapter, bound, _ledger = world
    provider = integration.optimizer_candidate_provider(bound, episode_id="episode-1", network_anchor={"x": 3, "y": -1})
    proposals = provider(adapter, {}, {"needs": []})
    assert provider.observe_unlinked is True
    assert len(proposals) == 1 and proposals[0]["product"] == bound.plan.throughput_output
    assert proposals[0]["stock_observation"]["status"] == "unavailable"
    assert provider(adapter, {}, {"needs": [{"item": bound.plan.throughput_output, "condition": "satisfied"}]}) == []
