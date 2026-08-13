-- Path: factorio_training_lab/plan_execution.lua
-- Purpose: Execute approved, chunk-uploaded physical training plans without RCON-size coupling.

local shared = require("training_shared")
local geometry = require("training_geometry")

local MAX_CHUNKS = 256
local MAX_CHUNK_BYTES = 1800

local function is_integer(value)
  return type(value) == "number" and value == math.floor(value)
end

local function command_payload(command)
  return shared.parse_command(command)
end

local function valid_upload_fields(payload)
  if type(payload.upload_id) ~= "string" or payload.upload_id == "" or #payload.upload_id > 128 then
    error("upload_id is invalid")
  end
  if not is_integer(payload.chunk_index) or not is_integer(payload.chunk_count)
      or payload.chunk_count < 1 or payload.chunk_count > MAX_CHUNKS
      or payload.chunk_index < 1 or payload.chunk_index > payload.chunk_count then
    error("upload chunk indexes are invalid")
  end
  if type(payload.chunk) ~= "string" or #payload.chunk > MAX_CHUNK_BYTES then
    error("upload chunk is invalid")
  end
end

local function upload(payload)
  valid_upload_fields(payload)
  shared.episode_for(payload.episode_id)
  local uploads = shared.ensure_storage().uploads
  local item = uploads[payload.upload_id]
  if not item then
    item = { episode_id = payload.episode_id, count = payload.chunk_count, chunks = {} }
    uploads[payload.upload_id] = item
  end
  if item.episode_id ~= payload.episode_id or item.count ~= payload.chunk_count then
    error("upload_id belongs to a different request")
  end
  local existing = item.chunks[payload.chunk_index]
  if existing and existing ~= payload.chunk then error("upload chunk conflicts with prior content") end
  item.chunks[payload.chunk_index] = payload.chunk
end

local function uploaded_package(payload)
  local uploads = shared.ensure_storage().uploads
  local item = uploads[payload.upload_id]
  if not item or item.episode_id ~= payload.episode_id then error("training upload is missing") end
  local chunks = {}
  for index = 1, item.count do
    if not item.chunks[index] then error("training upload is incomplete") end
    chunks[index] = item.chunks[index]
  end
  uploads[payload.upload_id] = nil
  local ok, package = pcall(function() return helpers.json_to_table(table.concat(chunks)) end)
  if not ok or type(package) ~= "table" then error("uploaded plan JSON is invalid") end
  return package
end

local function approved_entity_actions(package, episode)
  local authorization, plan = package.authorization, package.build_plan
  if type(authorization) ~= "table" or type(plan) ~= "table" then
    error("uploaded package must contain authorization and build_plan")
  end
  local permitted = false
  for _, action in ipairs(authorization.approved_actions or {}) do
    if action == "place_core_infrastructure" then permitted = true end
  end
  if not permitted then error("authorization does not permit physical placement") end
  if plan.surface ~= episode.surface_name or plan.force ~= episode.force_name then
    error("BuildPlan identity does not match its training episode")
  end
  if type(plan.phases) ~= "table" then error("BuildPlan must include phases") end
  return plan
end

local function exact_entity(surface, force, action)
  local position = action.position
  local entities = surface.find_entities_filtered({
    name = action.entity,
    area = { { position.x - 0.01, position.y - 0.01 }, { position.x + 0.01, position.y + 0.01 } },
    force = force
  })
  for _, entity in pairs(entities) do
    if entity.position.x == position.x and entity.position.y == position.y then return entity end
  end
  return nil
end

local function fixture_units(episode)
  local units = {}
  for _, fixture in pairs(episode.fixtures) do units[fixture.unit_number] = true end
  return units
end

local function committed_counts(surface, force, episode)
  local counts, fixtures = {}, fixture_units(episode)
  for _, entity in pairs(surface.find_entities_filtered({ force = force })) do
    if not fixtures[entity.unit_number] then
      local name = entity.type == "entity-ghost" and entity.ghost_name or entity.name
      counts[name] = (counts[name] or 0) + 1
    end
  end
  return counts
end

local function plan_actions(plan, episode, surface, force)
  local allowed, counts, result, new_positions = {}, committed_counts(surface, force, episode), {}, {}
  for _, entity in ipairs(episode.scenario.constraints.allowed_entities) do allowed[entity] = true end
  for _, phase in ipairs(plan.phases) do
    if type(phase.actions) ~= "table" then error("BuildPlan phase actions are invalid") end
    for _, action in ipairs(phase.actions) do
      if action.action_type ~= "place_entity" or not allowed[action.entity]
          or not geometry.fits(episode.scenario.constraints.allowed_build_area, action.entity, action.position) then
        error("BuildPlan contains a disallowed training action")
      end
      local key = action.entity .. ":" .. action.position.x .. ":" .. action.position.y
      if not exact_entity(surface, force, action) and not new_positions[key] then
        new_positions[key] = true
        counts[action.entity] = (counts[action.entity] or 0) + 1
      end
      if (counts[action.entity] or 0) > episode.scenario.construction_budget[action.entity] then
        error("BuildPlan exceeds the training construction budget")
      end
      table.insert(result, { phase = phase.name or "training", action = action })
    end
  end
  if #result == 0 then error("BuildPlan has no physical placement actions") end
  return result
end
local function point_distance(left, right)
  local dx, dy = left.x - right.x, left.y - right.y
  return math.sqrt(dx * dx + dy * dy)
end

local function connect_power_network(surface, force, episode, placed)
  if placed.type ~= "electric-pole" then return end
  local function connect(left, right)
    if left and right and left.valid and right.valid and left ~= right then
      pcall(function() left.connect_neighbour({wire = defines.wire_type.copper, target_entity = right}) end)
    end
  end
  for _, other in pairs(surface.find_entities_filtered({ force = force, type = "electric-pole" })) do
    if other ~= placed and point_distance(other.position, placed.position) <= 9.1 then connect(placed, other) end
  end
  for _, fixture in pairs(episode.fixtures) do
    if fixture.kind == "power_source" then
      local source = surface.find_entity(fixture.name, fixture.position)
      if source and point_distance(source.position, placed.position) <= 9.1 then connect(placed, source) end
    end
  end
end

local function place_action(surface, force, episode, item, execution)
  local action, direction = item.action, nil
  if action.direction then
    direction = defines.direction[action.direction]
    if direction == nil then error("BuildPlan direction is invalid") end
  end
  execution.attempted_placements = execution.attempted_placements + 1
  if exact_entity(surface, force, action) then
    execution.already_present_placements = execution.already_present_placements + 1
    return
  end
  local details = { name = action.entity, position = action.position, force = force, direction = direction }
  if action.underground_type then details.type = action.underground_type end
  if action.recipe then details.recipe = action.recipe end
  local entity = surface.create_entity(details)
  if entity and entity.valid then

    execution.succeeded_placements = execution.succeeded_placements + 1
    connect_power_network(surface, force, episode, entity)
  else
    execution.failed_placements = execution.failed_placements + 1
    table.insert(execution.placement_failures, {
      phase = item.phase, entity = action.entity, position = action.position,
      reason = "create_entity_returned_nil"
    })
  end
end

local function execute_plan(payload)
  if payload.confirmation_token ~= "EXECUTE_TRAINING_PLAN" then error("execution confirmation token is invalid") end
  local episode = shared.episode_for(payload.episode_id)
  local package = uploaded_package(payload)
  local plan = approved_entity_actions(package, episode)
  local surface, force = game.surfaces[episode.surface_name], game.forces[episode.force_name]
  if not surface or not force then error("episode surface or force is missing") end
  local actions = plan_actions(plan, episode, surface, force)
  local execution = {
    attempted_placements = 0, succeeded_placements = 0, already_present_placements = 0,
    failed_placements = 0, placement_failures = {}
  }
  for _, item in ipairs(actions) do place_action(surface, force, episode, item, execution) end
  local ok = execution.failed_placements == 0
  local result = {
    request_id = payload.request_id, episode_id = payload.episode_id, ok = ok,
    status = ok and "ready" or "failed", scenario_id = episode.scenario_id,
    surface = episode.surface_name, force = episode.force_name, execution = execution
  }
  if not ok then
    result.error = tostring(execution.failed_placements) .. " placement(s) failed"
    episode.status = "failed"
    episode.failure_kind = "execution"
    episode.failure_reason = result.error
  end
  return result
end

local function execute_command(command)
  local request_id, episode_id = "invalid", "invalid"
  local ok, result = pcall(function()
    local payload = command_payload(command)
    request_id, episode_id = payload.request_id, payload.episode_id
    return execute_plan(payload)
  end)
  if not ok then result = { request_id = request_id, episode_id = episode_id, ok = false, status = "failed", error = tostring(result) } end
  shared.write_report("execution", result)
end

local function upload_command(command)
  pcall(function() upload(command_payload(command)) end)
end

local function register_commands()
  commands.add_command("training_upload", "Upload one bounded training-plan chunk (RCON only).", upload_command)
  commands.add_command("training_execute", "Execute one approved uploaded training BuildPlan (RCON only).", execute_command)
end

return {
  MAX_CHUNK_BYTES = MAX_CHUNK_BYTES,
  plan_actions = plan_actions,
  execute_plan = execute_plan,
  register_commands = register_commands,
  upload = upload
}