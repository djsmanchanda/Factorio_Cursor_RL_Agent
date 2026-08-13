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
local POWER_CONSUMER_NAMES = { ["electric-mining-drill"] = true, ["electric-furnace"] = true }
local RATE_WINDOW_TICKS = 600
local FOOTPRINT_TILE_CACHE = {}
local electricity_role

local function active_stage(episode)
  local stages = episode.scenario.objective.stages
  if stages then
    local index = episode.stage_index or 1
    return stages[index], index, #stages
  end
  return episode.scenario.objective, 1, 1
end

local function stage_destinations(episode, stage)
  if stage.destination_fixture_ids then return stage.destination_fixture_ids end
  return { episode.sink_fixture_id or stage.destination_fixture_id or "__legacy_sink" }
end

local function window_rate(samples)
  local items, ticks = 0, 0
  for _, sample in ipairs(samples or {}) do
    items = items + sample.items
    ticks = ticks + sample.ticks
  end
  while ticks > RATE_WINDOW_TICKS and #samples > 1 do
    local expired = table.remove(samples, 1)
    items = items - expired.items
    ticks = ticks - expired.ticks
  end
  return ticks > 0 and items / ticks or 0
end

local function reset_stage_window(episode, tick)
  episode.last_sample_tick = tick
  episode.sample_ticks, episode.sample_items = 0, 0
  episode.rate_samples = {}
  episode.sink_rate_samples = {}
  episode.sink_rate_per_tick = {}
  episode.rate_per_tick, episode.sustained_ticks = 0, 0
end

local function advance_sample(episode, delivered, tick, sink_deliveries)
  local sample_ticks = tick - episode.last_sample_tick
  if sample_ticks <= 0 then return episode end
  local stage, stage_index, stage_count = active_stage(episode)
  local destinations = stage_destinations(episode, stage)
  sink_deliveries = sink_deliveries or {}
  if next(sink_deliveries) == nil and destinations[1] then sink_deliveries[destinations[1]] = delivered end
  episode.stage_index, episode.stage_id = stage_index, stage.id or "default"
  episode.last_sample_tick = tick
  episode.sample_ticks = sample_ticks
  episode.sample_items = delivered
  episode.delivered_items = (episode.delivered_items or 0) + delivered
  episode.stage_delivered_items = (episode.stage_delivered_items or 0) + delivered
  episode.rate_samples = episode.rate_samples or {}
  table.insert(episode.rate_samples, { items = delivered, ticks = sample_ticks })
  episode.rate_per_tick = window_rate(episode.rate_samples)
  episode.sink_rate_samples = episode.sink_rate_samples or {}
  episode.sink_rate_per_tick = episode.sink_rate_per_tick or {}
  local target_per_sink = stage.target_rate_per_tick / #destinations
  local meets_target = episode.rate_per_tick + 1e-9 >= stage.target_rate_per_tick
  for _, fixture_id in ipairs(destinations) do
    local samples = episode.sink_rate_samples[fixture_id] or {}
    table.insert(samples, { items = sink_deliveries[fixture_id] or 0, ticks = sample_ticks })
    episode.sink_rate_samples[fixture_id] = samples
    local sink_rate = window_rate(samples)
    episode.sink_rate_per_tick[fixture_id] = sink_rate
    if sink_rate + 1e-9 < target_per_sink then meets_target = false end
  end
  if meets_target then
    episode.sustained_ticks = (episode.sustained_ticks or 0) + sample_ticks
  else
    episode.sustained_ticks = 0
  end
  episode.status = "running"
  if episode.sustained_ticks >= stage.sustain_ticks then
    if stage_index < stage_count then
      episode.completed_stage_count = stage_index
      episode.stage_index = stage_index + 1
      episode.stage_id = active_stage(episode).id or "default"
      reset_stage_window(episode, tick)
      return episode
    end
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
  local sink_ids = episode.sink_fixture_ids or { episode.sink_fixture_id }
  local delivered_by_sink, delivered = {}, 0
  local item = episode.scenario.objective.item
  for _, sink_id in ipairs(sink_ids) do
    local sink = fixture_entity(surface, force, episode.fixtures[sink_id])
    local inventory = sink and sink.get_inventory(defines.inventory.chest) or nil
    if not inventory then fail_safety(episode, "item sink inventory is unavailable"); return end
    local count = inventory.get_item_count(item)
    if count > 0 then inventory.remove({ name = item, count = count }) end
    delivered_by_sink[sink_id], delivered = count, delivered + count
  end
  episode.forbidden_entities, episode.out_of_bounds_entities = forbidden, outside
  episode.budget_overruns = overruns
  if forbidden > 0 or outside > 0 or overruns > 0 then
    fail_safety(episode, "training entity constraint violated")
    return
  end
  advance_sample(episode, delivered, tick, delivered_by_sink)
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
  local stage, stage_index, stage_count = active_stage(episode)
  return {
    request_id = payload.request_id, episode_id = payload.episode_id,
    ok = true, status = episode.status, scenario_id = episode.scenario_id,
    surface = episode.surface_name, force = episode.force_name,
    started_tick = episode.started_tick, elapsed_ticks = game.tick - episode.started_tick,
    objective = {
      item = episode.scenario.objective.item,
      target_rate_per_tick = stage.target_rate_per_tick,
      sustain_ticks = stage.sustain_ticks,
      stage_index = stage_index, stage_count = stage_count, stage_id = stage.id or "default",
      destination_fixture_ids = stage_destinations(episode, stage)
    },
    metrics = {
      delivered_items = episode.delivered_items, stage_delivered_items = episode.stage_delivered_items,
      sample_ticks = episode.sample_ticks,
      sample_items = episode.sample_items, rate_per_tick = episode.rate_per_tick,
      sustained_ticks = episode.sustained_ticks, stage_index = stage_index,
      stage_id = stage.id or "default", sink_rate_per_tick = episode.sink_rate_per_tick or {},
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
