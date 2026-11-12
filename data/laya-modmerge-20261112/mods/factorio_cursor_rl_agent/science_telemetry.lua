-- Path: factorio_mod/science_telemetry.lua
-- Purpose: Export a read-only, lab-level science status report for one force and surface.

local SCHEMA_VERSION = "1.0.0"
local REPORT_DIRECTORY = "factorio_mod/science_reports/"
local NULL_CURRENT_SENTINEL = "__science_status_null__"

local STATUS_NAMES = {}
for name, value in pairs(defines.entity_status) do
  STATUS_NAMES[value] = name
end

local function parse_payload(json_text)
  if not json_text or json_text == "" then
    return nil, "Missing science-status payload JSON"
  end

  local ok, payload = pcall(function()
    return helpers.json_to_table(json_text)
  end)
  if not ok or type(payload) ~= "table" then
    return nil, "Invalid science-status payload JSON"
  end
  if type(payload.surface) ~= "string" or payload.surface == "" then
    return nil, "Science-status payload must include a non-empty surface"
  end
  if type(payload.force) ~= "string" or payload.force == "" then
    return nil, "Science-status payload must include a non-empty force"
  end
  return payload, nil
end

local function status_name(entity)
  local ok, status = pcall(function()
    return entity.status
  end)
  if not ok or status == nil then
    return "unknown"
  end
  return STATUS_NAMES[status] or "unknown"
end

-- LuaInventory.get_contents() returns ItemWithQualityCount entries in Factorio
-- 2.1. ScienceStatus intentionally aggregates those entries by item prototype
-- name, because the current research ingredient contract is item-name based.
local function inventory_item_counts(inventory)
  if not inventory or inventory.valid == false then
    error("Lab input inventory is unavailable")
  end

  local counts = {}
  for _, item in ipairs(inventory.get_contents()) do
    if type(item.name) ~= "string" or item.name == "" then
      error("Lab input inventory returned an item without a name")
    end
    if type(item.count) ~= "number" or item.count < 0 then
      error("Lab input inventory returned an invalid item count for " .. item.name)
    end
    if item.count > 0 then
      counts[item.name] = (counts[item.name] or 0) + item.count
    end
  end
  return counts
end

local function add_counts(target, source)
  for name, count in pairs(source) do
    target[name] = (target[name] or 0) + count
  end
end

local function current_science_packs(technology)
  local packs = {}
  if not technology then
    return packs
  end

  for _, ingredient in pairs(technology.research_unit_ingredients) do
    if type(ingredient.name) ~= "string" or ingredient.name == "" then
      error("Current research returned an ingredient without a name")
    end
    if type(ingredient.amount) ~= "number" or ingredient.amount < 0 then
      error("Current research returned an invalid ingredient amount for " .. ingredient.name)
    end
    if ingredient.amount > 0 then
      packs[ingredient.name] = (packs[ingredient.name] or 0) + ingredient.amount
    end
  end
  return packs
end

local function read_labs(surface, force)
  local entries = {}
  local status_counts = {}
  local input_inventory = {}
  local working = 0

  for _, lab in pairs(surface.find_entities_filtered({ type = "lab", force = force })) do
    if lab.valid then
      local unit_number = lab.unit_number
      if type(unit_number) ~= "number" then
        error("Lab is missing a stable unit number")
      end

      local inventory = lab.get_inventory(defines.inventory.lab_input)
      local lab_inventory = inventory_item_counts(inventory)
      local status = status_name(lab)
      status_counts[status] = (status_counts[status] or 0) + 1
      if status == "working" then
        working = working + 1
      end
      add_counts(input_inventory, lab_inventory)

      table.insert(entries, {
        unit_number = unit_number,
        position = { x = lab.position.x, y = lab.position.y },
        status = status,
        input_inventory = lab_inventory
      })
    end
  end

  table.sort(entries, function(left, right)
    return left.unit_number < right.unit_number
  end)

  return {
    total = #entries,
    working = working,
    status_counts = status_counts,
    input_inventory = input_inventory,
    entries = entries
  }
end

local function read_research(force)
  local current = force.current_research
  local queue = {}
  for _, technology in ipairs(force.research_queue) do
    table.insert(queue, technology.name)
  end

  return {
    -- Lua nil keys are omitted by helpers.table_to_json, so encoding replaces
    -- this sentinel with the contract's required JSON null below.
    current = current and current.name or NULL_CURRENT_SENTINEL,
    progress = force.research_progress or 0,
    queue = queue,
    current_science_packs = current_science_packs(current),
    current_research_unit_count = current and current.research_unit_count or 0
  }
end

local function build_status(payload)
  local surface = game.surfaces[payload.surface]
  if not surface then
    error("Unknown surface: " .. payload.surface)
  end
  local force = game.forces[payload.force]
  if not force then
    error("Unknown force: " .. payload.force)
  end

  return {
    schema_version = SCHEMA_VERSION,
    ok = true,
    tick = game.tick,
    surface = surface.name,
    force = force.name,
    research = read_research(force),
    labs = read_labs(surface, force)
  }
end

local function encode_report(report)
  local json = helpers.table_to_json(report)
  if report.ok then
    -- Empty Lua tables are objects in Factorio JSON. These two fields are
    -- contractually arrays, so retain their unambiguous JSON representation.
    json = json:gsub('"queue":{}', '"queue":[]')
    json = json:gsub('"entries":{}', '"entries":[]')
    json = json:gsub('"' .. NULL_CURRENT_SENTINEL .. '"', "null")
  end
  return json
end

commands.add_command(
  "science_status",
  "Export read-only lab and research status. Parameter: JSON {surface,force}.",
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
      local ok, result = pcall(function()
        return build_status(payload)
      end)
      if ok then
        report = result
      else
        report.error = tostring(result)
      end
    end

    helpers.write_file(
      REPORT_DIRECTORY .. "science_status_" .. game.tick .. ".json",
      encode_report(report),
      false
    )
  end
)
