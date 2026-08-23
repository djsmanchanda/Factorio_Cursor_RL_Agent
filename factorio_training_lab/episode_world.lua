-- Path: factorio_training_lab/episode_world.lua
-- Purpose: Provision and recycle only mod-owned isolated training episodes.

local shared = require("training_shared")
local validation = require("scenario_validation")

local LAB_TILE_A = "lab-dark-1"
local LAB_TILE_B = "lab-dark-2"
local FLOOR_BATCH_SIZE = 1024
local OBSERVATORY_SURFACE = "training-observatory"
local TRAINING_SURFACE_PREFIX = "training/"
local ORPHAN_GRACE_TICKS = 3600
local OBSERVATORY_BOUNDS = {
  x_min = -16, y_min = -16,
  x_max_exclusive = 16, y_max_exclusive = 16
}

local function valid_hash(value)
  if type(value) ~= "string" or #value ~= 71 or string.sub(value, 1, 7) ~= "sha256:" then
    return false
  end
  return string.match(string.sub(value, 8), "^[0-9a-f]+$") ~= nil
end

local function map_settings(bounds, seed)
  return {
    seed = seed,
    width = bounds.x_max_exclusive - bounds.x_min,
    height = bounds.y_max_exclusive - bounds.y_min,
    default_enable_all_autoplace_controls = false,
    autoplace_settings = {
      entity = { treat_missing_as_default = false, settings = {} },
      tile = { treat_missing_as_default = false, settings = {} },
      decorative = { treat_missing_as_default = false, settings = {} }
    }
  }
end

local function lab_tile_name(x, y)
  return (x + y) % 2 == 0 and LAB_TILE_A or LAB_TILE_B
end

local function fill_visible_floor(surface, bounds)
  local tiles = {}
  for y = bounds.y_min, bounds.y_max_exclusive - 1 do
    for x = bounds.x_min, bounds.x_max_exclusive - 1 do
      tiles[#tiles + 1] = { name = lab_tile_name(x, y), position = { x = x, y = y } }
      if #tiles == FLOOR_BATCH_SIZE then
        surface.set_tiles(tiles)
        tiles = {}
      end
    end
  end
  if #tiles > 0 then surface.set_tiles(tiles) end
end

local function chart_episode(player, surface, bounds)
  player.force.chart(surface, {
    { bounds.x_min, bounds.y_min }, { bounds.x_max_exclusive, bounds.y_max_exclusive }
  })
end

local function reveal_surface_to_connected_players(surface, bounds)
  for _, player in pairs(game.connected_players) do
    chart_episode(player, surface, bounds)
  end
end

local function reveal_active_episodes(player)
  for _, episode in pairs(shared.ensure_storage().episodes) do
    local surface = game.surfaces[episode.surface_name]
    if surface then
      chart_episode(player, surface, episode.scenario.environment.bounds)
    end
  end
end

local function create_surface(scenario)
  local environment = scenario.environment
  local surface = game.create_surface(
    environment.surface_name,
    map_settings(environment.bounds, scenario.seed)
  )
  surface.generate_with_lab_tiles = false
  local width = environment.bounds.x_max_exclusive - environment.bounds.x_min
  local height = environment.bounds.y_max_exclusive - environment.bounds.y_min
  surface.request_to_generate_chunks({ x = 0, y = 0 }, math.ceil(math.max(width, height) / 64) + 1)
  surface.force_generate_chunk_requests()
  fill_visible_floor(surface, environment.bounds)
  return surface
end

local function observatory_surface()
  local existing = game.surfaces[OBSERVATORY_SURFACE]
  if existing then return existing end
  local surface = game.create_surface(OBSERVATORY_SURFACE, map_settings(OBSERVATORY_BOUNDS, 0))
  surface.request_to_generate_chunks({ x = 0, y = 0 }, 1)
  surface.force_generate_chunk_requests()
  fill_visible_floor(surface, OBSERVATORY_BOUNDS)
  return surface
end

local INFINITE_TECH_LEVEL = 4294967295

local function complete_primary_research(force)
  local completed = 0
  for _, technology in pairs(force.technologies) do
    local max_level = technology.prototype.max_level
    if technology.enabled and max_level ~= INFINITE_TECH_LEVEL
        and (not technology.researched or technology.level < max_level) then
      technology.level = max_level
      technology.researched = true
      completed = completed + 1
    end
  end
  force.research_queue = {}
  force.reset_technology_effects()
  return completed
end

local function create_force(scenario)
  local force = game.forces[scenario.environment.force_name]
  if not force then force = game.create_force(scenario.environment.force_name) end
  force.reset()
  complete_primary_research(force)
  return force
end

local function place_resources(surface, scenario)
  local patch, count = scenario.resource_patch, 0
  for x = patch.bounds.x1, patch.bounds.x2 do
    for y = patch.bounds.y1, patch.bounds.y2 do
      local entity = surface.create_entity({
        name = patch.resource,
        position = { x = x + 0.5, y = y + 0.5 },
        amount = patch.amount_per_tile
      })
      if not entity then error("could not place resource at " .. x .. "," .. y) end
      count = count + 1
    end
  end
  return count
end

local function place_obstacles(surface, scenario)
  local units = {}
  for _, obstacle in ipairs(scenario.obstacles or {}) do
    local bounds = obstacle.bounds
    for x = bounds.x1, bounds.x2 do
      for y = bounds.y1, bounds.y2 do
        local entity = surface.create_entity({
          name = obstacle.entity,
          position = { x = x + 0.5, y = y + 0.5 },
          force = game.forces.neutral
        })
        if not entity then error("could not place obstacle " .. obstacle.id .. " at " .. x .. "," .. y) end
        entity.minable_flag, entity.destructible, entity.rotatable = false, false, false
        units[entity.unit_number] = true
      end
    end
  end
  return units
end

local function place_fixtures(surface, force, scenario)
  local fixture_records, sink_fixture_id, sink_fixture_ids = {}, nil, {}
  for _, fixture in ipairs(scenario.fixtures) do
    local entity = surface.create_entity({
      name = fixture.entity,
      position = { x = fixture.position[1], y = fixture.position[2] },
      force = force
    })
    if not entity then error("could not place fixture " .. fixture.id) end
    -- Factorio 2.1 exposes the computed minable state as read-only; this is the
    -- script-controlled guard that keeps episode fixtures protected.
    entity.minable_flag, entity.destructible, entity.rotatable = false, false, false
    if fixture.kind == "item_source" then
      local input_item = scenario.objective.input_item
      if not input_item then error("item_source requires objective.input_item") end
      local inventory = entity.get_inventory(defines.inventory.chest)
      if not inventory then error("item_source inventory is unavailable") end
      inventory.insert({ name = input_item, count = 1000000 })
      entity.remove_unfiltered_items = false
    elseif fixture.kind == "item_sink" then
      entity.remove_unfiltered_items = false
      sink_fixture_id = sink_fixture_id or fixture.id
      sink_fixture_ids[#sink_fixture_ids + 1] = fixture.id
    end
    fixture_records[fixture.id] = {
      name = fixture.entity,
      kind = fixture.kind,
      unit_number = entity.unit_number,
      position = { x = entity.position.x, y = entity.position.y }
    }
  end
  return fixture_records, sink_fixture_id, sink_fixture_ids
end

local function begin_partial_cleanup(surface, force, request_id, episode_id)
  if surface and surface.valid then game.delete_surface(surface) end
  if force and force.valid then
    local state = shared.ensure_storage()
    state.pending_force_merges[force.name] = {
      request_id = request_id, episode_id = episode_id, provision_rollback = true
    }
    game.merge_forces(force, game.forces.neutral)
  end
end

local function build_episode(payload, scenario)
  local surface, force
  local ok, result = pcall(function()
    surface = create_surface(scenario)
    force = create_force(scenario)
    local resource_tiles = place_resources(surface, scenario)
    local obstacle_units = place_obstacles(surface, scenario)
    local fixtures, sink_fixture_id, sink_fixture_ids = place_fixtures(surface, force, scenario)
    return {
      resource_tiles = resource_tiles,
      obstacle_units = obstacle_units,
      fixtures = fixtures,
      sink_fixture_id = sink_fixture_id,
      sink_fixture_ids = sink_fixture_ids
    }
  end)
  if not ok then
    begin_partial_cleanup(surface, force, payload.request_id, payload.episode_id)
    error(result)
  end
  return surface, force, result
end

local function existing_result(payload, scenario)
  local episode = shared.ensure_storage().episodes[payload.episode_id]
  if not episode then return nil end
  if episode.scenario_hash ~= payload.scenario_hash
      or episode.surface_name ~= scenario.environment.surface_name
      or episode.force_name ~= scenario.environment.force_name then
    error("episode_id already belongs to a different scenario")
  end
  return {
    request_id = payload.request_id, episode_id = payload.episode_id,
    ok = true, status = episode.status, scenario_id = episode.scenario_id,
    scenario_hash = episode.scenario_hash, surface = episode.surface_name,
    force = episode.force_name, started_tick = episode.started_tick,
    unchanged = true
  }
end


local function players_on_surface(surface)
  for _, player in pairs(game.connected_players) do
    if player.surface == surface then return true end
  end
  return false
end
local function episode_for_surface(state, surface_name)
  for _, episode in pairs(state.episodes) do
    if episode.surface_name == surface_name then return episode end
  end
  return nil
end

local function episode_is_stale(episode, tick)
  if episode.status == "recycling" then return false end
  if episode.status ~= "ready" and episode.status ~= "running" then return true end
  local heartbeat = episode.last_sample_tick or episode.started_tick
  return type(heartbeat) ~= "number" or tick - heartbeat > ORPHAN_GRACE_TICKS
end

local function reclaim_existing_surface(surface_name, owner)
  local surface = game.surfaces[surface_name]
  if not surface then return end
  if players_on_surface(surface) then
    error("training surface is occupied by a connected observer: " .. surface_name)
  end
  local ok, deleted = pcall(function() return game.delete_surface(surface) end)
  if not ok or deleted == false then
    error("Factorio refused stale training surface cleanup: " .. surface_name)
  end
  local state = shared.ensure_storage()
  if owner then
    state.episodes[owner.episode_id] = nil
    shared.clear_episode_uploads(owner.episode_id)
  end
end

local function ensure_names_available(scenario)
  local state = shared.ensure_storage()
  local surface_name, force_name = scenario.environment.surface_name, scenario.environment.force_name
  local owner = episode_for_surface(state, surface_name)
  local surface = game.surfaces[surface_name]
  if surface then
    if owner and not episode_is_stale(owner, game.tick) then
      error("training surface is already owned by an active episode: " .. surface_name)
    end
    reclaim_existing_surface(surface_name, owner)
  end
  -- A previous recycle can leave its force behind after the surface is gone.
  -- create_force safely resets and reuses that isolated force.
  if state.pending_force_merges[force_name] then
    error("training force is still being merged: " .. force_name)
  end
end

local function provision(payload)
  if payload.confirmation_token ~= "PROVISION_TRAINING_EPISODE" then
    error("provision confirmation token is invalid")
  end
  if not valid_hash(payload.scenario_hash) then error("scenario_hash must be sha256") end
  local scenario = validation.validate_scenario(payload.scenario)
  if payload.scenario_hash ~= scenario.scenario_hash then
    error("provision envelope scenario_hash does not match embedded scenario_hash")
  end
  local existing = existing_result(payload, scenario)
  if existing then return existing end
  ensure_names_available(scenario)
  local _, _, created = build_episode(payload, scenario)
  local episode = {
    owner = shared.OWNER, episode_id = payload.episode_id,
    scenario_id = scenario.scenario_id, scenario_hash = payload.scenario_hash,
    surface_name = scenario.environment.surface_name,
    force_name = scenario.environment.force_name, scenario = scenario,
    started_tick = game.tick, last_sample_tick = game.tick, status = "ready",
    fixtures = created.fixtures, obstacle_units = created.obstacle_units,
    sink_fixture_id = created.sink_fixture_id, sink_fixture_ids = created.sink_fixture_ids,
    delivered_items = 0, stage_delivered_items = 0, sample_items = 0, sample_ticks = 0, rate_samples = {},
    sink_rate_samples = {}, sink_rate_per_tick = {}, rate_per_tick = 0, sustained_ticks = 0,
    stage_index = 1, stage_id = scenario.objective.stages and scenario.objective.stages[1].id or "default",
    failure_kind = "none", failure_reason = ""
  }
  shared.ensure_storage().episodes[payload.episode_id] = episode
  reveal_surface_to_connected_players(
    game.surfaces[episode.surface_name], scenario.environment.bounds
  )
  return {
    request_id = payload.request_id, episode_id = payload.episode_id,
    ok = true, status = "ready", scenario_id = scenario.scenario_id,
    scenario_hash = payload.scenario_hash, surface = episode.surface_name,
    force = episode.force_name, started_tick = game.tick, unchanged = false,
    created = { surface = true, force = true,
      resource_tiles = created.resource_tiles, fixtures = #scenario.fixtures }
  }
end


local function training_surface_name(name)
  return type(name) == "string"
    and string.sub(name, 1, #TRAINING_SURFACE_PREFIX) == TRAINING_SURFACE_PREFIX
end

local function matching_force_name(surface_name)
  return "training-" .. string.sub(surface_name, #TRAINING_SURFACE_PREFIX + 1)
end

local function owned_training_surfaces(state, tick)
  local owned = {}
  for _, episode in pairs(state.episodes) do
    -- Terminal records remain in storage for evidence, but no longer own a
    -- live surface. Keep only states that can still receive worker commands.
    local active = episode.status == "ready"
        or episode.status == "running"
        or episode.status == "recycling"
    local heartbeat = episode.last_sample_tick or episode.started_tick
    local fresh = episode.status == "recycling"
        or (type(heartbeat) == "number" and tick - heartbeat <= ORPHAN_GRACE_TICKS)
    if active and fresh and type(episode.surface_name) == "string" then
      owned[episode.surface_name] = true
    end
  end
  return owned
end

local function owned_training_forces(state, tick)
  local owned = {}
  for _, episode in pairs(state.episodes) do
    local active = episode.status == "ready"
        or episode.status == "running"
        or episode.status == "recycling"
    local heartbeat = episode.last_sample_tick or episode.started_tick
    local fresh = episode.status == "recycling"
        or (type(heartbeat) == "number" and tick - heartbeat <= ORPHAN_GRACE_TICKS)
    if active and fresh and type(episode.force_name) == "string" then
      owned[episode.force_name] = true
    end
  end
  for force_name, _ in pairs(state.pending_force_merges) do
    owned[force_name] = true
  end
  return owned
end

local function has_surface_for_force(force_name)
  local expected = "training/" .. string.sub(force_name, 10)
  for _, surface in pairs(game.surfaces) do
    if surface.name == expected then return true end
  end
  return false
end

local function cleanup_orphan_forces(state, tick)
  local owned = owned_training_forces(state, tick)
  local merged = {}
  for name, force in pairs(game.forces) do
    if string.sub(name, 1, 9) == "training-"
        and not owned[name]
        and not has_surface_for_force(name)
        and force.valid then
      local ok = pcall(function() game.merge_forces(force, game.forces.neutral) end)
      if ok then merged[#merged + 1] = name end
    end
  end
  return merged
end

local function recycle_orphan_surface(surface, tick, owned, immediate)
  local state = shared.ensure_storage()
  local name = surface.name
  if owned[name] then
    state.orphan_surfaces[name] = nil
    return "owned"
  end
  local first_seen = state.orphan_surfaces[name] or tick
  state.orphan_surfaces[name] = first_seen
  if not immediate and tick - first_seen < ORPHAN_GRACE_TICKS then return "pending" end
  if players_on_surface(surface) then
    log("[factorio_training_lab] preserving orphan training surface with a connected player: " .. name)
    return "connected"
  end
  local force = game.forces[matching_force_name(name)]
  local ok, deleted = pcall(function() return game.delete_surface(surface) end)
  if not ok or deleted == false then
    log("[factorio_training_lab] Factorio refused orphan surface cleanup: " .. name)
    return "refused"
  end
  state.orphan_surfaces[name] = nil
  if force and force.valid and force.name ~= "neutral" then
    game.merge_forces(force, game.forces.neutral)
  end
  log("[factorio_training_lab] recycled orphan training surface: " .. name)
  return "recycled"
end

local function cleanup_orphan_surfaces(tick, immediate)
  if type(tick) ~= "number" or tick < 0 then error("cleanup tick must be non-negative") end
  local state = shared.ensure_storage()
  local owned = owned_training_surfaces(state, tick)
  local candidates = {}
  local result = {
    inspected = 0, recycled = {}, recycled_forces = cleanup_orphan_forces(state, tick),
    pending = {}, connected = {}, refused = {}
  }
  for _, surface in pairs(game.surfaces) do
    if training_surface_name(surface.name) then candidates[#candidates + 1] = surface end
  end
  for _, surface in pairs(candidates) do
    if surface.valid then
      result.inspected = result.inspected + 1
      local name = surface.name
      local status = recycle_orphan_surface(surface, tick, owned, immediate)
      if status == "recycled" then result.recycled[#result.recycled + 1] = name end
      if status == "pending" then result.pending[#result.pending + 1] = name end
      if status == "connected" then result.connected[#result.connected + 1] = name end
      if status == "refused" then result.refused[#result.refused + 1] = name end
    end
  end
  return result
end

local function cleanup_command(command)
  local payload = shared.parse_command(command, false)
  if payload.confirmation_token ~= "RECYCLE_STALE_TRAINING_SURFACES" then
    error("stale-surface cleanup confirmation token is invalid")
  end
  local result = cleanup_orphan_surfaces(game.tick, true)
  result.ok = true
  result.status = "completed"
  result.request_id = payload.request_id
  result.tick = game.tick
  rcon.print(helpers.table_to_json(result))
end

local function evacuate_players(surface)
  local observatory = observatory_surface()
  for _, player in pairs(game.connected_players) do
    if player.surface == surface then player.teleport({ x = 0, y = 0 }, observatory) end
  end
end

local function recycle(payload)
  if payload.confirmation_token ~= "RECYCLE_TRAINING_EPISODE" then
    error("recycle confirmation token is invalid")
  end
  local episode = shared.episode_for(payload.episode_id)
  local surface, force = game.surfaces[episode.surface_name], game.forces[episode.force_name]
  if not surface or not force then error("episode surface or force is missing") end
  evacuate_players(surface)
  if players_on_surface(surface) then error("cannot recycle a surface containing a connected player") end
  if not game.delete_surface(surface) then error("Factorio refused to delete the training surface") end
  shared.clear_episode_uploads(payload.episode_id)
  episode.status = "recycling"
  shared.ensure_storage().pending_force_merges[force.name] = {
    request_id = payload.request_id, episode_id = payload.episode_id,
    scenario_id = episode.scenario_id, surface = episode.surface_name
  }
  game.merge_forces(force, game.forces.neutral)
  return {
    request_id = payload.request_id, episode_id = payload.episode_id,
    ok = true, status = "pending_force_merge", scenario_id = episode.scenario_id,
    surface = episode.surface_name, force = episode.force_name
  }
end

local function view_episode(command)
  if command.player_index == nil then return end
  local requested = command.parameter or ""
  local episode
  for _, item in pairs(shared.ensure_storage().episodes) do
    if item.surface_name == requested then episode = item break end
  end
  local player = game.get_player(command.player_index)
  if not episode then
    player.print("Unknown active training surface: " .. requested)
    return
  end
  local surface = game.surfaces[episode.surface_name]
  local bounds = episode.scenario.environment.bounds
  player.set_controller({ type = defines.controllers.spectator })
  chart_episode(player, surface, bounds)
  if not player.teleport({ x = bounds.x_min + 2, y = bounds.y_min + 2 }, surface) then
    player.print("Unable to enter observer view for " .. episode.surface_name)
    return
  end
  player.print("Viewing " .. episode.surface_name .. " as a spectator; observer moves before recycle.")
end

local function focus_observer(payload)
  if payload.confirmation_token ~= "FOCUS_TRAINING_OBSERVER" then
    error("observer focus confirmation token is invalid")
  end
  if type(payload.observer_name) ~= "string" or payload.observer_name == ""
      or #payload.observer_name > 128 then
    error("observer_name must be a nonempty string of at most 128 characters")
  end
  local episode = shared.episode_for(payload.episode_id)
  local player = game.get_player(payload.observer_name)
  if not player or not player.connected then
    error("configured observer is not connected: " .. payload.observer_name)
  end
  local surface = game.surfaces[episode.surface_name]
  if not surface then error("training episode surface is missing") end
  local bounds = episode.scenario.environment.bounds
  player.set_controller({ type = defines.controllers.spectator })
  chart_episode(player, surface, bounds)
  if not player.teleport({ x = bounds.x_min + 2, y = bounds.y_min + 2 }, surface) then
    error("unable to enter observer view for " .. episode.surface_name)
  end
  return {
    request_id = payload.request_id, episode_id = payload.episode_id,
    ok = true, status = "focused", observer_name = player.name,
    surface = episode.surface_name
  }
end

local function focus_command(command)
  local ok, result = pcall(function()
    return focus_observer(shared.parse_command(command))
  end)
  if not ok then result = { ok = false, error = tostring(result) } end
  rcon.print(helpers.table_to_json(result))
end
local function command_handler(kind, operation)
  return function(command)
    local request_id, episode_id = "invalid", "invalid"
    local ok, result = pcall(function()
      local payload = shared.parse_command(command)
      request_id, episode_id = payload.request_id, payload.episode_id
      return operation(payload)
    end)
    if not ok then
      result = { request_id = request_id, episode_id = episode_id,
        ok = false, status = "failed", error = tostring(result) }
    end
    shared.write_report(kind, result)
  end
end

local function complete_force_merge(event)
  local state = shared.ensure_storage()
  local pending = state.pending_force_merges[event.source_name]
  if not pending then return end
  state.pending_force_merges[event.source_name] = nil
  state.episodes[pending.episode_id] = nil
  shared.write_report("recycle", {
    request_id = pending.request_id, episode_id = pending.episode_id,
    ok = true, status = "completed", scenario_id = pending.scenario_id,
    surface = pending.surface, force = event.source_name
  })
end

local function register_commands()
  commands.add_command("training_provision", "Provision one isolated training episode (RCON only).",
    command_handler("provision", provision))
  commands.add_command("training_recycle", "Recycle one owned training episode (RCON only).",
    command_handler("recycle", recycle))
  commands.add_command("training_cleanup_orphans", "Recycle unowned training surfaces (RCON only).",
    cleanup_command)
  commands.add_command("training_focus", "Focus the configured observer on one training surface (RCON only).",
    focus_command)
  commands.add_command("training_view", "View one active training surface (client only).", view_episode)
end

return {
  complete_primary_research = complete_primary_research,
  cleanup_orphan_surfaces = cleanup_orphan_surfaces,
  cleanup_command = cleanup_command,
  complete_force_merge = complete_force_merge,
  fill_visible_floor = fill_visible_floor,
  focus_observer = focus_observer,
  reveal_active_episodes = reveal_active_episodes,
  register_commands = register_commands,
  view_episode = view_episode,
  ensure_names_available = ensure_names_available
}
