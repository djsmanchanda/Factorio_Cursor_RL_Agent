-- Path: factorio_training_lab/episode_measurement.lua
-- Purpose: Sample item delivery and audit active training episodes every 60 ticks.

local shared = require("training_shared")
local geometry = require("training_geometry")

local POWER_SOURCE_TYPES = {
  ["electric-energy-interface"] = true,
  ["solar-panel"] = true,
  generator = true,
  ["fusion-generator"] = true
}
local POWER_STORAGE_TYPES = { accumulator = true }
local POWER_CONSUMER_TYPES = { inserter = true }
local POWER_CONSUMER_NAMES = { ["electric-mining-drill"] = true }
local RATE_WINDOW_TICKS = 600
local FOOTPRINT_TILE_CACHE = {}
local electricity_role

local function advance_sample(episode, delivered, tick)
  local sample_ticks = tick - episode.last_sample_tick
  if sample_ticks <= 0 then return episode end
  episode.last_sample_tick = tick
  episode.sample_ticks = sample_ticks
  episode.sample_items = delivered
  episode.delivered_items = episode.delivered_items + delivered
  episode.rate_samples = episode.rate_samples or {}
  table.insert(episode.rate_samples, { items = delivered, ticks = sample_ticks })
  local window_items, window_ticks = 0, 0
  for _, sample in pairs(episode.rate_samples) do
    window_items = window_items + sample.items
    window_ticks = window_ticks + sample.ticks
  end
  while window_ticks > RATE_WINDOW_TICKS and #episode.rate_samples > 1 do
    local expired = table.remove(episode.rate_samples, 1)
    window_items = window_items - expired.items
    window_ticks = window_ticks - expired.ticks
  end
  episode.rate_per_tick = window_items / window_ticks
  local objective = episode.scenario.objective
  if episode.rate_per_tick + 1e-9 >= objective.target_rate_per_tick then
    episode.sustained_ticks = episode.sustained_ticks + sample_ticks
  else
    episode.sustained_ticks = 0
  end
  episode.status = "running"
  if episode.sustained_ticks >= objective.sustain_ticks then
    episode.status = "completed"
  elseif tick - episode.started_tick >= episode.scenario.constraints.max_episode_ticks then
    episode.status = "timed_out"
    episode.failure_kind = "timeout"
    episode.failure_reason = "maximum episode ticks elapsed"
  end
  return episode
end

local function fixture_units(episode)
  local units = {}
  for _, fixture in pairs(episode.fixtures) do units[fixture.unit_number] = true end
  return units
end

local function footprint_tiles(name)
  local cached = FOOTPRINT_TILE_CACHE[name]
  if cached then return cached end
  local prototype = prototypes.entity[name]
  local box = prototype and (prototype.collision_box or prototype.selection_box)
  local width, height = 1, 1
  if box and box.left_top and box.right_bottom then
    width = math.max(1, math.ceil(box.right_bottom.x - box.left_top.x))
    height = math.max(1, math.ceil(box.right_bottom.y - box.left_top.y))
  end
  local tiles = width * height
  FOOTPRINT_TILE_CACHE[name] = tiles
  return tiles
end

local function drill_status(entity)
  local statuses = defines and defines.entity_status or {}
  local status = entity.status
  if statuses.working ~= nil and status == statuses.working then return "working" end
  if statuses.full_output ~= nil and status == statuses.full_output then return "blocked" end
  return "idle"
end

local function mark_productive_drill(episode, unit_number)
  if not unit_number then return end
  episode.productive_drill_units = episode.productive_drill_units or {}
  if episode.productive_drill_units[unit_number] then return end
  episode.productive_drill_units[unit_number] = true
  episode.productive_mining_drills = (episode.productive_mining_drills or 0) + 1
end

local function drill_efficiency(episode, placed, working, blocked, idle, sample_ticks)
  episode.productive_drill_units = episode.productive_drill_units or {}
  episode.mining_drill_capacity_ticks = episode.mining_drill_capacity_ticks or 0
  episode.mining_drill_working_ticks = episode.mining_drill_working_ticks or 0
  episode.mining_drill_blocked_ticks = episode.mining_drill_blocked_ticks or 0
  episode.mining_drill_idle_ticks = episode.mining_drill_idle_ticks or 0
  if sample_ticks and sample_ticks > 0 then
    episode.mining_drill_capacity_ticks = episode.mining_drill_capacity_ticks + placed * sample_ticks
    episode.mining_drill_working_ticks = episode.mining_drill_working_ticks + working * sample_ticks
    episode.mining_drill_blocked_ticks = episode.mining_drill_blocked_ticks + blocked * sample_ticks
    episode.mining_drill_idle_ticks = episode.mining_drill_idle_ticks + idle * sample_ticks
  end
  local productive = episode.productive_mining_drills or 0
  return {
    placed_mining_drills = placed,
    productive_mining_drills = productive,
    productive_mining_drill_ratio = placed > 0 and math.min(1, productive / placed) or 0,
    mining_drill_capacity_ticks = episode.mining_drill_capacity_ticks,
    mining_drill_working_ticks = episode.mining_drill_working_ticks,
    mining_drill_blocked_ticks = episode.mining_drill_blocked_ticks,
    mining_drill_idle_ticks = episode.mining_drill_idle_ticks,
  }
end

local function audit_entities(surface, force, episode, sample_ticks)
  local allowed = episode.scenario.constraints.allowed_entities
  local allowed_lookup, counts, fixture_lookup = {}, {}, fixture_units(episode)
  for _, name in ipairs(allowed) do allowed_lookup[name] = true end
  local forbidden, outside, pole_count, occupied_tiles = 0, 0, 0, 0
  local placed_drills, working_drills, blocked_drills, idle_drills = 0, 0, 0, 0
  local power_consumers = {}
  for _, entity in pairs(surface.find_entities()) do
    local neutral_resource = entity.type == "resource" and entity.force.name == "neutral"
    if not neutral_resource and not fixture_lookup[entity.unit_number] then
      local name = entity.type == "entity-ghost" and entity.ghost_name or entity.name
      counts[name] = (counts[name] or 0) + 1
      if entity.force ~= force or not allowed_lookup[name] then forbidden = forbidden + 1 end
      if not geometry.fits(episode.scenario.constraints.allowed_build_area, name, entity.position) then
        outside = outside + 1
      end
      if entity.type ~= "entity-ghost" then
        occupied_tiles = occupied_tiles + footprint_tiles(name)
        if entity.force == force and electricity_role(entity) == "consumer" then
          power_consumers[#power_consumers + 1] = entity
        end
        if entity.type == "electric-pole" then pole_count = pole_count + 1 end
        if entity.type == "mining-drill" then
          placed_drills = placed_drills + 1
          local status = drill_status(entity)
          if status == "working" then
            working_drills = working_drills + 1
            mark_productive_drill(episode, entity.unit_number)
          elseif status == "blocked" then
            blocked_drills = blocked_drills + 1
          else
            idle_drills = idle_drills + 1
          end
        end
      end
    end
  end
  local overruns = 0
  for name, count in pairs(counts) do
    if count > (episode.scenario.construction_budget[name] or 0) then overruns = overruns + 1 end
  end
  local efficiency = drill_efficiency(
    episode, placed_drills, working_drills, blocked_drills, idle_drills, sample_ticks
  )
  efficiency.electric_pole_count = pole_count
  efficiency.occupied_footprint_tiles = occupied_tiles
  return counts, forbidden, outside, overruns, efficiency, power_consumers
end

local function fixture_entity(surface, force, fixture)
  local candidates = surface.find_entities_filtered({
    name = fixture.name,
    area = {
      { fixture.position.x - 0.01, fixture.position.y - 0.01 },
      { fixture.position.x + 0.01, fixture.position.y + 0.01 }
    },
    force = force
  })
  for _, entity in pairs(candidates) do
    if entity.valid and entity.position.x == fixture.position.x
        and entity.position.y == fixture.position.y
        and entity.unit_number == fixture.unit_number then return entity end
  end
  return nil
end

local function check_fixtures(surface, force, episode)
  for _, fixture in pairs(episode.fixtures) do
    local entity = fixture_entity(surface, force, fixture)
    if not entity or entity.surface.name ~= episode.surface_name then return false end
  end
  return true
end

local function electric_network_id(entity)
  local network = entity and entity.electric_network_id
  return type(network) == "number" and network >= 0 and network or nil
end

electricity_role = function(entity)
  if POWER_STORAGE_TYPES[entity.type] or entity.name == "accumulator" then return "storage" end
  if POWER_SOURCE_TYPES[entity.type] or POWER_SOURCE_TYPES[entity.name] then return "source" end
  if POWER_CONSUMER_TYPES[entity.type] or POWER_CONSUMER_NAMES[entity.name] then return "consumer" end
  return nil
end

local function power_state(surface, force, episode, consumers)
  if consumers == nil then
    consumers = {}
    for _, entity in pairs(surface.find_entities_filtered({ force = force })) do
      if electricity_role(entity) == "consumer" then consumers[#consumers + 1] = entity end
    end
  end
  if #consumers == 0 then return true, "" end
  local source, storage = nil, nil
  for _, fixture in pairs(episode.fixtures) do
    if fixture.kind == "power_source" then
      source = fixture_entity(surface, force, fixture)
    elseif fixture.kind == "power_storage" then
      storage = fixture_entity(surface, force, fixture)
    end
  end
  if not source or electricity_role(source) ~= "source" then
    return false, "power source fixture is not an electricity producer"
  end
  local network = electric_network_id(source)
  if not network then return false, "power source has no electric network" end
  if storage then
    if electricity_role(storage) ~= "storage" then return false, "power storage fixture is not an accumulator" end
    if electric_network_id(storage) ~= network then
      return false, "power storage is disconnected from the power source"
    end
  end
  for _, consumer in pairs(consumers) do
    if electric_network_id(consumer) ~= network then
      return false, "electricity consumer is disconnected from the power source"
    end
  end
  return true, ""
end

local function fail_safety(episode, reason)
  episode.status = "failed"
  episode.failure_kind = "safety"
  episode.failure_reason = reason
end

local function fail_power(episode, reason)
  episode.status = "failed"
  episode.failure_kind = "power_unconnected"
  episode.failure_reason = reason
end

local function sample_episode(episode, tick)
  local surface, force = game.surfaces[episode.surface_name], game.forces[episode.force_name]
  if not surface or not force then fail_safety(episode, "episode surface or force is missing"); return end
  if not check_fixtures(surface, force, episode) then
    fail_safety(episode, "protected fixture is missing or changed")
    return
  end
  local sample_ticks = tick - episode.last_sample_tick
  local _, forbidden, outside, overruns, _, consumers = audit_entities(
    surface, force, episode, sample_ticks
  )
  local powered, power_reason = power_state(surface, force, episode, consumers)
  episode.power_connected = powered
  if not powered then fail_power(episode, power_reason); return end
  local sink = fixture_entity(surface, force, episode.fixtures[episode.sink_fixture_id])
  local inventory = sink and sink.get_inventory(defines.inventory.chest) or nil
  if not inventory then fail_safety(episode, "item sink inventory is unavailable"); return end
  local item = episode.scenario.objective.item
  local delivered = inventory.get_item_count(item)
  if delivered > 0 then inventory.remove({ name = item, count = delivered }) end
  episode.forbidden_entities, episode.out_of_bounds_entities = forbidden, outside
  episode.budget_overruns = overruns
  if forbidden > 0 or outside > 0 or overruns > 0 then
    fail_safety(episode, "training entity constraint violated")
    return
  end
  advance_sample(episode, delivered, tick)
end

local function sample_all(tick)
  for _, episode in pairs(shared.ensure_storage().episodes) do
    if episode.status == "ready" or episode.status == "running" then
      sample_episode(episode, tick)
    end
  end
end

local function resource_remaining(surface, episode)
  local patch = episode.scenario.resource_patch
  local total = 0
  for _, entity in pairs(surface.find_entities_filtered({
    type = "resource", name = patch.resource,
    area = { { patch.bounds.x1, patch.bounds.y1 }, { patch.bounds.x2 + 1, patch.bounds.y2 + 1 } }
  })) do total = total + (entity.amount or 0) end
  return total
end

local function observation(payload)
  local episode = shared.episode_for(payload.episode_id)
  local surface, force = game.surfaces[episode.surface_name], game.forces[episode.force_name]
  if not surface or not force then error("episode surface or force is missing") end
  local counts, forbidden, outside, overruns, efficiency, consumers = audit_entities(
    surface, force, episode, 0
  )
  if not check_fixtures(surface, force, episode) then
    fail_safety(episode, "protected fixture is missing or changed")
  end
  local powered, power_reason = power_state(surface, force, episode, consumers)
  episode.power_connected = powered
  if not powered then fail_power(episode, power_reason) end
  if forbidden > 0 or outside > 0 or overruns > 0 then
    fail_safety(episode, "training entity constraint violated")
  end
  return {
    request_id = payload.request_id, episode_id = payload.episode_id,
    ok = true, status = episode.status, scenario_id = episode.scenario_id,
    surface = episode.surface_name, force = episode.force_name,
    started_tick = episode.started_tick, elapsed_ticks = game.tick - episode.started_tick,
    objective = {
      item = episode.scenario.objective.item,
      target_rate_per_tick = episode.scenario.objective.target_rate_per_tick,
      sustain_ticks = episode.scenario.objective.sustain_ticks
    },
    metrics = {
      delivered_items = episode.delivered_items, sample_ticks = episode.sample_ticks,
      sample_items = episode.sample_items, rate_per_tick = episode.rate_per_tick,
      sustained_ticks = episode.sustained_ticks,
      resource_remaining = resource_remaining(surface, episode), built_entities = counts,
      forbidden_entities = forbidden, out_of_bounds_entities = outside,
      budget_overruns = overruns, fixtures_valid = check_fixtures(surface, force, episode),
      power_connected = powered,
      electric_pole_count = efficiency.electric_pole_count,
      occupied_footprint_tiles = efficiency.occupied_footprint_tiles,
      placed_mining_drills = efficiency.placed_mining_drills,
      productive_mining_drills = efficiency.productive_mining_drills,
      productive_mining_drill_ratio = efficiency.productive_mining_drill_ratio,
      mining_drill_capacity_ticks = efficiency.mining_drill_capacity_ticks,
      mining_drill_working_ticks = efficiency.mining_drill_working_ticks,
      mining_drill_blocked_ticks = efficiency.mining_drill_blocked_ticks,
      mining_drill_idle_ticks = efficiency.mining_drill_idle_ticks,
    },
    failure = { kind = episode.failure_kind, reason = episode.failure_reason }
  }
end

local function observe_command(command)
  local request_id, episode_id = "invalid", "invalid"
  local ok, result = pcall(function()
    local payload = shared.parse_command(command)
    request_id, episode_id = payload.request_id, payload.episode_id
    return observation(payload)
  end)
  if not ok then
    result = { request_id = request_id, episode_id = episode_id,
      ok = false, status = "failed", error = tostring(result) }
  end
  shared.write_report("observation", result)
end

local function register_commands()
  commands.add_command("training_observe", "Observe one owned training episode (RCON only).",
    observe_command)
end

return {
  advance_sample = advance_sample,
  audit_entities = audit_entities,
  power_state = power_state,
  check_fixtures = check_fixtures,
  register_commands = register_commands,
  sample_all = sample_all
}
