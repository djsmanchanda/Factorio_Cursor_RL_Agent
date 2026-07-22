-- Path: factorio_mod/snapshot.lua
-- Purpose: Export deterministic planner and neutral-resource snapshots.



local shared = require("sandbox_shared")
local ensure_storage = shared.ensure_storage
local get_or_create_planner_force = shared.get_or_create_planner_force

local function pick_surface()
  if game.surfaces["nauvis"] then
    return game.surfaces["nauvis"]
  end
  return game.surfaces[1]
end

-- Factorio 2.0: get_recipe() hard-errors on non-crafting entities, so gate by type.
local CRAFTING_ENTITY_TYPES = {
  ["assembling-machine"] = true,
  ["furnace"] = true,
  ["rocket-silo"] = true
}

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

  if CRAFTING_ENTITY_TYPES[entity.type] then
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

local function build_snapshot(surface, force)
  local entities = {}
  local observed = force and surface.find_entities_filtered({ force = force }) or surface.find_entities()
  for _, entity in pairs(observed) do
    if entity.type ~= "character" then
      table.insert(entities, entity_to_snapshot(entity))
    end
  end
  if force then
    for _, resource in pairs(surface.find_entities_filtered({ type = "resource", force = "neutral" })) do
      table.insert(entities, entity_to_snapshot(resource))
    end
  end
  sort_entities(entities)

  return {
    tick = game.tick,
    surface = surface.name,
    entities = entities
  }
end

local function write_snapshot(snapshot)
  local json = helpers.table_to_json(snapshot)
  -- Empty Lua tables serialize as {} but the schema requires an array.
  if #snapshot.entities == 0 then
    json = json:gsub('"entities":{}', '"entities":[]')
  end
  local path = "factorio_mod/snapshots/snapshot_" .. snapshot.tick .. ".json"

  helpers.write_file(path, json, false)

  local storage = ensure_storage()
  storage.last_snapshot_path = path
  storage.last_snapshot_tick = snapshot.tick

  return path
end


commands.add_command("snapshot", "Export deterministic factory snapshot JSON. Optional parameter: surface name.", function(command)
  local surface
  if command.parameter and command.parameter ~= "" then
    surface = game.surfaces[command.parameter]
    if not surface then
      local message = "Snapshot error: unknown surface " .. command.parameter
      if command.player_index then
        local player = game.get_player(command.player_index)
        if player then player.print(message) end
      else
        game.print(message)
      end
      return
    end
  else
    surface = pick_surface()
  end
  local snapshot_force = nil
  if surface.name == "planner-sandbox" then
    snapshot_force = get_or_create_planner_force()
  end
  local snapshot = build_snapshot(surface, snapshot_force)
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
