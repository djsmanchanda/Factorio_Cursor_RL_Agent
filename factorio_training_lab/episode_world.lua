-- Path: factorio_training_lab/episode_world.lua
-- Purpose: Provision and recycle only mod-owned isolated training episodes.

local shared = require("training_shared")
local validation = require("scenario_validation")

local LAB_TILE_A = "lab-dark-1"
local LAB_TILE_B = "lab-dark-2"
local FLOOR_BATCH_SIZE = 1024
local OBSERVATORY_SURFACE = "training-observatory"
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
  local force = game.create_force(scenario.environment.force_name)
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

local function place_fixtures(surface, force, scenario)
  local fixture_records, sink_fixture_id = {}, nil
  for _, fixture in ipairs(scenario.fixtures) do
    local entity = surface.create_entity({
      name = fixture.entity,
      position = { x = fixture.position[1], y = fixture.position[2] },
      force = force
    })
    if not entity then error("could not place fixture " .. fixture.id) end
    entity.minable, entity.destructible, entity.rotatable = false, false, false
    if fixture.kind == "item_sink" then
      entity.remove_unfiltered_items = false
      sink_fixture_id = fixture.id
    end
    fixture_records[fixture.id] = {
      name = fixture.entity,
      kind = fixture.kind,
      unit_number = entity.unit_number,
      position = { x = entity.position.x, y = entity.position.y }
    }
  end
  return fixture_records, sink_fixture_id
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
    local fixtures, sink_fixture_id = place_fixtures(surface, force, scenario)
    return { resource_tiles = resource_tiles, fixtures = fixtures, sink_fixture_id = sink_fixture_id }
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

local function ensure_names_available(scenario)
  if game.surfaces[scenario.environment.surface_name] then
    error("training surface already exists without matching episode ownership")
  end
  if game.forces[scenario.environment.force_name] then
    error("training force already exists without matching episode ownership")
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
    fixtures = created.fixtures, sink_fixture_id = created.sink_fixture_id,
    delivered_items = 0, sample_items = 0, sample_ticks = 0, rate_samples = {},
    rate_per_tick = 0, sustained_ticks = 0,
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

local function players_on_surface(surface)
  for _, player in pairs(game.connected_players) do
    if player.surface == surface then return true end
  end
  return false
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
  commands.add_command("training_focus", "Focus the configured observer on one training surface (RCON only).",
    focus_command)
  commands.add_command("training_view", "View one active training surface (client only).", view_episode)
end

return {
  complete_primary_research = complete_primary_research,
  complete_force_merge = complete_force_merge,
  fill_visible_floor = fill_visible_floor,
  focus_observer = focus_observer,
  reveal_active_episodes = reveal_active_episodes,
  register_commands = register_commands,
  view_episode = view_episode
}
