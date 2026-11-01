-- Path: factorio_mod/rocket_launch_telemetry.lua
-- Purpose: Record actual rocket ascent-completion events for one explicit surface and force; observation only.
--
-- Factorio 2.1.24: on_rocket_launched fires when a rocket finishes its ascent.
-- Cargo-pod delivery is a separate event; this module records neither cargo
-- delivery nor victory, and grants no execution authority.
--
-- Event filenames combine the caller-supplied per-arm session ID with the tick
-- and save-persisted sequence. A reused or missing session ID is not globally
-- collision-proof: restoring an older save can rewind both tick and sequence.
-- Independent history requires a fresh session identity per arm (the bridge
-- supplies a UUID), not a timestamp or sequence alone.

local SCHEMA_VERSION = "1.0.0"
local SCOPE_REPORT_DIRECTORY = "factorio_mod/rocket_launch_observations/"
local EVENT_REPORT_DIRECTORY = "factorio_mod/rocket_launch_events/"
local EVENT_NAME = "on_rocket_launched"

local function observation_state()
  local state = storage.rocket_launch_observation
  if not state then
    state = {
      enabled = false,
      sequence = 0,
      recorded_launches = 0,
      scope_since_tick = 0
    }
    storage.rocket_launch_observation = state
  end
  return state
end

local function valid_filename_token(value)
  return type(value) == "string" and value ~= ""
    and #value <= 64 and value:match("^[%w_-]+$") ~= nil
end

local function resolve_session_id(payload)
  if valid_filename_token(payload.session_id) then
    return payload.session_id
  end
  if valid_filename_token(payload.request_id) then
    return payload.request_id
  end
  return nil
end

local function parse_payload(json_text)
  if not json_text or json_text == "" then
    return nil, "Missing rocket-launch payload JSON"
  end

  local ok, payload = pcall(function()
    return helpers.json_to_table(json_text)
  end)
  if not ok or type(payload) ~= "table" then
    return nil, "Invalid rocket-launch payload JSON"
  end
  if type(payload.surface) ~= "string" or payload.surface == "" then
    return nil, "Rocket-launch payload must include a non-empty surface"
  end
  if type(payload.force) ~= "string" or payload.force == "" then
    return nil, "Rocket-launch payload must include a non-empty force"
  end
  if payload.enabled ~= nil and type(payload.enabled) ~= "boolean" then
    return nil, "Rocket-launch payload enabled must be a boolean"
  end
  if payload.request_id ~= nil and not valid_filename_token(payload.request_id) then
    return nil, "Rocket-launch request_id must contain 1-64 filename-safe characters"
  end
  if payload.session_id ~= nil and not valid_filename_token(payload.session_id) then
    return nil, "Rocket-launch session_id must contain 1-64 filename-safe characters"
  end
  return payload, nil
end

local function identity_of(entity)
  if not entity then
    return nil
  end

  local ok, surface_name, force_name = pcall(function()
    if not entity.valid then
      return nil, nil
    end
    local surface = entity.surface
    local force = entity.force
    if not surface or not force then
      return nil, nil
    end
    return surface.name, force.name
  end)
  if not ok or type(surface_name) ~= "string" or type(force_name) ~= "string" then
    return nil
  end
  return surface_name, force_name
end

local function entity_reference(entity)
  if not entity then
    return nil
  end

  local ok, reference = pcall(function()
    if not entity.valid then
      return nil
    end
    local reference = {
      name = entity.name,
      position = { x = entity.position.x, y = entity.position.y }
    }
    if type(entity.unit_number) == "number" then
      reference.unit_number = entity.unit_number
    end
    return reference
  end)
  if not ok then
    return nil
  end
  return reference
end

local function record_launch(event)
  local state = storage.rocket_launch_observation
  if not state or not state.enabled or type(event.tick) ~= "number" then
    return
  end

  local rocket_surface, rocket_force = identity_of(event.rocket)
  local silo_surface, silo_force = identity_of(event.rocket_silo)
  if not rocket_surface and not silo_surface then
    return
  end
  if rocket_surface and (rocket_surface ~= state.surface or rocket_force ~= state.force) then
    return
  end
  if silo_surface and (silo_surface ~= state.surface or silo_force ~= state.force) then
    return
  end

  local surface_name = rocket_surface or silo_surface
  local force_name = rocket_force or silo_force
  local rocket = entity_reference(event.rocket)
  if not rocket then
    return
  end

  if not state.session_id then
    -- A save armed before session identity existed still records evidence, but
    -- marks it so no unique session history is implied.
    state.session_id = "unidentified"
  end

  state.sequence = (state.sequence or 0) + 1
  state.recorded_launches = (state.recorded_launches or 0) + 1

  local record = {
    schema_version = SCHEMA_VERSION,
    kind = "rocket_launch",
    event = EVENT_NAME,
    tick = event.tick,
    surface = surface_name,
    force = force_name,
    sequence = state.sequence,
    session_id = state.session_id,
    rocket = rocket
  }
  local rocket_silo = entity_reference(event.rocket_silo)
  if rocket_silo then
    record.rocket_silo = rocket_silo
  end

  helpers.write_file(
    EVENT_REPORT_DIRECTORY .. "rocket_launch_" .. state.session_id .. "_"
      .. tostring(record.tick) .. "_" .. tostring(state.sequence) .. ".json",
    helpers.table_to_json(record),
    false
  )
end

local function build_scope_report(payload, enabled)
  local surface = game.surfaces[payload.surface]
  if not surface then
    error("Unknown surface: " .. payload.surface)
  end
  local force = game.forces[payload.force]
  if not force then
    error("Unknown force: " .. payload.force)
  end

  local state = observation_state()
  local same_scope = state.surface == surface.name and state.force == force.name
  -- A gap while disabled breaks observation continuity even for the same scope.
  local fresh_start = (not same_scope) or (enabled and not state.enabled)
  local session_id = nil
  if fresh_start then
    if enabled then
      session_id = resolve_session_id(payload)
      if not session_id then
        error("Rocket-launch payload must include a filename-safe session_id when starting observation")
      end
      if state.session_id == session_id then
        error("Rocket-launch session_id " .. session_id
          .. " was already used; supply a fresh per-arm identity")
      end
    end
    state.surface = surface.name
    state.force = force.name
    state.recorded_launches = 0
    state.scope_since_tick = game.tick
    state.session_id = session_id
  end
  state.enabled = enabled

  local report = {
    schema_version = SCHEMA_VERSION,
    kind = "rocket_launch_scope",
    ok = true,
    tick = game.tick,
    surface = surface.name,
    force = force.name,
    enabled = enabled,
    scope_since_tick = state.scope_since_tick,
    recorded_launches = state.recorded_launches,
    session_started = fresh_start and enabled
  }
  if state.session_id then
    report.session_id = state.session_id
  end
  if valid_filename_token(payload.request_id) then
    report.request_id = payload.request_id
  end
  return report
end

local function build_scope_failure(payload, message)
  local report = {
    schema_version = SCHEMA_VERSION,
    kind = "rocket_launch_scope",
    ok = false,
    tick = game.tick,
    surface = payload and payload.surface or "",
    force = payload and payload.force or "",
    error = tostring(message)
  }
  if payload and valid_filename_token(payload.request_id) then
    report.request_id = payload.request_id
  end
  return report
end

local function scope_file_suffix(payload, sequence)
  if payload and valid_filename_token(payload.request_id) then
    return payload.request_id
  end
  return tostring(game.tick) .. "_" .. tostring(sequence)
end

commands.add_command(
  "rocket_launch_observation",
  "Arm, disarm, or report actual rocket ascent observation for one explicit surface and force. Parameter: JSON {surface,force,enabled?,request_id?,session_id?}.",
  function(command)
    local payload, parse_err = parse_payload(command.parameter)
    local report

    if parse_err then
      report = build_scope_failure(payload, parse_err)
    else
      local enabled = payload.enabled
      if enabled == nil then
        enabled = true
      end
      local ok, result = pcall(function()
        return build_scope_report(payload, enabled)
      end)
      if ok then
        report = result
      else
        report = build_scope_failure(payload, result)
      end
    end

    local scope_sequence = (storage.rocket_launch_scope_sequence or 0) + 1
    storage.rocket_launch_scope_sequence = scope_sequence
    local suffix = scope_file_suffix(payload, scope_sequence)
    helpers.write_file(
      SCOPE_REPORT_DIRECTORY .. "rocket_launch_scope_" .. suffix .. ".json",
      helpers.table_to_json(report),
      false
    )
  end
)

return {
  on_rocket_launched = function(event)
    if event then
      record_launch(event)
    end
  end
}
