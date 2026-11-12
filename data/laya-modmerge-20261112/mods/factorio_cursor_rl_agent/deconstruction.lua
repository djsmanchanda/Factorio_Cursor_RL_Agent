-- Path: factorio_mod/deconstruction.lua
-- Purpose: Validate and execute authorized deterministic deconstruction plans.



local shared = require("sandbox_shared")
local get_or_create_sandbox_surface = shared.get_or_create_sandbox_surface
local get_or_create_planner_force = shared.get_or_create_planner_force
local find_exact_entity = shared.find_exact_entity

local function parse_deconstruction_payload(json_text)
  if not json_text or json_text == "" then
    return nil, "Missing deconstruction payload JSON"
  end

  local ok, payload = pcall(function()
    return helpers.json_to_table(json_text)
  end)
  if not ok or type(payload) ~= "table" then
    return nil, "Invalid deconstruction payload JSON"
  end

  if type(payload.authorization) ~= "table" then
    return nil, "Deconstruction payload must include authorization"
  end
  if type(payload.deconstruction_plan) ~= "table" then
    return nil, "Deconstruction payload must include deconstruction_plan"
  end

  return payload, nil
end

local function validate_deconstruction_payload(payload)
  local authorization = payload.authorization
  local plan = payload.deconstruction_plan

  if type(authorization.approved_actions) ~= "table" then
    return nil, "Authorization must include approved_actions"
  end
  if type(plan.actions) ~= "table" then
    return nil, "DeconstructionPlan must include actions"
  end

  return { authorization = authorization, deconstruction_plan = plan }, nil
end

local function execute_deconstruction(authorization, plan, surface, force)
  local approved = {}
  for _, action in ipairs(authorization.approved_actions) do
    approved[action] = true
  end

  if not approved["apply_deconstruction"] then
    error("Authorization does not permit apply_deconstruction")
  end

  local scope_limits = authorization.scope_limits or {}
  local max_count = scope_limits.max_count
  local block_filter = {}
  if type(scope_limits.block_filter) == "table" then
    for _, block in ipairs(scope_limits.block_filter) do
      block_filter[block] = true
    end
  end

  local results = {}
  local processed = 0
  local function compare_entities_for_deconstruction(a, b)
    if a.name ~= b.name then
      return a.name < b.name
    end
    if a.type ~= b.type then
      return a.type < b.type
    end
    if a.position.x ~= b.position.x then
      return a.position.x < b.position.x
    end
    if a.position.y ~= b.position.y then
      return a.position.y < b.position.y
    end
    local a_force = (a.force and a.force.name) or ""
    local b_force = (b.force and b.force.name) or ""
    if a_force ~= b_force then
      return a_force < b_force
    end
    local a_unit = a.unit_number or -1
    local b_unit = b.unit_number or -1
    return a_unit < b_unit
  end

  local function select_target(entry, position)
    if entry.name ~= nil then
      return find_exact_entity(surface, force, entry.name, position)
    end

    local candidates = surface.find_entities_filtered({ position = position, force = force })
    if #candidates == 0 then
      return nil
    end

    local exact = {}
    for _, candidate in ipairs(candidates) do
      if candidate.position.x == position.x and candidate.position.y == position.y then
        table.insert(exact, candidate)
      end
    end

    if #exact > 0 then
      table.sort(exact, compare_entities_for_deconstruction)
      return exact[1]
    end

    table.sort(candidates, compare_entities_for_deconstruction)
    return candidates[1]
  end

  for _, entry in ipairs(plan.actions) do
    if max_count and processed >= max_count then
      break
    end

    if type(entry) ~= "table" then
      error("Deconstruction action must be an object")
    end

    if entry.block and next(block_filter) ~= nil and not block_filter[entry.block] then
      goto continue
    end

    local position = entry.position
    if type(position) ~= "table" or position.x == nil or position.y == nil then
      error("Deconstruction action must include position")
    end

    local target = select_target(entry, position)
    if not target then
      table.insert(results, { action = entry.action, status = "failed", reason = "entity_not_found" })
      goto continue
    end

    target.order_deconstruction(force)
    table.insert(results, { action = entry.action, status = "success" })
    processed = processed + 1
    ::continue::
  end

  return results
end

commands.add_command("execute_deconstruction_plan", "Execute authorized deterministic deconstruction via construction bots.", function(command)
  local payload, err = parse_deconstruction_payload(command.parameter)
  if err then
    if command.player_index then
      local player = game.get_player(command.player_index)
      if player then
        player.print("Deconstruction error: " .. err)
      end
    else
      game.print("Deconstruction error: " .. err)
    end
    return
  end

  local validated, validation_err = validate_deconstruction_payload(payload)
  if validation_err then
    if command.player_index then
      local player = game.get_player(command.player_index)
      if player then
        player.print("Deconstruction error: " .. validation_err)
      end
    else
      game.print("Deconstruction error: " .. validation_err)
    end
    return
  end

  local surface = payload.surface and game.surfaces[payload.surface] or nil
  local force = payload.force and game.forces[payload.force] or nil
  if payload.surface and not surface then
    error("Unknown deconstruction surface: " .. tostring(payload.surface))
  end
  if payload.force and not force then
    error("Unknown deconstruction force: " .. tostring(payload.force))
  end
  surface = surface or get_or_create_sandbox_surface()
  force = force or get_or_create_planner_force()
  local results = execute_deconstruction(
    validated.authorization, validated.deconstruction_plan, surface, force
  )

  local report = {
    tick = game.tick,
    surface = surface.name,
    actions = results
  }

  local json = helpers.table_to_json(report)
  local path = "factorio_mod/execution_reports/deconstruction_report_" .. game.tick .. ".json"
  helpers.write_file(path, json, false)

  if command.player_index then
    local player = game.get_player(command.player_index)
    if player then
      player.print("Deconstruction report written to script-output/" .. path)
    end
  else
    game.print("Deconstruction report written to script-output/" .. path)
  end
end)
