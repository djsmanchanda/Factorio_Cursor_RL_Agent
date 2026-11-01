# Path: tests/test_rocket_launch_telemetry_lua.py
# Purpose: Exercise scope-explicit, observation-only rocket launch recording against focused Factorio API stubs.

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "factorio_mod" / "rocket_launch_telemetry.lua"
CONTROL = ROOT / "factorio_mod" / "control.lua"

try:
    import lupa
except ImportError:  # pragma: no cover - static coverage remains useful without Lua.
    lupa = None


_STUB = r'''
commands = { registered = {} }
function commands.add_command(name, _, callback)
  commands.registered[name] = callback
end

written = {}
last_payload = nil
helpers = {
  json_to_table = function(_) return next_payload end,
  table_to_json = function(payload)
    last_payload = payload
    return "{}"
  end,
  write_file = function(path, _, append)
    written[#written + 1] = { path = path, payload = last_payload, append = append }
  end
}
function write_count() return #written end
function write_at(index) return written[index] end

local nauvis = { name = "nauvis" }
local planner_sandbox = { name = "planner-sandbox" }
game = {
  tick = 1000,
  surfaces = { nauvis = nauvis, ["planner-sandbox"] = planner_sandbox },
  forces = { player = { name = "player" }, planner = { name = "planner" } }
}
storage = {}
next_payload = { surface = "nauvis", force = "player", enabled = true }

function entity(name, surface, force, x, y, unit_number)
  return {
    valid = true,
    name = name,
    surface = surface,
    force = force,
    position = { x = x, y = y },
    unit_number = unit_number
  }
end
'''


def test_rocket_launch_telemetry_is_registered_once_and_never_self_registers() -> None:
    source = MODULE.read_text(encoding="utf-8")
    control = CONTROL.read_text(encoding="utf-8")
    registration = "script.on_event(defines.events.on_rocket_launched"

    assert 'local rocket_launch_telemetry = require("rocket_launch_telemetry")' in control
    assert (
        "script.on_event(defines.events.on_rocket_launched, "
        "rocket_launch_telemetry.on_rocket_launched)"
    ) in control
    assert "script.on_event" not in source
    assert "on_rocket_launch_ordered" not in source
    assert "on_rocket_launch_ordered" not in control
    assert sum(
        path.read_text(encoding="utf-8").count(registration)
        for path in (ROOT / "factorio_mod").glob("*.lua")
    ) == 1


def test_rocket_launch_module_has_no_game_mutation_surface() -> None:
    source = MODULE.read_text(encoding="utf-8")

    for forbidden in (
        "create_entity", "create_force", "launch_rocket", "set_research",
        "insert_item", "remove_item", "destroy(", "revive(",
    ):
        assert forbidden not in source


@pytest.fixture
def lua():
    if lupa is None:
        pytest.skip("pip install lupa for behavioural Lua validation")
    runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
    runtime.execute(_STUB)
    runtime.globals().module = runtime.eval(f'dofile("{MODULE.as_posix()}")')
    return runtime


def _arm(
    lua, surface: str = "nauvis", force: str = "player", enabled: bool = True,
    request_id: str = "request42", session_id: str = "session42",
) -> None:
    lua.execute(
        "next_payload = { surface = "
        + repr(surface)
        + ", force = "
        + repr(force)
        + ", enabled = "
        + ("true" if enabled else "false")
        + ", request_id = "
        + repr(request_id)
        + ", session_id = "
        + repr(session_id)
        + " }"
    )
    lua.execute('commands.registered["rocket_launch_observation"]({ parameter = "{}" })')


def _launch(
    lua, surface: str = "nauvis", force: str = "player", tick: int = 1100,
) -> None:
    target = (
        "game.surfaces.nauvis, game.forces.player"
        if (surface, force) == ("nauvis", "player")
        else "game.surfaces['planner-sandbox'], game.forces.planner"
    )
    lua.execute(
        "local surface, force = "
        + target
        + f"; module.on_rocket_launched({{ tick = {tick}, "
        "rocket = entity('rocket', surface, force, 1.5, 2.5, 42), "
        "rocket_silo = entity('rocket-silo', surface, force, 3.5, 4.5, 7) })"
    )


def test_scope_command_arms_an_explicit_surface_and_force(lua) -> None:
    _arm(lua)

    assert lua.globals().write_count() == 1
    write = lua.globals().write_at(1)
    assert write["path"] == (
        "factorio_mod/rocket_launch_observations/rocket_launch_scope_request42.json"
    )
    assert write["append"] is False
    report = write["payload"]
    assert report["schema_version"] == "1.0.0"
    assert report["kind"] == "rocket_launch_scope"
    assert report["ok"] is True
    assert report["request_id"] == "request42"
    assert report["surface"] == "nauvis"
    assert report["force"] == "player"
    assert report["enabled"] is True
    assert report["scope_since_tick"] == 1000
    assert report["recorded_launches"] == 0
    assert report["session_started"] is True
    assert report["session_id"] == "session42"
    assert lua.globals().storage["rocket_launch_observation"]["enabled"] is True


def test_matching_launch_records_the_actual_event_and_not_victory(lua) -> None:
    _arm(lua)
    _launch(lua)

    assert lua.globals().write_count() == 2
    write = lua.globals().write_at(2)
    assert write["path"] == (
        "factorio_mod/rocket_launch_events/rocket_launch_session42_1100_1.json"
    )
    event = write["payload"]
    assert event["schema_version"] == "1.0.0"
    assert event["kind"] == "rocket_launch"
    assert event["event"] == "on_rocket_launched"
    assert event["tick"] == 1100
    assert event["surface"] == "nauvis"
    assert event["force"] == "player"
    assert event["sequence"] == 1
    assert event["session_id"] == "session42"
    rocket = event["rocket"]
    assert rocket["name"] == "rocket"
    assert rocket["unit_number"] == 42
    assert rocket["position"]["x"] == 1.5
    assert rocket["position"]["y"] == 2.5
    silo = event["rocket_silo"]
    assert silo["name"] == "rocket-silo"
    assert silo["unit_number"] == 7
    assert silo["position"]["x"] == 3.5
    assert silo["position"]["y"] == 4.5
    assert "victory" not in event
    assert "cargo_delivered" not in event
    assert "ok" not in event
    assert lua.globals().storage["rocket_launch_observation"]["recorded_launches"] == 1


def test_mocked_lua_session_replays_as_targeted_evidence(lua, tmp_path) -> None:
    import json

    from tests.test_runtime_catalog_lua import _lua_to_python
    from tools.rocket_launch_evidence import collect_launch_session

    _arm(lua, request_id="arm42", session_id="session42")
    _launch(lua, tick=1100)
    lua.execute("game.tick = 1200")
    _arm(lua, enabled=False, request_id="close42", session_id="unused")
    start = _lua_to_python(lua.globals().write_at(1)["payload"])
    event_write = lua.globals().write_at(2)
    event = _lua_to_python(event_write["payload"])
    end = _lua_to_python(lua.globals().write_at(3)["payload"])
    (tmp_path / Path(event_write["path"]).name).write_text(json.dumps(event))
    evidence = collect_launch_session(tmp_path, start=start, end=end, target_silo=event["rocket_silo"])
    assert evidence.status == "matching_event_found"
    assert evidence.matching_sequences == (1,)


def test_fresh_session_keeps_rewound_tick_and_sequence_events_distinct(lua) -> None:
    _arm(lua, request_id="arm-1", session_id="session-a")
    _launch(lua, tick=1100)

    lua.execute(
        "storage.rocket_launch_observation.sequence = 0; "
        "storage.rocket_launch_observation.enabled = false"
    )
    _arm(lua, request_id="arm-2", session_id="session-b")
    _launch(lua, tick=1100)

    paths = [lua.globals().write_at(index)["path"] for index in range(1, 5)]
    assert paths[1] == (
        "factorio_mod/rocket_launch_events/rocket_launch_session-a_1100_1.json"
    )
    assert paths[3] == (
        "factorio_mod/rocket_launch_events/rocket_launch_session-b_1100_1.json"
    )


def test_launch_outside_the_armed_scope_is_ignored(lua) -> None:
    _arm(lua)
    _launch(lua, surface="planner-sandbox", force="planner")

    assert lua.globals().write_count() == 1
    assert lua.globals().storage["rocket_launch_observation"]["recorded_launches"] == 0


def test_disabled_scope_records_no_launch(lua) -> None:
    _arm(lua, enabled=False)
    _launch(lua)

    assert lua.globals().write_count() == 1
    assert lua.globals().write_at(1)["payload"]["enabled"] is False
    assert lua.globals().storage["rocket_launch_observation"]["recorded_launches"] == 0


def test_invalid_target_writes_only_a_correlated_failure_report(lua) -> None:
    lua.execute(
        'next_payload = { surface = "missing", force = "player", enabled = true, '
        'request_id = "request-fail" }'
    )
    lua.execute('commands.registered["rocket_launch_observation"]({ parameter = "{}" })')

    assert lua.globals().write_count() == 1
    write = lua.globals().write_at(1)
    assert write["path"] == (
        "factorio_mod/rocket_launch_observations/"
        "rocket_launch_scope_request-fail.json"
    )
    report = write["payload"]
    assert report["ok"] is False
    assert report["request_id"] == "request-fail"
    assert report["surface"] == "missing"
    assert report["force"] == "player"
    assert "Unknown surface: missing" in report["error"]
    assert lua.globals().storage["rocket_launch_observation"] is None


def test_invalid_request_id_is_rejected_before_arming(lua) -> None:
    lua.execute(
        'next_payload = { surface = "nauvis", force = "player", enabled = true, '
        'request_id = "bad id!" }'
    )
    lua.execute('commands.registered["rocket_launch_observation"]({ parameter = "{}" })')

    assert lua.globals().write_count() == 1
    write = lua.globals().write_at(1)
    assert write["path"] == (
        "factorio_mod/rocket_launch_observations/rocket_launch_scope_1000_1.json"
    )
    report = write["payload"]
    assert report["ok"] is False
    assert "request_id" in report["error"]
    assert lua.globals().storage["rocket_launch_observation"] is None


def test_rearming_a_different_scope_resets_the_launch_count_but_not_file_sequence(lua) -> None:
    _arm(lua)
    _launch(lua)
    lua.execute("game.tick = 2000")
    _arm(
        lua, surface="planner-sandbox", force="planner",
        request_id="request99", session_id="session99",
    )
    lua.execute(
        "module.on_rocket_launched({ tick = 2100, "
        "rocket = entity('rocket', game.surfaces['planner-sandbox'], game.forces.planner, 0, 0, 50) })"
    )

    state = lua.globals().storage["rocket_launch_observation"]
    assert state["surface"] == "planner-sandbox"
    assert state["force"] == "planner"
    assert state["session_id"] == "session99"
    assert state["recorded_launches"] == 1
    assert lua.globals().write_at(4)["path"] == (
        "factorio_mod/rocket_launch_events/rocket_launch_session99_2100_2.json"
    )


def test_same_scope_disarm_rearm_starts_a_fresh_observation(lua) -> None:
    _arm(lua, request_id="arm-1", session_id="session-a")
    _launch(lua)
    lua.execute("game.tick = 2000")
    _arm(
        lua, enabled=False, request_id="disarm", session_id="session-b",
    )
    disarmed = lua.globals().write_at(3)["payload"]
    assert disarmed["enabled"] is False
    assert disarmed["session_started"] is False
    assert disarmed["scope_since_tick"] == 1000
    assert disarmed["recorded_launches"] == 1
    assert disarmed["session_id"] == "session-a"

    lua.execute("game.tick = 3000")
    _arm(lua, request_id="arm-2", session_id="session-c")
    rearmed = lua.globals().write_at(4)["payload"]
    assert rearmed["enabled"] is True
    assert rearmed["session_started"] is True
    assert rearmed["scope_since_tick"] == 3000
    assert rearmed["recorded_launches"] == 0
    assert rearmed["session_id"] == "session-c"

    _launch(lua, tick=3100)
    event = lua.globals().write_at(5)
    assert event["path"] == (
        "factorio_mod/rocket_launch_events/rocket_launch_session-c_3100_2.json"
    )
    assert event["payload"]["sequence"] == 2
    assert event["payload"]["session_id"] == "session-c"
    assert lua.globals().storage["rocket_launch_observation"]["recorded_launches"] == 1


def test_disarm_of_a_new_scope_does_not_claim_a_session_start(lua) -> None:
    _arm(
        lua, surface="planner-sandbox", force="planner",
        enabled=False, request_id="disarm", session_id="session-b",
    )

    report = lua.globals().write_at(1)["payload"]
    assert report["enabled"] is False
    assert report["session_started"] is False
    assert "session_id" not in report


def test_redundant_enable_of_the_same_scope_preserves_continuity(lua) -> None:
    _arm(lua, request_id="arm-1", session_id="session-a")
    _launch(lua)
    lua.execute("game.tick = 1500")
    _arm(lua, request_id="arm-2", session_id="session-b")

    report = lua.globals().write_at(3)["payload"]
    assert report["session_started"] is False
    assert report["scope_since_tick"] == 1000
    assert report["recorded_launches"] == 1
    assert report["session_id"] == "session-a"


def test_same_tick_scope_requests_with_distinct_ids_do_not_collide(lua) -> None:
    _arm(lua, request_id="request-a", session_id="session-a")
    _arm(lua, request_id="request-b", session_id="session-b")

    assert lua.globals().write_count() == 2
    first = lua.globals().write_at(1)
    second = lua.globals().write_at(2)
    assert first["path"] == (
        "factorio_mod/rocket_launch_observations/rocket_launch_scope_request-a.json"
    )
    assert second["path"] == (
        "factorio_mod/rocket_launch_observations/rocket_launch_scope_request-b.json"
    )
    assert first["payload"]["request_id"] == "request-a"
    assert second["payload"]["request_id"] == "request-b"


def test_same_tick_requests_without_request_ids_fall_back_to_distinct_sequences(lua) -> None:
    lua.execute(
        "next_payload = { surface = 'nauvis', force = 'player', enabled = true, "
        "session_id = 'session-a' }"
    )
    lua.execute('commands.registered["rocket_launch_observation"]({ parameter = "{}" })')
    lua.execute(
        "next_payload = { surface = 'nauvis', force = 'player', enabled = true, "
        "session_id = 'session-b' }"
    )
    lua.execute('commands.registered["rocket_launch_observation"]({ parameter = "{}" })')

    assert lua.globals().write_at(1)["path"] == (
        "factorio_mod/rocket_launch_observations/rocket_launch_scope_1000_1.json"
    )
    assert lua.globals().write_at(2)["path"] == (
        "factorio_mod/rocket_launch_observations/rocket_launch_scope_1000_2.json"
    )


def test_fresh_arm_without_session_identity_is_rejected(lua) -> None:
    lua.execute(
        "next_payload = { surface = 'nauvis', force = 'player', enabled = true }"
    )
    lua.execute('commands.registered["rocket_launch_observation"]({ parameter = "{}" })')

    report = lua.globals().write_at(1)["payload"]
    assert report["ok"] is False
    assert "session_id" in report["error"]
    assert lua.globals().storage["rocket_launch_observation"]["enabled"] is False
    assert lua.globals().storage["rocket_launch_observation"]["recorded_launches"] == 0


def test_invalid_session_identity_is_rejected_before_arming(lua) -> None:
    lua.execute(
        'next_payload = { surface = "nauvis", force = "player", enabled = true, '
        'session_id = "bad id!" }'
    )
    lua.execute('commands.registered["rocket_launch_observation"]({ parameter = "{}" })')

    report = lua.globals().write_at(1)["payload"]
    assert report["ok"] is False
    assert "session_id" in report["error"]
    assert lua.globals().storage["rocket_launch_observation"] is None


def test_reused_session_identity_is_rejected_on_same_scope_rearm(lua) -> None:
    _arm(lua, request_id="arm-1", session_id="session-a")
    _launch(lua)
    _arm(lua, enabled=False, request_id="disarm", session_id="session-b")
    _arm(lua, request_id="arm-2", session_id="session-a")

    report = lua.globals().write_at(4)["payload"]
    assert report["ok"] is False
    assert "already used" in report["error"]
    state = lua.globals().storage["rocket_launch_observation"]
    assert state["enabled"] is False
    assert state["session_id"] == "session-a"

    _launch(lua, tick=2500)
    assert lua.globals().write_count() == 4


def test_legacy_armed_state_without_session_id_marks_events_unidentified(lua) -> None:
    lua.execute(
        "storage.rocket_launch_observation = { enabled = true, sequence = 0, "
        "recorded_launches = 0, scope_since_tick = 1000, "
        "surface = 'nauvis', force = 'player' }"
    )
    _launch(lua)

    write = lua.globals().write_at(1)
    assert write["path"] == (
        "factorio_mod/rocket_launch_events/rocket_launch_unidentified_1100_1.json"
    )
    assert write["payload"]["session_id"] == "unidentified"
