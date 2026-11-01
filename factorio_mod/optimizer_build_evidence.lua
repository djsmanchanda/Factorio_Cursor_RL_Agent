-- Path: factorio_mod/optimizer_build_evidence.lua | Purpose: Record tagged robot-build facts without granting authority.

local M = {}
local MAX_RECORDS = 4096
local REPORT_DIRECTORY = "factorio_mod/optimizer_build_evidence/"

local function valid_text(value)
  return type(value) == "string" and #value >= 1 and #value <= 128
end

local function valid_hex(value, length)
  return type(value) == "string" and #value == length and value:match("^[0-9a-f]+$") ~= nil
end

local function valid_request(value)
  return type(value) == "string" and #value >= 1 and #value <= 64
    and value:match("^[%w_-]+$") ~= nil
end

function M.valid_tags(tags)
  if type(tags) ~= "table" then return false end
  local allowed = {optimizer_episode=true, optimizer_project=true, optimizer_transaction=true, optimizer_plan_hash=true}
  for key in pairs(tags) do if not allowed[key] then return false end end
  return valid_text(tags.optimizer_episode) and valid_text(tags.optimizer_project)
    and valid_hex(tags.optimizer_transaction, 32) and valid_hex(tags.optimizer_plan_hash, 64)
end

local function identity(entity)
  if not entity or not entity.valid or not entity.surface or not entity.force then return nil end
  local position, unit = entity.position, entity.unit_number
  if not valid_text(entity.name) or not valid_text(entity.surface.name) or not valid_text(entity.force.name)
    or type(position) ~= "table" or type(position.x) ~= "number" or type(position.y) ~= "number"
    or position.x ~= position.x or position.y ~= position.y
    or math.abs(position.x) == math.huge or math.abs(position.y) == math.huge
    or (unit ~= nil and (type(unit) ~= "number" or unit <= 0 or unit % 1 ~= 0)) then return nil end
  return {entity=entity.name, surface=entity.surface.name, force=entity.force.name,
    position={x=position.x,y=position.y}, unit_number=unit}
end

function M.capture(event)
  if type(event) ~= "table" or not event.robot or not event.robot.valid
    or type(event.tick) ~= "number" or event.tick < 0 or event.tick % 1 ~= 0
    or not M.valid_tags(event.tags) then return nil end
  local result = identity(event.entity)
  if not result then return nil end
  result.tick = event.tick
  result.tags = {optimizer_episode=event.tags.optimizer_episode, optimizer_project=event.tags.optimizer_project,
    optimizer_transaction=event.tags.optimizer_transaction, optimizer_plan_hash=event.tags.optimizer_plan_hash}
  return result
end

function M.record(captured, entity)
  if type(captured) ~= "table" or not M.valid_tags(captured.tags) then return end
  local final = identity(entity)
  if not final or final.entity ~= captured.entity or final.surface ~= captured.surface
    or final.force ~= captured.force or final.position.x ~= captured.position.x
    or final.position.y ~= captured.position.y then return end
  storage.optimizer_build_evidence = storage.optimizer_build_evidence or {records={}, overflow=false}
  local state = storage.optimizer_build_evidence
  if #state.records >= MAX_RECORDS then state.overflow = true; return end
  final.tick = captured.tick
  final.tags = captured.tags
  state.records[#state.records + 1] = final
end

-- A build event is historical. Look up its final unit again at report time;
-- missing methods or unreadable properties remain unknown, never "absent".
local function current_entity(record)
  -- A position can later hold a replacement: it cannot establish continuity.
  if record.unit_number == nil then return {state="identity_unavailable"} end
  local ok, entity = pcall(function() return game.get_entity_by_unit_number(record.unit_number) end)
  if not ok or entity == nil then
    -- Some prototypes have no get-by-unit-number flag. Probe only this
    -- recorded position/name/force, and require the original unit number.
    local search_ok, candidates = pcall(function()
      return game.surfaces[record.surface].find_entities_filtered({position=record.position,
        name=record.entity, force=record.force, limit=64})
    end)
    if not search_ok or type(candidates) ~= "table" then return {state="unavailable"} end
    entity = nil
    for _, candidate in ipairs(candidates) do
      local candidate_ok, current = pcall(function() return identity(candidate) end)
      if candidate_ok and current and current.unit_number == record.unit_number
        and current.entity == record.entity and current.surface == record.surface and current.force == record.force
        and current.position.x == record.position.x and current.position.y == record.position.y then
        entity = candidate
        break
      end
    end
    if entity == nil then return {state="not_found"} end
  end
  local read_ok, current = pcall(function() return identity(entity) end)
  if not read_ok or not current then return {state="unavailable"} end
  if current.unit_number ~= record.unit_number or current.entity ~= record.entity
    or current.surface ~= record.surface or current.force ~= record.force
    or current.position.x ~= record.position.x or current.position.y ~= record.position.y then
    return {state="mismatch"}
  end
  local result = {state="present", direction={state="unavailable"},
    quality={state="unavailable"}, recipe={state="unavailable"}}
  local direction_ok, direction = pcall(function() return entity.direction end)
  if direction_ok and type(direction) == "number" and direction >= 0 and direction <= 15
    and direction % 1 == 0 then result.direction = {state="available", value=direction} end
  local quality_ok, quality = pcall(function() return entity.quality.name end)
  if quality_ok and valid_text(quality) then result.quality = {state="available", value=quality} end
  local recipe_ok, recipe = pcall(function() return entity.get_recipe() end)
  if recipe_ok then
    if recipe == nil then
      result.recipe = {state="unset"}
    else
      local name_ok, name = pcall(function() return recipe.name end)
      if name_ok and valid_text(name) then result.recipe = {state="available", value=name} end
    end
  end
  return result
end

local function report_command(command, include_unidentified)
  local ok, payload = pcall(function() return helpers.json_to_table(command.parameter or "") end)
  if not ok or type(payload) ~= "table" or not valid_request(payload.request_id) then return end
  local report = {version=include_unidentified and "optimizer-build-evidence-v3" or "optimizer-build-evidence-v2",
    source="robot_built_entity_tags",
    request_id=payload.request_id, tick=game.tick, ok=false, ownership_claimed=false, dispatch_authorized=false}
  local allowed = {request_id=true, surface=true, force=true, tags=true}
  local valid = true
  for key in pairs(payload) do if not allowed[key] then valid = false end end
  valid = valid and valid_text(payload.surface) and valid_text(payload.force) and M.valid_tags(payload.tags)
    and game.surfaces[payload.surface] ~= nil and game.forces[payload.force] ~= nil
  local state = storage.optimizer_build_evidence or {records={},overflow=false}
  if valid and not state.overflow then
    report.ok = true
    report.surface, report.force, report.tags = payload.surface, payload.force, payload.tags
    report.entities = {}
    for _, record in ipairs(state.records) do
      local tags = record.tags
      if (include_unidentified or record.unit_number ~= nil)
        and record.surface == payload.surface and record.force == payload.force
        and tags.optimizer_episode == payload.tags.optimizer_episode
        and tags.optimizer_project == payload.tags.optimizer_project
        and tags.optimizer_transaction == payload.tags.optimizer_transaction
        and tags.optimizer_plan_hash == payload.tags.optimizer_plan_hash then
        report.entities[#report.entities+1] = {entity=record.entity, position=record.position,
          unit_number=record.unit_number, tick=record.tick, current=current_entity(record)}
      end
    end
  else
    report.error = state.overflow and "robot_build_record_capacity_exceeded" or "invalid_scope"
  end
  local json = helpers.table_to_json(report):gsub('"entities":{}', '"entities":[]')
  helpers.write_file(REPORT_DIRECTORY .. "optimizer_build_evidence_" .. payload.request_id .. ".json", json, false)
end

commands.add_command("optimizer_build_evidence", "Report identified tagged robot-build facts.",
  function(command) report_command(command, false) end)
commands.add_command("optimizer_build_evidence_v3", "Report tagged robot-build facts including unidentified events.",
  function(command) report_command(command, true) end)

return M
