-- Path: factorio_mod/control.lua
-- Purpose: Export deterministic factory snapshots as JSON for schema validation.

local function ensure_storage()
  if not global.storage then
    global.storage = {}
  end
  return global.storage
end

local function pick_surface()
  if game.surfaces["nauvis"] then
    return game.surfaces["nauvis"]
  end
  return game.surfaces[1]
end

local function entity_to_snapshot(entity)
  local data = {
    name = entity.name,
    type = entity.type,
    position = { x = entity.position.x, y = entity.position.y }
  }

  if entity.direction ~= nil then
    data.direction = entity.direction
  end

  if entity.force ~= nil and entity.force.name ~= nil then
    data.force = entity.force.name
  end

  if entity.get_recipe then
    local recipe = entity.get_recipe()
    if recipe and recipe.name then
      data.recipe = recipe.name
    end
  end

  return data
end

local function sort_entities(entities)
  table.sort(entities, function(a, b)
    if a.name ~= b.name then
      return a.name < b.name
    end
    if a.type ~= b.type then
      return a.type < b.type
    end

    local ax, ay = a.position.x, a.position.y
    local bx, by = b.position.x, b.position.y

    if ax ~= bx then
      return ax < bx
    end
    if ay ~= by then
      return ay < by
    end

    local af = a.force or ""
    local bf = b.force or ""

    if af ~= bf then
      return af < bf
    end

    local ad = a.direction or -1
    local bd = b.direction or -1

    return ad < bd
  end)
end

local function build_snapshot(surface)
  local entities = {}
  for _, entity in pairs(surface.find_entities()) do
    table.insert(entities, entity_to_snapshot(entity))
  end
  sort_entities(entities)

  return {
    tick = game.tick,
    surface = surface.name,
    entities = entities
  }
end

local function write_snapshot(snapshot)
  local json = game.table_to_json(snapshot)
  local path = "factorio_mod/snapshots/snapshot_" .. snapshot.tick .. ".json"

  game.write_file(path, json, false)

  local storage = ensure_storage()
  storage.last_snapshot_path = path
  storage.last_snapshot_tick = snapshot.tick

  return path
end

local function get_or_create_sandbox_surface()
  local surface = game.surfaces["planner-sandbox"]
  if surface then
    return surface
  end

  return game.create_surface("planner-sandbox")
end

local function parse_ghost_plan(json_text)
  if not json_text or json_text == "" then
    return nil, "Missing GhostPlan JSON"
  end

  local ok, payload = pcall(function()
    return game.json_to_table(json_text)
  end)
  if not ok or type(payload) ~= "table" then
    return nil, "Invalid GhostPlan JSON"
  end
  if type(payload.ghosts) ~= "table" then
    return nil, "GhostPlan must include ghosts array"
  end

  return payload, nil
end

local function apply_ghost_plan(payload)
  local surface = get_or_create_sandbox_surface()
  local force = game.forces["player"] or game.forces[1]

  local origin_x = 0
  local origin_y = 0
  local spacing = 2

  for index, ghost in ipairs(payload.ghosts) do
    if type(ghost) ~= "table" then
      error("Ghost entry must be an object")
    end

    local prototype = ghost.prototype or "assembling-machine-1"
    local tags = ghost.tags
    if type(tags) ~= "table" then
      error("Ghost tags must be an object")
    end

    local position = {
      x = origin_x + ((index - 1) * spacing),
      y = origin_y
    }

    surface.create_entity({
      name = "entity-ghost",
      inner_name = prototype,
      position = position,
      force = force,
      tags = tags
    })
  end
end

commands.add_command("snapshot", "Export deterministic factory snapshot JSON.", function(command)
  local surface = pick_surface()
  local snapshot = build_snapshot(surface)
  local path = write_snapshot(snapshot)

  if command.player_index then
    local player = game.get_player(command.player_index)
    if player then
      player.print("Snapshot written to script-output/" .. path)
    end
  else
    game.print("Snapshot written to script-output/" .. path)
  end
end)

commands.add_command("apply_ghost_plan", "Render GhostPlan JSON as ghosts in a sandbox surface.", function(command)
  local payload, err = parse_ghost_plan(command.parameter)
  if err then
    if command.player_index then
      local player = game.get_player(command.player_index)
      if player then
        player.print("GhostPlan error: " .. err)
      end
    else
      game.print("GhostPlan error: " .. err)
    end
    return
  end

  local ok, apply_err = pcall(function()
    apply_ghost_plan(payload)
  end)
  if not ok then
    local message = "GhostPlan error: " .. tostring(apply_err)
    if command.player_index then
      local player = game.get_player(command.player_index)
      if player then
        player.print(message)
      end
    else
      game.print(message)
    end
    return
  end

  if command.player_index then
    local player = game.get_player(command.player_index)
    if player then
      player.print("GhostPlan applied to planner-sandbox")
    end
  else
    game.print("GhostPlan applied to planner-sandbox")
  end
end)

script.on_init(function()
  ensure_storage()
end)

script.on_configuration_changed(function()
  ensure_storage()
end)
