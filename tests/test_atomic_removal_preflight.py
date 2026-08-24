# Path: tests/test_atomic_removal_preflight.py
# Purpose: Exercise atomic remove-then-place preflight against a stub Factorio surface.

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
EXECUTOR = REPO_ROOT / "factorio_mod" / "layout_executor.lua"

_HARNESS = r"""
mutation_log = {}
captured_report = nil
registered = {}
payload = nil

defines = {
  build_check_type = { manual = 1, ghost_revive = 2 },
  direction = { north = 0, east = 4, south = 8, west = 12 },
  inventory = { chest = 1 },
  wire_connector_id = { pole_copper = 1 },
  wire_origin = { script = 1 },
}

prototypes = { entity = {}, item = {} }
local function prototype(name)
  prototypes.entity[name] = {
    type = "transport-belt",
    collision_box = {
      left_top = { x = -0.4, y = -0.4 },
      right_bottom = { x = 0.4, y = 0.4 },
    },
    collision_mask = { layers = { object = true } },
    get_max_wire_distance = function() return 0 end,
  }
end
prototype("transport-belt")
prototype("fast-transport-belt")
prototype("stone-furnace")
prototype("rectangular-machine")
prototypes.entity["rectangular-machine"].collision_box = {
  left_top = { x = -1.4, y = -0.4 },
  right_bottom = { x = 1.4, y = 0.4 },
}

local force = { name = "player" }
local entities = {}

local function in_area(entity, area)
  local left_top, right_bottom = area[1], area[2]
  return entity.position.x >= left_top[1]
    and entity.position.x <= right_bottom[1]
    and entity.position.y >= left_top[2]
    and entity.position.y <= right_bottom[2]
end

local function matches_name(entity, name)
  if name == nil then return true end
  if type(name) == "table" then
    for _, candidate in ipairs(name) do
      if entity.name == candidate then return true end
    end
    return false
  end
  return entity.name == name
end

local surface = {}
function surface.find_entities_filtered(filter)
  local found = {}
  for _, entity in ipairs(entities) do
    if entity.valid and matches_name(entity, filter.name)
      and (filter.force == nil or entity.force == filter.force)
      and (filter.area == nil or in_area(entity, filter.area)) then
      found[#found + 1] = entity
    end
  end
  return found
end
function surface.can_place_entity(spec)
  mutation_log[#mutation_log + 1] = "preflight:" .. spec.name
  for _, entity in ipairs(entities) do
    if entity.valid
      and math.abs(entity.position.x - spec.position[1]) < 0.5
      and math.abs(entity.position.y - spec.position[2]) < 0.5 then
      return false
    end
  end
  return true
end
function surface.create_entity(spec)
  mutation_log[#mutation_log + 1] = "create:" .. spec.name
  local entity = {
    name = spec.name,
    type = "transport-belt",
    position = { x = spec.position[1], y = spec.position[2] },
    force = spec.force,
    valid = true,
    direction = spec.direction or 0,
  }
  function entity.destroy()
    mutation_log[#mutation_log + 1] = "destroy:" .. entity.name
    entity.valid = false
  end
  entities[#entities + 1] = entity
  return entity
end
function surface.get_tile(_x, _y)
  return {
    valid = true,
    collides_with = function(_layer) return false end,
  }
end

function add_entity(name, x, y, entity_force)
  local entity = {
    name = name,
    type = "transport-belt",
    position = { x = x, y = y },
    force = entity_force or force,
    valid = true,
    direction = 0,
  }
  function entity.destroy()
    mutation_log[#mutation_log + 1] = "destroy:" .. entity.name
    entity.valid = false
  end
  entities[#entities + 1] = entity
  return entity
end

function count_valid(name)
  local count = 0
  for _, entity in ipairs(entities) do
    if entity.valid and entity.name == name then count = count + 1 end
  end
  return count
end

package.preload["sandbox_shared"] = function()
  return {
    get_or_create_sandbox_surface = function(_name) return surface end,
    get_or_create_planner_force = function(_name) return force end,
    find_exact_entity = function(s, wanted_force, name, position)
      for _, entity in ipairs(s.find_entities_filtered({ name = name, force = wanted_force })) do
        if entity.position.x == position.x and entity.position.y == position.y then
          return entity
        end
      end
      return nil
    end,
  }
end

package.preload["logistic_sections"] = function()
  return {
    find_section_by_group = function() return nil end,
    claim_section_for_group = function() return nil end,
    verify_section_slots = function() end,
    write_section_slots = function() end,
    clear_logistic_groups = function() end,
    has_settings = function() return false end,
    needs_reconfiguration = function() return false end,
  }
end

commands = {
  add_command = function(name, _description, callback) registered[name] = callback end,
}
helpers = {
  json_to_table = function(_text) return payload end,
  table_to_json = function(report) captured_report = report return "{}" end,
  write_file = function() end,
}
game = { tick = 101, print = function() end }
"""


def _run(
    tmp_path: Path,
    *,
    remove_name: str,
    remove_x: float = 0,
    foreign: bool = False,
    assertions: str,
) -> None:
    scenario = f"""
        dofile("{EXECUTOR.as_posix()}")
        add_entity("transport-belt", 0, 0)
        {f'add_entity("stone-furnace", 0.25, 0)' if foreign else ''}
        payload = {{
          authorization = {{ approved_actions = {{ "remove_entities", "place_core_infrastructure" }} }},
          build_plan = {{ surface = "nauvis", force = "player", atomic = true, phases = {{
            {{ name = "replace", actions = {{
              {{ action_type = "remove_entity", entity = "{remove_name}", position = {{ x = {remove_x}, y = 0 }} }},
              {{ action_type = "place_entity", entity = "fast-transport-belt", position = {{ x = 0, y = 0 }}, direction = "north" }},
            }} }},
          }} }},
        }}
        registered["build_layout_plan"]({{ parameter = "ignored" }})
        {assertions}
    """
    script = tmp_path / "atomic_removal_preflight.lua"
    script.write_text(_HARNESS + scenario, encoding="utf-8")
    result = subprocess.run(
        ["lua", str(script)], text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_atomic_exact_remove_then_place_preflights_before_mutation(tmp_path: Path) -> None:
    _run(
        tmp_path,
        remove_name="transport-belt",
        assertions="""
          assert(captured_report.ok == true)
          assert(captured_report.removed_entities == 1)
          assert(captured_report.placed_entities == 1)
          assert(mutation_log[1] == "preflight:fast-transport-belt")
          assert(mutation_log[2] == "destroy:transport-belt")
          assert(mutation_log[3] == "create:fast-transport-belt")
          assert(#mutation_log == 3)
        """,
    )


def test_atomic_replacement_keeps_unrelated_overlap_blocking(tmp_path: Path) -> None:
    _run(
        tmp_path,
        remove_name="transport-belt",
        foreign=True,
        assertions="""
          assert(captured_report.ok == false)
          assert(captured_report.error == "1 placement(s) failed")
          assert(#mutation_log == 1)
          assert(mutation_log[1] == "preflight:fast-transport-belt")
        """,
    )


def test_execution_removes_only_the_exact_matched_entity(tmp_path: Path) -> None:
    assertions = """
      assert(captured_report.ok == true)
      assert(captured_report.removed_entities == 1)
      assert(count_valid("transport-belt") == 1)
    """
    scenario = f"""
      dofile("{EXECUTOR.as_posix()}")
      add_entity("transport-belt", 0, 0)
      add_entity("transport-belt", 0.55, 0)
      payload = {{
        authorization = {{ approved_actions = {{ "remove_entities", "place_core_infrastructure" }} }},
        build_plan = {{ surface = "nauvis", force = "player", atomic = true, phases = {{
          {{ name = "replace", actions = {{
            {{ action_type = "remove_entity", entity = "transport-belt", position = {{ x = 0, y = 0 }} }},
            {{ action_type = "place_entity", entity = "fast-transport-belt", position = {{ x = 0, y = 0 }}, direction = "north" }},
          }} }},
        }} }},
      }}
      registered["build_layout_plan"]({{ parameter = "ignored" }})
      {assertions}
    """
    script = tmp_path / "exact_removal.lua"
    script.write_text(_HARNESS + scenario, encoding="utf-8")
    result = subprocess.run(["lua", str(script)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr or result.stdout


def test_malformed_atomic_removal_fails_before_any_mutation(tmp_path: Path) -> None:
    scenario = f"""
      dofile("{EXECUTOR.as_posix()}")
      add_entity("transport-belt", 0, 0)
      payload = {{
        authorization = {{ approved_actions = {{ "remove_entities", "place_core_infrastructure" }} }},
        build_plan = {{ surface = "nauvis", force = "player", atomic = true, phases = {{
          {{ name = "replace", actions = {{
            {{ action_type = "remove_entity", entity = "transport-belt", position = {{ y = 0 }} }},
            {{ action_type = "place_entity", entity = "fast-transport-belt", position = {{ x = 0, y = 0 }}, direction = "north" }},
          }} }},
        }} }},
      }}
      registered["build_layout_plan"]({{ parameter = "ignored" }})
      assert(captured_report.ok == false)
      assert(#mutation_log == 0)
      assert(count_valid("transport-belt") == 1)
    """
    script = tmp_path / "malformed_removal.lua"
    script.write_text(_HARNESS + scenario, encoding="utf-8")
    result = subprocess.run(["lua", str(script)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr or result.stdout


def test_overlapping_atomic_placements_fail_before_partial_execution(tmp_path: Path) -> None:
    scenario = f"""
      dofile("{EXECUTOR.as_posix()}")
      payload = {{
        authorization = {{ approved_actions = {{ "place_core_infrastructure" }} }},
        build_plan = {{ surface = "nauvis", force = "player", atomic = true, phases = {{
          {{ name = "overlap", actions = {{
            {{ action_type = "place_entity", entity = "transport-belt", position = {{ x = 0, y = 0 }}, direction = "north" }},
            {{ action_type = "place_entity", entity = "fast-transport-belt", position = {{ x = 0, y = 0 }}, direction = "north" }},
          }} }},
        }} }},
      }}
      registered["build_layout_plan"]({{ parameter = "ignored" }})
      assert(captured_report.ok == false)
      assert(captured_report.failed_placements == 1)
      assert(count_valid("transport-belt") == 0)
      assert(count_valid("fast-transport-belt") == 0)
      assert(#mutation_log == 2)
      assert(mutation_log[1] == "preflight:transport-belt")
      assert(mutation_log[2] == "preflight:fast-transport-belt")
    """
    script = tmp_path / "overlapping_placements.lua"
    script.write_text(_HARNESS + scenario, encoding="utf-8")
    result = subprocess.run(["lua", str(script)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr or result.stdout


def test_factorio_2_east_rotates_rectangular_atomic_footprints(tmp_path: Path) -> None:
    scenario = f"""
      dofile("{EXECUTOR.as_posix()}")
      payload = {{
        authorization = {{ approved_actions = {{ "place_core_infrastructure" }} }},
        build_plan = {{ surface = "nauvis", force = "player", atomic = true, phases = {{
          {{ name = "overlap", actions = {{
            {{ action_type = "place_entity", entity = "rectangular-machine", position = {{ x = 0, y = 0 }}, direction = "east" }},
            {{ action_type = "place_entity", entity = "rectangular-machine", position = {{ x = 0, y = 2 }}, direction = "east" }},
          }} }},
        }} }},
      }}
      registered["build_layout_plan"]({{ parameter = "ignored" }})
      assert(captured_report.ok == false)
      assert(captured_report.failed_placements == 1)
      assert(count_valid("rectangular-machine") == 0)
      assert(#mutation_log == 2)
      assert(mutation_log[1] == "preflight:rectangular-machine")
      assert(mutation_log[2] == "preflight:rectangular-machine")
    """
    script = tmp_path / "factorio_2_cardinal_footprint.lua"
    script.write_text(_HARNESS + scenario, encoding="utf-8")
    result = subprocess.run(["lua", str(script)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.parametrize(
    ("remove_name", "remove_x"),
    [("stone-furnace", 0), ("transport-belt", 0.25)],
)
def test_unmatched_removal_never_creates_a_preflight_hole(
    tmp_path: Path, remove_name: str, remove_x: float,
) -> None:
    _run(
        tmp_path,
        remove_name=remove_name,
        remove_x=remove_x,
        assertions="""
          assert(captured_report.ok == false)
          assert(#mutation_log == 1)
          assert(mutation_log[1] == "preflight:fast-transport-belt")
        """,
    )


def test_flying_robot_does_not_block_ghost_placement(tmp_path: Path) -> None:
    """A logistic robot crossing the target tile has an empty collision mask
    and must not read as real infrastructure (live run of 2026-08-24 18:47
    killed a promoted pipe line on exactly this false occupant)."""
    scenario = f"""
        dofile("{EXECUTOR.as_posix()}")
        add_entity("transport-belt", 4, 0)
        local robot = {{
          name = "logistic-robot", type = "logistic-robot",
          position = {{ x = 8, y = 0 }}, force = force, valid = true,
          prototype = {{ collision_mask = {{ layers = {{}} }} }},
        }}
        entities[#entities + 1] = robot
        payload = {{
          authorization = {{ approved_actions = {{ "place_core_infrastructure" }} }},
          build_plan = {{ surface = "nauvis", force = "player", phases = {{
            {{ name = "p", actions = {{
              {{ action_type = "place_entity", entity = "transport-belt",
                 position = {{ x = 8, y = 0 }}, direction = "north" }},
            }} }},
          }} }},
        }}
        registered["build_layout_plan"]({{ parameter = "ignored" }})
        assert(captured_report.ok == true, captured_report.error)
        assert(captured_report.placed_entities == 1)
    """
    script = tmp_path / "flying_robot.lua"
    script.write_text(_HARNESS + scenario, encoding="utf-8")
    result = subprocess.run(["lua", str(script)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr or result.stdout
