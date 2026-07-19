-- Path: factorio_mod/control.lua
-- Purpose: Export deterministic factory snapshots as JSON for schema validation.

-- Factorio 2.0: the engine-provided persistent table is `storage` (was `global` in 1.1).
local function ensure_storage()
  return storage
end

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

local function get_or_create_sandbox_surface()
  local surface = game.surfaces["planner-sandbox"]
  if surface then
    return surface
  end

  surface = game.create_surface("planner-sandbox")
  -- Deterministic buildable canvas: default mapgen can produce alien terrain
  -- (ice, oil ocean) where bots cannot place entities.
  surface.generate_with_lab_tiles = true
  return surface
end

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
  local force = game.forces["player"] or game.forces[1]

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
  local force = game.forces["player"] or game.forces[1]
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

local function parse_upgrade_payload(json_text)
  if not json_text or json_text == "" then
    return nil, "Missing upgrade payload JSON"
  end

  local ok, payload = pcall(function()
    return helpers.json_to_table(json_text)
  end)
  if not ok or type(payload) ~= "table" then
    return nil, "Invalid upgrade payload JSON"
  end

  if type(payload.authorization) ~= "table" then
    return nil, "Upgrade payload must include authorization"
  end
  if type(payload.upgrade_plan) ~= "table" then
    return nil, "Upgrade payload must include upgrade_plan"
  end

  return payload, nil
end

local function validate_upgrade_payload(payload)
  local authorization = payload.authorization
  local upgrade_plan = payload.upgrade_plan

  if type(authorization.approved_actions) ~= "table" then
    return nil, "Authorization must include approved_actions"
  end
  if type(upgrade_plan.actions) ~= "table" then
    return nil, "UpgradePlan must include actions"
  end

  return { authorization = authorization, upgrade_plan = upgrade_plan }, nil
end

local function execute_upgrades(authorization, upgrade_plan)
  local approved = {}
  for _, action in ipairs(authorization.approved_actions) do
    approved[action] = true
  end

  if not approved["apply_upgrades"] then
    error("Authorization does not permit apply_upgrades")
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

  local results = {}
  local processed = 0

  for _, entry in ipairs(upgrade_plan.actions) do
    if max_count and processed >= max_count then
      break
    end

    if type(entry) ~= "table" then
      error("Upgrade action must be an object")
    end
    if entry.block and next(block_filter) ~= nil and not block_filter[entry.block] then
      goto continue
    end

    local position = entry.position
    if type(position) ~= "table" or position.x == nil or position.y == nil then
      error("Upgrade action must include position")
    end

    local target = surface.find_entity(entry.from_name, position)
    if not target then
      table.insert(results, { action = entry.action, status = "failed", reason = "target_missing" })
      goto continue
    end

    if entry.action == "assembler_tier_upgrade" then
      if target.name == entry.to_name then
        table.insert(results, { action = entry.action, status = "skipped", reason = "already_upgraded" })
      else
        surface.create_entity({
          name = "entity-ghost",
          inner_name = entry.to_name,
          position = position,
          force = force,
          tags = { block = entry.block or "", phase = "upgrade", capacity_slice = "n/a" }
        })
        table.insert(results, { action = entry.action, status = "success" })
      end
    elseif entry.action == "module_upgrade" then
      local module_name = entry.module_to
      local module_count = entry.module_count
      if not module_name or not module_count then
        error("Module upgrade requires module_to and module_count")
      end
      -- Factorio 2.0: module requests are BlueprintInsertPlan entries targeting
      -- explicit module-inventory slots, not a name→count map.
      local insert_plans = {}
      for slot = 1, module_count do
        table.insert(insert_plans, {
          id = { name = module_name },
          items = {
            in_inventory = {
              { inventory = defines.inventory.crafter_modules, stack = slot - 1, count = 1 }
            }
          }
        })
      end
      surface.create_entity({
        name = "item-request-proxy",
        position = target.position,
        force = force,
        target = target,
        modules = insert_plans
      })
      table.insert(results, { action = entry.action, status = "success" })
    else
      error("Unsupported upgrade action: " .. tostring(entry.action))
    end

    processed = processed + 1
    ::continue::
  end

  return results
end

commands.add_command("execute_upgrade_plan", "Execute authorized upgrades in planner-sandbox.", function(command)
  local payload, err = parse_upgrade_payload(command.parameter)
  if err then
    if command.player_index then
      local player = game.get_player(command.player_index)
      if player then
        player.print("Upgrade error: " .. err)
      end
    else
      game.print("Upgrade error: " .. err)
    end
    return
  end

  local validated, validation_err = validate_upgrade_payload(payload)
  if validation_err then
    if command.player_index then
      local player = game.get_player(command.player_index)
      if player then
        player.print("Upgrade error: " .. validation_err)
      end
    else
      game.print("Upgrade error: " .. validation_err)
    end
    return
  end

  local ok, results = pcall(function()
    return execute_upgrades(validated.authorization, validated.upgrade_plan)
  end)

  if not ok then
    local message = "Upgrade error: " .. tostring(results)
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
    actions = results
  }

  local json = helpers.table_to_json(report)
  local path = "factorio_mod/execution_reports/upgrade_report_" .. game.tick .. ".json"
  helpers.write_file(path, json, false)

  if command.player_index then
    local player = game.get_player(command.player_index)
    if player then
      player.print("Upgrade report written to script-output/" .. path)
    end
  else
    game.print("Upgrade report written to script-output/" .. path)
  end
end)

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

local function execute_deconstruction(authorization, plan)
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

  local surface = get_or_create_sandbox_surface()
  local force = game.forces["player"] or game.forces[1]
  require_construction_network(surface, force)

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
      return surface.find_entity(entry.name, position)
    end

    local candidates = surface.find_entities_filtered({ position = position })
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

commands.add_command("execute_deconstruction_plan", "Execute authorized deconstruction via bots in planner-sandbox.", function(command)
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

  local results = execute_deconstruction(validated.authorization, validated.deconstruction_plan)

  local report = {
    tick = game.tick,
    surface = get_or_create_sandbox_surface().name,
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

local function ensure_entity(surface, force, name, position)
  local existing = surface.find_entities_filtered({
    name = name,
    area = { { position[1] - 2, position[2] - 2 }, { position[1] + 2, position[2] + 2 } },
    limit = 1
  })
  if #existing > 0 then
    return existing[1], false
  end
  return surface.create_entity({ name = name, position = position, force = force }), true
end

local function ensure_scaffolding(payload)
  local surface = get_or_create_sandbox_surface()
  local force = game.forces["player"]
  local anchors = payload.anchors
  if type(anchors) ~= "table" or #anchors == 0 then
    error("Scaffolding payload must include anchors array")
  end

  local created = 0
  local bots_target = tonumber(payload.bots_per_roboport) or 30
  local inserted = {}

  -- Anchors are far enough apart to form separate logistic networks, so
  -- every anchor gets its own power, bots, chests, and materials.
  for _, anchor in ipairs(anchors) do
    local x = tonumber(anchor.x or anchor)
    if x == nil then
      error("Scaffolding anchor must be a number or an object with x")
    end

    local _, new_eei = ensure_entity(surface, force, "electric-energy-interface", { x - 4, -8 })
    local _, new_sub = ensure_entity(surface, force, "substation", { x, -7 })
    local roboport, new_rp = ensure_entity(surface, force, "roboport", { x, -3 })
    created = created + (new_eei and 1 or 0) + (new_sub and 1 or 0) + (new_rp and 1 or 0)

    local robot_inventory = roboport.get_inventory(defines.inventory.roboport_robot)
    local have_bots = robot_inventory.get_item_count("construction-robot")
    if have_bots < bots_target then
      roboport.insert({ name = "construction-robot", count = bots_target - have_bots })
    end

    local provider, new_chest = ensure_entity(surface, force, "passive-provider-chest", { x + 4, -8 })
    local _, new_storage = ensure_entity(surface, force, "storage-chest", { x + 6, -8 })
    created = created + (new_chest and 1 or 0) + (new_storage and 1 or 0)

    local materials = type(anchor) == "table" and anchor.materials or nil
    if materials then
      local network = surface.find_logistic_network_by_position({ x, -3 }, force)
      for item, count in pairs(materials) do
        local target = tonumber(count) or 0
        local have = network and network.get_item_count(item) or 0
        if target > have then
          local added = provider.insert({ name = item, count = target - have })
          inserted[item] = (inserted[item] or 0) + added
        end
      end
    end
  end

  return { created_entities = created, inserted = inserted }
end

commands.add_command("ensure_sandbox_scaffolding", "Idempotently provision power, roboports, bots, and materials on planner-sandbox.", function(command)
  local ok, payload = pcall(function()
    return helpers.json_to_table(command.parameter or "")
  end)
  if not ok or type(payload) ~= "table" then
    game.print("Scaffolding error: invalid JSON payload")
    return
  end

  local run_ok, result = pcall(function()
    return ensure_scaffolding(payload)
  end)

  local report = { tick = game.tick, ok = run_ok }
  if run_ok then
    report.created_entities = result.created_entities
    report.inserted = result.inserted
  else
    report.error = tostring(result)
  end

  local json = helpers.table_to_json(report)
  helpers.write_file("factorio_mod/scaffold_reports/scaffold_" .. game.tick .. ".json", json, false)
end)

local function execute_build_plan(authorization, build_plan)
  local approved = {}
  for _, action in ipairs(authorization.approved_actions or {}) do
    approved[action] = true
  end
  if not approved["project_more_ghosts"] then
    error("Authorization does not permit project_more_ghosts")
  end
  if type(build_plan.phases) ~= "table" then
    error("BuildPlan must include phases")
  end

  local surface = get_or_create_sandbox_surface()
  local force = game.forces["player"]
  local placed_ghosts = 0
  local placed_entities = 0
  local recipe_failures = 0

  for _, phase in ipairs(build_plan.phases) do
    for _, action in ipairs(phase.actions or {}) do
      local position = action.position
      if type(position) ~= "table" then
        error("BuildPlan action requires position")
      end
      local direction = nil
      if action.direction then
        direction = defines.direction[action.direction]
        if direction == nil then
          error("Unknown direction: " .. tostring(action.direction))
        end
      end

      if action.action_type == "place_ghost" then
        local ghost = surface.create_entity({
          name = "entity-ghost",
          inner_name = action.entity,
          position = { position.x, position.y },
          direction = direction,
          force = force
        })
        if ghost and action.recipe then
          local ok = pcall(function() ghost.set_recipe(action.recipe) end)
          if not ok then
            recipe_failures = recipe_failures + 1
          end
        end
        placed_ghosts = placed_ghosts + 1
      elseif action.action_type == "place_entity" then
        local existing = surface.find_entities_filtered({
          name = action.entity,
          area = { { position.x - 1, position.y - 1 }, { position.x + 1, position.y + 1 } },
          limit = 1
        })
        if #existing == 0 then
          local entity = surface.create_entity({
            name = action.entity,
            position = { position.x, position.y },
            direction = direction,
            force = force
          })
          if entity and action.infinity_filter then
            entity.set_infinity_container_filter(1, {
              name = action.infinity_filter, count = 1000, mode = "exactly", index = 1
            })
            entity.remove_unfiltered_items = true
          end
          placed_entities = placed_entities + 1
        end
      else
        error("Unsupported build plan action: " .. tostring(action.action_type))
      end
    end
  end

  return { placed_ghosts = placed_ghosts, placed_entities = placed_entities, recipe_failures = recipe_failures }
end

commands.add_command("build_layout_plan", "Execute an authorized BuildPlan with explicit positions on planner-sandbox.", function(command)
  local ok, payload = pcall(function()
    return helpers.json_to_table(command.parameter or "")
  end)
  if not ok or type(payload) ~= "table" or type(payload.authorization) ~= "table" or type(payload.build_plan) ~= "table" then
    game.print("BuildPlan error: payload must include authorization and build_plan")
    return
  end

  local run_ok, result = pcall(function()
    return execute_build_plan(payload.authorization, payload.build_plan)
  end)

  local report = { tick = game.tick, ok = run_ok }
  if run_ok then
    report.placed_ghosts = result.placed_ghosts
    report.placed_entities = result.placed_entities
    report.recipe_failures = result.recipe_failures
  else
    report.error = tostring(result)
  end

  local json = helpers.table_to_json(report)
  helpers.write_file("factorio_mod/layout_reports/layout_" .. game.tick .. ".json", json, false)
end)

script.on_event(defines.events.on_robot_built_entity, function(event)
  -- Factorio 2.0: event field renamed from created_entity to entity.
  if not event or not event.entity then
    return
  end

  local entity = event.entity
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
