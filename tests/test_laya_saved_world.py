# Path: tests/test_laya_saved_world.py | Purpose: Protect saved-world isolation, funding representation and exact model/action boundaries.

import copy
import json
from pathlib import Path

import pytest

from training.laya_input_profile import input_profile, require_input_profile
from training.laya_saved_world import (
    PROFILE, SavedWorldAdapter, SavedWorldError, bounded_menu, compile_candidates,
    decision_state, measured_outcome, owned_root,
    submission_plan, configuration_actions,
)


def facts():
    return {"tick": 100, "ports": [{"position": {"x": 3, "y": -1}, "network": "1", "energy": 100,
            "construction_radius": 55, "logistic_radius": 25}], "poles": [{"generated": True, "position": {"x": 0, "y": 0}, "wire": 18}],
            "networks": {"1": {"items": {"electric-furnace": 3, "inserter": 4},
                                  "reserved": {"electric-furnace": 2}, "construction_robots": 50}},
            "machines": [], "pending_ghosts": 2, "resources": [],
            "production": {"iron-plate": 20}, "consumption": {"iron-ore": 20},
            "recipes": {"iron-gear-wheel": {"ingredients": [{"name": "iron-plate", "amount": 2}], "product_amount": 1, "energy_ticks": 30}}}


def test_saved_world_profile_cannot_reuse_a_lab_checkpoint_or_unrecorded_override():
    state = decision_state(facts(), [{"option_id": "wait.300", "family": "wait", "summary": "Wait", "wait_ticks": 300}],
                           "test", {"model_id": "test", "model_hash": "sha256:" + "0" * 64}, "Produce plates")
    assert state["state_digest"]["stock"]["accessible"] == 5
    assert state["state_digest"]["resource"]["remaining"] is None
    assert input_profile(state) == PROFILE
    with pytest.raises(ValueError, match="not trained"):
        require_input_profile({"input_profiles": ["workflow-health-v1"]}, state, allow_unrecorded=True)
    with pytest.raises(ValueError, match="no recorded saved-world"):
        require_input_profile({}, state, allow_unrecorded=True)
    assert require_input_profile({"input_profiles": [PROFILE]}, state) == PROFILE
    state["observation_digest"]["work_branches"] = {}
    with pytest.raises(ValueError, match="disguised"):
        input_profile(state)


@pytest.mark.parametrize("path", ["/tmp/test-world", "/home/djsm/.local/share/factorio-rl/deterministic",
                                  "data/laya-saved-world/../../saves", "data/laya-saved-world"])
def test_root_guard_rejects_non_owned_runtime_and_traversal(path):
    with pytest.raises(SavedWorldError, match="dedicated children"):
        owned_root(Path(path))


def test_small_menu_retains_all_legal_families_and_seeded_site_diversity():
    candidates = [{"family": family, "option_id": f"{family}.{i}"}
                  for family, count in [("mine", 50), ("smelt", 4), ("expand", 2), ("assemble", 8)]
                  for i in range(count)]
    menu = bounded_menu(candidates, 12)
    assert {c["family"] for c in menu} == {"mine", "smelt", "expand", "assemble"}
    assert len(menu) == len({c["option_id"] for c in menu}) == 10
    assert menu == bounded_menu(candidates, 12)
    assert menu != bounded_menu(candidates, 13)


def test_compiler_produces_ghost_intents_with_horizontal_seed_and_long_arm_expansion():
    candidates = compile_candidates(facts())
    assert {c["family"] for c in candidates} == {"expand", "smelt", "assemble"}
    for candidate in candidates:
        for phase in candidate["plan"]["phases"]:
            for action in phase["actions"]:
                assert action["action_type"] == "place_ghost"
    assembly = [c for c in candidates if c["family"] == "assemble"]
    assert any(any(a["entity"] == "long-handed-inserter" for a in c["plan"]["phases"][0]["actions"]) for c in assembly)
    assert any(not any(a["entity"] == "long-handed-inserter" for a in c["plan"]["phases"][0]["actions"]) for c in assembly)


def test_measured_flow_uses_engine_counters_not_inventory_or_elapsed_wall_time():
    before = facts()
    after = copy.deepcopy(before)
    after["tick"] += 600
    after["production"]["iron-plate"] += 7
    after["consumption"]["iron-ore"] += 9
    after["networks"]["1"]["items"]["iron-plate"] = 999  # Transfer is not production.
    result = measured_outcome(before, after)
    assert result["production"]["iron-plate"] == {"count": 7, "per_second": .7}
    assert result["consumption"]["iron-ore"] == {"count": 9, "per_second": .9}
    after["production"]["iron-plate"] = 0
    with pytest.raises(SavedWorldError, match="counters reset"):
        measured_outcome(before, after)
    with pytest.raises(SavedWorldError, match="positive"):
        measured_outcome(before, before)


@pytest.mark.parametrize("mutation", ["surface", "force", "direct", "grant", "unknown"])
def test_execute_refuses_scope_substitution_and_unpresented_or_direct_actions(mutation):
    adapter = object.__new__(SavedWorldAdapter)
    candidate = compile_candidates(facts())[0]
    selected = candidate["option_id"]
    if mutation in {"surface", "force"}:
        candidate["plan"][mutation] = "real-other"
    elif mutation == "direct":
        candidate["plan"]["phases"][0]["actions"][0]["action_type"] = "place_entity"
    elif mutation == "grant":
        candidate["plan"]["phases"][0]["actions"][0]["infinity_filter"] = "iron-plate"
    else:
        selected = "absent"
    adapter.query = lambda *a, **kw: pytest.fail("invalid selection reached live queries")
    with pytest.raises(SavedWorldError):
        adapter.execute(selected, [candidate])


def test_fresh_funding_rejection_never_reaches_executor(tmp_path):
    adapter = object.__new__(SavedWorldAdapter)
    adapter.root = tmp_path
    candidate = compile_candidates(facts())[0]
    adapter.query = lambda candidates=None: ({"validations": {candidate["option_id"]: {"reason": "item_shortfall:roboport"}}}
                                             if candidates else facts())
    class Bridge:
        def build_layout(self, *a, **kw):
            pytest.fail("unfunded plan reached executor")
    adapter.bridge = Bridge()
    with pytest.raises(SavedWorldError, match="became illegal"):
        adapter.execute(candidate["option_id"], [candidate])
    assert json.loads(next((tmp_path / "evidence").glob("transaction-*.json")).read_text())["status"] == "rejected"


def test_interrupted_submission_is_not_automatically_replayed(tmp_path):
    adapter = object.__new__(SavedWorldAdapter)
    adapter.root = tmp_path
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence/transaction-test.json").write_text('{"status":"pending"}')
    with pytest.raises(SavedWorldError, match="needs review"):
        adapter.reconcile()


@pytest.mark.parametrize("lost_response", [False, True])
def test_correlated_submission_is_journaled_and_never_replayed_during_recovery(tmp_path, lost_response):
    adapter = object.__new__(SavedWorldAdapter)
    adapter.root = tmp_path
    candidate = compile_candidates(facts())[0]
    option_id = candidate["option_id"]
    adapter.validate_plan = lambda _plan: None
    adapter.query = lambda candidates=None, inspect=None: (
        {"validations": {option_id: {"reason": None}}} if candidates else facts())
    def stop_after_submission(_ticks):
        raise RuntimeError("test interruption")
    adapter.advance = stop_after_submission
    class Bridge:
        def __init__(self): self.calls = 0; self.report = None
        def optimizer_build_evidence(self, *, surface, force, episode_id, project_id, transaction_id, plan_hash,
                                     timeout, include_unidentified):
            assert include_unidentified is True
            tags = {"optimizer_episode": episode_id, "optimizer_project": project_id,
                    "optimizer_transaction": transaction_id, "optimizer_plan_hash": plan_hash}
            return {"version": "optimizer-build-evidence-v3", "source": "robot_built_entity_tags",
                    "request_id": "baseline", "tick": 100, "ok": True, "ownership_claimed": False,
                    "dispatch_authorized": False, "surface": surface, "force": force, "tags": tags, "entities": []}
        def build_layout_correlated(self, authorization, plan, *, request_id, stream_id, sequence, timeout):
            self.calls += 1
            transaction = next((tmp_path / "evidence").glob("transaction-*.json"))
            durable = json.loads(transaction.read_text())
            assert durable["status"] == "submitting" and durable["layout_request"]["request_id"] == request_id
            assert durable["build_evidence_baseline"]["entities"] == []
            self.report = {"ok": True, "request_id": request_id}
            if lost_response: raise TimeoutError("lost response")
            return self.report
        def read_layout_receipt(self, authorization, plan, *, request_id, stream_id, sequence):
            assert request_id == self.report["request_id"]
            return self.report
    adapter.bridge = Bridge()
    with pytest.raises(TimeoutError if lost_response else RuntimeError):
        adapter.execute(option_id, [candidate])
    transaction = next((tmp_path / "evidence").glob("transaction-*.json"))
    record = json.loads(transaction.read_text())
    assert record["status"] == ("submitting" if lost_response else "submission_received")
    with pytest.raises(SavedWorldError, match="needs review"):
        adapter.reconcile()
    assert adapter.bridge.calls == 1
    record = json.loads(transaction.read_text())
    assert record["status"] == ("submission_recovered" if lost_response else "submission_received")
    assert json.loads((tmp_path / "evidence/layout-stream.json").read_text())["pending"] is None


@pytest.mark.parametrize("missing_evidence", [False, True])
def test_built_configuration_requires_matching_robot_units(tmp_path, missing_evidence):
    adapter = object.__new__(SavedWorldAdapter)
    adapter.root = tmp_path
    candidate = next(c for c in compile_candidates(facts()) if c["family"] == "assemble")
    option_id = candidate["option_id"]
    tick = [100]
    def query(candidates=None, inspect=None):
        if candidates: return {"validations": {option_id: {"reason": None}}}
        if inspect: return {"built": 10, "pending": 0, "missing": 0}
        value = copy.deepcopy(facts()); value["tick"] = tick[0]; return value
    adapter.query = query
    adapter.validate_plan = lambda _plan: None
    adapter.advance = lambda ticks: tick.__setitem__(0, tick[0] + ticks)
    class Bridge:
        def __init__(self): self.ghosts = None; self.configs = None
        def build_layout_correlated(self, _authorization, plan, **_kwargs):
            action = plan["phases"][0]["actions"][0]
            if action["action_type"] == "configure_entity_if_unit_number":
                self.configs = plan
                record = json.loads(next((tmp_path / "evidence").glob("transaction-*.json")).read_text())
                assert record["status"] == "configuration_submitting"
                assert record["configuration_request"]["sequence"] == 2
            else:
                self.ghosts = plan
            return {"ok": True, "tick": 100}
        def optimizer_build_evidence(self, *, surface, force, episode_id, project_id, transaction_id, plan_hash, timeout, include_unidentified):
            assert include_unidentified is True
            tags = {"optimizer_episode": episode_id, "optimizer_project": project_id,
                    "optimizer_transaction": transaction_id, "optimizer_plan_hash": plan_hash}
            tagged = ([] if self.ghosts is None else
                      [a for p in self.ghosts["phases"] for a in p["actions"] if a.get("optimizer_ownership")])
            entities = [] if missing_evidence or self.ghosts is None else [{"entity": a["entity"], "position": a["position"],
                "unit_number": i + 1, "tick": 101, "current": {"state": "present",
                "direction": {"state": "available", "value": 0}, "quality": {"state": "available", "value": "normal"},
                "recipe": {"state": "unset"}}} for i, a in enumerate(tagged)]
            return {"version": "optimizer-build-evidence-v3", "source": "robot_built_entity_tags", "request_id": "req",
                    "tick": tick[0], "ok": True, "ownership_claimed": False, "dispatch_authorized": False,
                    "surface": surface, "force": force, "tags": tags, "entities": entities}
    adapter.bridge = Bridge()
    if missing_evidence:
        with pytest.raises(SavedWorldError, match="lacks current correlated"):
            adapter.execute(option_id, [candidate], measure_ticks=600)
        assert adapter.bridge.configs is None
    else:
        receipt = adapter.execute(option_id, [candidate], measure_ticks=600)
        assert receipt["status"] == "observed"
        configured = [a for phase in adapter.bridge.configs["phases"] for a in phase["actions"]]
        assert configured and all(a["action_type"] == "configure_entity_if_unit_number" and a["expected_unit_number"] > 0
                                  for a in configured)
    assert all(a["action_type"] == "place_ghost_no_reconcile" for p in adapter.bridge.ghosts["phases"] for a in p["actions"])


def _preexisting_adapter(tmp_path, standing, ghost=()):
    """Adapter whose world already holds `standing` entities (name -> unit number) of the candidate plan."""
    adapter = object.__new__(SavedWorldAdapter)
    adapter.root = tmp_path
    candidate = next(c for c in compile_candidates(facts()) if c["family"] == "assemble")
    option_id = candidate["option_id"]
    tick = [100]
    def query(candidates=None, inspect=None):
        if candidates: return {"validations": {option_id: {"reason": None}}}
        if inspect:
            targets = [{"entity": a["entity"], "position": a["position"],
                        "state": "built" if a["entity"] in standing else "ghost" if a["entity"] in ghost else "missing",
                        **({"unit_number": standing[a["entity"]]} if a["entity"] in standing else {})}
                       for a in (x for ph in inspect["phases"] for x in ph["actions"])]
            return {"built": len(standing), "pending": 0, "missing": 0, "targets": targets}
        value = copy.deepcopy(facts()); value["tick"] = tick[0]; return value
    adapter.query = query
    adapter.validate_plan = lambda _plan: None
    adapter.advance = lambda ticks: tick.__setitem__(0, tick[0] + ticks)
    class Bridge:
        def __init__(self): self.ghosts = None; self.configs = None
        def build_layout_correlated(self, _authorization, plan, **_kwargs):
            if plan["phases"][0]["actions"][0]["action_type"] == "configure_entity_if_unit_number":
                self.configs = plan
            else:
                self.ghosts = plan
            return {"ok": True, "tick": 100}
        def optimizer_build_evidence(self, *, surface, force, episode_id, project_id, transaction_id, plan_hash, **_kw):
            tags = {"optimizer_episode": episode_id, "optimizer_project": project_id,
                    "optimizer_transaction": transaction_id, "optimizer_plan_hash": plan_hash}
            tagged = ([] if self.ghosts is None else
                      [a for p in self.ghosts["phases"] for a in p["actions"] if a.get("optimizer_ownership")])
            entities = [{"entity": a["entity"], "position": a["position"], "unit_number": 500 + i, "tick": 101,
                         "current": {"state": "present", "direction": {"state": "available", "value": 0},
                                     "quality": {"state": "available", "value": "normal"},
                                     "recipe": {"state": "unset"}}} for i, a in enumerate(tagged)]
            return {"version": "optimizer-build-evidence-v3", "source": "robot_built_entity_tags", "request_id": "req",
                    "tick": tick[0], "ok": True, "ownership_claimed": False, "dispatch_authorized": False,
                    "surface": surface, "force": force, "tags": tags, "entities": entities}
    adapter.bridge = Bridge()
    return adapter, candidate, option_id


@pytest.mark.parametrize("standing_names", [
    ["passive-provider-chest", "requester-chest"],  # second quad slot sharing the chest column
    ["assembling-machine-1"],                        # slot retargeted: existing assembler gets a new recipe
])
def test_preexisting_targets_are_untagged_and_configured_by_their_unit_number(tmp_path, standing_names):
    standing = {name: 70 + i for i, name in enumerate(standing_names)}
    adapter, candidate, option_id = _preexisting_adapter(tmp_path, standing)
    receipt = adapter.execute(option_id, [candidate], measure_ticks=600)
    assert receipt["status"] == "observed"
    submitted = [a for p in adapter.bridge.ghosts["phases"] for a in p["actions"]]
    # Standing entities stay in the request (the executor verifies them) but carry no ownership claim.
    assert {a["entity"] for a in submitted if not a.get("optimizer_ownership")} >= set(standing)
    assert all(not a.get("optimizer_ownership") for a in submitted if a["entity"] in standing)
    assert all(a.get("optimizer_ownership") for a in submitted
               if a["entity"] not in standing and a["entity"] in {"passive-provider-chest", "assembling-machine-1", "requester-chest"})
    configured = {a["entity"]: a for p in adapter.bridge.configs["phases"] for a in p["actions"]}
    assert set(configured) == {"passive-provider-chest", "assembling-machine-1", "requester-chest"}
    assert all(a["action_type"] == "configure_entity_if_unit_number" for a in configured.values())
    for name, unit in standing.items():
        assert configured[name]["expected_unit_number"] == unit
    for name in set(configured) - set(standing):
        assert configured[name]["expected_unit_number"] >= 500  # robot-built, correlated to this transaction
    # The shared chest keeps the group-keyed section request that preserves the earlier slot's requests.
    assert configured["requester-chest"]["logistic_sections"][0]["group"].startswith("laya-quad:")
    journal = {(t["entity"], t["unit_number"]) for t in receipt["preexisting_targets"]}
    assert journal == set(standing.items())


def test_nothing_preexisting_keeps_every_configuration_target_tagged(tmp_path):
    adapter, candidate, option_id = _preexisting_adapter(tmp_path, {})
    receipt = adapter.execute(option_id, [candidate], measure_ticks=600)
    assert receipt["preexisting_targets"] == []
    tagged = {a["entity"] for p in adapter.bridge.ghosts["phases"] for a in p["actions"] if a.get("optimizer_ownership")}
    assert tagged == {"passive-provider-chest", "assembling-machine-1", "requester-chest"}


def test_unowned_pending_ghost_target_is_refused_before_submission(tmp_path):
    adapter, candidate, option_id = _preexisting_adapter(tmp_path, {}, ghost={"requester-chest"})
    with pytest.raises(SavedWorldError, match="unowned pending ghost"):
        adapter.execute(option_id, [candidate], measure_ticks=600)
    assert adapter.bridge.ghosts is None
    record = json.loads(next((tmp_path / "evidence").glob("transaction-*.json")).read_text())
    assert record["status"] == "rejected" and record["reason"] == "configuration_target_unowned_ghost"


@pytest.mark.parametrize("outcome", ["lost_response", "after_response", "journal_failure", "rejected"])
def test_configuration_interruptions_are_correlated_and_never_resent(tmp_path, outcome, monkeypatch):
    adapter = object.__new__(SavedWorldAdapter)
    adapter.root = tmp_path
    candidate = next(c for c in compile_candidates(facts()) if c["family"] == "assemble")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    transaction = evidence / f"transaction-{'a' * 32}.json"
    transaction.write_text(json.dumps({"status": "construction_pending", "candidate": candidate,
        "configuration_protocol": "correlated-v1", "submission": {"ok": True, "tick": 100}}))
    adapter.query = lambda inspect=None: {"pending": 0, "missing": 0} if inspect else {"tick": 300}
    action = {"action_type": "configure_entity_if_unit_number", "entity": "assembling-machine-1",
              "position": {"x": 0, "y": 0}, "expected_unit_number": 7, "recipe": "iron-gear-wheel"}
    adapter._guarded_configuration = lambda *_args: [action]
    class Bridge:
        def __init__(self): self.calls = 0; self.report = None
        def build_layout_correlated(self, _authorization, plan, *, request_id, stream_id, sequence, timeout):
            self.calls += 1
            record = json.loads(transaction.read_text())
            assert record["status"] == "configuration_submitting"
            assert record["configuration_request"]["request_id"] == request_id
            self.report = {"ok": outcome != "rejected", "request_id": request_id}
            if outcome == "lost_response": raise TimeoutError("response lost after command")
            return self.report
        def read_layout_receipt(self, _authorization, _plan, *, request_id, stream_id, sequence):
            assert self.report["request_id"] == request_id
            return self.report
    adapter.bridge = Bridge()
    if outcome == "after_response":
        adapter.query = lambda inspect=None: ({"pending": 0, "missing": 0} if inspect else
                                             (_ for _ in ()).throw(RuntimeError("interrupted after response")))
    with monkeypatch.context() as patcher:
        if outcome == "journal_failure":
            from training import laya_saved_world as module
            real_atomic_json = module.atomic_json
            def fail_receipt_save(path, value):
                if value.get("status") == "configuration_received":
                    raise RuntimeError("configuration receipt write failed")
                return real_atomic_json(path, value)
            patcher.setattr(module, "atomic_json", fail_receipt_save)
        with pytest.raises((TimeoutError, RuntimeError)):
            adapter.reconcile()
    with pytest.raises(SavedWorldError, match="configuration outcome needs review"):
        adapter.reconcile()
    assert adapter.bridge.calls == 1
    record = json.loads(transaction.read_text())
    assert record["status"] == {"lost_response": "configuration_recovered",
                               "after_response": "configuration_received",
                               "journal_failure": "configuration_recovered",
                               "rejected": "configuration_failed"}[outcome]
    assert json.loads((evidence / "layout-stream.json").read_text())["pending"] is None


def test_legacy_pending_configuration_requires_review_before_replay(tmp_path):
    adapter = object.__new__(SavedWorldAdapter)
    adapter.root = tmp_path
    candidate = next(c for c in compile_candidates(facts()) if c["family"] == "assemble")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / f"transaction-{'a' * 32}.json").write_text(json.dumps(
        {"status": "construction_pending", "candidate": candidate, "submission": {"ok": True, "tick": 100}}))
    adapter.query = lambda inspect=None: {"pending": 0, "missing": 0}
    adapter._guarded_configuration = lambda *_args: pytest.fail("legacy transaction reached configuration")
    with pytest.raises(SavedWorldError, match="legacy configuration outcome needs review"):
        adapter.reconcile()


def test_changed_live_bill_rejects_before_submission(tmp_path):
    adapter = object.__new__(SavedWorldAdapter)
    adapter.root = tmp_path
    candidate = compile_candidates(facts())[0]
    candidate["bill"] = {"inserter": 1}
    adapter.validate_plan = lambda _plan: None
    adapter.query = lambda candidates=None: ({"validations": {candidate["option_id"]: {"bill": {"inserter": 2}}}}
                                             if candidates else facts())
    adapter.bridge = object()
    with pytest.raises(SavedWorldError, match="bill changed"):
        adapter.execute(candidate["option_id"], [candidate])
    assert json.loads(next((tmp_path / "evidence").glob("transaction-*.json")).read_text())["status"] == "rejected"


def test_cli_profile_gate_runs_before_connection_or_pending_configuration(monkeypatch, tmp_path):
    from tools import run_laya_saved_world
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    (checkpoint / "rl_agent_config.json").write_text('{"input_profiles":["workflow-health-v1"]}')
    def forbidden(*args, **kwargs):
        pytest.fail("incompatible checkpoint reached live adapter")
    monkeypatch.setattr(run_laya_saved_world, "SavedWorldAdapter", forbidden)
    with pytest.raises(ValueError, match="not trained"):
        run_laya_saved_world.main(["run", "--root", "data/laya-saved-world/profile-gate-test",
                                  "--checkpoint", str(checkpoint)])


def test_measured_feedback_enters_next_policy_state_without_inventing_capacity():
    before = facts()
    after = copy.deepcopy(before)
    after["tick"] += 600
    after["production"]["iron-plate"] += 7
    flow = measured_outcome(before, after)
    state = decision_state(after, [{"option_id": "wait.300", "family": "wait", "summary": "Wait", "wait_ticks": 300}],
                           "test", {"model_id": "test", "model_hash": "sha256:" + "0" * 64}, "Produce plates", measurement=flow)
    feedback = state["observation_digest"]["saved_world"]["measured_flow"]
    assert feedback["production_per_second"]["iron-plate"] == .7
    assert feedback["window_ticks"] == 600
    assert state["state_digest"]["production"]["rate_target"] is None


def test_quad_chest_inventory_settings_are_deferred_until_bot_construction():
    candidate = next(c for c in compile_candidates(facts()) if c["family"] == "assemble")
    original = copy.deepcopy(candidate["plan"])
    ghosts = submission_plan(original)
    assert all(set(a) <= {"action_type", "entity", "position", "direction"}
               for p in ghosts["phases"] for a in p["actions"])
    configs = configuration_actions(original)
    assert any(a.get("clear_inventory_limit") for a in configs)
    assert any(a.get("logistic_sections") for a in configs)
    assert any(a.get("recipe") for a in configs)
    assert all(a["action_type"] == "configure_entity" for a in configs)
    assert original == candidate["plan"]


_SURVEY_LUA_FIXTURE = r"""
local function recipe(name, ingredients, products, opts)
  opts = opts or {}
  return {enabled=opts.enabled ~= false, ingredients=ingredients or {},
    products=products or {}, energy=opts.energy or .5,
    categories=opts.categories or {'crafting'}, productivity_bonus=opts.productivity_bonus or 0}
end
local function item(name, amount) return {name=name,type="item",amount=amount} end
local function fluid(name, amount) return {name=name,type="fluid",amount=amount} end
request={extra_recipes=__EXTRA_RECIPES__, bounds={x_min=0,x_max_exclusive=0,y_min=0,y_max_exclusive=0}}
local recipes={
  ['iron-plate']=recipe('iron-plate',{item('iron-ore',1)},{item('iron-plate',1)}),
  ['electric-furnace']=recipe('electric-furnace',{item('advanced-circuit',1),item('steel-plate',1)},{item('electric-furnace',1)}),
  ['advanced-circuit']=recipe('advanced-circuit',{item('plastic-bar',2),item('copper-cable',4),item('electronic-circuit',2)},{item('advanced-circuit',1)}),
  ['electronic-circuit']=recipe('electronic-circuit',{item('iron-plate',1)},{item('electronic-circuit',1)}),
  ['plastic-bar']=recipe('plastic-bar',{fluid('petroleum-gas',20),item('coal',1)},{item('plastic-bar',2)}),
  ['automation-science-pack']=recipe('automation-science-pack',{item('iron-gear-wheel',1),item('copper-plate',1)},{item('automation-science-pack',1)}),
  ['disabled-thing']=recipe('disabled-thing',{item('iron-plate',1)},{item('disabled-thing',1)},{enabled=false}),
  ['pumpjack']=recipe('pumpjack',{item('steel-plate',1),item('automation-science-pack',1)},{item('pumpjack',1)}),
__MALFORMED_RECIPES__
}
local recipe_count=0
for _ in pairs(recipes) do recipe_count=recipe_count+1 end
local production={['automation-science-pack']=42,['plastic-bar']=9,['advanced-circuit']=4}
local consumption={['automation-science-pack']=7,['plastic-bar']=2,['advanced-circuit']=1}
local stats={get_input_count=function(name) return production[name] or 0 end,
  get_output_count=function(name) return consumption[name] or 0 end}
local surface={find_entities_filtered=function() return {} end}
game={tick=100,tick_paused=true,ticks_to_run=0,surfaces={nauvis=surface},players={},
  forces={player={name='player',recipes=recipes,technologies={},
    get_item_production_statistics=function() return stats end}}}
defines={entity_status={},inventory={chest=1},direction={north=0,west=4,east=8,south=12}}
script={active_mods={base='2.1.23'}}
prototypes={
  item={['iron-plate']={},['iron-ore']={},['advanced-circuit']={},['steel-plate']={},
    ['plastic-bar']={},['electronic-circuit']={},['coal']={},['automation-science-pack']={},
    ['copper-plate']={},['disabled-thing']={},['electric-furnace']={},['pumpjack']={}},
  entity={
    ['assembling-machine-1']={type='assembling-machine',ingredient_count=3,
      crafting_categories={crafting=true},get_crafting_speed=function() return .5 end},
    ['electric-furnace']={type='furnace',ingredient_count=2,
      crafting_categories={crafting=true},get_crafting_speed=function() return 2 end},
  }}
"""


def _run_survey_lua(extra_recipes=(), assertions="", malformed="", setup=""):
    import shutil
    import subprocess
    lua = shutil.which("lua")
    if lua is None:
        pytest.skip("Lua interpreter required for saved-world survey tests")
    requested = "{" + ",".join(json.dumps(name) for name in extra_recipes) + "}"
    fixture = (_SURVEY_LUA_FIXTURE.replace("__EXTRA_RECIPES__", requested)
               .replace("__MALFORMED_RECIPES__", malformed))
    source = Path("training/laya_saved_world.lua").read_text()
    body = fixture + "\n" + setup + "\nlocal facts=(function()\n" + source + "\nend)()\n" + assertions
    result = subprocess.run([lua, "-"], input=body, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.lua
def test_fluid_prototype_survey_reports_capability_and_unknowns_without_authorizing():
    _run_survey_lua(setup="""
prototypes.entity['empty']={type='container',fluidbox_prototypes={}}
prototypes.entity['fluid']={type='pipe',fluidbox_prototypes={[3]={}}}
prototypes.entity['missing']={type='container'}
prototypes.entity['bad']={type='container',fluidbox_prototypes='unsupported'}
prototypes.entity['throws']=setmetatable({type='container'}, {__index=function(_, key)
  if key=='fluidbox_prototypes' then error('unavailable') end end})
request.candidates={{option_id='check',required_checks={'fluid_purity'},plan={phases={{actions={
  {entity='empty'},{entity='fluid'},{entity='missing'},{entity='bad'},{entity='throws'},
  {entity='absent'} }}}}},
  {option_id='ordinary',plan={phases={{actions={{entity='empty'}}}}}}}
""", assertions="""
local result=facts.validations.check
assert(result.reason=='no_construction_network')
local survey=result.fluid_prototype_survey
assert(survey.actions==6 and survey.without_fluidboxes==1 and survey.with_fluidboxes==1 and survey.unknown==4)
assert(facts.validations.ordinary.fluid_prototype_survey==nil)
""")


@pytest.mark.lua
def test_underground_survey_reports_existing_and_ghost_competitors_and_unknowns():
    _run_survey_lua(setup="""
surface.find_entities_filtered=function(filter)
  if filter.name=='pipe-to-ground' then return {
    {name='pipe-to-ground',position={x=3.5,y=.5},direction=4},
    {name='pipe-to-ground',position={x=4.5,y=.5},direction=12},
    {name='pipe-to-ground',position={x=11.5,y=.5},direction=4}}
  end
  if filter.type=='entity-ghost' and filter.area then return {
    {ghost_name='pipe-to-ground',position={x=5.5,y=.5},direction=4},
    {ghost_name='pipe-to-ground',position={x=6.5,y=.5}},
    {ghost_name='transport-belt',position={x=7.5,y=.5},direction=4}}
  end
  return {}
end
request.candidates={{option_id='pipes',required_checks={'fluid_purity'},plan={phases={{actions={
  {entity='pipe-to-ground',position={x=.5,y=.5},direction='west'} }}}}}}
""", assertions="""
local result=facts.validations.pipes
assert(result.reason=='no_construction_network')
local survey=result.underground_competitors
assert(survey.planned==1 and survey.conflicts==2 and survey.unknown==1 and survey.complete==true)
""")


@pytest.mark.lua
def test_underground_survey_fails_closed_on_missing_scan_and_limit():
    _run_survey_lua(setup="""
surface.find_entities_filtered=function(filter)
  if filter.name=='pipe-to-ground' and filter.area and filter.area[1][1]>=10 then
    local truncated={}
    for i=1,4097 do truncated[i]={name='pipe-to-ground'} end
    return truncated
  end
  if filter.name=='pipe-to-ground' then error('scan unavailable') end
  return {}
end
local one={entity='pipe-to-ground',position={x=.5,y=.5},direction='west'}
local many={}
for i=1,257 do many[i]=one end
request.candidates={
  {option_id='missing',required_checks={'fluid_purity'},plan={phases={{actions={one}}}}},
  {option_id='limited',required_checks={'fluid_purity'},plan={phases={{actions=many}}}},
  {option_id='truncated',required_checks={'fluid_purity'},plan={phases={{actions={
    {entity='pipe-to-ground',position={x=25.5,y=.5},direction='west'}}}}}}}
""", assertions="""
local missing=facts.validations.missing.underground_competitors
assert(missing.planned==1 and missing.unknown==1 and missing.conflicts==0)
local limited=facts.validations.limited.underground_competitors
assert(limited.planned==257 and limited.complete==false and limited.unknown==256)
assert(facts.validations.truncated.underground_competitors.complete==false)
""")


@pytest.mark.lua
def test_surface_pipe_survey_reports_adjacent_and_overlapping_pipes_and_ghosts():
    _run_survey_lua(setup="""
surface.find_entities_filtered=function(filter)
  if type(filter.name)=='table' and filter.area then return {
    {name='pipe',position={x=1.5,y=.5}},
    {name='pipe-to-ground',position={x=1.5,y=1.5}},
    {name='pipe',position={x=.5,y=.5}},
    {name='pipe',position={x=2,y=.5}}}
  end
  if filter.type=='entity-ghost' and filter.area then return {
    {ghost_name='pipe-to-ground',position={x=.5,y=1.5}},
    {ghost_name='transport-belt',position={x=-.5,y=.5}}}
  end
  return {}
end
request.candidates={{option_id='surface',required_checks={'fluid_purity'},plan={phases={{actions={
  {entity='pipe',position={x=.5,y=.5}}}}}}}}
""", assertions="""
local survey=facts.validations.surface.surface_pipe_contacts
assert(survey.planned==1 and survey.contacts==3 and survey.unknown==1 and survey.complete==true)
assert(facts.validations.surface.reason=='no_construction_network')
""")


@pytest.mark.lua
def test_surface_pipe_survey_marks_unreadable_and_truncated_scans_unverified():
    _run_survey_lua(setup="""
surface.find_entities_filtered=function(filter)
  if type(filter.name)=='table' and filter.area then
    if filter.area[1][1] < 0 then error('scan unavailable') end
    local truncated={}
    for i=1,4097 do truncated[i]={name='pipe'} end
    return truncated
  end
  return {}
end
local function candidate(id,x)
  return {option_id=id,required_checks={'fluid_purity'},plan={phases={{actions={
    {entity='pipe',position={x=x,y=.5}}}}}}}
end
local many={}
for i=1,2049 do many[i]={entity='pipe',position={x=.5,y=.5}} end
request.candidates={candidate('missing',.5),candidate('truncated',10.5),
  {option_id='limited',required_checks={'fluid_purity'},plan={phases={{actions=many}}}}}
""", assertions="""
local missing=facts.validations.missing.surface_pipe_contacts
assert(missing.planned==1 and missing.unknown==1 and missing.contacts==0)
assert(facts.validations.truncated.surface_pipe_contacts.complete==false)
assert(facts.validations.limited.surface_pipe_contacts.planned==2049)
assert(facts.validations.limited.surface_pipe_contacts.complete==false)
""")


@pytest.mark.lua
def test_underground_competitor_rejects_a_funded_covered_candidate():
    _run_survey_lua(setup="""
request.bounds={x_min=-32,y_min=-32,x_max_exclusive=32,y_max_exclusive=32}
local net={network_id=1,all_construction_robots=2,available_construction_robots=2,all_logistic_robots=0}
local port={name='roboport',type='roboport',position={x=0,y=0},energy=100,logistic_network=net,
  prototype={construction_radius=20,logistic_radius=10}}
local storage={prototype={logistic_mode='storage'},position={x=0,y=0},
  get_inventory=function() return {get_contents=function() return {['pipe-to-ground']=3} end} end}
surface.find_logistic_network_by_position=function() return net end
surface.can_place_entity=function() return true end
surface.find_entities_filtered=function(filter)
  if type(filter.name)=='table' and filter.area and filter.area[1][1] > 5 then return {{name='pipe'}} end
  if type(filter.name)=='table' and filter.area and filter.area[1][1] < -10 then return {
    {name='pipe',position={x=-9.5,y=.5}}} end
  if filter.name=='pipe-to-ground' and filter.area and filter.area[1][1]>=0 then return {
    {name='pipe-to-ground',position={x=12.5,y=.5}}} end
  if filter.name=='pipe-to-ground' and filter.area then return {
    {name='pipe-to-ground',position={x=3.5,y=.5},direction=4}} end
  if filter.type=='logistic-container' then return {storage} end
  if filter.type then return {} end
  if filter.position then return {} end
  return {port}
end
prototypes.entity['pipe-to-ground']={type='pipe-to-ground',fluidbox_prototypes={{}},
  items_to_place_this={{name='pipe-to-ground',count=1}}}
local function candidate(id,direction,x)
  return {option_id=id,network_anchor={x=0,y=0},required_checks={'fluid_purity'},
    plan={phases={{actions={{action_type='place_ghost',entity='pipe-to-ground',
      position={x=x or .5,y=.5},direction=direction}}}}}}
end
request.candidates={candidate('conflict','west'),candidate('clear','east'),candidate('unknown','west',10.5),
                    candidate('surface','east',-10.5),candidate('surface_unknown','east',8.5)}
""", assertions="""
assert(facts.validations.conflict.reason=='underground_world_competitor')
assert(facts.validations.conflict.underground_competitors.conflicts==1)
assert(facts.validations.clear.reason==nil and facts.validations.clear.bill['pipe-to-ground']==1)
assert(facts.validations.clear.underground_competitors.conflicts==0)
assert(facts.validations.unknown.reason=='underground_world_unverified')
assert(facts.validations.surface.reason=='surface_pipe_world_contact')
assert(facts.validations.surface_unknown.reason=='surface_pipe_world_unverified')
""")


@pytest.mark.lua
def test_survey_inventories_every_force_recipe_and_item_product_counter():
    _run_survey_lua(assertions="""
assert(facts.recipe_catalog.version == '1.2.0')
assert(facts.recipe_catalog.force == 'player' and facts.recipe_catalog.surface == 'nauvis')
assert(facts.recipe_catalog.tick == facts.tick and facts.recipe_catalog.tick == 100)
assert(facts.recipe_catalog.source == 'force.recipes' and facts.recipe_catalog.complete == true)
assert(facts.recipe_catalog.document == 'saved-world-recipe-survey')
assert(facts.recipe_catalog.metadata_only == true)
assert(facts.recipe_catalog.category_metadata_unsupported == 0)
assert(facts.recipe_catalog.recipe_count == 8)
assert(facts.recipe_observations['iron-plate'].category == 'crafting')
assert(type(facts.recipe_observations['iron-plate'].categories[1]) == 'string')
assert(facts.recipe_observations['iron-plate'].categories[1] == 'crafting')
assert(facts.recipe_observations['pumpjack'].categories[1] == 'crafting')
assert(facts.recipe_observations['disabled-thing'].enabled == false)
assert(facts.enabled_recipes['automation-science-pack'] == true)
assert(facts.enabled_recipes['disabled-thing'] == nil)
assert(facts.recipe_observations['plastic-bar'].enabled == true)
assert(facts.recipe_observations['plastic-bar'].ingredients[1].type == 'fluid')
assert(facts.recipe_observations['advanced-circuit'].machine_speeds['assembling-machine-1'] == .5)
assert(facts.recipe_observations['advanced-circuit'].energy_ticks == 30)
assert(facts.recipe_observations['plastic-bar'].machine_speeds['electric-furnace'] == 2)
assert(facts.production['automation-science-pack'] == 42)
assert(facts.consumption['automation-science-pack'] == 7)
assert(facts.production['plastic-bar'] == 9 and facts.consumption['plastic-bar'] == 2)
assert(facts.recipes['iron-plate'])
assert(facts.recipes['plastic-bar'] == nil)
assert(facts.recipes['advanced-circuit'])
""")


@pytest.mark.lua
@pytest.mark.fast
def test_complete_solid_recipes_do_not_require_explicit_extras():
    _run_survey_lua(assertions="""
local menu={}
for name in pairs(facts.recipes) do menu[#menu+1]=name end
table.sort(menu)
assert(table.concat(menu, ',') == 'advanced-circuit,automation-science-pack,electric-furnace,electronic-circuit,iron-plate,pumpjack')
assert(facts.recipes['automation-science-pack'])
assert(facts.recipes['advanced-circuit'])
assert(facts.recipes['plastic-bar'] == nil)
""")


_MALFORMED_CATEGORY_RECIPES = """
  ['bad-category-numbers']=recipe('bad-category-numbers',{item('iron-plate',1)},{item('bad-category-numbers',1)},{categories={1,2}}),
  ['bad-category-map']=recipe('bad-category-map',{item('iron-plate',1)},{item('bad-category-map',1)},{categories={crafting=true}}),
"""


@pytest.mark.lua
def test_malformed_category_metadata_fails_closed_and_marks_the_survey_incomplete():
    _run_survey_lua(malformed=_MALFORMED_CATEGORY_RECIPES, assertions="""
assert(facts.recipe_catalog.complete == false)
assert(facts.recipe_catalog.category_metadata_unsupported == 2)
for _,name in ipairs({'bad-category-numbers','bad-category-map'}) do
  local observation=facts.recipe_observations[name]
  assert(observation and #observation.categories == 0)
  assert(observation.category == nil)
  assert(next(observation.machine_speeds) == nil)
end
assert(facts.recipe_observations['iron-plate'].category == 'crafting')
assert(facts.recipe_observations['iron-plate'].machine_speeds['assembling-machine-1'] == .5)
""")


@pytest.mark.lua
def test_extra_recipes_keep_fluid_recipes_out_of_the_build_menu():
    _run_survey_lua(extra_recipes=("advanced-circuit",), assertions="""
assert(facts.recipes['advanced-circuit'])
assert(facts.recipes['pumpjack'])
assert(facts.enabled_recipes['plastic-bar'] == true)
assert(facts.recipes['plastic-bar'] == nil)
assert(facts.recipes['disabled-thing'] == nil)
assert(facts.recipes['automation-science-pack'])
assert(facts.production['plastic-bar'] == 9)
""")


def test_query_request_carries_demanded_recipes_without_changing_the_fact_scope():
    from training.laya_saved_world import BOUNDS, _query_request
    request = _query_request(extra_recipes=("advanced-circuit", "plastic-bar"))
    assert request["extra_recipes"] == ["advanced-circuit", "plastic-bar"]
    assert request["bounds"] == BOUNDS and request["bounds"] is not BOUNDS
    assert request["candidates"] is None and request["inspect"] is None
    assert _query_request()["extra_recipes"] == []


def test_saved_world_schema_accepts_recipe_survey_and_completeness_metadata():
    from training.laya_saved_world import FACTS_VALIDATOR
    payload = {
        "version": "saved-world-v1", "tick": 100, "surface": "nauvis", "force": "player",
        "mods": {"base": "2.1.23"}, "networks": {}, "ports": [], "poles": [], "machines": [],
        "resources": [], "player_stock": {}, "researched": {}, "pending_ghosts": 0,
        "production": {"automation-science-pack": 42},
        "consumption": {"automation-science-pack": 7},
        "enabled_recipes": {"automation-science-pack": True},
        "recipe_observations": {"automation-science-pack": {
            "enabled": True,
            "ingredients": [{"name": "iron-plate", "type": "item", "amount": 1}],
            "products": [{"name": "automation-science-pack", "type": "item", "amount": 1}],
            "category": "crafting", "categories": ["crafting"],
            "machine_speeds": {"assembling-machine-1": 0.5},
            "energy_ticks": 30, "productivity_bonus": 0}},
        "recipe_catalog": {"version": "1.2.0", "force": "player", "tick": 100,
                           "surface": "nauvis", "recipe_count": 1,
                           "source": "force.recipes", "complete": True,
                           "document": "saved-world-recipe-survey", "metadata_only": True,
                           "category_metadata_unsupported": 0},
        "recipes": {},
    }
    FACTS_VALIDATOR.validate(payload)
    incomplete = copy.deepcopy(payload)
    incomplete["recipe_catalog"].update(complete=False, category_metadata_unsupported=1)
    incomplete["recipe_observations"]["automation-science-pack"]["categories"] = []
    incomplete["recipe_observations"]["automation-science-pack"].pop("category")
    FACTS_VALIDATOR.validate(incomplete)
    oil = copy.deepcopy(payload)
    oil["fluid_recipes"] = {"basic-oil-processing": {
        "ingredients": [{"name": "crude-oil", "type": "fluid", "amount": 100}],
        "products": [{"name": "petroleum-gas", "type": "fluid", "amount": 45}],
        "energy_ticks": 300, "category": "oil-processing", "machine_speeds": {"oil-refinery": 1}, "productivity_bonus": 0}}
    oil["oil_field"] = {"anchor": {"x": 0, "y": 0}, "anchor_source": "spawn", "search_radius": 512, "found": True,
                        "wells": 3, "total_amount": 1050000, "yield_percent": 350, "max_crude_per_s": 35,
                        "center": {"x": 62, "y": 0.3}, "distance": 62, "nearest_well_distance": 60,
                        "water": {"found": True, "position": {"x": 30.5, "y": 40.5}, "distance": 50.7}}
    FACTS_VALIDATOR.validate(oil)
    oil["oil_field"] = {"anchor": {"x": 0, "y": 0}, "anchor_source": "roboport", "search_radius": 512,
                        "found": False, "wells": 0, "water": {"found": False, "unknown": True}}
    FACTS_VALIDATOR.validate(oil)


_FLUID_SNAPSHOT_SETUP = """
prototypes.entity['pipe']={type='pipe',fluidbox_prototypes={{index=1}}}
prototypes.entity['chemical-plant']={type='assembling-machine',fluidbox_prototypes={{index=1},{index=2}}}
local function built(name, kind, x, y, boxes, fluids, extra)
  local e={name=name,type=kind,position={x=x,y=y},direction=4,force={name='player'},mirroring=false,unit_number=9,
    prototype=prototypes.entity[name],
    get_fluid_box_prototype=function(i) if i==2 then return {{index=2},{index=3}} end return {index=i} end,
    has_fluid_segment=function(i) return true end,
    get_fluid_segment_fluid=function(i) return fluids[i] and {name=fluids[i],amount=5} or nil end,
    get_fluid=function(i) return nil end,
    get_fluid_filter=function(i) return nil end,
    get_recipe=function() return {name='sulfuric-acid'} end}
  for k,v in pairs(extra or {}) do e[k]=v end
  return e
end
local found={built('pipe','pipe',3.5,.5,1,{'water'}),
  built('chemical-plant','assembling-machine',8.5,.5,2,{'water'})}
local ghost={ghost_name='pipe',ghost_type='pipe',position={x=4.5,y=.5},direction=0,force={name='enemy'},
  mirroring=false,ghost_prototype=prototypes.entity['pipe']}
local belt={ghost_name='transport-belt',ghost_type='transport-belt',position={x=5.5,y=.5},direction=0,
  force={name='player'},mirroring=false,ghost_prototype={fluidbox_prototypes={}}}
surface.find_entities_filtered=function(filter)
  if filter.type=='entity-ghost' and filter.area then return {ghost,belt} end
  if type(filter.type)=='table' then
    assert(filter.area and filter.limit==20001)
    return found
  end
  return {}
end
request.candidates={{option_id='fluid',required_checks={'fluid_purity'},plan={phases={{actions={
  {entity='pipe',position={x=.5,y=.5}},{entity='pipe',position={x=2.5,y=1.5}} }}}}}}
"""


@pytest.mark.lua
def test_power_snapshot_is_same_tick_opt_in_and_records_pole_identity():
    _run_survey_lua(setup="""
surface.name='nauvis'
local pole={name='medium-electric-pole',type='electric-pole',position={x=.5,y=1.5},
  unit_number=42, quality={name='normal'},electric_network_id=7,
  prototype={get_max_wire_distance=function(q) assert(q=='normal'); return 9 end,
    get_supply_area_distance=function(q) assert(q=='normal'); return 3.5 end}}
local generator={name='steam-engine',type='generator',electric_network_id=7}
surface.find_entities_filtered=function(filter)
  if filter.force and filter.type==nil then return {pole,generator} end
  return {}
end
request.candidates={{option_id='power',inspect_power_topology=true,plan={phases={{actions={}}}}},
  {option_id='ordinary',plan={phases={{actions={}}}}}}
""", assertions="""
local s=facts.validations.power.power_topology_snapshot
assert(s.version=='optimizer-power-snapshot-v1' and s.tick==100 and s.complete==true)
assert(s.surface=='nauvis' and s.force=='player' and s.mods.base=='2.1.23')
assert(#s.poles==1 and s.poles[1].unit_number==42 and s.poles[1].quality=='normal')
assert(s.poles[1].wire==9 and s.poles[1].grid=='7' and s.poles[1].generated==true)
assert(facts.validations.ordinary.power_topology_snapshot==nil)
""")


@pytest.mark.lua
def test_power_snapshot_limit_marks_observation_incomplete():
    _run_survey_lua(setup="""
surface.name='nauvis'
local pole={name='medium-electric-pole',type='electric-pole',position={x=.5,y=1.5},
  unit_number=42, quality={name='normal'},electric_network_id=7,
  prototype={get_max_wire_distance=function() return 9 end,get_supply_area_distance=function() return 3.5 end}}
local poles={}; for i=1,5001 do poles[i]=pole end
surface.find_entities_filtered=function(filter)
  if filter.force and filter.type==nil then return poles end
  return {}
end
request.candidates={{option_id='power',inspect_power_topology=true,plan={phases={{actions={}}}}}}
""", assertions="""
local s=facts.validations.power.power_topology_snapshot
assert(s.complete==false and #s.poles==5000)
""")


@pytest.mark.lua
def test_fluid_topology_snapshot_separates_observed_built_identity_from_ghosts():
    _run_survey_lua(setup=_FLUID_SNAPSHOT_SETUP + """
prototypes.entity['assembling-machine-1'].fluidbox_prototypes={}
prototypes.entity['electric-furnace'].fluidbox_prototypes={}
""", assertions="""
local s=facts.validations.fluid.fluid_topology_snapshot
assert(s.version=='fluid-topology-snapshot-v1' and s.complete==true and s.forces=='all' and s.tick==100)
assert(s.area.x_min==-16 and s.area.y_min==-16 and s.area.x_max_exclusive==19 and s.area.y_max_exclusive==18)
assert(#s.entities==3)
local pipe,plant,ghost=s.entities[1],s.entities[2],s.entities[3]
assert(pipe.name=='pipe' and pipe.ghost==false and pipe.direction=='east' and pipe.unit_number==9)
assert(pipe.fluidboxes[1].readable==true and pipe.fluidboxes[1].segment_fluid=='water' and pipe.recipe==nil)
assert(plant.recipe=='sulfuric-acid' and plant.fluidboxes[1].readable==true)
assert(plant.fluidboxes[2].readable==false and plant.fluidboxes[2].segment_fluid==nil)
assert(ghost.ghost==true and ghost.force=='enemy' and ghost.fluidboxes==nil and ghost.unit_number==nil)
assert(facts.validations.fluid.reason=='no_construction_network')
""")


@pytest.mark.lua
def test_fluid_topology_snapshot_is_incomplete_when_prototypes_are_unreadable():
    # The fixture's machine prototypes lack fluidbox data, so fluid types cannot be enumerated.
    _run_survey_lua(setup=_FLUID_SNAPSHOT_SETUP, assertions="""
local s=facts.validations.fluid.fluid_topology_snapshot
assert(s.complete==false and #s.entities==0)
""")


def test_saved_world_configuration_refuses_legacy_event_reports(tmp_path):
    adapter = object.__new__(SavedWorldAdapter)
    adapter.root = tmp_path
    candidate = next(c for c in compile_candidates(facts()) if c["family"] == "assemble")
    class Bridge:
        def optimizer_build_evidence(self, **kwargs):
            assert kwargs["include_unidentified"] is True
            return {"version": "optimizer-build-evidence-v2"}
    adapter.bridge = Bridge()
    tags = {"optimizer_episode": "e", "optimizer_project": "p",
            "optimizer_transaction": "a" * 32, "optimizer_plan_hash": "b" * 64}
    baseline = {"version": "optimizer-build-evidence-v3", "source": "robot_built_entity_tags",
                "request_id": "baseline", "tick": 100, "ok": True, "ownership_claimed": False,
                "dispatch_authorized": False, "surface": "nauvis", "force": "player", "tags": tags, "entities": []}
    with pytest.raises(SavedWorldError, match="lacks current correlated"):
        adapter._guarded_configuration(candidate["plan"], tags, 100, baseline)


@pytest.mark.parametrize("mode", ["legacy", "nonempty", "stale", "unavailable"])
def test_configuration_baseline_failure_prevents_ghost_submission(tmp_path, mode):
    adapter = object.__new__(SavedWorldAdapter)
    adapter.root = tmp_path
    candidate = next(c for c in compile_candidates(facts()) if c["family"] == "assemble")
    adapter.validate_plan = lambda _plan: None
    adapter.query = lambda candidates=None: ({"validations": {candidate["option_id"]: {"reason": None}}}
                                             if candidates else facts())
    class Bridge:
        def optimizer_build_evidence(self, *, surface, force, episode_id, project_id, transaction_id, plan_hash,
                                     timeout, include_unidentified):
            if mode == "unavailable":
                raise OSError("probe unavailable")
            tags = {"optimizer_episode": episode_id, "optimizer_project": project_id,
                    "optimizer_transaction": transaction_id, "optimizer_plan_hash": plan_hash}
            events = ([{"entity": "pipe", "position": {"x": 1.5, "y": 2.5}, "tick": 99,
                        "current": {"state": "identity_unavailable"}}] if mode == "nonempty" else [])
            return {"version": "optimizer-build-evidence-v2" if mode == "legacy" else "optimizer-build-evidence-v3",
                    "source": "robot_built_entity_tags", "request_id": "req", "tick": 99 if mode == "stale" else 100, "ok": True,
                    "ownership_claimed": False, "dispatch_authorized": False, "surface": surface,
                    "force": force, "tags": tags, "entities": events}
        def build_layout_correlated(self, *_args, **_kwargs):
            pytest.fail("unverified baseline reached ghost submission")
    adapter.bridge = Bridge()
    with pytest.raises(SavedWorldError, match="baseline is unavailable"):
        adapter.execute(candidate["option_id"], [candidate])
    receipt = json.loads(next((tmp_path / "evidence").glob("transaction-*.json")).read_text())
    assert receipt["status"] == "rejected" and receipt["reason"] == "configuration_evidence_baseline_unavailable"


@pytest.mark.lua
def test_fluid_snapshot_does_not_scan_tiles_without_explicit_request():
    _run_survey_lua(setup=_FLUID_SNAPSHOT_SETUP + """
prototypes.entity['assembling-machine-1'].fluidbox_prototypes={}
prototypes.entity['electric-furnace'].fluidbox_prototypes={}
surface.get_tile=function() error('unexpected tile scan') end
""", assertions="""
local s=facts.validations.fluid.fluid_topology_snapshot
assert(s.complete==true and s.tiles==nil)
""")


def test_query_keeps_request_data_containing_lua_comment_marker(tmp_path):
    """Regression (live 2026-11-01): comment stripping cut request JSON at "--"."""
    import re
    adapter = object.__new__(SavedWorldAdapter)
    adapter.root = tmp_path
    adapter.lua_source = "local probe = request.inspect -- trailing comment\nreturn {echo = probe}"
    adapter._check_instance = lambda: None
    adapter.quad_centers = lambda: []
    sent = []

    class Bridge:
        script_output = tmp_path

        def command(self, cmd):
            sent.append(cmd)
            name = re.search(r'helpers\.write_file\("([^"]+)"', cmd).group(1)
            (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / name).write_text(json.dumps({"ok": True, "value": {"echo": "kept"}}))
            return "saved-world-report"

    adapter.bridge = Bridge()
    adapter.query(inspect={"note": "a--b"})
    assert '"note":"a--b"' in sent[0] and "trailing comment" not in sent[0]


def test_query_without_report_surfaces_server_reply(tmp_path):
    adapter = object.__new__(SavedWorldAdapter)
    adapter.root = tmp_path
    adapter.lua_source = "return {}"
    adapter._check_instance = lambda: None
    adapter.quad_centers = lambda: []

    class Bridge:
        script_output = tmp_path

        def command(self, cmd):
            return "Cannot execute command. Error: unfinished long string"

    adapter.bridge = Bridge()
    with pytest.raises(SavedWorldError, match="unfinished long string"):
        adapter.query(inspect={})


@pytest.mark.lua
def test_fluid_recipes_are_exported_typed_and_separate_from_the_solid_menu():
    _run_survey_lua(setup="""
prototypes.entity['chemical-plant']={type='assembling-machine',ingredient_count=4,
  crafting_categories={chemistry=true},get_crafting_speed=function() return 1 end}
prototypes.entity['oil-refinery']={type='assembling-machine',ingredient_count=2,
  crafting_categories={['oil-processing']=true},get_crafting_speed=function() return 1 end}
recipes['plastic-bar'].categories={'chemistry'}
recipes['basic-oil-processing']=recipe('basic-oil-processing',{fluid('crude-oil',100)},{fluid('petroleum-gas',45)},
  {energy=5,categories={'oil-processing'}})
recipes['advanced-oil-processing']=recipe('advanced-oil-processing',{fluid('crude-oil',100),fluid('water',50)},
  {fluid('heavy-oil',25),fluid('light-oil',45),fluid('petroleum-gas',55)},{energy=5,categories={'oil-processing'}})
recipes['sulfuric-acid']=recipe('sulfuric-acid',{item('iron-plate',1),fluid('water',100)},{fluid('sulfuric-acid',50)},
  {enabled=false,categories={'chemistry'}})
""", assertions="""
local fluid=facts.fluid_recipes
local plastic=fluid['plastic-bar']
assert(plastic.category=='chemistry' and plastic.energy_ticks==30 and plastic.machine_speeds['chemical-plant']==1)
assert(plastic.ingredients[1].name=='petroleum-gas' and plastic.ingredients[1].type=='fluid' and plastic.ingredients[1].amount==20)
assert(plastic.products[1].name=='plastic-bar' and plastic.products[1].type=='item' and plastic.products[1].amount==2)
local basic=fluid['basic-oil-processing']
assert(basic.energy_ticks==300 and basic.machine_speeds['oil-refinery']==1 and basic.products[1].amount==45)
assert(#fluid['advanced-oil-processing'].products==3)
assert(fluid['sulfuric-acid']==nil and fluid['iron-plate']==nil)
local menu={}
for name in pairs(facts.recipes) do menu[#menu+1]=name end
table.sort(menu)
assert(table.concat(menu, ',') == 'advanced-circuit,automation-science-pack,electric-furnace,electronic-circuit,iron-plate,pumpjack')
assert(facts.oil_field==nil)
""")


@pytest.mark.lua
def test_oil_field_survey_reports_nearest_patch_yield_and_water_without_creating_anything():
    _run_survey_lua(setup="""
request.oil_search_radius=100
game.forces.player.get_spawn_position=function() return {x=0,y=0} end
prototypes.entity['crude-oil']={normal_resource_amount=300000,
  mineable_properties={mining_time=1,products={{name='crude-oil',amount=10}}}}
prototypes.entity['pumpjack']={mining_speed=1}
prototypes.tile={water={fluid={name='water'}},deepwater={fluid={name='water'}},grass={}}
local function well(x,y,amount) return {name='crude-oil',position={x=x,y=y},amount=amount} end
local wells={well(60,0,300000),well(64,4,150000),well(62,-3,600000),well(95,0,900000),well(300,0,900000)}
local created=false
surface.create_entity=function() created=true end
surface.find_entities_filtered=function(f)
  if f.name=='crude-oil' then return wells end
  return {}
end
local water_distance=math.sqrt(30.5^2+40.5^2)
surface.count_tiles_filtered=function(f)
  assert(#f.name==2)
  return f.radius>=water_distance and 1 or 0
end
surface.find_tiles_filtered=function(f) return {{position={x=30,y=40}}} end
""", assertions="""
local field=facts.oil_field
assert(field.anchor_source=='spawn' and field.search_radius==100 and field.found)
assert(field.wells==3 and field.total_amount==1050000 and field.nearest_well_distance==60)
assert(math.abs(field.center.x-62)<1e-9 and math.abs(field.center.y-1/3)<1e-9)
assert(math.abs(field.yield_percent-350)<1e-9 and math.abs(field.max_crude_per_s-35)<1e-9)
assert(field.water.found and field.water.position.x==30.5 and math.abs(field.water.distance-water_distance)<1e-9)
assert(not created)
""")


def test_recorded_runtime_root_is_passed_on_every_lifecycle_call(tmp_path, monkeypatch):
    from tools import run_laya_saved_world
    calls = []
    monkeypatch.setattr(run_laya_saved_world.subprocess, "run",
                        lambda cmd, **_: calls.append(cmd) or type("R", (), {"stdout": ""})())
    manifest = {"game_port": 1, "rcon_port": 2}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    run_laya_saved_world.manager(tmp_path, "stop")
    (tmp_path / "manifest.json").write_text(json.dumps({**manifest, "runtime_root": "/rt/factorio-2.1.24"}))
    run_laya_saved_world.manager(tmp_path, "start", ["--nice", "5"])
    assert "--runtime-root" not in calls[0]
    assert calls[1][-4:] == ["--runtime-root", "/rt/factorio-2.1.24", "--nice", "5"]


def test_complete_solid_catalog_reaches_candidate_compilation():
    from training.laya_clean_runner import _compile
    recipe = {"ingredients": [{"name": "electronic-circuit", "amount": 2},
                              {"name": "plastic-bar", "amount": 2},
                              {"name": "copper-cable", "amount": 4}],
              "product_amount": 1, "energy_ticks": 360}
    world = {**facts(), "recipes": {"advanced-circuit": recipe, "automation-science-pack": {"ingredients": [{"name": "copper-plate", "amount": 1}, {"name": "iron-gear-wheel", "amount": 1}], "product_amount": 1, "energy_ticks": 300}}}
    for product in world["recipes"]:
        assert any(c.get("product") == product for c in _compile(world, {product}, 0))


@pytest.mark.lua
@pytest.mark.fast
def test_survey_indexes_aliases_disabled_and_unsupported_products():
    _run_survey_lua(malformed="""
 ['alias-recipe']=recipe('alias-recipe',{item('iron-plate',1)},{item('alias-product',2)}),
 ['multi-recipe']=recipe('multi-recipe',{item('iron-plate',1)},{item('first',1),item('second',1)}),
""", setup="""
-- LuaTechnology has no effects field on 2.1.24; effects live on its prototype.
game.forces.player.technologies['unlock-test']={researched=false,prototype={effects={{type='unlock-recipe',recipe='disabled-thing'}}}}
""", assertions="""
assert(facts.recipe_product_index.products['item:alias-product'][1]=='alias-recipe')
assert(facts.recipe_product_index.products['item:first'][1]=='multi-recipe')
assert(facts.recipe_product_index.products['item:second'][1]=='multi-recipe')
assert(facts.recipes['alias-product'].recipe=='alias-recipe')
assert(facts.recipes['first']==nil)
assert(facts.recipe_observations['disabled-thing'].unlock_technologies[1]=='unlock-test')
""")


@pytest.mark.fast
def test_alias_candidate_places_and_recognizes_actual_recipe():
    from training.laya_clean_runner import _compile
    from planners.plan_validation import actions
    world = facts()
    world['recipes'] = {'alias-product': {'recipe': 'alias-recipe', 'ingredients': [{'name': 'iron-plate', 'amount': 1}], 'product_amount': 2, 'energy_ticks': 30}}
    candidates = [c for c in _compile(world, {'alias-product'}, 0) if c.get('product') == 'alias-product']
    assert candidates
    machine = next(a for a in actions(candidates[0]['plan']) if a['entity'] == 'assembling-machine-1')
    assert machine['recipe'] == 'alias-recipe'
    world['machines'] = [{'position': machine['position'], 'recipe': 'alias-recipe'}]
    remaining = _compile(world, {'alias-product'}, 0)
    assert not any(a['entity'] == 'assembling-machine-1' and a['position'] == machine['position'] for c in remaining if c.get('product') == 'alias-product' for a in actions(c['plan']))


@pytest.mark.lua
@pytest.mark.fast
def test_classified_alternatives_probability_and_quality():
    _run_survey_lua(malformed="""
 ['a-bad']=recipe('a-bad',{item('iron-plate',1)},{item('choice',1)}),
 ['b-good']=recipe('b-good',{item('iron-plate',1)},{item('choice',1)}),
 ['quality-recipe']=recipe('quality-recipe',{item('iron-plate',1)},{item('quality-product',1)}),
""", setup="""
local recipes=game.forces.player.recipes
recipes['a-bad'].products[1].independent_probability=0.5
recipes['b-good'].products[1].independent_probability=1
recipes['b-good'].products[1].shared_probability={min=0,max=1}
recipes['quality-recipe'].products[1].quality_min='rare'
""", assertions="""
assert(facts.recipes.choice.recipe=='b-good')
assert(facts.recipe_observations['a-bad'].solid_classification.cause=='nondeterministic_output')
assert(facts.recipe_observations['b-good'].solid_classification.supported)
assert(facts.recipe_observations['quality-recipe'].solid_classification.cause=='quality_unsupported')
assert(facts.recipes['quality-product']==nil)
""")


@pytest.mark.fast
@pytest.mark.lua
def test_survey_keeps_existing_furnace_operations_without_assembler_compatibility():
    _run_survey_lua(setup="""
recipes['iron-plate'].categories={'smelting'}
prototypes.entity['electric-furnace'].crafting_categories={smelting=true}
""", assertions="""
assert(facts.recipe_observations['iron-plate'].machine_speeds['assembling-machine-1']==nil)
assert(facts.recipe_observations['iron-plate'].solid_classification.machine=='electric-furnace')
assert(facts.recipes['iron-plate'].product_amount==1)
""")


@pytest.mark.fast
@pytest.mark.lua
def test_captured_recipe_resurvey_reaches_actual_candidate_material_check():
    from collections import Counter
    from training.laya_clean_runner import compile_menu
    from training.laya_factory_status import compile_status, recipe_evidence

    capture = json.loads(Path('tests/fixtures/laya_recipe_omission_c0b84004.json').read_text())
    observed = capture['recipe']
    assert capture['source_tick'] == 256291 and observed['enabled'] is True
    assert capture['operation_entry_was_present'] is False
    legacy = {'recipe_catalog': {'complete': False}, 'recipe_observations': {'advanced-circuit': observed}}
    # The original serializer omitted quality bounds; do not authorize old data.
    assert recipe_evidence(legacy, 'advanced-circuit')['cause'] == 'quality_unknown'

    def literal(value):
        if isinstance(value, dict):
            return '{' + ','.join('[' + json.dumps(k) + ']=' + literal(v) for k, v in value.items()) + '}'
        if isinstance(value, list):
            return '{' + ','.join(literal(v) for v in value) + '}'
        if isinstance(value, bool):
            return str(value).lower()
        return json.dumps(value)

    prototype = {**copy.deepcopy(observed), 'energy': observed['energy_ticks'] / 60}
    # Explicit unconstrained quality is an API-fixture condition for a new
    # survey, not an assertion about fields missing from the historical JSON.
    for part in prototype['ingredients'] + prototype['products']:
        part.update(quality_min='normal', quality_max='normal', quality_change=0)
    output = _run_survey_lua(setup='recipes["advanced-circuit"]=' + literal(prototype), assertions="""
local entry=facts.recipes['advanced-circuit']
assert(facts.recipe_observations['advanced-circuit'].solid_classification.supported)
local parts={}
for _,p in ipairs(entry.ingredients) do
  parts[#parts+1]=string.format('{"name":%q,"amount":%s}',p.name,p.amount)
end
print(string.format('{"recipe":%q,"ingredients":[%s],"product_amount":%s,"energy_ticks":%s}',
  entry.recipe,table.concat(parts,','),entry.product_amount,entry.energy_ticks))
""")
    entry = json.loads(output)
    assert all('quality_min' not in p for p in observed['ingredients'] + observed['products'])
    assert entry['recipe'] == 'advanced-circuit' and entry['ingredients'] == [
        {'name': p['name'], 'amount': p['amount']} for p in observed['ingredients']]
    world = facts()
    world['recipes'] = {'advanced-circuit': entry}
    world['poles'][0].update(name='substation', supply=9, grid='1')
    # Fixture supply isolates the real candidate-to-construction-bill boundary.
    world['networks']['1']['items'].update({'plastic-bar': 2, 'copper-cable': 4, 'electronic-circuit': 2})
    checked = []

    def validate(candidates):
        results = {}
        for candidate in candidates:
            bill = Counter(a['entity'] for p in candidate['plan']['phases'] for a in p['actions'])
            missing = sorted(item for item, count in bill.items()
                             if world['networks']['1']['items'].get(item, 0) < count)
            results[candidate['option_id']] = {'reason': 'item_shortfall:' + missing[0]} if missing else {'reason': None, 'bill': dict(bill)}
            if candidate.get('product') == 'advanced-circuit':
                checked.append(results[candidate['option_id']])
        return results

    menu = compile_menu(world, compile_status(world, {'advanced-circuit': 1}), validate, seed=0)
    assert checked and all(row['reason'].startswith('item_shortfall:') for row in checked)
    assert any(reason.startswith('item_shortfall:') for reason in menu['blocked']['item:advanced-circuit'])
    assert 'item:advanced-circuit' not in menu['recipe_diagnostics']
