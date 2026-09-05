# Path: tests/test_trash_requesters_lua.py
# Purpose: Prove bot-built requester chests gain blueprint trash behavior through creation params and the revive swap.

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
lupa = pytest.importorskip(
    "lupa",
    reason="pip install lupa to execute the mod's Lua; deliberately not a "
           "runtime dependency -- the game ships its own interpreter",
)

_MODULE = REPO_ROOT / "factorio_mod" / "trash_requesters.lua"

# Stub surface/entity/defines modelling only what the module touches: entity
# validity, position, direction, force, chest inventory stacks, destroy,
# create_entity params capture, inserted-stack capture, and ghost fallback.
# Inventories are dense arrays (no holes), so the module's 1..#inventory loop
# behaves exactly as in game.
_STUB = """
local created_params = nil
local last_inserted = nil
local destroyed_total = 0

local function new_chest(name, x, y, stacks)
  local chest = {
    valid = true, name = name,
    position = { x = x, y = y }, direction = 4,
    force = { name = "player" },
    inserted = {},
  }
  function chest.get_inventory(_which)
    local inv = {}
    for _, stack in ipairs(stacks) do
      inv[#inv + 1] = stack
    end
    return inv
  end
  function chest.destroy() chest.destroyed_flag = true; destroyed_total = destroyed_total + 1 end
  function chest.insert(stack)
    chest.inserted[#chest.inserted + 1] = { name = stack.name, count = stack.count }
    last_inserted = chest.inserted
    return stack.count
  end
  return chest
end

local function new_surface(fail_create)
  local surface = {}
  function surface.create_entity(params)
    created_params = params
    if fail_create then return nil end
    if params.name == "entity-ghost" then
      return { valid = true, ghost = true }
    end
    return new_chest(params.name, params.position[1], params.position[2], {})
  end
  return surface
end

return {
  created_params = function() return created_params end,
  last_inserted = function() return last_inserted end,
  destroyed_total = function() return destroyed_total end,
  new_chest = new_chest,
  new_surface = new_surface,
    reset = function() created_params = nil; last_inserted = nil; destroyed_total = 0 end,
}
"""


@pytest.fixture
def lua():
    runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
    runtime.execute(f'package.path = "{REPO_ROOT.as_posix()}/factorio_mod/?.lua;" .. package.path')
    runtime.globals()["M"] = runtime.eval(f'dofile("{_MODULE.as_posix()}")')
    runtime.globals()["stub"] = runtime.execute(_STUB)
    runtime.execute("defines = { inventory = { chest = 1 } }")
    return runtime


def test_creation_params_carry_trash_only_for_requesters(lua) -> None:
    assert lua.eval("""(function()
        local params = M.creation_params("requester-chest")
        return params.request_filters.trash_not_requested
    end)()""") is True
    assert lua.eval("""(function()
        local params = M.creation_params("assembling-machine-1")
        local count = 0
        for _ in pairs(params) do count = count + 1 end
        return count
    end)()""") == 0


def test_revive_swap_preserves_contents_and_flag(lua) -> None:
    ok = lua.eval("""(function()
        stub.reset()
        local chest = stub.new_chest("requester-chest", 39.5, 32.5,
            { { valid_for_read = true, name = "iron-plate", count = 4 } })
        local surface = stub.new_surface(false)
        return M.revive_with_trash(surface, chest)
    end)()""")
    params = lua.eval("stub.created_params()")
    inserted = lua.eval("stub.last_inserted()")

    assert ok is True
    assert lua.eval("stub.destroyed_total()") == 1
    assert params["name"] == "requester-chest"
    assert params["request_filters"]["trash_not_requested"] is True
    assert params["position"][1] == 39.5
    assert params["direction"] == 4
    assert inserted[1]["name"] == "iron-plate"
    assert inserted[1]["count"] == 4


def test_revive_falls_back_to_a_ghost_when_recreate_fails(lua) -> None:
    outcome = lua.eval("""(function()
        stub.reset()
        local chest = stub.new_chest("requester-chest", 1.5, 2.5, {})
        local surface = stub.new_surface(true)
        local ok, reason = M.revive_with_trash(surface, chest)
        local params = stub.created_params()
        return { ok, reason, params.name, params.ghost_name }
    end)()""")

    assert outcome[1] is False
    assert outcome[2] == "recreate_failed"
    assert outcome[3] == "entity-ghost"
    assert outcome[4] == "requester-chest"


def test_only_managed_forces_opt_in(lua) -> None:
    assert lua.eval("M.managed_force('player')") is True
    assert lua.eval("M.managed_force('planner')") is True
    assert lua.eval("M.managed_force('training-01')") is False
    assert lua.eval("M.managed_force(nil)") is False
