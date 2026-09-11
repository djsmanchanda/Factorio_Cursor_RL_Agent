# Path: tests/test_oil_transaction.py
# Purpose: Protect oil layout ownership across interrupted construction and restarts.

import copy
import json
from types import SimpleNamespace

import pytest

from orchestrator import stage_chemical
from orchestrator.oil_transaction import OilTransactionError, OilTransactionStore


def _bridge(tmp_path, episode="episode-a"):
    return SimpleNamespace(script_output=tmp_path / "script-output", episode_id=episode)


def _intent():
    plan = {"phases": [{"name": "test", "actions": [{
        "action_type": "place_ghost", "entity": "pipe",
        "position": {"x": 1.5, "y": 2.5},
    }]}]}
    return {
        "target_output": "plastic-bar", "plans": [copy.deepcopy(plan) for _ in range(5)],
        "links": [], "packets": [["chemical_test", plan]],
        "coal": [0, 0], "coal_actions": [], "coal_belt_type": "transport-belt",
    }


def test_store_identity_isolation_and_immutable_geometry(tmp_path):
    store = OilTransactionStore.for_bridge(_bridge(tmp_path), "nauvis", "player")
    store.save(_intent(), complete=False)
    assert OilTransactionStore.for_bridge(_bridge(tmp_path, "episode-b"), "nauvis", "player").load() is None
    assert OilTransactionStore.for_bridge(_bridge(tmp_path), "other", "player").load() is None
    changed = _intent()
    changed["coal"] = [100, 0]
    with pytest.raises(OilTransactionError, match="Cannot replace"):
        store.save(changed, complete=False)
    assert store.load()["intent"]["coal"] == [0, 0]


@pytest.mark.parametrize("damage", ["json", "version", "identity", "checksum", "shape"])
def test_corruption_never_becomes_permission_to_replan(tmp_path, damage):
    store = OilTransactionStore.for_bridge(_bridge(tmp_path), "nauvis", "player")
    store.save(_intent(), complete=False)
    data = json.loads(store.path.read_text())
    if damage == "json":
        store.path.write_text("{")
    else:
        if damage == "version":
            data["version"] = 2
        elif damage == "identity":
            data["identity"]["episode_id"] = "other"
        elif damage == "checksum":
            data["intent"]["coal"] = [100, 0]
        else:
            del data["intent"]["packets"]
        store.path.write_text(json.dumps(data))
    with pytest.raises(OilTransactionError):
        store.load()


def test_partial_submit_restart_reuses_layout_and_live_validator(monkeypatch, tmp_path):
    # Real planning/service path with deterministic surveys; inject failure only
    # at the packet submit boundary, then emulate new terrain at that boundary.
    from tests.test_stage_chemical import _oil_cell_world
    real_submit = stage_chemical._submit_oil_cell_packets
    _oil_cell_world(monkeypatch, [])
    monkeypatch.setattr(stage_chemical, "_submit_oil_cell_packets", real_submit)
    monkeypatch.setattr(stage_chemical.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(stage_chemical, "construction_supply_chain_is_scheduled", lambda *_a: True)
    monkeypatch.setattr(stage_chemical, "_connect_oil_cell_power", lambda *_a, **_k: None)
    attempts = []
    mode = ["coverage"]
    def submit(_client, bridge, _surface, plan, name, _emit, **_kwargs):
        store = OilTransactionStore.for_bridge(bridge, "nauvis", "player")
        assert store.load() is not None  # Persist before even the first packet.
        attempts.append((name, copy.deepcopy(plan)))
        if name == "chemical_refinery_and_plastic_machines":
            raise stage_chemical.StuckError(mode[0])
    monkeypatch.setattr(stage_chemical, "_submit", submit)
    def run(bridge):
        return stage_chemical.ensure_oil_cell(
            object(), bridge, "nauvis", "player", (0, 0),
            lambda *_a, **_k: None, lambda _m: None, target_output="plastic-bar",
        )
    with pytest.raises(stage_chemical.StuckError, match="coverage"):
        run(_bridge(tmp_path))
    first = copy.deepcopy(attempts)
    assert len(first) >= 2
    store = OilTransactionStore.for_bridge(_bridge(tmp_path), "nauvis", "player")
    frozen = store.load()
    assert frozen["complete"] is False
    monkeypatch.setattr(stage_chemical, "_find_oil_cell_site", lambda *_a: pytest.fail("resited"))
    # Even a now-producing chest cannot bypass the pending transaction.
    monkeypatch.setattr(stage_chemical, "_existing_outputs", lambda *_a: {"plastic-bar": (1, 2)})
    attempts.clear()
    mode[0] = "new terrain collision"
    with pytest.raises(stage_chemical.StuckError, match="new terrain collision"):
        run(_bridge(tmp_path))
    assert attempts == first
    assert store.load() == frozen


def test_target_change_finishes_reserved_plastic_before_sulfur(monkeypatch, tmp_path):
    store = OilTransactionStore.for_bridge(_bridge(tmp_path), "nauvis", "player")
    store.save(_intent(), complete=False)
    events = []
    def execute(*args):
        events.append(args[-1]["target_output"])
        return {"plastic-bar": (1, 2)}
    monkeypatch.setattr(stage_chemical, "_execute_opening_oil_cell", execute)
    monkeypatch.setattr(stage_chemical, "_extend_sulfur_stage", lambda *_a: events.append("sulfur") or {"sulfur": (3, 4)})
    result = stage_chemical.ensure_oil_cell(
        object(), _bridge(tmp_path), "nauvis", "player", (0, 0),
        lambda *_a, **_k: None, lambda _m: None, target_output="sulfur",
    )
    assert events == ["plastic-bar", "sulfur"]
    assert result == {"sulfur": (3, 4)}
    assert store.load()["complete"] is True


def test_plastic_transaction_does_not_release_deferred_sulfur_power(monkeypatch):
    from tests.test_stage_chemical import _oil_cell_world
    _oil_cell_world(monkeypatch, [])
    intent = stage_chemical._plan_opening_oil_cell(
        object(), object(), "nauvis", "player", (0, 0),
        lambda *_a, **_k: None, lambda _m: None, target_output="plastic-bar",
    )
    sulfur_power, _ = stage_chemical._split_plan_power(intent["plans"][-1])
    deferred = {tuple(action["position"].values())
                for phase in sulfur_power["phases"] for action in phase["actions"]}
    assert deferred
    released = {tuple(action["position"].values())
                for phase in dict(intent["packets"])["chemical_power_backbone"]["phases"]
                for action in phase["actions"]}
    assert deferred.isdisjoint(released)
