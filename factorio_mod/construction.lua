-- Path: factorio_mod/construction.lua
-- Purpose: Validate and begin authorized construction-bot execution.



local shared = require("sandbox_shared")
local ensure_storage = shared.ensure_storage
local get_or_create_sandbox_surface = shared.get_or_create_sandbox_surface
local get_or_create_planner_force = shared.get_or_create_planner_force

local function parse_construction_payload(json_text)
  if not json_text or json_text == "" then
    return nil, "Missing construction payload JSON"
  end

  local ok, payload = pcall(function()
    return helpers.json_to_table(json_text)
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

local function require_construction_network(surface, force, position)
  local probe = position
  if not probe then
    local roboports = surface.find_entities_filtered({ name = "roboport", force = force })
    table.sort(roboports, function(a, b)
      if a.position.x ~= b.position.x then return a.position.x < b.position.x end
      return a.position.y < b.position.y
    end)
    probe = roboports[1] and roboports[1].position or nil
  end
  local network = probe and surface.find_logistic_network_by_position(probe, force) or nil
  if not network then
    error("No planner logistic network available on planner-sandbox")
  end
  if network.construction_robots == 0 or network.available_construction_robots == 0 then
    error("No available construction robots")
  end
  return network
end

local function collect_eligible_ghosts(surface, force, scope_limits)
  local ghosts = surface.find_entities_filtered({ name = "entity-ghost", force = force })
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
    -- Factorio 2.0: items_to_place_this is an array of ItemToPlace {name, count}.
    for _, item in pairs(items) do
      required[item.name] = (required[item.name] or 0) + item.count
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
  local force = get_or_create_planner_force()

  local eligible = collect_eligible_ghosts(surface, force, authorization.scope_limits)
  local probe = eligible[1] and eligible[1].position or nil
  local network = require_construction_network(surface, force, probe)

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

  local json = helpers.table_to_json(report)
  local path = "factorio_mod/construction_reports/construction_report_" .. game.tick .. ".json"
  helpers.write_file(path, json, false)

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
