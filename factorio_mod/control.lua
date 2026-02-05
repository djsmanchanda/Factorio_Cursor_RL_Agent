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

local function export_ghost_observation()
  local surface = get_or_create_sandbox_surface()
  local ghosts = surface.find_entities_filtered({ name = "entity-ghost" })

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

  local json = game.table_to_json(payload)
  local path = "factorio_mod/ghost_observations/ghost_observation_" .. game.tick .. ".json"
  game.write_file(path, json, false)

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
    return game.json_to_table(json_text)
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
  local force = game.forces["player"] or game.forces[1]
  local origin_x = 0
  local origin_y = 10
  local spacing = 2

  local placed = 0
  local ghosts = ghost_plan.ghosts
  for index, ghost in ipairs(ghosts) do
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
    local position = {
      x = origin_x + ((index - 1) * spacing),
      y = origin_y
    }

    local existing = surface.find_entities_filtered({ name = "entity-ghost", position = position, limit = 1 })
    if #existing == 0 then
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

local function parse_construction_payload(json_text)
  if not json_text or json_text == "" then
    return nil, "Missing construction payload JSON"
  end

  local ok, payload = pcall(function()
    return game.json_to_table(json_text)
  end)
  if not ok or type(payload) ~= "table" then
    return nil, "Invalid construction payload JSON"
  end

  if type(payload.authorization) ~= "table" then
    return nil, "Construction payload must include authorization"
  end
  if type(payload.execution_report) ~= "table" then
    return nil, "Construction payload must include execution_report"
  end

  return payload, nil
end

local function validate_construction_payload(payload)
  local authorization = payload.authorization
  local execution_report = payload.execution_report

  if type(authorization.approved_actions) ~= "table" then
    return nil, "Authorization must include approved_actions"
  end
  if type(execution_report.actions) ~= "table" then
    return nil, "ExecutionReport must include actions"
  end

  return { authorization = authorization, execution_report = execution_report }, nil
end

local function require_construction_network(surface, force)
  local network = surface.find_logistic_network_by_position({ x = 0, y = 0 }, force)
  if not network then
    error("No logistic network available on planner-sandbox")
  end
  if network.construction_robots == 0 or network.available_construction_robots == 0 then
    error("No available construction robots")
  end
  return network
end

local function collect_eligible_ghosts(surface, scope_limits)
  local ghosts = surface.find_entities_filtered({ name = "entity-ghost" })
  local eligible = {}

  local block_filter = {}
  if scope_limits and type(scope_limits.block_filter) == "table" then
    for _, block in ipairs(scope_limits.block_filter) do
      block_filter[block] = true
    end
  end

  for _, ghost in ipairs(ghosts) do
    local tags = ghost.tags
    if type(tags) ~= "table" then
      error("Ghost tags missing")
    end
    if not tags.block or not tags.phase or not tags.capacity_slice then
      error("Ghost tags must include block, phase, capacity_slice")
    end

    if next(block_filter) ~= nil and not block_filter[tags.block] then
      goto continue
    end

    table.insert(eligible, ghost)
    ::continue::
  end

  table.sort(eligible, function(a, b)
    if a.tags.block ~= b.tags.block then
      return a.tags.block < b.tags.block
    end
    if a.tags.phase ~= b.tags.phase then
      return a.tags.phase < b.tags.phase
    end
    if a.tags.capacity_slice ~= b.tags.capacity_slice then
      return a.tags.capacity_slice < b.tags.capacity_slice
    end
    return a.ghost_name < b.ghost_name
  end)

  return eligible
end

local function check_materials(network, ghosts)
  local required = {}

  for _, ghost in ipairs(ghosts) do
    local prototype = ghost.ghost_prototype
    if not prototype then
      error("Ghost prototype missing")
    end
    local items = prototype.items_to_place_this
    if not items then
      error("Ghost prototype missing items_to_place_this")
    end
    for item, count in pairs(items) do
      required[item] = (required[item] or 0) + count
    end
  end

  for item, count in pairs(required) do
    local available = network.get_item_count(item)
    if available < count then
      error("Missing materials for construction: " .. item)
    end
  end
end

local function execute_construction(authorization, execution_report)
  local approved = {}
  for _, action in ipairs(authorization.approved_actions) do
    approved[action] = true
  end

  if not approved["project_more_ghosts"] then
    error("Authorization does not permit project_more_ghosts")
  end

  local report_allows = false
  for _, action in ipairs(execution_report.actions) do
    if action.action == "project_more_ghosts" and action.status == "success" then
      report_allows = true
    end
  end
  if not report_allows then
    error("ExecutionReport does not confirm ghost placement")
  end

  local surface = get_or_create_sandbox_surface()
  local force = game.forces["player"] or game.forces[1]

  local network = require_construction_network(surface, force)
  local eligible = collect_eligible_ghosts(surface, authorization.scope_limits)

  local max_count = authorization.scope_limits and authorization.scope_limits.max_count
  if max_count and #eligible > max_count then
    error("Eligible ghost count exceeds scope limit")
  end

  check_materials(network, eligible)

  local storage = ensure_storage()
  storage.construction_session = {
    started = #eligible,
    completed = 0,
    tick = game.tick
  }

  return { started = #eligible, completed = 0 }
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

  local json = game.table_to_json(report)
  local path = "factorio_mod/execution_reports/execution_report_" .. game.tick .. ".json"
  game.write_file(path, json, false)

  if command.player_index then
    local player = game.get_player(command.player_index)
    if player then
      player.print("Execution report written to script-output/" .. path)
    end
  else
    game.print("Execution report written to script-output/" .. path)
  end
end)

commands.add_command("execute_construction", "Allow construction bots to build authorized ghosts in planner-sandbox.", function(command)
  local payload, err = parse_construction_payload(command.parameter)
  if err then
    if command.player_index then
      local player = game.get_player(command.player_index)
      if player then
        player.print("Construction error: " .. err)
      end
    else
      game.print("Construction error: " .. err)
    end
    return
  end

  local validated, validation_err = validate_construction_payload(payload)
  if validation_err then
    if command.player_index then
      local player = game.get_player(command.player_index)
      if player then
        player.print("Construction error: " .. validation_err)
      end
    else
      game.print("Construction error: " .. validation_err)
    end
    return
  end

  local ok, result = pcall(function()
    return execute_construction(validated.authorization, validated.execution_report)
  end)

  local report = {
    tick = game.tick,
    surface = get_or_create_sandbox_surface().name,
    started = 0,
    completed = 0,
    blocked = false
  }

  if not ok then
    report.blocked = true
    report.blocked_reason = tostring(result)
  else
    report.started = result.started
    report.completed = result.completed
  end

  local json = game.table_to_json(report)
  local path = "factorio_mod/construction_reports/construction_report_" .. game.tick .. ".json"
  game.write_file(path, json, false)

  if report.blocked then
    local message = "Construction blocked: " .. report.blocked_reason
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
      player.print("Construction report written to script-output/" .. path)
    end
  else
    game.print("Construction report written to script-output/" .. path)
  end
end)

script.on_event(defines.events.on_robot_built_entity, function(event)
  if not event or not event.created_entity then
    return
  end

  local entity = event.created_entity
  if entity.surface and entity.surface.name == "planner-sandbox" then
    local storage = ensure_storage()
    if storage.construction_session then
      storage.construction_session.completed = storage.construction_session.completed + 1
    end
  end
end)

script.on_init(function()
  ensure_storage()
end)

script.on_configuration_changed(function()
  ensure_storage()
end)
