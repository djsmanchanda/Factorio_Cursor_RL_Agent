-- Path: factorio_mod/runtime_identity_probe.lua
-- Purpose: Export a request-correlated, read-only snapshot of engine facts for a disposable-lane handshake.
--
-- This module reports only facts the Factorio runtime API actually exposes to
-- mods: script.active_mods, game.tick, and an explicitly requested existing
-- surface and force. Factorio does not expose a loaded-save identity or engine
-- version to Lua, so neither is reported. The command never mutates game state,
-- never grants authority, and never claims lane ownership.

local SCHEMA_VERSION = "1.0.0"
local REPORT_DIRECTORY = "factorio_mod/runtime_identity_probes/"
local KIND = "runtime_identity_probe"
local MAX_NONCE_LENGTH = 64

local function valid_nonce(value)
  return type(value) == "string" and value ~= ""
    and #value <= MAX_NONCE_LENGTH
    and value:match("^[%w_-]+$") ~= nil
end

local function parse_payload(json_text)
  if not json_text or json_text == "" then
    return nil, "Missing runtime-identity payload JSON"
  end

  local ok, payload = pcall(function()
    return helpers.json_to_table(json_text)
  end)
  if not ok or type(payload) ~= "table" then
    return nil, "Invalid runtime-identity payload JSON"
  end

  local allowed = { nonce = true, surface = true, force = true }
  for key, _ in pairs(payload) do
    if type(key) ~= "string" or not allowed[key] then
      return payload, "Runtime-identity payload has an unsupported field"
    end
  end
  if not valid_nonce(payload.nonce) then
    return payload, "Runtime-identity nonce must contain 1-64 filename-safe characters"
  end
  if type(payload.surface) ~= "string" or payload.surface == "" then
    return payload, "Runtime-identity payload must include a non-empty surface"
  end
  if type(payload.force) ~= "string" or payload.force == "" then
    return payload, "Runtime-identity payload must include a non-empty force"
  end
  return payload, nil
end

local function read_active_mods()
  local active_mods = script.active_mods
  if type(active_mods) ~= "table" then
    error("Active mod list is unavailable")
  end

  local facts = {}
  local count = 0
  for name, version in pairs(active_mods) do
    if type(name) ~= "string" or name == "" or type(version) ~= "string" or version == "" then
      error("Active mod list is malformed")
    end
    facts[name] = version
    count = count + 1
  end
  return facts, count
end

local function build_report(payload)
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

  local active_mods, active_mods_count = read_active_mods()
  return {
    schema_version = SCHEMA_VERSION,
    kind = KIND,
    ok = true,
    nonce = payload.nonce,
    tick = game.tick,
    surface = { name = surface.name, index = surface.index },
    force = { name = force.name, index = force.index },
    active_mods = active_mods,
    active_mods_count = active_mods_count,
    ownership_claimed = false,
    dispatch_authorized = false
  }
end

local function failure_report(payload, message)
  local report = {
    schema_version = SCHEMA_VERSION,
    kind = KIND,
    ok = false,
    nonce = payload and valid_nonce(payload.nonce) and payload.nonce or nil,
    tick = game.tick,
    surface = payload and type(payload.surface) == "string" and payload.surface or "",
    force = payload and type(payload.force) == "string" and payload.force or "",
    error = tostring(message),
    ownership_claimed = false,
    dispatch_authorized = false
  }
  return report
end

commands.add_command(
  "runtime_identity_probe",
  "Export read-only runtime identity facts. Parameter: JSON {nonce,surface,force}; nonce must be 1-64 filename-safe characters.",
  function(command)
    local payload, parse_err = parse_payload(command.parameter)
    if not payload or not valid_nonce(payload.nonce) then
      -- Without a safe request nonce there is no safe correlation filename.
      -- Refuse to write anything rather than invent one or reflect caller text.
      return
    end

    local report = failure_report(payload, parse_err)
    if not parse_err then
      local ok, result = pcall(function()
        return build_report(payload)
      end)
      if ok then
        report = result
      else
        report = failure_report(payload, result)
      end
    end

    helpers.write_file(
      REPORT_DIRECTORY .. "runtime_identity_" .. payload.nonce .. ".json",
      helpers.table_to_json(report),
      false
    )
  end
)
