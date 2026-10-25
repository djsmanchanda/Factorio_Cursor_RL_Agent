# Path: tests/test_runtime_mod_split.py
# Purpose: Execute both built runtime mods and prove they load together safely,
# route distinct command/output namespaces, and build byte-stable artifacts.

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.build_runtime_mods import build_runtime_mods
from tools.runtime_namespace import (
    DETERMINISTIC_RUNTIME,
    LAYA_RUNTIME,
    RUNTIMES,
    by_mod_name,
    command_for,
    resolve,
)

LUA = shutil.which("lua") or shutil.which("lua5.4") or shutil.which("lua5.1")
pytestmark = pytest.mark.skipif(LUA is None, reason="Lua interpreter unavailable")
ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "factorio_mod"

# Every mod-registered command base name (the pre-split inventory plus the two
# runtime activation commands added by control.lua).
_BASE_COMMANDS = frozenset({
    "snapshot", "export_ghost_observation", "execute_ghost_plan",
    "execute_construction", "inspect_sandbox_topology", "reconcile_sandbox_topology",
    "export_recipe_catalog", "execute_upgrade_plan", "execute_deconstruction_plan",
    "ensure_sandbox_scaffolding", "seed_ore_patches", "seed_water_lakes",
    "build_layout_plan", "verify_electronics_execution", "set_research",
    "research_status", "research_options", "science_status", "logistic_inventory",
    "create_planner_world", "apply_ghost_plan", "activate_runtime", "deactivate_runtime",
    "spawn_construction_spidertron", "layout_executor_capabilities",
})

_LUA_HARNESS = r"""
package.path = "@@MOD@@/?.lua;" .. package.path
local registered, writes, remotes, event_fns, command_fns = {}, {}, {}, {}, {}
commands = { add_command = function(name, help, fn) registered[#registered + 1] = name; command_fns[name] = fn end }
helpers = { write_file = function(path) writes[#writes + 1] = path end,
  json_to_table = function() return {} end, table_to_json = function() return "{}" end }
remote = { interfaces = {}, add_interface = function(n) remotes[#remotes + 1] = n; remote.interfaces[n] = true end,
  call = function() return {} end }
script = { on_event = function(_name, fn) event_fns[#event_fns + 1] = fn end,
  on_init = function() end, on_configuration_changed = function() end }
defines = setmetatable({}, { __index = function(t, k) local v = {}; rawset(t, k, v); return v end })
storage = { construction_session = { completed = 0 } }
game = { tick = 1, surfaces = {}, forces = {}, players = {}, print = function() end,
  get_player = function() return nil end }
settings = { global = { @@SETTING@@ = { value = @@ACTIVE@@ } } }
local ok, err = pcall(dofile, "@@MOD@@/control.lua")
helpers.write_file("factorio_mod/snapshots/probe.json", "{}")
commands.add_command("probe_cmd", "probe", function() end)
-- Invoke every registered event with a planner-sandbox bot build. Only an
-- active runtime's robot handler mutates the construction session.
local fake = { entity = { surface = { name = "planner-sandbox" }, force = { name = "planner" },
  valid = true, name = "transport-belt" } }
for _, fn in ipairs(event_fns) do pcall(fn, fake) end
print("ok=" .. tostring(ok))
print("err=" .. tostring(err))
print("commands=" .. table.concat(registered, ","))
print("writes=" .. table.concat(writes, ","))
print("remotes=" .. table.concat(remotes, ","))
print("mutated=" .. tostring(storage.construction_session.completed))
-- Toggle activation live (no reload) and re-run the events.
for name, fn in pairs(command_fns) do
  if name:sub(-#"activate_runtime") == "activate_runtime"
     and name:sub(-#"deactivate_runtime") ~= "deactivate_runtime" then
    pcall(fn, { parameter = "" })
  end
end
for _, fn in ipairs(event_fns) do pcall(fn, fake) end
print("mutated_after=" .. tostring(storage.construction_session.completed))
"""


def _run_control(mod_dir: Path, setting_name: str, active: bool) -> dict:
    source = (
        _LUA_HARNESS.replace("@@MOD@@", str(mod_dir))
        .replace("@@SETTING@@", setting_name)
        .replace("@@ACTIVE@@", "true" if active else "false")
    )
    result = subprocess.run(
        [LUA, "-"], input=source, text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    parsed: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            parsed[key] = value
    assert parsed["ok"] == "true", parsed.get("err")
    return {
        "commands": set(filter(None, parsed["commands"].split(","))),
        "writes": [w for w in parsed["writes"].split(",") if w],
        "remotes": [r for r in parsed["remotes"].split(",") if r],
        "mutated": int(parsed["mutated"]),
        "mutated_after": int(parsed["mutated_after"]),
    }


def _build(tmp_path: Path) -> tuple[Path, dict]:
    output = tmp_path / "runtime_mods"
    manifest = build_runtime_mods(SHARED, output)
    return output, manifest


def test_both_runtime_mods_load_and_register_disjoint_commands(tmp_path: Path) -> None:
    output, _manifest = _build(tmp_path)

    deterministic = _run_control(
        output / "factorio_cursor_rl_agent", RUNTIMES[DETERMINISTIC_RUNTIME].setting_name, True,
    )
    laya = _run_control(
        output / "factorio_laya_agent", RUNTIMES[LAYA_RUNTIME].setting_name, True,
    )

    # No command name is registered twice across the two co-loaded mods.
    assert not deterministic["commands"] & laya["commands"]
    # The harness probes the shadowing by registering one extra command each.
    assert deterministic["commands"] - {"probe_cmd"} == _BASE_COMMANDS
    assert laya["commands"] - {"laya_probe_cmd"} == {f"laya_{name}" for name in _BASE_COMMANDS}
    assert "laya_probe_cmd" in laya["commands"] and "probe_cmd" in deterministic["commands"]
    assert "probe_cmd" not in laya["commands"]


def test_runtime_mods_route_output_and_remote_interfaces(tmp_path: Path) -> None:
    output, _manifest = _build(tmp_path)

    deterministic = _run_control(
        output / "factorio_cursor_rl_agent", RUNTIMES[DETERMINISTIC_RUNTIME].setting_name, True,
    )
    laya = _run_control(
        output / "factorio_laya_agent", RUNTIMES[LAYA_RUNTIME].setting_name, True,
    )

    assert deterministic["writes"] == ["factorio_mod/snapshots/probe.json"]
    assert laya["writes"] == ["factorio_laya_mod/snapshots/probe.json"]
    assert deterministic["remotes"] == ["factorio_cursor_rl_agent"]
    assert laya["remotes"] == ["factorio_laya_agent"]
    # An inactive runtime applies no gameplay side effects, even though the
    # Laya coach module is loaded; the active runtime's robot handler runs once.
    inactive_laya = _run_control(
        output / "factorio_laya_agent", RUNTIMES[LAYA_RUNTIME].setting_name, False,
    )
    inactive_deterministic = _run_control(
        output / "factorio_cursor_rl_agent", RUNTIMES[DETERMINISTIC_RUNTIME].setting_name, False,
    )
    assert deterministic["mutated"] == 1
    assert laya["mutated"] == 1
    assert inactive_laya["mutated"] == 0
    assert inactive_deterministic["mutated"] == 0
    # Activating the runtime in-game takes effect on the next event without a
    # reload: the setting is a runtime-global data setting, not startup code.
    assert inactive_laya["mutated_after"] == 1
    assert inactive_deterministic["mutated_after"] == 1


def test_runtime_mods_share_source_and_only_stamp_identity(tmp_path: Path) -> None:
    output, manifest = _build(tmp_path)
    deterministic_dir = output / "factorio_cursor_rl_agent"
    laya_dir = output / "factorio_laya_agent"

    # The deterministic artifact is the shared source verbatim.
    shared_files = {p.relative_to(SHARED) for p in SHARED.rglob("*") if p.is_file()}
    for relative in shared_files:
        assert (deterministic_dir / relative).read_bytes() == (SHARED / relative).read_bytes()

    # Only the stamped identity files differ between the two mods.
    for relative in shared_files:
        if relative.name in {"info.json", "settings.lua", "namespace_override.lua"}:
            continue
        assert (laya_dir / relative).read_bytes() == (SHARED / relative).read_bytes()

    assert manifest["factorio_cursor_rl_agent"]["tree_sha256"] == (
        build_runtime_mods(SHARED, tmp_path / "again")["factorio_cursor_rl_agent"]["tree_sha256"]
    )


def test_build_hashes_are_independent_of_runtime_selection(tmp_path: Path) -> None:
    both = build_runtime_mods(SHARED, tmp_path / "both")
    only_deterministic = build_runtime_mods(
        SHARED, tmp_path / "det", runtimes=(DETERMINISTIC_RUNTIME,),
    )
    only_laya = build_runtime_mods(SHARED, tmp_path / "laya", runtimes=(LAYA_RUNTIME,))
    assert both["factorio_cursor_rl_agent"]["tree_sha256"] == only_deterministic["factorio_cursor_rl_agent"]["tree_sha256"]
    assert both["factorio_laya_agent"]["tree_sha256"] == only_laya["factorio_laya_agent"]["tree_sha256"]


def test_game_bridge_routes_commands_and_reports_by_runtime() -> None:
    from orchestrator.game_bridge import (
        RESEARCH_REPORT_SUBDIR,
        SNAPSHOT_SUBDIR,
        GameBridge,
    )

    def recording(runtime: str):
        bridge = GameBridge.__new__(GameBridge)
        bridge._namespace = resolve(runtime)
        calls: list[tuple[str, Path]] = []
        bridge._run_and_collect = (  # type: ignore[method-assign]
            lambda command, subdir, timeout, **kw: calls.append((command, subdir)) or Path("x.json")
        )
        return bridge, calls

    det_bridge, det_calls = recording(DETERMINISTIC_RUNTIME)
    det_bridge.request_snapshot(surface="nauvis")
    det_bridge.research_status(force="player", technology="logistics")
    assert det_calls[0] == ("/snapshot nauvis", SNAPSHOT_SUBDIR)
    assert det_calls[1][1] == RESEARCH_REPORT_SUBDIR
    assert det_calls[1][0].startswith("/research_status ")

    laya_bridge, laya_calls = recording(LAYA_RUNTIME)
    laya_bridge.request_snapshot(surface="nauvis")
    laya_bridge.research_status(force="player", technology="logistics")
    assert laya_calls[0] == ("/laya_snapshot nauvis", Path("factorio_laya_mod/snapshots"))
    assert laya_calls[1][1] == Path("factorio_laya_mod/research_reports")
    assert laya_calls[1][0].startswith("/laya_research_status ")


def test_runtime_namespace_api_matches_lua_contract() -> None:
    deterministic = resolve(DETERMINISTIC_RUNTIME)
    laya = resolve(LAYA_RUNTIME)
    assert deterministic.command("snapshot") == "/snapshot"
    assert laya.command("/snapshot") == "/laya_snapshot"
    assert command_for(LAYA_RUNTIME, "build_layout_plan") == "/laya_build_layout_plan"
    assert laya.remap_output_subdir("factorio_mod/snapshots") == Path("factorio_laya_mod/snapshots")
    assert deterministic.remap_output_subdir("factorio_mod/snapshots") == Path("factorio_mod/snapshots")
    assert by_mod_name("factorio_laya_agent") is laya
    assert {ns.mod_name for ns in RUNTIMES.values()} == {
        "factorio_cursor_rl_agent", "factorio_laya_agent",
    }
    # The Python table and the stamped Lua override agree.
    for namespace in RUNTIMES.values():
        override = (SHARED / "namespace_override.lua").read_text()
        if namespace.runtime == DETERMINISTIC_RUNTIME:
            assert f'command_prefix = "{namespace.command_prefix}"' in override


def test_activation_writes_runtime_setting_back_to_engine(tmp_path):
    # LuaSettings returns a setting value record; mutating that record alone
    # does not write through to the engine's settings dictionary.
    source = f'''package.path = "{SHARED}/?.lua;" .. package.path
local values = {{ factorio_cursor_rl_agent_active = true }}
settings = {{ global = setmetatable({{}}, {{
  __index = function(_, key) return {{ value = values[key] }} end,
  __newindex = function(_, key, record) values[key] = record.value end,
}}) }}
local namespace = require("namespace")
assert(namespace.set_active(false))
assert(not namespace.is_active(), "runtime activation did not persist")
assert(namespace.set_active(true))
assert(namespace.is_active())
'''
    result = subprocess.run([LUA, '-'], input=source, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_campaign_builder_accepts_frozen_root_and_includes_training_mod(tmp_path):
    import shutil
    import sys
    code = tmp_path / 'frozen-code'
    shutil.copytree(SHARED, code / 'factorio_mod')
    (code / 'factorio_training_lab').mkdir()
    (code / 'factorio_training_lab/control.lua').write_text('-- training fixture\n')
    (code / 'factorio_training_lab/info.json').write_text('{"name":"factorio_training_lab","version":"0.1.0"}')
    output = tmp_path / 'mods'
    command = [sys.executable, str(SHARED.parent / 'tools/build_runtime_mods.py'),
               '--source-root', str(code), '--output-root', str(output)]
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert {p.name for p in output.iterdir() if (p / 'info.json').is_file()} == {
        'factorio_cursor_rl_agent', 'factorio_laya_agent', 'factorio_training_lab'}
