# Path: tests/test_runtime_identity_probe_lua.py
# Purpose: Exercise the read-only, nonce-correlated runtime identity probe against focused Factorio API stubs.

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "factorio_mod" / "runtime_identity_probe.lua"
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

local nauvis = { name = "nauvis", index = 1, valid = true }
local planner_sandbox = { name = "planner-sandbox", index = 2, valid = true }
game = {
  tick = 1000,
  surfaces = { nauvis = nauvis, ["planner-sandbox"] = planner_sandbox },
  forces = {
    player = { name = "player", index = 1, valid = true },
    planner = { name = "planner", index = 2, valid = true }
  }
}
script = {
  active_mods = {
    base = "2.0.77",
    factorio_cursor_rl_agent = "0.2.0"
  }
}
storage = {}
next_payload = nil
'''


def test_runtime_identity_probe_is_registered_once_and_never_self_registers() -> None:
    source = MODULE.read_text(encoding="utf-8")
    control = CONTROL.read_text(encoding="utf-8")
    registration = 'commands.add_command(\n  "runtime_identity_probe"'

    assert 'require("runtime_identity_probe")' in control
    assert registration in source
    assert "script.on_event" not in source
    assert sum(
        path.read_text(encoding="utf-8").count(registration)
        for path in (ROOT / "factorio_mod").glob("*.lua")
    ) == 1


def test_runtime_identity_probe_has_no_game_mutation_surface() -> None:
    source = MODULE.read_text(encoding="utf-8")

    for forbidden in (
        "create_entity", "create_force", "create_surface", "destroy(",
        "set_research", "insert_item", "remove_item", "revive(", "write_file",
    ):
        if forbidden == "write_file":
            # The report itself is the only allowed write.
            assert source.count(forbidden) == 1
            continue
        assert forbidden not in source


@pytest.fixture
def lua():
    if lupa is None:
        pytest.skip("pip install lupa for behavioural Lua validation")
    runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
    runtime.execute(_STUB)
    runtime.execute(f'dofile("{MODULE.as_posix()}")')
    return runtime


def _set_payload(lua, fields: dict | None) -> None:
    if fields is None:
        lua.execute("next_payload = nil")
        return
    parts = []
    for key, value in fields.items():
        if isinstance(value, bool):
            encoded = "true" if value else "false"
        elif isinstance(value, (int, float)):
            encoded = str(value)
        elif isinstance(value, str):
            encoded = repr(value)
        else:
            raise TypeError(f"unsupported test payload value: {value!r}")
        parts.append(f"{key} = {encoded}")
    lua.execute("next_payload = { " + ", ".join(parts) + " }")


def _probe(lua) -> None:
    lua.execute(
        'commands.registered["runtime_identity_probe"]({ parameter = "{}" })'
    )


def _report(lua, index: int = 1):
    return lua.globals().write_at(index)["payload"]


def test_probe_reports_only_engine_facts_with_a_nonce_correlation(lua) -> None:
    _set_payload(lua, {
        "nonce": "probe-abc_123",
        "surface": "nauvis",
        "force": "player",
    })
    _probe(lua)

    assert lua.globals().write_count() == 1
    write = lua.globals().write_at(1)
    assert write["path"] == (
        "factorio_mod/runtime_identity_probes/runtime_identity_probe-abc_123.json"
    )
    assert write["append"] is False
    report = _report(lua)
    assert report["schema_version"] == "1.0.0"
    assert report["kind"] == "runtime_identity_probe"
    assert report["ok"] is True
    assert report["nonce"] == "probe-abc_123"
    assert report["tick"] == 1000
    assert report["surface"]["name"] == "nauvis"
    assert report["surface"]["index"] == 1
    assert report["force"]["name"] == "player"
    assert report["force"]["index"] == 1
    assert report["active_mods"]["base"] == "2.0.77"
    assert report["active_mods"]["factorio_cursor_rl_agent"] == "0.2.0"
    assert report["active_mods_count"] == 2
    assert report["ownership_claimed"] is False
    assert report["dispatch_authorized"] is False
    assert "save_identity" not in report
    assert "engine_version" not in report
    assert len(lua.globals().storage) == 0


def test_probe_uses_the_explicitly_requested_existing_surface_and_force(lua) -> None:
    _set_payload(lua, {
        "nonce": "sandbox-1",
        "surface": "planner-sandbox",
        "force": "planner",
    })
    _probe(lua)

    report = _report(lua)
    assert report["ok"] is True
    assert report["surface"]["name"] == "planner-sandbox"
    assert report["surface"]["index"] == 2
    assert report["force"]["name"] == "planner"
    assert report["force"]["index"] == 2


def test_unknown_surface_fails_closed_but_keeps_the_request_nonce(lua) -> None:
    _set_payload(lua, {
        "nonce": "missing-surface",
        "surface": "missing",
        "force": "player",
    })
    _probe(lua)

    assert lua.globals().write_count() == 1
    assert lua.globals().write_at(1)["path"] == (
        "factorio_mod/runtime_identity_probes/runtime_identity_missing-surface.json"
    )
    report = _report(lua)
    assert report["ok"] is False
    assert report["nonce"] == "missing-surface"
    assert report["surface"] == "missing"
    assert report["force"] == "player"
    assert "Unknown surface: missing" in report["error"]
    assert report["ownership_claimed"] is False
    assert report["dispatch_authorized"] is False
    assert "active_mods" not in report


def test_unknown_force_fails_closed(lua) -> None:
    _set_payload(lua, {
        "nonce": "missing-force",
        "surface": "nauvis",
        "force": "missing",
    })
    _probe(lua)

    report = _report(lua)
    assert report["ok"] is False
    assert "Unknown force: missing" in report["error"]


@pytest.mark.parametrize(
    "fields, expected_error",
    [
        ({"nonce": "no-surface", "force": "player"}, "non-empty surface"),
        ({"nonce": "no-force", "surface": "nauvis"}, "non-empty force"),
        (
            {"nonce": "extra-field", "surface": "nauvis", "force": "player", "authorized": True},
            "unsupported field",
        ),
        (
            {"nonce": "bad-value", "surface": 7, "force": "player"},
            "non-empty surface",
        ),
    ],
)
def test_invalid_payload_with_a_safe_nonce_writes_only_a_correlated_failure(
    lua, fields, expected_error,
) -> None:
    _set_payload(lua, fields)
    _probe(lua)

    assert lua.globals().write_count() == 1
    assert lua.globals().write_at(1)["path"] == (
        "factorio_mod/runtime_identity_probes/runtime_identity_"
        + fields["nonce"] + ".json"
    )
    report = _report(lua)
    assert report["ok"] is False
    assert report["nonce"] == fields["nonce"]
    assert expected_error in report["error"]
    assert report["ownership_claimed"] is False
    assert report["dispatch_authorized"] is False


@pytest.mark.parametrize(
    "nonce",
    [None, "", "bad nonce", "../escape", "a" * 65, "semi;colon"],
)
def test_missing_or_unsafe_nonce_writes_nothing(lua, nonce) -> None:
    fields = {"surface": "nauvis", "force": "player"}
    if nonce is not None:
        fields["nonce"] = nonce
    _set_payload(lua, fields)
    _probe(lua)

    assert lua.globals().write_count() == 0
    assert len(lua.globals().storage) == 0


def test_invalid_json_writes_nothing_because_there_is_no_safe_nonce(lua) -> None:
    lua.execute("next_payload = 'not-a-table'")
    _probe(lua)

    assert lua.globals().write_count() == 0


def test_missing_active_mods_fails_closed(lua) -> None:
    lua.execute("script.active_mods = nil")
    _set_payload(lua, {
        "nonce": "no-mods",
        "surface": "nauvis",
        "force": "player",
    })
    _probe(lua)

    report = _report(lua)
    assert report["ok"] is False
    assert "Active mod list is unavailable" in report["error"]
    assert "active_mods" not in report


def test_reusing_a_nonce_overwrites_the_same_correlated_report_path(lua) -> None:
    fields = {"nonce": "repeat-1", "surface": "nauvis", "force": "player"}
    _set_payload(lua, fields)
    _probe(lua)
    lua.execute("game.tick = 2000")
    _set_payload(lua, fields)
    _probe(lua)

    assert lua.globals().write_count() == 2
    assert lua.globals().write_at(1)["path"] == lua.globals().write_at(2)["path"]
    assert _report(lua, 1)["tick"] == 1000
    assert _report(lua, 2)["tick"] == 2000
