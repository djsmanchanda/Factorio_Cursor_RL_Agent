-- Path: factorio_mod/logistic_inventory.lua
-- Purpose: Export read-only contents for every live logistic network on an explicit surface and force.

local SCHEMA_VERSION = "1.0.0"
local REPORT_DIRECTORY = "factorio_mod/logistic_inventory_reports/"

local function parse_payload(json_text)
  if not json_text or json_text == "" then
    return nil, "Missing logistic-inventory payload JSON"
  end
  local ok, payload = pcall(function() return helpers.json_to_table(json_text) end)
  if not ok or type(payload) ~= "table" then
    return nil, "Invalid logistic-inventory payload JSON"
  end
  if type(payload.surface) ~= "string" or payload.surface == "" then
    return nil, "Logistic-inventory payload must include a non-empty surface"
  end
  if type(payload.force) ~= "string" or payload.force == "" then
    return nil, "Logistic-inventory payload must include a non-empty force"
  end
  return payload, nil
end

local function add_count(target, name, quality, count)
  if type(name) ~= "string" or name == "" or type(count) ~= "number" or count <= 0 then
    return
  end
  -- Preserve quality when Factorio returns ItemWithQualityCount entries. Normal
  -- items retain the familiar prototype name shown by a roboport combinator.
  local key = name
  if type(quality) == "string" and quality ~= "" and quality ~= "normal" then
    key = name .. " [" .. quality .. "]"
  end
  target[key] = (target[key] or 0) + count
end

local function normalise_contents(contents)
  local result = {}
  for key, value in pairs(contents) do
    if type(value) == "number" then
      add_count(result, key, nil, value)
    elseif type(value) == "table" then
      add_count(result, value.name, value.quality, value.count)
    end
  end
  return result
end

local function add_contents(target, source)
  for name, count in pairs(source) do
    target[name] = (target[name] or 0) + count
  end
end

local function sort_networks(networks)
  table.sort(networks, function(left, right) return left.network_id < right.network_id end)
end

local function build_report(payload)
  local surface = game.surfaces[payload.surface]
  if not surface then error("Unknown surface: " .. payload.surface) end
  local force = game.forces[payload.force]
  if not force then error("Unknown force: " .. payload.force) end

  local seen = {}
  local networks = {}
  local total_items = {}
  local disconnected_roboports = 0
  for _, roboport in pairs(surface.find_entities_filtered({ type = "roboport", force = force })) do
    if roboport.valid and roboport.logistic_network then
      local network = roboport.logistic_network
      local id = tostring(network.network_id)
      local entry = seen[id]
      if not entry then
        entry = {
          network_id = id,
          roboports = 0,
          items = normalise_contents(network.get_contents())
        }
        seen[id] = entry
        table.insert(networks, entry)
        add_contents(total_items, entry.items)
      end
      entry.roboports = entry.roboports + 1
    elseif roboport.valid then
      disconnected_roboports = disconnected_roboports + 1
    end
  end
  sort_networks(networks)
  return {
    schema_version = SCHEMA_VERSION,
    ok = true,
    tick = game.tick,
    surface = surface.name,
    force = force.name,
    networks = networks,
    total_items = total_items,
    disconnected_roboports = disconnected_roboports
  }
end

local function encode_report(report)
  local json = helpers.table_to_json(report)
  if report.ok then json = json:gsub('"networks":{}', '"networks":[]') end
  return json
end

commands.add_command(
  "logistic_inventory",
  "Export read-only logistic-network contents. Parameter: JSON {surface,force}.",
  function(command)
    local payload, parse_err = parse_payload(command.parameter)
    local report = {
      schema_version = SCHEMA_VERSION,
      ok = false,
      tick = game.tick,
      surface = payload and payload.surface or "",
      force = payload and payload.force or ""
    }
    if parse_err then
      report.error = parse_err
    else
      local ok, result = pcall(function() return build_report(payload) end)
      if ok then report = result else report.error = tostring(result) end
    end
    helpers.write_file(
      REPORT_DIRECTORY .. "logistic_inventory_" .. game.tick .. ".json",
      encode_report(report), false
    )
  end
)
