-- Path: factorio_mod/research_lab_ownership.lua | Purpose: Record scoped robot-built lab packet identities without granting execution authority.

local M = {}
local MAX_RECORDS = 4096
local REPORT_DIRECTORY = "factorio_mod/research_lab_ownership/"

local function valid_id(value)
  return type(value) == "string" and #value >= 1 and #value <= 64
    and value:match("^[%w_-]+$") ~= nil
end

local function valid_scope_id(value)
  return type(value) == "string" and #value >= 1 and #value <= 128
end

local function valid_transaction_id(value)
  return type(value) == "string" and #value == 32 and value:match("^[0-9a-f]+$") ~= nil
end

local function valid_coordinate(value)
  return type(value) == "number" and value == value and value ~= math.huge and value ~= -math.huge
end

local function valid_unit(value)
  return type(value) == "number" and value > 0 and value % 1 == 0
end

local function entity_identity(entity)
  if not entity or not entity.valid or not entity.surface or not entity.force then return nil end
  local position = entity.position
  if type(entity.name) ~= "string" or type(entity.surface.name) ~= "string"
    or type(entity.force.name) ~= "string" or type(position) ~= "table"
    or not valid_coordinate(position.x) or not valid_coordinate(position.y)
    or not valid_unit(entity.unit_number) then return nil end
  return {entity=entity.name, surface=entity.surface.name, force=entity.force.name,
    position={x=position.x, y=position.y}, unit_number=entity.unit_number}
end

function M.capture(event)
  if type(event) ~= "table" or type(event.tags) ~= "table" or not event.robot
    or not event.robot.valid or type(event.tick) ~= "number" or event.tick < 0
    or event.tick % 1 ~= 0 then return nil end
  local tags = event.tags
  if not valid_scope_id(tags.research_lab_episode) or not valid_scope_id(tags.research_lab_packet)
    or not valid_transaction_id(tags.research_lab_transaction) then return nil end
  local identity = entity_identity(event.entity)
  if not identity then return nil end
  identity.tick = event.tick
  identity.episode_id = tags.research_lab_episode
  identity.packet_id = tags.research_lab_packet
  identity.transaction_id = tags.research_lab_transaction
  return identity
end

function M.record(captured, entity)
  if type(captured) ~= "table" then return end
  local final = entity_identity(entity)
  if not final or final.entity ~= captured.entity or final.surface ~= captured.surface
    or final.force ~= captured.force or final.position.x ~= captured.position.x
    or final.position.y ~= captured.position.y then return end
  storage.research_lab_ownership = storage.research_lab_ownership or {records={}, overflow=false}
  local state = storage.research_lab_ownership
  if #state.records >= MAX_RECORDS then state.overflow = true; return end
  state.records[#state.records + 1] = {
    entity=final.entity, surface=final.surface, force=final.force, position=final.position,
    unit_number=final.unit_number, tick=captured.tick, episode_id=captured.episode_id,
    packet_id=captured.packet_id, transaction_id=captured.transaction_id,
  }
end

commands.add_command("research_lab_ownership", "Report recorded robot-built lab packet identities.", function(command)
  local ok, payload = pcall(function() return helpers.json_to_table(command.parameter or "") end)
  if not ok or type(payload) ~= "table" or not valid_id(payload.request_id) then return end
  local report = {version="research-lab-ownership-v1", source="robot_built_entity_tags",
    request_id=payload.request_id, tick=game.tick, ok=false,
    ownership_claimed=false, dispatch_authorized=false}
  local allowed = {request_id=true, episode_id=true, packet_id=true, transaction_id=true, surface=true, force=true}
  local valid = true
  for key in pairs(payload) do if not allowed[key] then valid = false end end
  valid = valid and valid_scope_id(payload.episode_id) and valid_scope_id(payload.packet_id)
    and valid_transaction_id(payload.transaction_id) and type(payload.surface) == "string"
    and payload.surface ~= "" and type(payload.force) == "string" and payload.force ~= ""
    and game.surfaces[payload.surface] ~= nil and game.forces[payload.force] ~= nil
  local state = storage.research_lab_ownership or {records={}, overflow=false}
  if valid and not state.overflow then
    report.ok = true
    report.surface, report.force = payload.surface, payload.force
    report.episode_id, report.packet_id, report.transaction_id = payload.episode_id, payload.packet_id, payload.transaction_id
    report.entities = {}
    for _, record in ipairs(state.records) do
      if record.surface == payload.surface and record.force == payload.force
        and record.episode_id == payload.episode_id and record.packet_id == payload.packet_id
        and record.transaction_id == payload.transaction_id then
        report.entities[#report.entities + 1] = {entity=record.entity, position=record.position,
          unit_number=record.unit_number, tick=record.tick,
          tags={research_lab_packet=record.packet_id, research_lab_transaction=record.transaction_id}}
      end
    end
  else
    report.error = state.overflow and "robot_build_record_capacity_exceeded" or "invalid_scope"
  end
  local json = helpers.table_to_json(report):gsub('"entities":{}', '"entities":[]')
  helpers.write_file(REPORT_DIRECTORY .. "research_lab_ownership_" .. payload.request_id .. ".json", json, false)
end)

return M
