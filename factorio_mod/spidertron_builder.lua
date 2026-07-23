-- Path: factorio_mod/spidertron_builder.lua
-- Purpose: Spawn a self-powered construction spidertron on planner-sandbox so a
--          mobile roboport can build ghosts anywhere without a fixed, bootstrapping
--          roboport network (see docs/25_execution_rework_brief.md section 2).

local shared = require("sandbox_shared")
local get_or_create_sandbox_surface = shared.get_or_create_sandbox_surface
local get_or_create_planner_force = shared.get_or_create_planner_force

-- Self-powered, mobile-construction loadout. Verified live: the spidertron grid
-- is 10x6 and holds a fusion reactor + 4 personal roboports + 2 batteries. Four
-- personal-roboport-mk2 units yield a construction radius of 40 tiles.
local REACTOR = "fusion-reactor-equipment"
local ROBOPORT = "personal-roboport-mk2-equipment"
local BATTERY = "battery-mk2-equipment"

local function equip_spidertron(spidertron, roboports, batteries)
  local grid = spidertron.grid
  if not grid then
    error("spidertron has no equipment grid")
  end
  local placed = { reactor = 0, roboports = 0, batteries = 0 }
  if grid.put({ name = REACTOR }) then
    placed.reactor = 1
  else
    error("could not fit " .. REACTOR .. " in the spidertron grid")
  end
  for _ = 1, roboports do
    if grid.put({ name = ROBOPORT }) then
      placed.roboports = placed.roboports + 1
    end
  end
  for _ = 1, batteries do
    if grid.put({ name = BATTERY }) then
      placed.batteries = placed.batteries + 1
    end
  end
  if placed.roboports == 0 then
    error("could not fit any " .. ROBOPORT .. " in the spidertron grid")
  end
  return placed
end

local function load_trunk(spidertron, bots, materials)
  local trunk = spidertron.get_inventory(defines.inventory.spider_trunk)
  if not trunk then
    error("spidertron has no trunk inventory")
  end
  local inserted_bots = 0
  if bots > 0 then
    inserted_bots = trunk.insert({ name = "construction-robot", count = bots })
  end
  local inserted = {}
  if type(materials) == "table" then
    for item, count in pairs(materials) do
      local target = tonumber(count) or 0
      if target > 0 then
        inserted[item] = trunk.insert({ name = item, count = target })
      end
    end
  end
  return inserted_bots, inserted
end

local function spawn_construction_spidertron(params)
  local surface = get_or_create_sandbox_surface()
  local force = get_or_create_planner_force()

  local position = params.position or { x = 0, y = 0 }
  local pos = { x = tonumber(position.x) or 0, y = tonumber(position.y) or 0 }
  local roboports = tonumber(params.roboports) or 4
  local batteries = tonumber(params.batteries) or 2
  local bots = tonumber(params.bots) or 50

  local spidertron = surface.create_entity({
    name = "spidertron", position = pos, force = force,
  })
  if not spidertron then
    error("failed to create spidertron at " .. pos.x .. "," .. pos.y)
  end
  -- Construction-only vehicle: no weapons, no ammo, no fuel needed.
  local equipment = equip_spidertron(spidertron, roboports, batteries)
  local inserted_bots, inserted = load_trunk(spidertron, bots, params.materials)

  return {
    unit_number = spidertron.unit_number,
    position = { x = spidertron.position.x, y = spidertron.position.y },
    equipment = equipment,
    bots = inserted_bots,
    materials = inserted,
  }
end

commands.add_command(
  "spawn_construction_spidertron",
  "Spawn a self-powered construction spidertron (reactor + personal roboports + battery) on planner-sandbox.",
  function(command)
    local ok_parse, params = pcall(function()
      return helpers.json_to_table(command.parameter or "{}")
    end)
    if not ok_parse or type(params) ~= "table" then
      params = {}
    end

    local run_ok, result = pcall(function()
      return spawn_construction_spidertron(params)
    end)

    local report = { tick = game.tick, ok = run_ok }
    if run_ok then
      report.unit_number = result.unit_number
      report.position = result.position
      report.equipment = result.equipment
      report.bots = result.bots
      report.materials = result.materials
    else
      report.error = tostring(result)
    end

    local json = helpers.table_to_json(report)
    helpers.write_file(
      "factorio_mod/spidertron_reports/spawn_" .. game.tick .. ".json", json, false
    )
    -- Echo the unit id so an RCON caller can drive the build loop immediately.
    rcon.print(json)
  end
)
