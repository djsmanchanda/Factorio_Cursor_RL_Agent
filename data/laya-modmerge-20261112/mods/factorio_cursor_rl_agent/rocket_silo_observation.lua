-- Path: factorio_mod/rocket_silo_observation.lua
-- Purpose: Export a read-only, request-correlated observation of existing rocket silos for one explicit surface and force.
--
-- Reference: data/factorio-2.1.21/runtime-api-2.1.21.json
--   application = factorio, application_version = 2.1.21, api_version = 6.
-- Only attributes available in that reference are read:
--   LuaEntity.type, unit_number, position, status, rocket_silo_status,
--   rocket_parts, rocket, send_to_orbit_automatically, get_recipe();
--   LuaEntity.prototype.rocket_parts_required and rocket_parts_storage_cap.
-- A cargo pod is read only through the silo's valid rocket entity.
--
-- Provenance limits: mods cannot read the live engine build, the loaded save
-- identity, or a save's runtime API version, so the reference values above are
-- static provenance, not live verification. This module never launches a
-- rocket, never mutates an entity, and grants no execution authority. Its
-- reports live under factorio_mod/rocket_silo_observations/ and are separate
-- from rocket launch event and scope directories.

local SCHEMA_VERSION = "1.0.0"
local REPORT_DIRECTORY = "factorio_mod/rocket_silo_observations/"
local KIND = "rocket_silo_observation"
local RUNTIME_API_REFERENCE = "2.1.21"
local RUNTIME_API_VERSION = 6
local MAX_TOKEN_LENGTH = 64

local ROCKET_SILO_STATUS_NAMES = {}
for name, value in pairs(defines.rocket_silo_status) do
  ROCKET_SILO_STATUS_NAMES[value] = name
end

local ENTITY_STATUS_NAMES = {}
for name, value in pairs(defines.entity_status) do
  ENTITY_STATUS_NAMES[value] = name
end

local PROVENANCE = {
  observer = KIND,
  runtime_api_reference = RUNTIME_API_REFERENCE,
  runtime_api_version = RUNTIME_API_VERSION,
  live_engine_version_verified = false,
  limitations = {
    "Runtime API 2.1.21 is a static reference; mods cannot read or verify the live engine build, loaded save identity, or live API version.",
    "rocket and cargo_pod are reported only while the referenced entities exist and are valid; absent or invalid references are explicit.",
    "auto_launch maps to send_to_orbit_automatically and is only meaningful for launch products with an automated send_to_orbit_mode.",
    "recipe is the silo's current recipe when get_recipe() returns one; an absent recipe is not a failure.",
    "This report is observation evidence only and grants no launch, mutation, or execution authority."
  }
}

local function valid_token(value)
  return type(value) == "string" and value ~= ""
    and #value <= MAX_TOKEN_LENGTH
    and value:match("^[%w_-]+$") ~= nil
end

local function valid_integer(value)
  return type(value) == "number" and value >= 0 and value % 1 == 0
end

local function parse_payload(json_text)
  if not json_text or json_text == "" then
    return nil, "Missing rocket-silo payload JSON"
  end

  local ok, payload = pcall(function()
    return helpers.json_to_table(json_text)
  end)
  if not ok or type(payload) ~= "table" then
    return nil, "Invalid rocket-silo payload JSON"
  end

  local allowed = {
    request_id = true,
    surface = true,
    force = true,
    unit_number = true
  }
  for key, _ in pairs(payload) do
    if type(key) ~= "string" or not allowed[key] then
      return payload, "Rocket-silo payload has an unsupported field"
    end
  end
  if not valid_token(payload.request_id) then
    return payload, "Rocket-silo request_id must contain 1-64 filename-safe characters"
  end
  if type(payload.surface) ~= "string" or payload.surface == "" then
    return payload, "Rocket-silo payload must include a non-empty surface"
  end
  if type(payload.force) ~= "string" or payload.force == "" then
    return payload, "Rocket-silo payload must include a non-empty force"
  end
  if payload.unit_number ~= nil and not valid_integer(payload.unit_number) then
    return payload, "Rocket-silo unit_number must be a non-negative integer when supplied"
  end
  return payload, nil
end

local function available(value)
  return { state = "available", value = value }
end

local function absent(reason)
  local fact = { state = "absent" }
  if type(reason) == "string" and reason ~= "" then
    fact.reason = reason
  end
  return fact
end

local function unavailable(reason)
  return { state = "unavailable", reason = tostring(reason) }
end

local function uint_fact(value, label)
  if value == nil then
    return absent(label .. " is not set")
  end
  if not valid_integer(value) then
    return unavailable(label .. " returned an invalid value")
  end
  return available(value)
end

local function read_uint(getter, label)
  local ok, value = pcall(getter)
  if not ok then
    return unavailable(label .. " could not be read: " .. tostring(value))
  end
  return uint_fact(value, label)
end

local function read_position(entity)
  local ok, position = pcall(function()
    return entity.position
  end)
  if not ok then
    return unavailable("position could not be read: " .. tostring(position))
  end
  if position == nil then
    return absent("position is not set")
  end
  if type(position.x) ~= "number" or type(position.y) ~= "number" then
    return unavailable("position returned invalid coordinates")
  end
  return available({ x = position.x, y = position.y })
end

local function read_status(entity, attribute, names, label)
  local ok, value = pcall(function()
    return entity[attribute]
  end)
  if not ok then
    return unavailable(label .. " could not be read: " .. tostring(value))
  end
  if value == nil then
    return absent(label .. " is not set")
  end
  if type(value) ~= "number" then
    return unavailable(label .. " returned a non-numeric status")
  end
  local name = names[value]
  if not name then
    return unavailable(label .. " returned an unknown value: " .. tostring(value))
  end
  return available(name)
end

local function read_recipe(entity)
  local ok, recipe = pcall(function()
    return entity.get_recipe()
  end)
  if not ok then
    return unavailable("get_recipe failed: " .. tostring(recipe))
  end
  if recipe == nil then
    return absent("No recipe is active")
  end
  local name_ok, name = pcall(function()
    return recipe.name
  end)
  if not name_ok or type(name) ~= "string" or name == "" then
    return unavailable("recipe name is unavailable")
  end
  return available(name)
end

local function read_auto_launch(entity)
  local ok, value = pcall(function()
    return entity.send_to_orbit_automatically
  end)
  if not ok then
    return unavailable("send_to_orbit_automatically could not be read: " .. tostring(value))
  end
  if value == nil then
    return absent("send_to_orbit_automatically is not set")
  end
  if type(value) ~= "boolean" then
    return unavailable("send_to_orbit_automatically returned a non-boolean value")
  end
  return available(value)
end

local function reference_fact(entity, label)
  if entity == nil then
    return absent(label .. " is not attached")
  end

  local valid_ok, valid = pcall(function()
    return entity.valid
  end)
  if not valid_ok then
    return unavailable(label .. " validity could not be read: " .. tostring(valid))
  end
  if valid ~= true then
    return unavailable(label .. " references an invalid entity")
  end

  local ok, reference = pcall(function()
    local name = entity.name
    local position = entity.position
    if type(name) ~= "string" or name == "" then
      error("missing name")
    end
    if position == nil
      or type(position.x) ~= "number"
      or type(position.y) ~= "number" then
      error("missing position")
    end
    local result = {
      name = name,
      position = { x = position.x, y = position.y }
    }
    local unit_number = entity.unit_number
    if valid_integer(unit_number) then
      result.unit_number = unit_number
    end
    return result
  end)
  if not ok then
    return unavailable(label .. " reference is malformed: " .. tostring(reference))
  end
  return available(reference)
end

local function read_reference(getter, label)
  local ok, entity = pcall(getter)
  if not ok then
    return unavailable(label .. " could not be read: " .. tostring(entity))
  end
  return reference_fact(entity, label)
end

local function read_rocket_and_pod(entity)
  local ok, rocket_entity = pcall(function()
    return entity.rocket
  end)
  if not ok then
    return unavailable("rocket could not be read: " .. tostring(rocket_entity)),
      absent("No valid rocket entity is attached")
  end

  local rocket = reference_fact(rocket_entity, "rocket")
  if rocket.state ~= "available" then
    return rocket, absent("No valid rocket entity is attached")
  end

  local pod = read_reference(function()
    return rocket_entity.attached_cargo_pod
  end, "cargo_pod")
  return rocket, pod
end

local function observe_silo(entity)
  local unit_number = read_uint(function()
    return entity.unit_number
  end, "unit_number")
  local name_ok, name = pcall(function() return entity.name end)
  local prototype_name = name_ok and type(name) == "string" and name ~= ""
    and available(name) or unavailable("silo prototype name is unavailable")
  local rocket, cargo_pod = read_rocket_and_pod(entity)
  return {
    prototype_name = prototype_name,
    unit_number = unit_number,
    position = read_position(entity),
    status = {
      rocket_silo = read_status(
        entity, "rocket_silo_status", ROCKET_SILO_STATUS_NAMES, "rocket_silo_status"),
      entity = read_status(entity, "status", ENTITY_STATUS_NAMES, "status")
    },
    parts = {
      actual = read_uint(function()
        return entity.rocket_parts
      end, "rocket_parts"),
      required = read_uint(function()
        return entity.prototype.rocket_parts_required
      end, "rocket_parts_required"),
      storage_cap = read_uint(function()
        return entity.prototype.rocket_parts_storage_cap
      end, "rocket_parts_storage_cap")
    },
    recipe = read_recipe(entity),
    auto_launch = {
      source = "send_to_orbit_automatically",
      fact = read_auto_launch(entity)
    },
    rocket = rocket,
    cargo_pod = cargo_pod
  }
end

local function sort_rank(silo)
  if silo.unit_number.state == "available" then
    return 0, silo.unit_number.value, 0
  end
  if silo.position.state == "available" then
    return 1, silo.position.value.x, silo.position.value.y
  end
  return 2, 0, 0
end

local function observe_scope(payload, surface, force)
  local ok, entities = pcall(function()
    return surface.find_entities_filtered({ type = "rocket-silo", force = force.name })
  end)
  if not ok then
    error("Rocket-silo lookup failed: " .. tostring(entities))
  end
  if type(entities) ~= "table" then
    error("Rocket-silo lookup returned no entity list")
  end

  local silos = {}
  for _, entity in pairs(entities) do
    local include = true
    if payload.unit_number ~= nil then
      local unit_ok, unit_number = pcall(function()
        return entity.unit_number
      end)
      include = unit_ok and unit_number == payload.unit_number
    end
    if include then
      local observe_ok, observed = pcall(function()
        return observe_silo(entity)
      end)
      if not observe_ok then
        error("Rocket-silo observation failed: " .. tostring(observed))
      end
      silos[#silos + 1] = observed
    end
  end

  table.sort(silos, function(left, right)
    local left_rank, left_x, left_y = sort_rank(left)
    local right_rank, right_x, right_y = sort_rank(right)
    if left_rank ~= right_rank then
      return left_rank < right_rank
    end
    if left_x ~= right_x then
      return left_x < right_x
    end
    return left_y < right_y
  end)

  local requested = {}
  if payload.unit_number ~= nil then
    requested.unit_number = available(payload.unit_number)
  else
    requested.unit_number = absent("No unit_number filter was requested")
  end

  return {
    schema_version = SCHEMA_VERSION,
    kind = KIND,
    ok = true,
    request_id = payload.request_id,
    tick = game.tick,
    surface = surface.name,
    force = force.name,
    requested = requested,
    silo_count = #silos,
    silos = silos,
    provenance = PROVENANCE
  }
end

local function build_success(payload)
  if type(game.tick) ~= "number" then
    error("Runtime tick is unavailable")
  end
  local surface = game.surfaces[payload.surface]
  if not surface or surface.valid == false then
    error("Unknown surface: " .. payload.surface)
  end
  local force = game.forces[payload.force]
  if not force or force.valid == false then
    error("Unknown force: " .. payload.force)
  end
  if type(surface.name) ~= "string" or surface.name == "" then
    error("Surface name is unavailable")
  end
  if type(force.name) ~= "string" or force.name == "" then
    error("Force name is unavailable")
  end
  return observe_scope(payload, surface, force)
end

local function failure_report(payload, message)
  return {
    schema_version = SCHEMA_VERSION,
    kind = KIND,
    ok = false,
    request_id = payload and valid_token(payload.request_id) and payload.request_id or nil,
    tick = game.tick,
    surface = payload and type(payload.surface) == "string" and payload.surface or "",
    force = payload and type(payload.force) == "string" and payload.force or "",
    error = tostring(message)
  }
end

local function encode_report(report)
  local json = helpers.table_to_json(report)
  if report.ok then
    -- An empty Lua table serialises to `{}` (object), not `[]` (array). The
    -- success contract always requires silos to be an array, including zero.
    json = json:gsub('"silos":{}', '"silos":[]')
  end
  return json
end

commands.add_command(
  "rocket_silo_observation",
  "Observe existing rocket silos read-only for one explicit surface and force. Parameter: JSON {request_id,surface,force,unit_number?}.",
  function(command)
    local payload, parse_err = parse_payload(command.parameter)
    if type(payload) ~= "table" or not valid_token(payload.request_id) then
      -- Without a safe request identity there is no safe correlated report
      -- filename. Refuse to write rather than invent one or reflect caller text.
      return
    end

    local report
    if parse_err then
      report = failure_report(payload, parse_err)
    else
      local ok, result = pcall(function()
        return build_success(payload)
      end)
      if ok then
        report = result
      else
        report = failure_report(payload, result)
      end
    end

    helpers.write_file(
      REPORT_DIRECTORY .. "rocket_silo_observation_" .. payload.request_id .. ".json",
      encode_report(report),
      false
    )
  end
)
