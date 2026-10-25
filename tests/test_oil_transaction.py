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
    monkeypatch.setattr(stage_chemical.live_base, "transferable_items", lambda *_a: {})
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


def test_selected_bill_credits_installation_and_preserves_other_claims(monkeypatch, tmp_path):
    from orchestrator import live_base
    from orchestrator.material_reservations import MaterialReservationLedger

    store = OilTransactionStore.for_bridge(_bridge(tmp_path), "nauvis", "player")
    intent = _intent()
    intent["packets"][0][1]["phases"][0]["actions"] = [
        {"action_type": "place_ghost", "entity": "pipe", "position": {"x": n + .5, "y": .5}}
        for n in range(10)]
    store.save(intent, complete=False)
    frozen = store.path.read_bytes()
    ledger = MaterialReservationLedger(tmp_path / "script-output", episode_id="episode-a",
                                       surface="nauvis", force="player")
    ledger.declare("other", {"pipe": 2}, {"pipe": 3}, priority=100)
    ledger.declare("chemical_test", {"pipe": 10}, {"pipe": 3})
    observation = {"required": {"pipe": 10}, "installed": {"pipe": 6}, "pending": {"pipe": 2},
                   "unplaced": {"pipe": 2}, "remaining": {"pipe": 4}}
    monkeypatch.setattr(live_base, "plan_construction_status", lambda *_a: copy.deepcopy(observation))
    monkeypatch.setattr(live_base, "transferable_items", lambda *_a: {"pipe": 3})
    monkeypatch.setattr(live_base, "available_items", lambda *_a: pytest.fail("requester WIP used to pay bill"))
    def pending(*_a, exclude):
        assert len(exclude) == 10  # Its own ghosts are not charged as another consumer.
        return {"pipe": 1}
    monkeypatch.setattr(live_base, "pending_construction_items", pending)
    facts = store.material_facts(object(), ledger)
    assert facts["remaining"] == {"pipe": 4}  # Ghosts do not count as installed material.
    assert facts["available_to_owner"] == {"pipe": 0}  # Foreign pending work + foreign reservation.
    assert facts["missing"] == {"pipe": 4} and facts["stock_levels"] == {"pipe": 7}
    assert facts["identity"]["episode_id"] == "episode-a"
    observation.update(installed={"pipe": 8}, remaining={"pipe": 2})
    assert store.material_facts(object(), ledger)["stock_levels"] == {"pipe": 5}
    observation.update(installed={"pipe": 10}, remaining={})
    done = store.material_facts(object(), ledger)
    assert done["missing"] == {} and not done["complete"]  # Material delivery is not operating service.
    assert store.path.read_bytes() == frozen  # No replanning or lifecycle mutation from observation.


@pytest.mark.lua
def test_construction_observation_counts_exact_real_placements_and_tiles():
    import shutil
    import subprocess
    import re
    from orchestrator import live_base

    lua = shutil.which("lua")
    if lua is None:
        pytest.skip("Lua interpreter required for construction observation")
    class Client:
        def command(self, command):
            encoded = re.search(r'helpers.json_to_table\(("(?:\\.|[^"\\])*")\)', command).group(1)
            placements = json.loads(json.loads(encoded))
            literal = "{" + ",".join("{tile=" + str(a["tile"]).lower() + ",name=" + json.dumps(a["name"]) +
                                      f",x={a['x']},y={a['y']}" + "}" for a in placements) + "}"
            fixture = """
local owner={name='player'}
local surface={get_tile=function(p) return {name=p.x==5 and 'landfill' or 'water'} end,
 find_entities_filtered=function(o)
   assert(o.force==owner, 'force scope lost')
   if o.type=='entity-ghost' then return o.position.x==2.5 and {{}} or {} end
   if o.type=='tile-ghost' then return o.position.x==6 and {{}} or {} end
   assert(o.name=='pipe')
   return o.position.x==1.5 and {{type='pipe',position={x=1.5,y=.5}}} or {}
 end}
game={surfaces={nauvis=surface},forces={player=owner}}
rcon={print=print}
helpers={table_to_json=function(out)
 local encoded={};for _,v in ipairs(out) do encoded[#encoded+1]='"'..v..'"' end
 return '['..table.concat(encoded,',')..']' end}
"""
            fixture += "\nhelpers.json_to_table=function() return " + literal + " end\n"
            result = subprocess.run([lua, "-"], input=fixture + command.removeprefix("/sc "),
                                    capture_output=True, text=True)
            assert result.returncode == 0, result.stderr
            return result.stdout
    actions = [{"action_type": "place_ghost", "entity": "pipe", "position": {"x": n + .5, "y": .5}}
               for n in (1, 2, 3)]
    actions += [copy.deepcopy(actions[0]), {"action_type": "configure_entity", "entity": "pipe"}]
    actions += [{"action_type": "place_tile_ghost", "tile": "landfill", "position": {"x": n, "y": 0}}
                for n in (5, 6)]
    status = live_base.plan_construction_status(Client(), "nauvis", "player", {"phases": [{"actions": actions}]})
    assert status == {"required": {"pipe": 3, "landfill": 2}, "installed": {"pipe": 1, "landfill": 1},
                      "pending": {"pipe": 1, "landfill": 1}, "unplaced": {"pipe": 1},
                      "remaining": {"pipe": 2, "landfill": 1}}


@pytest.mark.parametrize("reply", ["", "[]", '["installed","other"]', '["other"]'])
def test_incomplete_construction_observation_is_unknown(reply):
    from orchestrator import live_base
    with pytest.raises(live_base.TelemetryError):
        live_base.plan_construction_status(SimpleNamespace(command=lambda _c: reply), "nauvis", "player",
                                           _intent()["packets"][0][1])


def test_oil_packet_retry_asks_only_for_remaining_transferable_items(monkeypatch):
    from orchestrator import live_base
    plan = _intent()["packets"][0][1]
    monkeypatch.setattr(live_base, "plan_construction_status", lambda *_a: {"remaining": {}})
    monkeypatch.setattr(live_base, "transferable_items", lambda *_a: {})
    monkeypatch.setattr(live_base, "available_items", lambda *_a: pytest.fail("requester WIP counted"))
    monkeypatch.setattr(stage_chemical, "construction_supply_chain_is_scheduled", lambda *_a: False)
    submitted = []
    monkeypatch.setattr(stage_chemical, "_submit", lambda *_a, **_kw: submitted.append(_a[4]))
    stage_chemical._submit_oil_cell_packets(SimpleNamespace(command=lambda _c: ""), object(),
                                            "nauvis", "player", [("chemical_test", plan)], lambda _m: None)
    assert submitted == ["chemical_test"]  # Existing plan still goes through normal submission/validation.


def test_session_retains_an_unreadable_transaction_as_unknown(monkeypatch, tmp_path):
    from training.laya_ops import DeterministicSession
    session = object.__new__(DeterministicSession)
    session.bridge, session.client = _bridge(tmp_path), object()
    monkeypatch.setattr(OilTransactionStore, "material_facts", lambda *_a: (_ for _ in ()).throw(ValueError("unreadable")))
    assert session.material_transactions()["opening_oil"]["error"] == "ValueError: unreadable"
