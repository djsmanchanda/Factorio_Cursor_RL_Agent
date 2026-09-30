-- Path: factorio_training_lab/workflow_health.lua
-- Purpose: Read bounded phase-owned stock and machine health without choosing a repair or expansion.

local M = {}
local STATES = {"working", "starved", "blocked", "unpowered", "disabled", "idle", "other"}
local MACHINES = {furnace=true, ["assembling-machine"]=true, ["mining-drill"]=true}

local function category(entity)
  local status, names = entity.status, defines.entity_status
  local groups = {
    working={"working"}, starved={"no_ingredients", "item_ingredient_shortage", "fluid_ingredient_shortage", "no_input_fluid"},
    blocked={"full_output", "waiting_for_space_in_destination", "fluid_production_overload"},
    unpowered={"no_power", "low_power"}, disabled={"disabled_by_control_behavior", "disabled_by_script", "marked_for_deconstruction"},
    idle={"no_recipe", "no_minable_resources", "waiting_for_source_items", "no_fuel"},
  }
  for group, members in pairs(groups) do
    for _, name in ipairs(members) do
      if names[name] ~= nil and status == names[name] then return group end
    end
  end
  return "other"
end

local function inventory_items(entity, inventory_id, names)
  local ok, inventory = pcall(function() return entity.get_inventory(inventory_id) end)
  if not ok then return nil, false end
  if not inventory or inventory.valid == false then return nil, true end
  local items = {}
  for _, item in ipairs(names) do
    local count_ok, count = pcall(function() return inventory.get_item_count(item) end)
    if not count_ok then return nil, false end
    if count > 0 then items[item] = count end
  end
  return items, true
end

local function merge(into, items)
  for item, count in pairs(items) do into[item] = (into[item] or 0) + count end
end

function M.snapshot(episode, surface, force, tick, names, electricity_role, counts, resource_remaining)
  local result = {version="1.0.0", surface=surface.name, force=force.name, tick=tick,
    stock_scope="owned_inventory_and_held_items", phases={}, sources={},
    resource_remaining=resource_remaining, construction_remaining={}}
  for name, allowance in pairs(episode.scenario.construction_budget or {}) do
    result.construction_remaining[name] = math.max(0, allowance - ((counts or {})[name] or 0))
  end
  local inventory_ids = defines.inventory
  for phase, entities in pairs(episode.phase_entities or {}) do
    local record = {entities=0, machines=0, statuses={}, power={consumers=0, disconnected=0, empty=0}, stock={}}
    for _, state in ipairs(STATES) do record.statuses[state] = 0 end
    for _, role in ipairs({"input", "output", "buffer", "held"}) do
      record.stock[role] = {available=true, items={}}
    end
    for _, entity in pairs(entities) do
      if entity.valid and entity.surface == surface and entity.force == force and entity.type ~= "entity-ghost" then
        record.entities = record.entities + 1
        if MACHINES[entity.type] then
          record.machines = record.machines + 1
          local state = category(entity)
          record.statuses[state] = record.statuses[state] + 1
        end
        if electricity_role(entity) == "consumer" then
          record.power.consumers = record.power.consumers + 1
          if not entity.electric_network_id or entity.electric_network_id == 0 then
            record.power.disconnected = record.power.disconnected + 1
          end
          if (entity.energy or 0) <= 0 then record.power.empty = record.power.empty + 1 end
        end
        local roles = {}
        if entity.type == "assembling-machine" then
          roles = {input=inventory_ids.assembling_machine_input, output=inventory_ids.assembling_machine_output}
        elseif entity.type == "furnace" then
          roles = {input=inventory_ids.furnace_source, output=inventory_ids.furnace_result}
        elseif entity.type == "container" or entity.type == "logistic-container" then
          roles = {buffer=inventory_ids.chest}
        end
        for role, id in pairs(roles) do
          local items, available = inventory_items(entity, id, names)
          if not available then record.stock[role].available = false end
          if items then merge(record.stock[role].items, items) end
        end
        if entity.type == "inserter" then
          local ok, item, count = pcall(function()
            local stack = entity.held_stack
            if stack and stack.valid_for_read then return stack.name, stack.count end
          end)
          if not ok then record.stock.held.available = false
          elseif item then
            for _, name in ipairs(names) do
              if name == item then merge(record.stock.held.items, {[item]=count}); break end
            end
          end
        end
      end
    end
    local samples = (episode.workflow_health_samples or {})[phase]
    record.machine_sample_ticks = samples and samples.machine_ticks or 0
    record.working_sample_ticks = samples and samples.working_ticks or 0
    result.phases[phase] = record
  end
  for id, fixture in pairs(episode.fixtures or {}) do
    if fixture.kind == "item_source" then
      local entities = surface.find_entities_filtered({force=force, name=fixture.name,
        area={{fixture.position.x-0.01,fixture.position.y-0.01},{fixture.position.x+0.01,fixture.position.y+0.01}}})
      local record = {available=false, items={}}
      for _, entity in pairs(entities) do
        if entity.valid and entity.unit_number == fixture.unit_number then
          local items, available = inventory_items(entity, inventory_ids.chest, names)
          record.available = available and items ~= nil
          if items then record.items = items end
        end
      end
      result.sources[id] = record
    end
  end
  return result
end

function M.sample(episode, surface, force, tick)
  local last_tick = episode.workflow_health_last_tick or episode.started_tick
  local dt = tick - last_tick
  if dt <= 0 then return end
  episode.workflow_health_samples = episode.workflow_health_samples or {}
  for phase, entities in pairs(episode.phase_entities or {}) do
    local sample = episode.workflow_health_samples[phase] or {machine_ticks=0, working_ticks=0}
    for _, entity in pairs(entities) do
      if entity.valid and entity.surface == surface and entity.force == force and MACHINES[entity.type] then
        sample.machine_ticks = sample.machine_ticks + dt
        if category(entity) == "working" then sample.working_ticks = sample.working_ticks + dt end
      end
    end
    episode.workflow_health_samples[phase] = sample
  end
  episode.workflow_health_last_tick = tick
end

return M
