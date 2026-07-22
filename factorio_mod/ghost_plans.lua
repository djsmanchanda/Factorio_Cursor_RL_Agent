-- Path: factorio_mod/ghost_plans.lua
-- Purpose: Parse, render, observe, and execute authorized ghost plans.



local shared = require("sandbox_shared")
local ensure_storage = shared.ensure_storage
local get_or_create_sandbox_surface = shared.get_or_create_sandbox_surface
local get_or_create_planner_force = shared.get_or_create_planner_force
local find_exact_entity = shared.find_exact_entity

local function parse_ghost_plan(json_text)
  if not json_text or json_text == "" then
    return nil, "Missing GhostPlan JSON"
  end

  local ok, payload = pcall(function()
    return helpers.json_to_table(json_text)
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
  local force = get_or_create_planner_force()

  local function zoned_position_from_tags(tags, zone_index)
    if tags.zone_origin_x == nil or tags.zone_origin_y == nil or tags.zone_stride_x == nil or tags.zone_stride_y == nil then
      error("Ghost tags must include zone_origin_x, zone_origin_y, zone_stride_x, zone_stride_y")
    end

    local origin_x = tonumber(tags.zone_origin_x)
    local origin_y = tonumber(tags.zone_origin_y)
    local stride_x = tonumber(tags.zone_stride_x)
    local stride_y = tonumber(tags.zone_stride_y)

    if origin_x == nil or origin_y == nil or stride_x == nil or stride_y == nil then
      error("Ghost zone values must be numeric")
    end
    if stride_x <= 0 or stride_y <= 0 then
      error("Ghost zone strides must be > 0")
    end

    local columns = 8
    local column = zone_index % columns
    local row = math.floor(zone_index / columns)

    return {
      x = origin_x + (column * stride_x),
      y = origin_y + (row * stride_y)
    }
  end

  local zone_counts = {}

  for _, ghost in ipairs(payload.ghosts) do
    if type(ghost) ~= "table" then
      error("Ghost entry must be an object")
    end

    local prototype = ghost.prototype or "assembling-machine-1"
    local tags = ghost.tags
    if type(tags) ~= "table" then
      error("Ghost tags must be an object")
    end

    local zone_key = tostring(tags.zone_block_id or tags.block or "") .. ":" .. tostring(tags.zone_origin_x) .. ":" .. tostring(tags.zone_origin_y)
    -- Planner-assigned cell index wins: it accounts for cells already
    -- occupied by earlier batches. The per-apply counter is only a fallback.
    local zone_index = tonumber(tags.zone_index)
    if zone_index == nil then
      zone_index = zone_counts[zone_key] or 0
    end
    local position = zoned_position_from_tags(tags, zone_index)
    zone_counts[zone_key] = (zone_counts[zone_key] or 0) + 1

    surface.create_entity({
      name = "entity-ghost",
      inner_name = prototype,
      position = position,
      force = force,
      tags = tags
    })
  end
end

local function export_ghost_observation()
  local surface = get_or_create_sandbox_surface()
  local force = get_or_create_planner_force()
  local ghosts = surface.find_entities_filtered({ name = "entity-ghost", force = force })

  local entries = {}
  for _, ghost in ipairs(ghosts) do
    local tags = ghost.tags
    if type(tags) ~= "table" then
      error("Ghost tags missing")
    end
    if not tags.block or not tags.phase or not tags.capacity_slice then
      error("Ghost tags must include block, phase, capacity_slice")
    end

    local prototype = ghost.ghost_name
    if not prototype then
      error("Ghost prototype missing")
    end

    table.insert(entries, {
      prototype = prototype,
      tags = {
        block = tostring(tags.block),
        phase = tostring(tags.phase),
        capacity_slice = tostring(tags.capacity_slice)
      }
    })
  end

  table.sort(entries, function(a, b)
    if a.tags.block ~= b.tags.block then
      return a.tags.block < b.tags.block
    end
    if a.tags.phase ~= b.tags.phase then
      return a.tags.phase < b.tags.phase
    end
    if a.tags.capacity_slice ~= b.tags.capacity_slice then
      return a.tags.capacity_slice < b.tags.capacity_slice
    end
    return a.prototype < b.prototype
  end)

  local payload = {
    tick = game.tick,
    surface = surface.name,
    ghosts = entries
  }

  local json = helpers.table_to_json(payload)
  -- Empty Lua tables serialize as {} but the schema requires an array.
  if #entries == 0 then
    json = json:gsub('"ghosts":{}', '"ghosts":[]')
  end
  local path = "factorio_mod/ghost_observations/ghost_observation_" .. game.tick .. ".json"
  helpers.write_file(path, json, false)

  local storage = ensure_storage()
  storage.last_ghost_observation_path = path
  storage.last_ghost_observation_tick = game.tick

  return path
end

local function parse_execution_payload(json_text)
  if not json_text or json_text == "" then
    return nil, "Missing execution payload JSON"
  end

  local ok, payload = pcall(function()
    return helpers.json_to_table(json_text)
  end)
  if not ok or type(payload) ~= "table" then
    return nil, "Invalid execution payload JSON"
  end

  if type(payload.authorization) ~= "table" then
    return nil, "Execution payload must include authorization"
  end
  if type(payload.ghost_plan) ~= "table" then
    return nil, "Execution payload must include ghost_plan"
  end

  return payload, nil
end

local function validate_execution_payload(payload)
  local authorization = payload.authorization
  local ghost_plan = payload.ghost_plan

  if type(authorization.approved_actions) ~= "table" then
    return nil, "Authorization must include approved_actions"
  end
  if type(ghost_plan.ghosts) ~= "table" then
    return nil, "GhostPlan must include ghosts array"
  end

  return { authorization = authorization, ghost_plan = ghost_plan }, nil
end

local function execute_ghost_plan(authorization, ghost_plan)
  local approved = {}
  for _, action in ipairs(authorization.approved_actions) do
    approved[action] = true
  end

  if not approved["project_more_ghosts"] then
    error("Authorization does not permit project_more_ghosts")
  end

  local scope_limits = authorization.scope_limits or {}
  local max_count = scope_limits.max_count
  local block_filter = {}
  if type(scope_limits.block_filter) == "table" then
    for _, block in ipairs(scope_limits.block_filter) do
      block_filter[block] = true
    end
  end

  local surface = get_or_create_sandbox_surface()
  local force = get_or_create_planner_force()
  local function zoned_position_from_tags(tags, zone_index)
    if tags.zone_origin_x == nil or tags.zone_origin_y == nil or tags.zone_stride_x == nil or tags.zone_stride_y == nil then
      error("Ghost tags must include zone_origin_x, zone_origin_y, zone_stride_x, zone_stride_y")
    end

    local origin_x = tonumber(tags.zone_origin_x)
    local origin_y = tonumber(tags.zone_origin_y)
    local stride_x = tonumber(tags.zone_stride_x)
    local stride_y = tonumber(tags.zone_stride_y)

    if origin_x == nil or origin_y == nil or stride_x == nil or stride_y == nil then
      error("Ghost zone values must be numeric")
    end
    if stride_x <= 0 or stride_y <= 0 then
      error("Ghost zone strides must be > 0")
    end

    local columns = 8
    local column = zone_index % columns
    local row = math.floor(zone_index / columns)

    return {
      x = origin_x + (column * stride_x),
      y = origin_y + (row * stride_y)
    }
  end

  local placed = 0
  local zone_counts = {}
  local ghosts = ghost_plan.ghosts
  for _, ghost in ipairs(ghosts) do
    if max_count and placed >= max_count then
      break
    end

    if type(ghost) ~= "table" then
      error("Ghost entry must be an object")
    end

    local tags = ghost.tags
    if type(tags) ~= "table" then
      error("Ghost tags must be an object")
    end
    if not tags.block then
      error("Ghost tags must include block")
    end

    if next(block_filter) ~= nil and not block_filter[tags.block] then
      goto continue
    end

    local prototype = ghost.prototype or "assembling-machine-1"
    local zone_key = tostring(tags.zone_block_id or tags.block or "") .. ":" .. tostring(tags.zone_origin_x) .. ":" .. tostring(tags.zone_origin_y)
    -- Planner-assigned cell index wins: it accounts for cells already
    -- occupied by earlier batches. The per-apply counter is only a fallback.
    local zone_index = tonumber(tags.zone_index)
    if zone_index == nil then
      zone_index = zone_counts[zone_key] or 0
    end
    local position = zoned_position_from_tags(tags, zone_index)
    zone_counts[zone_key] = (zone_counts[zone_key] or 0) + 1

    local existing = find_exact_entity(surface, force, "entity-ghost", position)
    if existing and existing.ghost_name ~= prototype then
      error("Different planner ghost already exists at requested exact position")
    end
    if not existing then
      surface.create_entity({
        name = "entity-ghost",
        inner_name = prototype,
        position = position,
        force = force,
        tags = tags
      })
    end

    placed = placed + 1
    ::continue::
  end

  return { placed = placed }
end


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

commands.add_command("export_ghost_observation", "Export GhostPlan observation JSON from planner-sandbox.", function(command)
  local ok, result = pcall(function()
    return export_ghost_observation()
  end)

  if not ok then
    local message = "Ghost observation error: " .. tostring(result)
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
      player.print("Ghost observation written to script-output/" .. result)
    end
  else
    game.print("Ghost observation written to script-output/" .. result)
  end
end)

commands.add_command("execute_ghost_plan", "Execute authorized ghost placement in planner-sandbox.", function(command)
  local payload, err = parse_execution_payload(command.parameter)
  if err then
    if command.player_index then
      local player = game.get_player(command.player_index)
      if player then
        player.print("Execution error: " .. err)
      end
    else
      game.print("Execution error: " .. err)
    end
    return
  end

  local validated, validation_err = validate_execution_payload(payload)
  if validation_err then
    if command.player_index then
      local player = game.get_player(command.player_index)
      if player then
        player.print("Execution error: " .. validation_err)
      end
    else
      game.print("Execution error: " .. validation_err)
    end
    return
  end

  local ok, result = pcall(function()
    return execute_ghost_plan(validated.authorization, validated.ghost_plan)
  end)

  if not ok then
    local message = "Execution error: " .. tostring(result)
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

  local report = {
    tick = game.tick,
    surface = get_or_create_sandbox_surface().name,
    actions = {
      {
        action = "project_more_ghosts",
        status = "success",
        count = result.placed
      }
    }
  }

  local json = helpers.table_to_json(report)
  local path = "factorio_mod/execution_reports/execution_report_" .. game.tick .. ".json"
  helpers.write_file(path, json, false)

  if command.player_index then
    local player = game.get_player(command.player_index)
    if player then
      player.print("Execution report written to script-output/" .. path)
    end
  else
    game.print("Execution report written to script-output/" .. path)
  end
end)
