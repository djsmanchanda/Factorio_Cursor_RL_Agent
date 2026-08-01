# Path: tests/test_logistic_sections_lua.py
# Purpose: Run factorio_mod/logistic_sections.lua in a real Lua interpreter against stubbed LuaLogisticSections, so the requester bookkeeping is checked by behaviour rather than by reading it.

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

lupa = pytest.importorskip(
    "lupa",
    reason="pip install lupa to execute the mod's Lua; deliberately not a "
           "runtime dependency -- the game ships its own interpreter",
)

_MODULE = REPO_ROOT / "factorio_mod" / "logistic_sections.lua"

# A stub LuaLogisticSections written in Lua rather than Python: crossing the
# bridge for every field access is what made an earlier attempt test the bridge
# instead of the module. Only what the module actually touches is modelled --
# valid, group, filters_count, get_slot/set_slot/clear_slot, add_section.
_STUB = """
local function new_section(group)
  local section = {
    valid = true, group = group or "", filters_count = 0, slots = {},
    log = {},
  }
  function section.get_slot(index) return section.slots[index] end
  function section.set_slot(index, slot)
    section.slots[index] = slot
    if index > section.filters_count then section.filters_count = index end
    section.log[#section.log + 1] = "set:" .. index
  end
  function section.clear_slot(index)
    section.slots[index] = nil
    section.log[#section.log + 1] = "clear:" .. index
  end
  return section
end

local function new_sections(groups)
  local holder = { sections = {}, added = 0 }
  for _, group in ipairs(groups or {}) do
    holder.sections[#holder.sections + 1] = new_section(group)
  end
  function holder.add_section()
    holder.added = holder.added + 1
    local section = new_section("")
    holder.sections[#holder.sections + 1] = section
    return section
  end
  return holder
end

return { new_section = new_section, new_sections = new_sections }
"""


@pytest.fixture
def lua():
    runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
    runtime.execute(f'package.path = "{REPO_ROOT.as_posix()}/factorio_mod/?.lua;" .. package.path')
    runtime.globals()["M"] = runtime.eval(f'dofile("{_MODULE.as_posix()}")')
    runtime.globals()["stub"] = runtime.execute(_STUB)
    return runtime


def test_a_labelled_section_is_found_by_its_group(lua) -> None:
    found = lua.eval("""(function()
        local holder = stub.new_sections({"mall:iron-gear-wheel", "mall:copper-cable"})
        return M.find_section_by_group(holder, "mall:copper-cable").group
    end)()""")

    assert found == "mall:copper-cable"


def test_an_absent_group_is_reported_as_absent(lua) -> None:
    assert lua.eval("""(function()
        local holder = stub.new_sections({"mall:pipe"})
        return M.find_section_by_group(holder, "mall:inserter") == nil
    end)()""")


def test_an_invalidated_section_is_never_matched(lua) -> None:
    """A destroyed chest's section can linger in the list."""
    assert lua.eval("""(function()
        local holder = stub.new_sections({"mall:pipe"})
        holder.sections[1].valid = false
        return M.find_section_by_group(holder, "mall:pipe") == nil
    end)()""")


def test_the_blank_starting_section_is_claimed_rather_than_left_behind(lua) -> None:
    """create_entity leaves one blank unnamed section. Adding beside it left an
    empty section trailing every single-machine cell."""
    count, added, group = lua.eval("""(function()
        local holder = stub.new_sections({""})
        local section = M.claim_section_for_group(holder, "mall:pipe")
        return #holder.sections, holder.added, section.group
    end)()""")

    assert (count, added, group) == (1, 0, "mall:pipe")


def test_a_blank_section_holding_filters_is_not_claimed(lua) -> None:
    """Unnamed but non-empty is somebody's manual request; claiming it would
    relabel a chest a player configured by hand."""
    count, added = lua.eval("""(function()
        local holder = stub.new_sections({""})
        holder.sections[1].filters_count = 2
        M.claim_section_for_group(holder, "mall:pipe")
        return #holder.sections, holder.added
    end)()""")

    assert (count, added) == (2, 1)


def test_a_second_machine_gets_its_own_section(lua) -> None:
    """The whole point: one chest feeding two assemblers carries two groups."""
    groups = lua.eval("""(function()
        local holder = stub.new_sections({})
        M.claim_section_for_group(holder, "mall:iron-gear-wheel")
        M.claim_section_for_group(holder, "mall:copper-cable")
        local names = {}
        for _, section in ipairs(holder.sections) do names[#names + 1] = section.group end
        return table.concat(names, ",")
    end)()""")

    assert groups == "mall:iron-gear-wheel,mall:copper-cable"


def test_slots_are_written_in_request_order(lua) -> None:
    first, second = lua.eval("""(function()
        local section = stub.new_section("mall:electronic-circuit")
        M.write_section_slots(section, {
            {name = "copper-cable", count = 30},
            {name = "iron-plate", count = 10},
        })
        return section.slots[1].value .. ":" .. section.slots[1].min,
               section.slots[2].value .. ":" .. section.slots[2].min
    end)()""")

    assert first == "copper-cable:30"
    assert second == "iron-plate:10"


def test_a_shorter_rewrite_clears_the_slots_it_no_longer_uses(lua) -> None:
    """Otherwise the chest keeps requesting an ingredient the recipe dropped."""
    remaining, cleared = lua.eval("""(function()
        local section = stub.new_section("mall:x")
        M.write_section_slots(section, {
            {name = "a", count = 1}, {name = "b", count = 2}, {name = "c", count = 3},
        })
        M.write_section_slots(section, {{name = "a", count = 5}})
        local count = 0
        for _ in pairs(section.slots) do count = count + 1 end
        return count, tostring(section.slots[2]) .. tostring(section.slots[3])
    end)()""")

    assert remaining == 1
    assert cleared == "nilnil"


def test_clearing_a_group_empties_it_without_removing_the_section(lua) -> None:
    slots, still_there = lua.eval("""(function()
        local holder = stub.new_sections({"mall:pipe"})
        M.write_section_slots(holder.sections[1], {{name = "iron-plate", count = 4}})
        local entity = { get_logistic_sections = function() return holder end }
        M.clear_logistic_groups(entity, {"mall:pipe"})
        local count = 0
        for _ in pairs(holder.sections[1].slots) do count = count + 1 end
        return count, #holder.sections
    end)()""")

    assert slots == 0
    assert still_there == 1


def test_clearing_an_absent_group_is_not_an_error(lua) -> None:
    """Retiring a mall cell that was already retired must not fail the plan."""
    assert lua.eval("""(function()
        local holder = stub.new_sections({"mall:pipe"})
        local entity = { get_logistic_sections = function() return holder end }
        local ok = pcall(M.clear_logistic_groups, entity, {"mall:gone"})
        return ok
    end)()""")


def test_a_chest_with_no_logistic_sections_fails_loudly(lua) -> None:
    ok, message = lua.eval("""(function()
        local entity = { get_logistic_sections = function() return nil end }
        return pcall(M.clear_logistic_groups, entity, {"mall:pipe"})
    end)()""")

    assert ok is False
    assert "no_logistic_sections" in message


def test_verification_accepts_what_was_just_written(lua) -> None:
    assert lua.eval("""(function()
        local section = stub.new_section("mall:x")
        local requests = {{name = "iron-plate", count = 4}, {name = "copper-plate", count = 2}}
        M.write_section_slots(section, requests)
        return pcall(M.verify_section_slots, section, requests)
    end)()""")


def test_verification_rejects_a_wrong_count_and_says_which_slot(lua) -> None:
    ok, message = lua.eval("""(function()
        local section = stub.new_section("mall:x")
        M.write_section_slots(section, {{name = "iron-plate", count = 4}})
        return pcall(M.verify_section_slots, section, {{name = "iron-plate", count = 9}})
    end)()""")

    assert ok is False
    assert "slot=1" in message and "iron-plate:9" in message


def test_verification_rejects_a_missing_slot(lua) -> None:
    """The live fault this whole path exists for: the chest holds nothing."""
    ok, message = lua.eval("""(function()
        local section = stub.new_section("mall:x")
        return pcall(M.verify_section_slots, section, {{name = "iron-plate", count = 4}})
    end)()""")

    assert ok is False
    assert "actual=nil:nil" in message


def test_a_slot_value_may_be_a_name_or_a_table(lua) -> None:
    """Factorio hands back either shape depending on how the slot was set."""
    assert lua.eval("""(function()
        local section = stub.new_section("mall:x")
        section.slots[1] = { value = { name = "iron-plate" }, min = 4 }
        section.filters_count = 1
        return pcall(M.verify_section_slots, section, {{name = "iron-plate", count = 4}})
    end)()""")


@pytest.mark.parametrize("field", [
    "logistic_request", "logistic_requests", "logistic_sections",
    "clear_logistic_groups", "inventory_limit", "infinity_filter",
])
def test_every_settable_field_marks_an_action_as_carrying_settings(lua, field: str) -> None:
    """An omission here is silent: the entity reports already_present, nothing
    fails, and the setting never lands."""
    assert lua.eval(f'M.has_settings({{ {field} = "anything" }})')


def test_an_action_with_no_settings_carries_none(lua) -> None:
    assert lua.eval('M.has_settings({ entity = "requester-chest", recipe = "pipe" })') is False


def test_an_existing_machine_still_needs_its_recipe_reapplied(lua) -> None:
    assert lua.eval("""M.needs_reconfiguration(
        { recipe = "pipe" }, { type = "assembling-machine" })""")


def test_a_ghost_carries_the_recipe_it_was_created_with(lua) -> None:
    assert lua.eval("""M.needs_reconfiguration(
        { recipe = "pipe" }, { type = "entity-ghost" })""") is False


def test_a_ghost_with_settings_is_still_reconfigured(lua) -> None:
    assert lua.eval("""M.needs_reconfiguration(
        { recipe = "pipe", inventory_limit = 4 }, { type = "entity-ghost" })""")
