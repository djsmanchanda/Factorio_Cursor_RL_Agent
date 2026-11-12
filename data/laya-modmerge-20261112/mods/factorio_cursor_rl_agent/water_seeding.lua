-- Path: factorio_mod/water_seeding.lua
-- Purpose: Idempotently seed bounded Nauvis-style water terrain for surveyed offshore pumps.

local shared = require("sandbox_shared")
local get_or_create_sandbox_surface = shared.get_or_create_sandbox_surface

local SURFACE_MIN = -250
local SURFACE_MAX = 249
local MAX_LAKE_TILES = 4096

local function require_coordinate(value, label)
  local number = tonumber(value)
  if number == nil or number ~= math.floor(number)
      or number < SURFACE_MIN or number > SURFACE_MAX then
    error(label .. " must be an integer inside planner-sandbox bounds")
  end
  return number
end

local function seed_water_lakes(payload)
  local lakes = payload.water_lakes
  if type(lakes) ~= "table" or #lakes == 0 then
    error("Water seeding payload must include a non-empty water_lakes array")
  end

  local surface = get_or_create_sandbox_surface()
  local ids = {}
  local seeded = 0
  local seeded_by_lake = {}
  for index, lake in ipairs(lakes) do
    local id = lake.id
    if type(id) ~= "string" or id == "" or ids[id] then
      error("Water lake entries need unique non-empty ids")
    end
    if lake.tile ~= "water" or not prototypes.tile[lake.tile] then
      error("Water lake " .. id .. " must use the valid water tile prototype")
    end
    local x1 = require_coordinate(lake.x1, "water_lakes[" .. index .. "].x1")
    local y1 = require_coordinate(lake.y1, "water_lakes[" .. index .. "].y1")
    local x2 = require_coordinate(lake.x2, "water_lakes[" .. index .. "].x2")
    local y2 = require_coordinate(lake.y2, "water_lakes[" .. index .. "].y2")
    if x1 > x2 or y1 > y2 then
      error("Water lake " .. id .. " bounds must be ordered")
    end
    local area = (x2 - x1 + 1) * (y2 - y1 + 1)
    if area > MAX_LAKE_TILES then
      error("Water lake " .. id .. " exceeds the bounded seeding limit")
    end

    local tiles = {}
    for x = x1, x2 do
      for y = y1, y2 do
        local existing = surface.get_tile(x, y).name
        if existing ~= "water" and existing ~= "deepwater" then
          tiles[#tiles + 1] = { name = "water", position = { x = x, y = y } }
        end
      end
    end
    if #tiles > 0 then
      surface.set_tiles(tiles, true)
    end
    ids[id] = true
    seeded_by_lake[id] = #tiles
    seeded = seeded + #tiles
  end
  return { seeded_water_tiles = seeded, seeded_by_lake = seeded_by_lake }
end

commands.add_command("seed_water_lakes", "Idempotently seed bounded water terrain behind surveyed offshore pumps on planner-sandbox.", function(command)
  local decoded, payload = pcall(function()
    return helpers.json_to_table(command.parameter or "")
  end)
  local ok, result
  if decoded and type(payload) == "table" then
    ok, result = pcall(function() return seed_water_lakes(payload) end)
  else
    ok, result = false, "invalid JSON payload"
  end

  local report = { tick = game.tick, ok = ok }
  if ok then
    report.seeded_water_tiles = result.seeded_water_tiles
    report.seeded_by_lake = result.seeded_by_lake
  else
    report.error = tostring(result)
  end
  helpers.write_file(
    "factorio_mod/water_seed_reports/water_seed_" .. game.tick .. ".json",
    helpers.table_to_json(report),
    false
  )
end)
