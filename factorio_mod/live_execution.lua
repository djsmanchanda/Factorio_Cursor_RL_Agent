-- Path: factorio_mod/live_execution.lua
-- Purpose: Measure live planner-sandbox invariants (power, logistics, ghosts, fluids) after a build settles.



local shared = require("sandbox_shared")
local get_or_create_sandbox_surface = shared.get_or_create_sandbox_surface
local get_or_create_planner_force = shared.get_or_create_planner_force

local MACHINE_TYPES = { "assembling-machine", "furnace" }
local DRILL_TYPES = { "mining-drill" }
local INSERTER_TYPES = { "inserter" }
local ROBOPORT_TYPES = { "roboport" }
local FLUID_CARRYING_TYPES = {
  "assembling-machine", "furnace", "boiler", "generator", "mining-drill"
}
local MAX_STUCK_GHOST_DETAILS = 25

local STATUS_NAMES = {}
for name, value in pairs(defines.entity_status) do
  STATUS_NAMES[value] = name
end

local function status_name(entity)
  local ok, status = pcall(function() return entity.status end)
  if not ok or status == nil then return "unknown" end
  return STATUS_NAMES[status] or tostring(status)
end

local function describe(entity)
  local ok, position = pcall(function() return entity.position end)
  if not ok then return entity.name .. "@unknown" end
  return entity.name .. "@(" .. tostring(position.x) .. "," .. tostring(position.y) .. ")"
end

-- Reads entity.electric_network_id and folds it into the shared sample state.
local function sample_electric(entities, network_ids, missing, violations)
  local sampled = 0
  for _, entity in pairs(entities) do
    if entity.valid then
      sampled = sampled + 1
      local ok, network_id = pcall(function() return entity.electric_network_id end)
      if not ok then
        table.insert(violations, "electric_network_id_read_failed:" .. describe(entity))
      elseif network_id == nil then
        table.insert(missing, describe(entity))
      else
        network_ids[tostring(network_id)] = true
      end
    end
  end
  return sampled
end

local function electric_samples(surface, force, roboports, violations)
  local network_ids = {}
  local missing = {}
  local sampled_by_category = {
    machines = sample_electric(
      surface.find_entities_filtered({ type = MACHINE_TYPES, force = force }),
      network_ids, missing, violations
    ),
    drills = sample_electric(
      surface.find_entities_filtered({ type = DRILL_TYPES, force = force }),
      network_ids, missing, violations
    ),
    inserters = sample_electric(
      surface.find_entities_filtered({ type = INSERTER_TYPES, force = force }),
      network_ids, missing, violations
    ),
    roboports = sample_electric(roboports, network_ids, missing, violations)
  }
  local ids = {}
  for id in pairs(network_ids) do table.insert(ids, id) end
  table.sort(ids)
  return {
    network_ids = ids,
    missing = missing,
    sampled_by_category = sampled_by_category
  }
end

local function logistic_roboports(roboports, force)
  local network_ids = {}
  local missing = {}
  for _, roboport in pairs(roboports) do
    if roboport.valid then
      if roboport.logistic_network then
        network_ids[tostring(roboport.logistic_network.network_id)] = true
      else
        table.insert(missing, describe(roboport))
      end
    end
  end
  local ids = {}
  for id in pairs(network_ids) do table.insert(ids, id) end
  table.sort(ids)
  return { network_ids = ids, missing = missing }
end

-- Best-effort diagnosis of why a single ghost has not been built yet.
local function stuck_reason(ghost, surface, force)
  local ok, network = pcall(function()
    return surface.find_logistic_network_by_position(ghost.position, force)
  end)
  if not ok or not network then return "out_of_construction_range" end
  local bots_ok, bots = pcall(function() return network.all_construction_robots end)
  if not bots_ok or bots == 0 then return "no_construction_robots" end
  local proto_ok, proto = pcall(function() return ghost.ghost_prototype end)
  if proto_ok and proto and proto.items_to_place_this and proto.items_to_place_this[1] then
    local item = proto.items_to_place_this[1]
    local have_ok, have = pcall(function() return network.get_item_count(item.name) end)
    if have_ok and have < (item.count or 1) then
      return "missing_material:" .. item.name
    end
  end
  return "pending"
end

local function measure_ghosts(surface, force, violations)
  local ghosts = surface.find_entities_filtered({ name = "entity-ghost", force = force })
  local stuck = {}
  for _, ghost in pairs(ghosts) do
    if ghost.valid and #stuck < MAX_STUCK_GHOST_DETAILS then
      local reason = stuck_reason(ghost, surface, force)
      table.insert(stuck, {
        entity = ghost.ghost_name,
        position = { x = ghost.position.x, y = ghost.position.y },
        reason = reason
      })
    end
  end
  if #ghosts > 0 then
    table.insert(violations, "remaining_ghosts=" .. tostring(#ghosts))
  end
  return { remaining = #ghosts, stuck = stuck }
end

-- Every input/input-output fluid box on a fluid-carrying entity, keyed by fluidbox index.
local function input_boxes(entity)
  local boxes = {}
  local fluidbox = entity.fluidbox
  if not fluidbox then return boxes end
  for index = 1, #fluidbox do
    local proto_ok, proto = pcall(function() return fluidbox.get_prototype(index) end)
    local direction = proto_ok and proto and proto.production_type or nil
    if direction == "input" or direction == "input-output" then
      local contents = fluidbox[index]
      table.insert(boxes, {
        index = index,
        amount = contents and contents.amount or 0,
        fluid = contents and contents.name or nil
      })
    end
  end
  return boxes
end

local function fluid_machines(surface, force)
  local machines = {}
  for _, entity in pairs(surface.find_entities_filtered({ type = FLUID_CARRYING_TYPES, force = force })) do
    if entity.valid and entity.fluidbox and #entity.fluidbox > 0 then
      local boxes = input_boxes(entity)
      if #boxes > 0 then
        table.insert(machines, {
          entity = entity.name,
          position = { x = entity.position.x, y = entity.position.y },
          status = status_name(entity),
          input_boxes = boxes
        })
      end
    end
  end
  return machines
end

local function verify_electronics_execution()
  local surface = get_or_create_sandbox_surface()
  local force = get_or_create_planner_force()
  local violations = {}

  local roboports = surface.find_entities_filtered({ type = ROBOPORT_TYPES, force = force })
  local power_sources = #surface.find_entities_filtered({
    name = "electric-energy-interface", force = force
  })

  return {
    power_sources = power_sources,
    electric_samples = electric_samples(surface, force, roboports, violations),
    logistic_roboports = logistic_roboports(roboports, force),
    ghosts = measure_ghosts(surface, force, violations),
    fluid_machines = fluid_machines(surface, force),
    violations = violations
  }
end

commands.add_command(
  "verify_electronics_execution",
  "Measure live power, logistic, ghost and fluid invariants on planner-sandbox.",
  function()
    local run_ok, result = pcall(verify_electronics_execution)
    local report = { tick = game.tick, ok = run_ok }
    if run_ok then
      report.power_sources = result.power_sources
      report.electric_samples = result.electric_samples
      report.logistic_roboports = result.logistic_roboports
      report.ghosts = result.ghosts
      report.fluid_machines = result.fluid_machines
      report.violations = result.violations
    else
      report.error = tostring(result)
      report.power_sources = 0
      report.electric_samples = { network_ids = {}, missing = {}, sampled_by_category = {
        machines = 0, drills = 0, inserters = 0, roboports = 0
      } }
      report.logistic_roboports = { network_ids = {}, missing = {} }
      report.ghosts = { remaining = 0, stuck = {} }
      report.fluid_machines = {}
      report.violations = { tostring(result) }
    end

    local json = helpers.table_to_json(report)
    -- Empty Lua tables serialise as {} even where the schema requires an array.
    json = json:gsub('"network_ids":{}', '"network_ids":[]')
    json = json:gsub('"missing":{}', '"missing":[]')
    json = json:gsub('"stuck":{}', '"stuck":[]')
    json = json:gsub('"violations":{}', '"violations":[]')
    json = json:gsub('"fluid_machines":{}', '"fluid_machines":[]')
    json = json:gsub('"input_boxes":{}', '"input_boxes":[]')

    helpers.write_file(
      "factorio_mod/live_execution_reports/verify_" .. game.tick .. ".json",
      json,
      false
    )
  end
)
