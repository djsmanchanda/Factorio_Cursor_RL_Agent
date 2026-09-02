# Path: tests/test_upgrades_lua.py
# Purpose: Exercise native bot-driven upgrades and the explicit real-base bridge scope.

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.game_bridge import EXECUTION_REPORT_SUBDIR, GameBridge

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "factorio_mod" / "upgrades.lua"

try:
    import lupa
except ImportError:  # pragma: no cover - static coverage remains useful without Lua.
    lupa = None


_STUB = r'''
commands = { registered = {} }
function commands.add_command(name, _, callback)
  commands.registered[name] = callback
end

captured_report, written_path, ordered_target, order_count = nil, nil, nil, 0
helpers = {
  json_to_table = function(_) return next_payload end,
  table_to_json = function(report)
    captured_report = report
    return '{}'
  end,
  write_file = function(path, _, _) written_path = path end,
}

player_force = { name = "player" }
replacement = {
  name = "assembling-machine-2",
  type = "assembling-machine",
  fast_replaceable_group = "assembling-machine",
  crafting_categories = { crafting = true },
}
downgrade = {
  name = "assembling-machine-1",
  type = "assembling-machine",
  fast_replaceable_group = "assembling-machine",
  crafting_categories = { crafting = true },
}
target = {
  valid = true,
  name = "assembling-machine-1",
  type = "assembling-machine",
  position = { x = 4.5, y = 2.5 },
  quality = { name = "normal" },
  prototype = downgrade,
}
function target.to_be_deconstructed() return false end
function target.to_be_upgraded() return false end
function target.get_upgrade_target() return nil, nil end
function target.get_recipe()
  return { name = "iron-gear-wheel", categories = { "crafting" } }
end
function target.order_upgrade(order)
  order_count = order_count + 1
  ordered_target = order.target
  ordered_force = order.force
  return true
end

surface = {
  name = "nauvis",
  create_entity = function(_) error("tier upgrades must not create entities") end,
}
game = {
  tick = 123456,
  surfaces = { nauvis = surface },
  forces = { player = player_force },
  print = function(_) end,
}
prototypes = { entity = {
  ["assembling-machine-1"] = downgrade,
  ["assembling-machine-2"] = replacement,
} }
defines = { inventory = { crafter_modules = 1 } }

package.preload["sandbox_shared"] = function()
  return {
    get_or_create_sandbox_surface = function() error("explicit scope expected") end,
    get_or_create_planner_force = function() error("explicit scope expected") end,
    find_exact_entity = function(_, _, name, position)
      if position.x ~= target.position.x or position.y ~= target.position.y then return nil end
      if name == target.name then return target end
      return nil
    end,
  }
end

next_payload = {
  surface = "nauvis",
  force = "player",
  authorization = {
    approved_actions = { "apply_upgrades" },
    scope_limits = { max_count = 1 },
  },
  upgrade_plan = { actions = {{
    action = "assembler_tier_upgrade",
    from_name = "assembling-machine-1",
    to_name = "assembling-machine-2",
    recipe = "iron-gear-wheel",
    position = { x = 4.5, y = 2.5 },
  }}},
}
'''


def test_upgrade_module_uses_the_native_order_api() -> None:
    source = MODULE.read_text(encoding="utf-8")

    assert "target.order_upgrade" in source
    assert 'name = "entity-ghost"' not in source
    assert "fast_replaceable_group" in source
    assert "recipe_not_supported" in source


@pytest.fixture
def lua():
    if lupa is None:
        pytest.skip("pip install lupa for behavioural Lua validation")
    runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
    runtime.execute(_STUB)
    runtime.execute(f'dofile("{MODULE.as_posix()}")')
    return runtime


def _run(lua) -> None:
    lua.execute('commands.registered["execute_upgrade_plan"]({ parameter = "{}" })')


def test_real_base_upgrade_is_ordered_without_rebuilding(lua) -> None:
    _run(lua)

    report = lua.globals().captured_report
    assert report["surface"] == "nauvis"
    assert report["force"] == "player"
    assert report["actions"][1]["status"] == "success"
    assert report["actions"][1]["reason"] == "upgrade_ordered"
    assert lua.globals().order_count == 1
    assert lua.globals().ordered_target["name"] == "assembling-machine-2"
    assert lua.globals().ordered_target["quality"] == "normal"
    assert lua.globals().ordered_force["name"] == "player"
    assert lua.globals().written_path == (
        "factorio_mod/execution_reports/upgrade_report_123456.json"
    )


def test_downgrade_is_rejected_when_the_active_recipe_is_incompatible(lua) -> None:
    lua.execute('''
      target.name = "assembling-machine-2"
      target.prototype = replacement
      replacement.crafting_categories = { ["crafting-with-fluid"] = true }
      function target.get_recipe()
        return { name = "processing-unit", categories = { "crafting-with-fluid" } }
      end
      next_payload.upgrade_plan.actions[1].from_name = "assembling-machine-2"
      next_payload.upgrade_plan.actions[1].to_name = "assembling-machine-1"
      next_payload.upgrade_plan.actions[1].recipe = "processing-unit"
    ''')

    _run(lua)

    result = lua.globals().captured_report["actions"][1]
    assert result["status"] == "failed"
    assert result["reason"] == "recipe_not_supported"
    assert lua.globals().order_count == 0


def _recording_bridge() -> tuple[GameBridge, list[tuple[str, Path, float]]]:
    bridge = GameBridge.__new__(GameBridge)
    calls: list[tuple[str, Path, float]] = []

    def collect(command: str, subdir: Path, timeout: float) -> Path:
        calls.append((command, subdir, timeout))
        return Path("upgrade-report.json")

    bridge._run_and_collect = collect  # type: ignore[method-assign]
    return bridge, calls


def test_upgrade_bridge_sends_explicit_surface_and_force() -> None:
    bridge, calls = _recording_bridge()
    authorization = {"approved_actions": ["apply_upgrades"]}
    plan = {"actions": []}

    result = bridge.execute_upgrade_plan(
        authorization, plan, surface="nauvis", force="player", timeout=7.0,
    )

    assert result == Path("upgrade-report.json")
    command, subdir, timeout = calls[0]
    payload = json.loads(command.removeprefix("/execute_upgrade_plan "))
    assert payload == {
        "authorization": authorization,
        "upgrade_plan": plan,
        "surface": "nauvis",
        "force": "player",
    }
    assert subdir == EXECUTION_REPORT_SUBDIR
    assert timeout == 7.0


@pytest.mark.parametrize("surface,force", [("", "player"), ("nauvis", ""), (None, "player")])
def test_upgrade_bridge_rejects_implicit_or_empty_scope(
    surface: str | None, force: str,
) -> None:
    bridge, _calls = _recording_bridge()

    with pytest.raises(ValueError, match="non-empty string"):
        bridge.execute_upgrade_plan(
            {}, {"actions": []}, surface=surface, force=force,  # type: ignore[arg-type]
        )
