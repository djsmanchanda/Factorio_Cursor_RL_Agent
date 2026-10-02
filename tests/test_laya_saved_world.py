# Path: tests/test_laya_saved_world.py | Purpose: Protect saved-world isolation, funding representation and exact model/action boundaries.

import copy
import json
from pathlib import Path

import pytest

from training.laya_input_profile import input_profile, require_input_profile
from training.laya_saved_world import (
    PROFILE, SavedWorldAdapter, SavedWorldError, bounded_menu, compile_candidates,
    decision_state, measured_outcome, owned_root,
)


def facts():
    return {"tick": 100, "ports": [{"position": {"x": 3, "y": -1}, "network": "1", "energy": 100,
            "construction_radius": 55, "logistic_radius": 25}], "poles": [{"generated": True, "position": {"x": 0, "y": 0}, "wire": 18}],
            "networks": {"1": {"items": {"electric-furnace": 3, "inserter": 4},
                                  "reserved": {"electric-furnace": 2}, "construction_robots": 50}},
            "machines": [], "pending_ghosts": 2, "resources": [],
            "production": {"iron-plate": 20}, "consumption": {"iron-ore": 20}}


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


def test_compiler_produces_funded_ghost_intents_and_reverses_inserter_pickup_direction():
    candidates = compile_candidates(facts())
    assert {c["family"] for c in candidates} == {"expand", "smelt", "assemble"}
    for candidate in candidates:
        for phase in candidate["plan"]["phases"]:
            for action in phase["actions"]:
                assert action["action_type"] == "place_ghost"
                if action["entity"] == "inserter":
                    assert action["direction"] == "west"  # Picks west, drops east.


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
