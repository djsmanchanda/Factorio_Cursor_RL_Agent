-- Path: factorio_mod/world_generation.lua
-- Purpose: Create or explicitly reset only a fully validated planner-owned world.

local SURFACE_NAME = "planner-sandbox"
local OWNER = "factorio_cursor_rl_agent"
local SPEC_VERSION = "1.0.0"
local CREATE_TOKEN = "CREATE_PLANNER_WORLD"
local RESET_TOKEN = "RESET_PLANNER_WORLD"
local ALLOWED_RESOURCES = {
  ["iron-ore"] = true, ["copper-ore"] = true, coal = true, stone = true
}
local CONSTRUCTION_MATERIALS = {
  ["assembling-machine-2"] = true, ["big-electric-pole"] = true,
  ["chemical-plant"] = true, ["electric-furnace"] = true,
  ["electric-mining-drill"] = true, ["express-transport-belt"] = true,
  ["express-underground-belt"] = true, ["long-handed-inserter"] = true,
  ["medium-electric-pole"] = true, ["offshore-pump"] = true,
  ["oil-refinery"] = true, ["passive-provider-chest"] = true, pipe = true,
  ["pipe-to-ground"] = true, pumpjack = true, roboport = true,
  ["stack-inserter"] = true, ["steel-chest"] = true,
  ["storage-chest"] = true, substation = true
}
local DIRECTIONS = { north = true, east = true, south = true, west = true }

local function is_integer(value)
  return type(value) == "number" and value == math.floor(value)
end

local function in_bounds(value)
  return type(value) == "number" and value >= -250 and value < 250
end

local function require_integer(value, label, minimum)
  if not is_integer(value) or (minimum ~= nil and value < minimum) then
    error(label .. " must be an integer" .. (minimum and " >= " .. minimum or ""))
  end
  return value
end

local function validate_position(position, label)
  if type(position) ~= "table" or #position ~= 2
      or not in_bounds(position[1]) or not in_bounds(position[2]) then
    error(label .. " must be a two-number position inside [-250,250)")
  end
end

local function same_position(left, right)
  return left[1] == right[1] and left[2] == right[2]
end

local function rectangles_overlap(left, right)
  return not (left.x2 < right.x1 or right.x2 < left.x1
    or left.y2 < right.y1 or right.y2 < left.y1)
end

local function validate_rectangle(rectangle, label)
  for _, key in ipairs({ "x1", "y1", "x2", "y2" }) do
    if not is_integer(rectangle[key]) or not in_bounds(rectangle[key]) then
      error(label .. "." .. key .. " must be an in-bounds integer")
    end
  end
  if rectangle.x1 > rectangle.x2 or rectangle.y1 > rectangle.y2 then
    error(label .. " bounds must be ordered")
  end
end

local function validate_bounds(bounds, label)
  if type(bounds) ~= "table" or bounds.x_min ~= -250 or bounds.y_min ~= -250
      or bounds.x_max_exclusive ~= 250 or bounds.y_max_exclusive ~= 250
      or bounds.width ~= 500 or bounds.height ~= 500 then
    error(label .. " must describe exact tiles [-250,250) on both axes")
  end
end

local function validate_resources(spec)
  if type(spec.resource_patches) ~= "table" or #spec.resource_patches == 0 then
    error("WorldSpec needs explicit resource patches")
  end
  local ids, patches, resources = {}, {}, {}
  for index, patch in ipairs(spec.resource_patches) do
    local label = "resource_patches[" .. index .. "]"
    if type(patch.id) ~= "string" or patch.id == "" or ids[patch.id] then
      error(label .. " needs a unique nonempty id")
    end
    if not ALLOWED_RESOURCES[patch.resource] then error(label .. " resource is not allowed") end
    validate_rectangle(patch, label)
    require_integer(patch.amount_per_tile, label .. ".amount_per_tile", 1)
    for _, existing in ipairs(patches) do
      if rectangles_overlap(patch, existing) then error(label .. " overlaps another resource patch") end
    end
    ids[patch.id], resources[patch.resource] = patch, true
    patches[#patches + 1] = patch
  end
  for resource in pairs(ALLOWED_RESOURCES) do
    if not resources[resource] then error("WorldSpec is missing " .. resource) end
  end
  return ids, patches
end

local function validate_crude(spec)
  if type(spec.crude_oil_spots) ~= "table" or #spec.crude_oil_spots == 0 then
    error("WorldSpec needs explicit crude-oil spots")
  end
  local ids = {}
  for index, spot in ipairs(spec.crude_oil_spots) do
    local label = "crude_oil_spots[" .. index .. "]"
    if type(spot.id) ~= "string" or spot.id == "" or ids[spot.id] then
      error(label .. " needs a unique nonempty id")
    end
    validate_position(spot.position, label .. ".position")
    require_integer(spot.amount, label .. ".amount", 1)
    ids[spot.id] = spot
  end
  return ids
end

local function validate_lake(spec, resource_patches)
  local lake = spec.water_lake
  if type(lake) ~= "table" or lake.tile ~= "water" then
    error("WorldSpec needs an explicit water lake")
  end
  validate_rectangle(lake, "water_lake")
  for _, patch in ipairs(resource_patches) do
    if rectangles_overlap(lake, patch) then error("water_lake overlaps resource patch " .. patch.id) end
  end
  if type(lake.offshore_edge_candidates) ~= "table"
      or #lake.offshore_edge_candidates == 0 then
    error("water_lake needs an offshore-pump edge candidate")
  end
  for index, site in ipairs(lake.offshore_edge_candidates) do
    local label = "water_lake.offshore_edge_candidates[" .. index .. "]"
    validate_position(site.position, label .. ".position")
    validate_position(site.output, label .. ".output")
    if not DIRECTIONS[site.direction] then error(label .. " direction is invalid") end
    local x, y = site.position[1], site.position[2]
    local on_edge = (site.direction == "south" and y == lake.y1 - 0.5 and x >= lake.x1 and x <= lake.x2)
      or (site.direction == "north" and y == lake.y2 + 0.5 and x >= lake.x1 and x <= lake.x2)
      or (site.direction == "east" and x == lake.x1 - 0.5 and y >= lake.y1 and y <= lake.y2)
      or (site.direction == "west" and x == lake.x2 + 0.5 and y >= lake.y1 and y <= lake.y2)
    if not on_edge then error(label .. " is not on the declared lake edge") end
  end
  return lake
end

local function validate_starter_kit(starter)
  if type(starter) ~= "table" or type(starter.production_ingredients) ~= "table"
      or next(starter.production_ingredients) ~= nil then
    error("Starter kit must declare zero production ingredients")
  end
  local power, hub = starter.power_source or {}, starter.roboport_hub or {}
  if power.entity ~= "electric-energy-interface" or type(power.position) ~= "table"
      or power.position[1] ~= -160 or power.position[2] ~= -160 then
    error("Starter kit must own the canonical electric-energy-interface")
  end
  if hub.entity ~= "roboport" or type(hub.position) ~= "table"
      or hub.position[1] ~= -128 or hub.position[2] ~= -128
      or hub.network_policy ~= "single-connected" then
    error("Starter kit must own the canonical single-connected roboport hub")
  end
  require_integer(starter.bots_per_roboport, "bots_per_roboport", 1)
  if type(starter.construction_materials) ~= "table"
      or next(starter.construction_materials) == nil then
    error("Starter kit needs declared construction materials")
  end
  for name, count in pairs(starter.construction_materials) do
    if not CONSTRUCTION_MATERIALS[name] or string.find(name, "infinity", 1, true) then
      error("Starter kit item is not construction-only: " .. tostring(name))
    end
    require_integer(count, "construction material " .. name, 1)
  end
end

local function validate_electronics_world(world, patches, crude_spots, lake)
  if type(world) ~= "table" or world.version ~= SPEC_VERSION
      or world.surface ~= SURFACE_NAME or world.survey_tick ~= 0 then
    error("electronics_world identity does not match the generated world")
  end
  local bounds = world.map_bounds or {}
  if bounds.x1 ~= -250 or bounds.y1 ~= -250 or bounds.x2 ~= 250 or bounds.y2 ~= 250 then
    error("electronics_world bounds do not match WorldSpec")
  end
  if type(world.ore_patches) ~= "table" then error("electronics_world needs ore patches") end
  for _, surveyed in ipairs(world.ore_patches) do
    local generated = patches[surveyed.id]
    if not generated or surveyed.item ~= generated.resource
        or surveyed.x1 ~= generated.x1 or surveyed.y1 ~= generated.y1
        or surveyed.x2 ~= generated.x2 or surveyed.y2 ~= generated.y2 then
      error("electronics ore patch does not match generated patch " .. tostring(surveyed.id))
    end
  end
  for _, site in ipairs(world.pumpjack_sites or {}) do
    validate_position(site.position, "electronics pumpjack position")
    validate_position(site.output, "electronics crude output")
    if site.resource ~= "crude-oil" then error("electronics pumpjack resource must be crude-oil") end
    local found = false
    for _, spot in pairs(crude_spots) do
      if same_position(site.position, spot.position) then found = true end
    end
    if not found then error("electronics pumpjack has no generated crude-oil spot") end
  end
  for _, site in ipairs(world.offshore_pump_sites or {}) do
    validate_position(site.position, "electronics offshore-pump position")
    validate_position(site.output, "electronics water output")
    if site.resource ~= "water" then error("electronics offshore resource must be water") end
    local found = false
    for _, candidate in ipairs(lake.offshore_edge_candidates) do
      if same_position(site.position, candidate.position)
          and same_position(site.output, candidate.output)
          and (site.direction or "north") == candidate.direction then found = true end
    end
    if not found then error("electronics offshore pump has no generated lake candidate") end
  end
end

local function validate_spec(spec)
  if type(spec) ~= "table" or spec.version ~= SPEC_VERSION then
    error("WorldSpec version must be " .. SPEC_VERSION)
  end
  if spec.surface ~= SURFACE_NAME then error("WorldSpec may only target " .. SURFACE_NAME) end
  require_integer(spec.seed, "seed", 0)
  validate_bounds(spec.bounds, "WorldSpec bounds")
  local map_generation = spec.map_generation or {}
  if map_generation.autoplace_enabled ~= false
      or map_generation.generate_with_lab_tiles ~= true then
    error("WorldSpec must disable autoplace and request deterministic lab tiles")
  end
  local patch_lookup, patch_list = validate_resources(spec)
  local crude_lookup = validate_crude(spec)
  local lake = validate_lake(spec, patch_list)
  validate_starter_kit(spec.starter_kit)
  validate_electronics_world(spec.electronics_world, patch_lookup, crude_lookup, lake)
  return spec
end

local function owns_existing_surface(surface)
  local saved = storage.planner_world
  return surface ~= nil and surface.name == SURFACE_NAME and saved ~= nil
    and saved.owner == OWNER and saved.surface == SURFACE_NAME
end

local function map_gen_settings(spec)
  return {
    seed = spec.seed, width = 500, height = 500,
    default_enable_all_autoplace_controls = false,
    autoplace_settings = {
      entity = { treat_missing_as_default = false, settings = {} },
      tile = { treat_missing_as_default = false, settings = {} },
      decorative = { treat_missing_as_default = false, settings = {} }
    }
  }
end

local function place_world(surface, spec)
  surface.generate_with_lab_tiles = true
  surface.request_to_generate_chunks({ x = 0, y = 0 }, 8)
  surface.force_generate_chunk_requests()
  local tiles = {}
  for x = spec.water_lake.x1, spec.water_lake.x2 do
    for y = spec.water_lake.y1, spec.water_lake.y2 do
      tiles[#tiles + 1] = { name = "water", position = { x = x, y = y } }
    end
  end
  surface.set_tiles(tiles, true)
  local ore_tiles = 0
  for _, patch in ipairs(spec.resource_patches) do
    for x = patch.x1, patch.x2 do
      for y = patch.y1, patch.y2 do
        if not surface.create_entity({ name = patch.resource,
            position = { x = x + 0.5, y = y + 0.5 }, amount = patch.amount_per_tile }) then
          error("Could not place resource patch " .. patch.id)
        end
        ore_tiles = ore_tiles + 1
      end
    end
  end
  for _, spot in ipairs(spec.crude_oil_spots) do
    if not surface.create_entity({ name = "crude-oil",
        position = { x = spot.position[1], y = spot.position[2] }, amount = spot.amount }) then
      error("Could not place crude-oil spot " .. spot.id)
    end
  end
  return ore_tiles, #spec.crude_oil_spots, #tiles
end

local function create_world(spec)
  local surface = game.create_surface(SURFACE_NAME, map_gen_settings(spec))
  local ore_tiles, oil_spots, water_tiles = place_world(surface, spec)
  storage.planner_world = {
    owner = OWNER, version = spec.version, surface = SURFACE_NAME,
    seed = spec.seed, bounds = spec.bounds, world_spec = spec
  }
  return { surface = SURFACE_NAME, seed = spec.seed, ore_tiles = ore_tiles,
    oil_spots = oil_spots, water_tiles = water_tiles, starter_kit = spec.starter_kit }
end

local function execute(payload)
  if type(payload) ~= "table" or payload.confirm ~= true then
    error("Planner world creation/reset requires confirm=true")
  end
  local mode = payload.mode
  local token = mode == "create" and CREATE_TOKEN or mode == "reset" and RESET_TOKEN or nil
  if token == nil or payload.confirmation_token ~= token then
    error("Planner world mode and confirmation token do not match")
  end
  local spec = validate_spec(payload.world_spec)
  local existing = game.surfaces[SURFACE_NAME]
  if existing and mode == "create" then
    if owns_existing_surface(existing) and storage.planner_world.version == spec.version
        and storage.planner_world.seed == spec.seed then
      return { surface = SURFACE_NAME, unchanged = true, starter_kit = spec.starter_kit }
    end
    error("planner-sandbox already exists; inspect it before an explicit reset")
  end
  if existing and mode == "reset" then
    if not owns_existing_surface(existing) then
      error("reset refused: existing planner-sandbox is not owned by this planner world")
    end
    game.delete_surface(existing)
    storage.planner_world = nil
  end
  return create_world(spec)
end

commands.add_command("create_planner_world", "Create/reset the confirmed bounded planner world.", function(command)
  local decoded, payload = pcall(function() return helpers.json_to_table(command.parameter or "") end)
  local ok, result
  if decoded then ok, result = pcall(function() return execute(payload) end)
  else ok, result = false, "invalid JSON payload" end
  local report = { tick = game.tick, ok = ok }
  if ok then for key, value in pairs(result) do report[key] = value end
  else report.error = tostring(result) end
  helpers.write_file("factorio_mod/world_reports/world_" .. game.tick .. ".json",
    helpers.table_to_json(report), false)
end)

return { validate_spec = validate_spec, owns_existing_surface = owns_existing_surface }
