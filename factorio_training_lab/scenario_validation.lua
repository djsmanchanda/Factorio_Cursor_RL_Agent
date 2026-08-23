-- Path: factorio_training_lab/scenario_validation.lua
-- Purpose: Validate the versioned mining-delivery contract before world mutation.

local ALLOWED_RESOURCES = {
  ["iron-ore"] = true, ["copper-ore"] = true, coal = true, stone = true
}
local ALLOWED_ENTITIES = {
  ["electric-mining-drill"] = true, ["fast-inserter"] = true,
  ["medium-electric-pole"] = true, ["electric-furnace"] = true, splitter = true,
  ["transport-belt"] = true, ["underground-belt"] = true,
  ["express-transport-belt"] = true, ["express-underground-belt"] = true,
  ["express-loader"] = true
}
local POWER_SOURCE_ENTITIES = {
  ["electric-energy-interface"] = true, ["solar-panel"] = true,
  ["steam-engine"] = true, ["steam-turbine"] = true,
  ["fusion-generator"] = true
}

local REWARD_WEIGHT_SIGNS = {
  completion = "positive",
  throughput = "nonnegative",
  elapsed_tick = "nonpositive",
  material_item = "nonpositive",
  failed_placement = "nonpositive",
  pole = "nonpositive",
  route_excess = "nonpositive",
  land = "nonpositive",
  unproductive_drill_capacity = "nonpositive"
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

local function validate_obstacles(scenario)
  local obstacles, ids = scenario.obstacles or {}, {}
  if type(obstacles) ~= "table" or #obstacles > 8 then
    error("obstacles must contain at most eight protected fields")
  end
  local world, patch = scenario.environment.bounds, scenario.resource_patch.bounds
  for index, obstacle in ipairs(obstacles) do
    local label, bounds = "obstacles[" .. index .. "]", obstacle.bounds
    if type(obstacle.id) ~= "string" or obstacle.id == "" or ids[obstacle.id]
        or obstacle.entity ~= "stone-wall" or obstacle.protected ~= true
        or type(bounds) ~= "table" or not is_integer(bounds.x1) or not is_integer(bounds.y1)
        or not is_integer(bounds.x2) or not is_integer(bounds.y2)
        or bounds.x1 > bounds.x2 or bounds.y1 > bounds.y2 then
      error(label .. " is invalid")
    end
    if not contains(world, bounds.x1, bounds.y1) or not contains(world, bounds.x2, bounds.y2) then
      error(label .. " must remain inside the environment")
    end
    local area = (bounds.x2 - bounds.x1 + 1) * (bounds.y2 - bounds.y1 + 1)
    if area > 512 then error(label .. " is too large") end
    local overlaps_patch = not (bounds.x2 < patch.x1 or bounds.x1 > patch.x2
      or bounds.y2 < patch.y1 or bounds.y1 > patch.y2)
    if overlaps_patch then error(label .. " may not cover the resource patch") end
    for _, fixture in ipairs(scenario.fixtures) do
      local x, y = fixture.position[1], fixture.position[2]
      if x >= bounds.x1 and x <= bounds.x2 + 1 and y >= bounds.y1 and y <= bounds.y2 + 1 then
        error(label .. " may not cover a protected fixture")
      end
    end
    ids[obstacle.id] = true
  end
end

local function validate_fixtures(scenario)
  local fixtures, ids, kinds = scenario.fixtures, {}, {}
  if type(fixtures) ~= "table" or #fixtures < 2 or #fixtures > 4 then
    error("mining delivery requires a source, one or two sinks, and optional power storage")
  end
  for index, fixture in ipairs(fixtures) do
    local label = "fixtures[" .. index .. "]"
    if type(fixture.id) ~= "string" or fixture.id == "" or ids[fixture.id]
        or fixture.protected ~= true then error(label .. " identity is invalid") end
    validate_position(fixture.position, scenario.environment.bounds, label)
    local valid_source = fixture.kind == "power_source" and POWER_SOURCE_ENTITIES[fixture.entity]
    local valid_storage = fixture.kind == "power_storage" and fixture.entity == "accumulator"
    local valid_item_source = fixture.kind == "item_source" and fixture.entity == "infinity-chest"
    local valid_sink = fixture.kind == "item_sink" and fixture.entity == "infinity-chest"
    if not (valid_source or valid_storage or valid_item_source or valid_sink)
        or ((valid_source or valid_storage) and kinds[fixture.kind]) then
      error(label .. " must be one approved fixture kind")
    end
    if fixture.kind == "power_source"
        and (not is_integer(fixture.position[1]) or not is_integer(fixture.position[2])) then
      error("power_source must use an integral centre for its 2x2 footprint")
    end
    if fixture.kind == "power_source"
        and (not contains(scenario.environment.bounds, fixture.position[1] - 1, fixture.position[2] - 1)
          or not contains(scenario.environment.bounds, fixture.position[1] + 1, fixture.position[2] + 1)) then
      error("power_source 2x2 footprint must remain inside the environment bounds")
    end
    ids[fixture.id] = fixture
    if valid_source or valid_storage or valid_item_source or valid_sink then kinds[fixture.kind] = true end
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
  if type(objective) ~= "table"
      or (scenario.family == "mining_delivery" and (objective.kind ~= "deliver_item_rate"
          or objective.item ~= scenario.resource_patch.resource))
      or (scenario.family == "furnace_refining" and (objective.kind ~= "smelt_item_rate"
          or objective.input_item ~= scenario.resource_patch.resource))
      or type(objective.target_rate_per_tick) ~= "number"
      or objective.target_rate_per_tick <= 0
      or not is_integer(objective.sustain_ticks) or objective.sustain_ticks < 60 then
    error("objective is invalid")
  end
  local destination = fixtures[objective.destination_fixture_id]
  if not destination or destination.kind ~= "item_sink" then
    error("objective destination must identify the item sink")
  end
  local stages = objective.stages
  if stages ~= nil then
    if type(stages) ~= "table" or #stages < 1 or #stages > 16 then
      error("objective stages must contain between 1 and 16 stages")
    end
    local stage_ids = {}
    for index, stage in ipairs(stages) do
      if type(stage) ~= "table" or type(stage.id) ~= "string"
          or stage.id == "" or stage_ids[stage.id] then
        error("objective stage " .. index .. " identity is invalid")
      end
      if type(stage.target_rate_per_tick) ~= "number" or stage.target_rate_per_tick <= 0
          or not is_integer(stage.sustain_ticks) or stage.sustain_ticks < 60 then
        error("objective stage " .. index .. " rate or sustain is invalid")
      end
      local destinations = stage.destination_fixture_ids
      if type(destinations) ~= "table" or #destinations < 1 or #destinations > 8 then
        error("objective stage " .. index .. " must have one to eight destinations")
      end
      local destination_ids = {}
      for _, fixture_id in ipairs(destinations) do
        if type(fixture_id) ~= "string" or destination_ids[fixture_id] then
          error("objective stage " .. index .. " destination ids are invalid")
        end
        local fixture = fixtures[fixture_id]
        if not fixture or fixture.kind ~= "item_sink" then
          error("objective stage destination must identify an item sink")
        end
        destination_ids[fixture_id] = true
      end
      stage_ids[stage.id] = true
    end
    local first = stages[1]
    if first.target_rate_per_tick ~= objective.target_rate_per_tick
        or first.sustain_ticks ~= objective.sustain_ticks then
      error("first objective stage must match the legacy objective fields")
    end
    local first_destinations = {}
    for _, fixture_id in ipairs(first.destination_fixture_ids) do first_destinations[fixture_id] = true end
    if not first_destinations[objective.destination_fixture_id] then
      error("legacy objective destination must be in the first stage")
    end
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

local function validate_rewards(scenario)
  if scenario.reward_profile ~= "mining-efficiency-v1"
      and scenario.reward_profile ~= "mining-throughput-leaky-v1"
      and scenario.reward_profile ~= "mining-throughput-cost-v1"
      and scenario.reward_profile ~= "furnace-efficiency-v1" then
    error("reward_profile is invalid")
  end
  local weights = scenario.reward_weights
  if type(weights) ~= "table" then error("reward_weights must be an object") end
  local count = 0
  for name, sign in pairs(REWARD_WEIGHT_SIGNS) do
    local value = weights[name]
    if type(value) ~= "number" then error("reward weight is missing: " .. name) end
    if sign == "positive" and value <= 0 then error(name .. " reward must be positive") end
    if sign == "nonnegative" and value < 0 then error(name .. " reward must be non-negative") end
    if sign == "nonpositive" and value > 0 then error(name .. " reward must be non-positive") end
    count = count + 1
  end
  local actual = 0
  for name, _ in pairs(weights) do
    if REWARD_WEIGHT_SIGNS[name] == nil then error("unknown reward weight: " .. tostring(name)) end
    actual = actual + 1
  end
  if actual ~= count then error("reward_weights are incomplete") end
end

local function validate_scenario(scenario)
  if type(scenario) ~= "table" or scenario.version ~= "1.2.0"
      or (scenario.family ~= "mining_delivery" and scenario.family ~= "furnace_refining")
      or type(scenario.scenario_id) ~= "string"
      or not valid_hash(scenario.scenario_hash)
      or not is_integer(scenario.seed) or scenario.seed < 0 or scenario.seed > 65535 then
    error("scenario identity is invalid")
  end
  validate_environment(scenario)
  validate_patch(scenario)
  validate_obstacles(scenario)
  local fixtures = validate_fixtures(scenario)
  validate_budget(scenario)
  validate_objective(scenario, fixtures)
  validate_rewards(scenario)
  return scenario
end

return {
  ALLOWED_ENTITIES = ALLOWED_ENTITIES,
  validate_scenario = validate_scenario
}
