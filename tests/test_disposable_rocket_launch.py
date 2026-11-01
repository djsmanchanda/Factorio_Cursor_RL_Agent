# Path: tests/test_disposable_rocket_launch.py | Purpose: Exercise journaled disposable launch ordering without Factorio.
import hashlib
import copy
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from orchestrator.game_bridge import ROCKET_LAUNCH_EVENT_SUBDIR
from tests.test_rocket_launch_observation import _launch_event, _scope_success
from tests.test_rocket_silo_observation import _success
from tests.test_laya_saved_world import facts
from tools import disposable_rocket_launch as launch
from training.laya_saved_world import SavedWorldAdapter


TARGET = {"name": "rocket-silo", "unit_number": 7, "position": {"x": 1.5, "y": 2.5}}


@pytest.fixture
def world(tmp_path, monkeypatch):
    adapter = object.__new__(SavedWorldAdapter)
    adapter.root = tmp_path
    adapter.code_hashes = {str(launch.SOURCE.relative_to(launch.ROOT)): hashlib.sha256(launch.SOURCE.read_bytes()).hexdigest()}
    monkeypatch.setattr(SavedWorldAdapter, "_check_instance", lambda _self: None)
    monkeypatch.setattr(SavedWorldAdapter, "query", lambda _self: {"tick": 1000})
    class Bridge:
        def __init__(self):
            self.script_output = tmp_path / "script-output"
            self.calls = []
            self.fail = None
            self.ordered = True
            self.silo = _success()
            self.silo["requested"]["unit_number"] = {"state": "available", "value": 7}
            self.silo["silos"][0]["prototype_name"] = {"state": "available", "value": "rocket-silo"}
            self.start = {**_scope_success(), "surface": "nauvis", "force": "player"}
            self.end = {**self.start, "request_id": "end42", "enabled": False, "session_started": False,
                        "recorded_launches": 1, "tick": 1200}
        def observe_rocket_silos(self, surface, force, *, unit_number=None):
            self.calls.append("silo")
            result = copy.deepcopy(self.silo)
            if unit_number is None:
                result["requested"]["unit_number"] = {"state": "absent", "reason": "all silos"}
            return result
        def rocket_launch_observation(self, surface, force, *, enabled):
            self.calls.append("arm" if enabled else "disarm")
            path = tmp_path / ("start.json" if enabled else "end.json")
            path.write_text(json.dumps(self.start if enabled else self.end))
            return path
        def command(self, command):
            self.calls.append("order")
            assert "\n" not in command
            assert json.loads(next((tmp_path / "evidence").glob("disposable-rocket-launch-*.json")).read_text())["status"] == "ordering"
            if self.fail == "timeout": raise TimeoutError("response lost")
            if self.fail == "malformed": return "not a receipt"
            request = json.loads(re.search(r'json_to_table\(\[=\[(.*?)\]=\]\)', command).group(1))
            return json.dumps({"version": "disposable-rocket-order-v1", "request_id": request["request_id"],
                "tick": 1000, "surface": "nauvis", "force": "player", "unit_number": 7, "ordered": self.ordered})
    adapter.bridge = Bridge()
    return adapter


def test_order_then_close_matches_observed_ascent_without_claiming_victory(world):
    result = launch.order_disposable_rocket(world, target=TARGET)
    assert result["status"] == "ordered" and world.bridge.calls == ["silo", "arm", "order"]
    event = {**_launch_event(), "surface": "nauvis", "force": "player", "rocket_silo": dict(TARGET)}
    directory = world.bridge.script_output / ROCKET_LAUNCH_EVENT_SUBDIR
    directory.mkdir(parents=True)
    (directory / "rocket_launch_session42_1100_1.json").write_text(json.dumps(event))
    closed = launch.close_disposable_launch_observation(world, Path(result["receipt_path"]))
    assert closed["status"] == "closed"
    assert closed["launch_evidence"]["status"] == "matching_event_found"
    assert any("not authenticated" in note for note in closed["launch_evidence"]["notes"])
    with pytest.raises(launch.DisposableLaunchError, match="previous launch"):
        launch.order_disposable_rocket(world, target=TARGET)
    assert world.bridge.calls.count("order") == 1


@pytest.mark.parametrize("failure", ["timeout", "malformed", "not_ready"])
def test_unknown_or_not_ready_order_is_not_replayed(world, failure):
    world.bridge.fail = failure
    if failure == "not_ready":
        world.bridge.ordered = False
        result = launch.order_disposable_rocket(world, target=TARGET)
        assert result["status"] == "not_ready"
    else:
        with pytest.raises((TimeoutError, launch.DisposableLaunchError)):
            launch.order_disposable_rocket(world, target=TARGET)
        receipt = json.loads(next((world.root / "evidence").glob("disposable-rocket-launch-*.json")).read_text())
        assert receipt["status"] == "ordering"
    with pytest.raises(launch.DisposableLaunchError, match="previous launch"):
        launch.order_disposable_rocket(world, target=TARGET)
    assert world.bridge.calls.count("order") == 1


def test_stale_or_wrong_silo_cannot_arm_or_order(world):
    world.bridge.silo["silos"][0]["position"]["value"]["x"] += 1
    with pytest.raises(launch.DisposableLaunchError, match="selected target"):
        launch.order_disposable_rocket(world, target=TARGET)
    assert world.bridge.calls == ["silo"]


def test_interrupted_arm_is_persisted_before_observation(world):
    def fail(*_args, **_kwargs):
        raise TimeoutError("arm response lost")
    world.bridge.rocket_launch_observation = fail
    with pytest.raises(TimeoutError):
        launch.order_disposable_rocket(world, target=TARGET)
    receipt = json.loads(next((world.root / "evidence").glob("disposable-rocket-launch-*.json")).read_text())
    assert receipt["status"] == "arming"
    with pytest.raises(launch.DisposableLaunchError, match="previous launch"):
        launch.order_disposable_rocket(world, target=TARGET)


@pytest.mark.parametrize("damage", ["missing_session", "legacy_session", "not_fresh", "wrong_tick", "float_scope_tick"])
def test_invalid_observation_arm_cannot_order_launch(world, damage):
    if damage == "missing_session": del world.bridge.start["session_id"]
    elif damage == "legacy_session": world.bridge.start["session_id"] = "unidentified"
    elif damage == "not_fresh": world.bridge.start["session_started"] = False
    elif damage == "wrong_tick": world.bridge.start["tick"] = 999
    else: world.bridge.start["scope_since_tick"] = 1000.0
    with pytest.raises(launch.DisposableLaunchError):
        launch.order_disposable_rocket(world, target=TARGET)
    assert "order" not in world.bridge.calls
    assert json.loads(next((world.root / "evidence").glob("disposable-rocket-launch-*.json")).read_text())["status"] == "arming"


def test_float_silo_tick_does_not_count_as_an_engine_tick(world):
    world.bridge.silo["tick"] = 1000.0
    with pytest.raises(launch.DisposableLaunchError, match="selected target"):
        launch.order_disposable_rocket(world, target=TARGET)
    assert world.bridge.calls == ["silo"]


def test_lost_close_response_never_reorders_or_recloses(world):
    result = launch.order_disposable_rocket(world, target=TARGET)
    def fail(*_args, **_kwargs):
        raise TimeoutError("disarm response lost")
    world.bridge.rocket_launch_observation = fail
    path = Path(result["receipt_path"])
    with pytest.raises(TimeoutError):
        launch.close_disposable_launch_observation(world, path)
    assert json.loads(path.read_text())["status"] == "closing"
    with pytest.raises(launch.DisposableLaunchError, match="not ready"):
        launch.close_disposable_launch_observation(world, path)
    assert world.bridge.calls.count("order") == 1


def test_not_ready_can_close_with_no_claimed_launch(world):
    world.bridge.ordered = False
    result = launch.order_disposable_rocket(world, target=TARGET)
    world.bridge.end["recorded_launches"] = 0
    closed = launch.close_disposable_launch_observation(world, Path(result["receipt_path"]))
    assert closed["status"] == "closed"
    assert closed["launch_evidence"]["status"] == "no_matching_event"
    assert closed["order_result"]["ordered"] is False


def test_zero_event_count_cannot_hide_existing_session_event_file(world):
    result = launch.order_disposable_rocket(world, target=TARGET)
    world.bridge.end["recorded_launches"] = 0
    directory = world.bridge.script_output / ROCKET_LAUNCH_EVENT_SUBDIR
    directory.mkdir(parents=True)
    (directory / "rocket_launch_session42_1100_1.json").write_text("{}")
    with pytest.raises(ValueError, match="event file count"):
        launch.close_disposable_launch_observation(world, Path(result["receipt_path"]))
    assert json.loads(Path(result["receipt_path"]).read_text())["status"] == "closing"


def test_observed_ready_silo_is_an_explicit_mutating_decision_option(world):
    from training.laya_saved_world import decision_state, state_option
    assert launch.disposable_launch_options(world, tick=1000) == []
    world.bridge.silo["silos"][0]["status"]["rocket_silo"]["value"] = "rocket_ready"
    options = launch.disposable_launch_options(world, tick=1000)
    assert len(options) == 1 and options[0]["launch_target"] == TARGET
    assert state_option(options[0])["risk_tier"] == "destructive_mutation"
    assert state_option(options[0])["scope"]["bounds"] is None
    sample = facts()
    sample["tick"] = 1000
    state = decision_state(sample, options, "run", {"model_id": "test", "model_hash": "sha256:" + "0" * 64},
                           "Launch a rocket")
    assert state["options"][0]["operation_family"] == "launch"
    world.bridge.silo["silos"][0].pop("prototype_name")
    assert launch.disposable_launch_options(world, tick=1000) == []


def test_selected_launch_is_revalidated_then_reports_matching_ascent(world, monkeypatch):
    clock = [1000]
    def query(_adapter):
        value = facts()
        value["tick"] = clock[0]
        return value
    monkeypatch.setattr(SavedWorldAdapter, "query", query)
    world.advance = lambda ticks: clock.__setitem__(0, clock[0] + ticks)
    world.bridge.silo["silos"][0]["status"]["rocket_silo"]["value"] = "rocket_ready"
    option = launch.disposable_launch_options(world, tick=1000)[0]
    event = {**_launch_event(), "surface": "nauvis", "force": "player", "rocket_silo": dict(TARGET)}
    directory = world.bridge.script_output / ROCKET_LAUNCH_EVENT_SUBDIR
    directory.mkdir(parents=True)
    (directory / "rocket_launch_session42_1100_1.json").write_text(json.dumps(event))
    result = launch.execute_selected_disposable_launch(world, selected=option["option_id"],
                                                       presented=[option], measure_ticks=200)
    assert result["status"] == "launch_observed" and result["measurement"]["window_ticks"] == 200
    assert world.bridge.calls.count("order") == 1
    assert launch.disposable_launch_options(world, tick=1200) == []


def test_changed_ready_silo_cannot_execute_selected_option(world):
    world.bridge.silo["silos"][0]["status"]["rocket_silo"]["value"] = "rocket_ready"
    option = launch.disposable_launch_options(world, tick=1000)[0]
    world.bridge.silo["silos"][0]["status"]["rocket_silo"]["value"] = "building_rocket"
    with pytest.raises(launch.DisposableLaunchError, match="no longer"):
        launch.execute_selected_disposable_launch(world, selected=option["option_id"],
                                                  presented=[option], measure_ticks=200)
    assert "arm" not in world.bridge.calls and "order" not in world.bridge.calls


def test_paused_replay_routes_selected_launch_to_launch_operation(world, monkeypatch, capsys):
    from tools import run_laya_saved_world
    world.bridge.silo["silos"][0]["status"]["rocket_silo"]["value"] = "rocket_ready"
    option = launch.disposable_launch_options(world, tick=1000)[0]
    sample = facts()
    sample["tick"] = 1000
    world.query = lambda: sample
    world.options = lambda *_args: ([], {"proposed": [], "validations": {}})
    world.reconcile = lambda: None
    world.close = lambda: None
    monkeypatch.setattr(run_laya_saved_world, "owned_root", lambda path: path)
    monkeypatch.setattr(run_laya_saved_world, "SavedWorldAdapter", lambda _root: world)
    called = []
    def execute(adapter, *, selected, presented, measure_ticks):
        called.append((adapter, selected, presented, measure_ticks))
        return {"status": "launch_observed", "measurement": {"window_ticks": 200, "production": {}, "consumption": {}}}
    monkeypatch.setattr(launch, "execute_selected_disposable_launch", execute)
    status = run_laya_saved_world.main(["replay", "--root", str(world.root), "--launch-options",
                                        "--option-id", option["option_id"], "--measure-ticks", "200"])
    assert status == 0 and len(called) == 1
    assert called[0][1] == option["option_id"] and called[0][3] == 200
    assert json.loads(capsys.readouterr().out)["authority"] == "mechanics_replay"


def test_unresolved_build_blocks_launch_before_any_bridge_command(world):
    evidence = world.root / "evidence"
    evidence.mkdir()
    (evidence / "transaction-pending.json").write_text('{"status":"construction_pending"}')
    with pytest.raises(launch.DisposableLaunchError, match="unresolved evidence"):
        launch.order_disposable_rocket(world, target=TARGET)
    assert world.bridge.calls == []


def test_lua_launch_requires_exact_paused_target_and_calls_engine_once():
    lua = shutil.which("lua")
    if lua is None:
        pytest.skip("Lua is unavailable")
    source = launch.SOURCE.read_text()
    flattened = " ".join(line.split("--", 1)[0] for line in source.splitlines())
    script = r'''
      calls=0
      request={request_id='req',surface='nauvis',force='player',expected_tick=1000,
               name='rocket-silo',unit_number=7,position={x=1.5,y=2.5}}
      silo={valid=true,name='rocket-silo',unit_number=7,position={x=1.5,y=2.5},
            launch_rocket=function() calls=calls+1; return true end}
      surface={name='nauvis',find_entities_filtered=function(filter)
        assert(filter.type=='rocket-silo' and filter.force=='player'); return {silo} end}
      game={tick_paused=true,ticks_to_run=0,tick=1000,surfaces={nauvis=surface},forces={player={name='player'}}}
      assert(load([==[ ''' + flattened + r''' ]==]))
      run=assert(load([==[ ''' + source + r''' ]==]))
      result=run(); assert(result.ordered==true and result.unit_number==7 and calls==1)
      game.tick=1001; assert(not pcall(run) and calls==1); game.tick=1000
      game.tick_paused=false; assert(not pcall(run) and calls==1); game.tick_paused=true
      silo.position.x=5; assert(not pcall(run) and calls==1); silo.position.x=1.5
      silo.unit_number=8; assert(not pcall(run) and calls==1); silo.unit_number=7
      surface.find_entities_filtered=function() return {silo,silo} end
      assert(not pcall(run) and calls==1)
    '''
    result = subprocess.run([lua, "-"], input=script, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
