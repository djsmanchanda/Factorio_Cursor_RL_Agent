-- Path: factorio_training_lab/scenario_validation.lua
-- Purpose: Validate the versioned mining-delivery contract before world mutation.

local ALLOWED_RESOURCES = {
  ["iron-ore"] = true, ["copper-ore"] = true, coal = true, stone = true
}
local ALLOWED_ENTITIES = {
  ["electric-mining-drill"] = true, ["fast-inserter"] = true,
  ["medium-electric-pole"] = true, splitter = true,
  ["transport-belt"] = true, ["underground-belt"] = true
}
local POWER_SOURCE_ENTITIES = {
  ["electric-energy-interface"] = true, ["solar-panel"] = true,
  ["steam-engine"] = true, ["steam-turbine"] = true,
  ["fusion-generator"] = true
}

local function is_integer(value)
  return type(value) == "number" and value == math.floor(value)
end

local function valid_hash(value)
  return type(value) == "string" and string.match(value, "^sha256:[0-9a-f]+$")
    and #value == 71
end

local function validate_name(value, pattern, label)
  if type(value) ~= "string" or #value > 100 or not string.match(value, pattern) then
    error(label .. " has an invalid training-only name")
  end
end

local function validate_bounds(bounds, label)
  if type(bounds) ~= "table" then error(label .. " must be an object") end
  for _, key in ipairs({ "x_min", "y_min", "x_max_exclusive", "y_max_exclusive" }) do
    if not is_integer(bounds[key]) or bounds[key] < -512 or bounds[key] > 512 then
      error(label .. "." .. key .. " must be an integer inside [-512,512]")
    end
  end
  if bounds.x_min >= bounds.x_max_exclusive or bounds.y_min >= bounds.y_max_exclusive then
    error(label .. " must have positive width and height")
  end
end

local function contains(bounds, x, y)
  return x >= bounds.x_min and x < bounds.x_max_exclusive
    and y >= bounds.y_min and y < bounds.y_max_exclusive
end

local function validate_environment(scenario)
  local environment = scenario.environment
  if type(environment) ~= "table" or environment.isolated_force ~= true
      or environment.autoplace_enabled ~= false then
    error("environment must disable autoplace and use an isolated force")
  end
  validate_name(environment.surface_name, "^training/[a-z0-9][a-z0-9%-]*$", "surface_name")
  validate_name(environment.force_name, "^training%-[a-z0-9][a-z0-9%-]*$", "force_name")
  validate_bounds(environment.bounds, "environment.bounds")
  local width = environment.bounds.x_max_exclusive - environment.bounds.x_min
  local height = environment.bounds.y_max_exclusive - environment.bounds.y_min
  if environment.bounds.x_min ~= -width / 2 or environment.bounds.y_min ~= -height / 2 then
    error("environment bounds must be even and centred on the origin")
  end
end

local function validate_patch(scenario)
  local patch = scenario.resource_patch
  local bounds = scenario.environment.bounds
  if type(patch) ~= "table" or not ALLOWED_RESOURCES[patch.resource]
      or not is_integer(patch.amount_per_tile) or patch.amount_per_tile < 1 then
    error("resource_patch is invalid")
  end
  local p = patch.bounds
  if type(p) ~= "table" or not is_integer(p.x1) or not is_integer(p.y1)
      or not is_integer(p.x2) or not is_integer(p.y2) or p.x1 > p.x2 or p.y1 > p.y2 then
    error("resource_patch bounds are invalid")
  end
  if not contains(bounds, p.x1, p.y1) or not contains(bounds, p.x2, p.y2) then
    error("resource_patch must remain inside the environment")
  end
end

local function validate_position(position, world, label)
  if type(position) ~= "table" or type(position[1]) ~= "number"
      or type(position[2]) ~= "number" or not contains(world, position[1], position[2]) then
    error(label .. " position is invalid")
  end
end

local function validate_fixtures(scenario)
  local fixtures, ids, kinds = scenario.fixtures, {}, {}
  if type(fixtures) ~= "table" or #fixtures < 2 or #fixtures > 3 then
    error("mining delivery requires a source, sink, and optional power storage")
  end
  for index, fixture in ipairs(fixtures) do
    local label = "fixtures[" .. index .. "]"
    if type(fixture.id) ~= "string" or fixture.id == "" or ids[fixture.id]
        or fixture.protected ~= true then error(label .. " identity is invalid") end
    validate_position(fixture.position, scenario.environment.bounds, label)
    local valid_source = fixture.kind == "power_source" and POWER_SOURCE_ENTITIES[fixture.entity]
    local valid_storage = fixture.kind == "power_storage" and fixture.entity == "accumulator"
    local valid_sink = fixture.kind == "item_sink" and fixture.entity == "infinity-chest"
    if not (valid_source or valid_storage or valid_sink) or kinds[fixture.kind] then
      error(label .. " must be one approved unique fixture kind")
    end
    if fixture.kind == "power_source"
        and (not is_integer(fixture.position[1]) or not is_integer(fixture.position[2])) then
      error("power_source must use an integral centre for its 2x2 footprint")
    end
    ids[fixture.id], kinds[fixture.kind] = fixture, true
  end
  if not kinds.power_source or not kinds.item_sink then
    error("mining delivery requires one power source and one item sink")
  end
  return ids
end

local function validate_budget(scenario)
  local budget, listed = scenario.construction_budget, {}
  if type(budget) ~= "table" or next(budget) == nil then error("construction_budget is empty") end
  for entity, count in pairs(budget) do
    if not ALLOWED_ENTITIES[entity] or not is_integer(count) or count < 0 then
      error("construction budget contains an invalid entity or count: " .. tostring(entity))
    end
  end
  for _, entity in ipairs(scenario.constraints.allowed_entities or {}) do
    if listed[entity] or budget[entity] == nil then error("allowed_entities differs from budget") end
    listed[entity] = true
  end
  for entity in pairs(budget) do
    if not listed[entity] then error("allowed_entities differs from budget") end
  end
end

local function validate_objective(scenario, fixtures)
  local objective, constraints = scenario.objective, scenario.constraints
  if type(objective) ~= "table" or objective.kind ~= "deliver_item_rate"
      or objective.item ~= scenario.resource_patch.resource
      or type(objective.target_rate_per_tick) ~= "number"
      or objective.target_rate_per_tick <= 0
      or not is_integer(objective.sustain_ticks) or objective.sustain_ticks < 60 then
    error("objective is invalid")
  end
  local destination = fixtures[objective.destination_fixture_id]
  if not destination or destination.kind ~= "item_sink" then
    error("objective destination must identify the item sink")
  end
  if type(constraints) ~= "table" or constraints.allow_fixture_deconstruction ~= false
      or not is_integer(constraints.max_episode_ticks) or constraints.max_episode_ticks < 60 then
    error("episode constraints are invalid")
  end
  validate_bounds(constraints.allowed_build_area, "allowed_build_area")
  local world, area = scenario.environment.bounds, constraints.allowed_build_area
  if not contains(world, area.x_min, area.y_min)
      or area.x_max_exclusive > world.x_max_exclusive
      or area.y_max_exclusive > world.y_max_exclusive then
    error("allowed_build_area must remain inside the environment")
  end
end

local function validate_scenario(scenario)
  if type(scenario) ~= "table" or scenario.version ~= "1.1.0"
      or scenario.family ~= "mining_delivery" or type(scenario.scenario_id) ~= "string"
      or not valid_hash(scenario.scenario_hash)
      or not is_integer(scenario.seed) or scenario.seed < 0 then
    error("scenario identity is invalid")
  end
  validate_environment(scenario)
  validate_patch(scenario)
  local fixtures = validate_fixtures(scenario)
  validate_budget(scenario)
  validate_objective(scenario, fixtures)
  return scenario
end

return {
  ALLOWED_ENTITIES = ALLOWED_ENTITIES,
  validate_scenario = validate_scenario
}
