-- Path: factorio_mod/sandbox_topology.lua
-- Purpose: Inspect topology read-only and perform only explicitly confirmed reset or reconcile.



local shared = require("sandbox_shared")
local get_or_create_planner_force = shared.get_or_create_planner_force
local find_exact_entity = shared.find_exact_entity
local is_factory_entity = shared.is_factory_entity
local CANONICAL_POWER_SOURCE = shared.CANONICAL_POWER_SOURCE
local CANONICAL_ROBOPORT_HUB = shared.CANONICAL_ROBOPORT_HUB

local function count_force_factory_entities(surface, force)
  if not force then return 0 end
  local count = 0
  for _, entity in pairs(surface.find_entities_filtered({ force = force })) do
    if is_factory_entity(entity) then count = count + 1 end
  end
  return count
end

local function inspect_sandbox_topology()
  local surface = game.surfaces["planner-sandbox"]
  local planner = game.forces.planner
  local human = game.forces.player
  if not surface then
    return {
      surface_exists = false,
      planner_factory_entities = 0,
      player_factory_entities = 0,
      power_sources = 0,
      electric_networks = 0,
      logistic_networks = 0,
      canonical_power_source = false,
      canonical_roboport_hub = false,
      unified = false
    }
  end

  local power_sources = planner and #surface.find_entities_filtered({
    name = "electric-energy-interface", force = planner
  }) or 0
  local electric_ids = {}
  if planner then
    for _, entity in pairs(surface.find_entities_filtered({
      type = { "electric-pole", "electric-energy-interface" }, force = planner
    })) do
      local ok, network_id = pcall(function() return entity.electric_network_id end)
      if ok and network_id then electric_ids[tostring(network_id)] = true end
    end
  end
  local logistic_ids = {}
  if planner then
    for _, roboport in pairs(surface.find_entities_filtered({ name = "roboport", force = planner })) do
      if roboport.logistic_network then
        logistic_ids[tostring(roboport.logistic_network.network_id)] = true
      end
    end
  end
  local electric_networks = 0
  for _ in pairs(electric_ids) do electric_networks = electric_networks + 1 end
  local logistic_networks = 0
  for _ in pairs(logistic_ids) do logistic_networks = logistic_networks + 1 end
  local canonical_power_source = planner and find_exact_entity(
    surface, planner, "electric-energy-interface", CANONICAL_POWER_SOURCE
  ) ~= nil or false
  local canonical_roboport_hub = planner and find_exact_entity(
    surface, planner, "roboport", CANONICAL_ROBOPORT_HUB
  ) ~= nil or false
  local planner_entities = count_force_factory_entities(surface, planner)
  local player_entities = count_force_factory_entities(surface, human)
  return {
    surface_exists = true,
    planner_factory_entities = planner_entities,
    player_factory_entities = player_entities,
    power_sources = power_sources,
    electric_networks = electric_networks,
    logistic_networks = logistic_networks,
    canonical_power_source = canonical_power_source,
    canonical_roboport_hub = canonical_roboport_hub,
    unified = planner_entities > 0 and player_entities == 0 and power_sources == 1
      and electric_networks == 1 and logistic_networks == 1
  }
end

commands.add_command("inspect_sandbox_topology", "Read-only planner-sandbox topology report.", function()
  local report = inspect_sandbox_topology()
  report.tick = game.tick
  report.ok = true
  helpers.write_file(
    "factorio_mod/topology_reports/topology_" .. game.tick .. ".json",
    helpers.table_to_json(report),
    false
  )
end)

local function reconcile_sandbox_topology(payload)
  if payload.confirm ~= true then
    error("Topology reset/reconcile requires confirm=true")
  end
  if payload.mode ~= "reset" and payload.mode ~= "reconcile" then
    error("Topology mode must be reset or reconcile")
  end
  local surface = game.surfaces["planner-sandbox"]
  if not surface then return { migrated = 0, removed = 0 } end
  local planner = get_or_create_planner_force()
  local human = game.forces.player
  local migrated, removed = 0, 0

  if payload.mode == "reconcile" and human then
    for _, entity in pairs(surface.find_entities_filtered({ force = human })) do
      if is_factory_entity(entity) then
        entity.force = planner
        migrated = migrated + 1
      end
    end
  elseif payload.mode == "reset" then
    for _, force in pairs({ planner, human }) do
      if force then
        for _, entity in pairs(surface.find_entities_filtered({ force = force })) do
          if is_factory_entity(entity) then
            entity.destroy()
            removed = removed + 1
          end
        end
      end
    end
  end
  return { migrated = migrated, removed = removed }
end

commands.add_command("reconcile_sandbox_topology", "Explicit reset/reconcile of sandbox factory entities.", function(command)
  local ok, payload = pcall(function() return helpers.json_to_table(command.parameter or "") end)
  local report = { tick = game.tick, ok = false }
  if ok and type(payload) == "table" then
    local run_ok, result = pcall(function() return reconcile_sandbox_topology(payload) end)
    report.ok = run_ok
    report.mode = payload.mode
    if run_ok then
      report.migrated = result.migrated
      report.removed = result.removed
    else
      report.error = tostring(result)
    end
  else
    report.error = "Invalid topology payload"
  end
  helpers.write_file(
    "factorio_mod/topology_reports/reconcile_" .. game.tick .. ".json",
    helpers.table_to_json(report),
    false
  )
end)
