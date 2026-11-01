-- Path: factorio_mod/runtime_catalog.lua
-- Purpose: Export read-only runtime technology, unlock, and selected prototype facts.
--
-- This module is intentionally independent of recipe_catalog.lua. It never
-- creates forces, entities, items, or research, and it never mutates prototype
-- or force state. It reads the live runtime API and writes one JSON report via
-- helpers.write_file. The command is only registered when control.lua requires
-- this module; this file does not self-register an event handler.

local SCHEMA_VERSION = "1.1.0"
local KIND = "runtime_catalog"
local REPORT_DIRECTORY = "factorio_mod/runtime_catalogs/"
local MAX_REQUEST_ID_LENGTH = 64

-- Entity types with production, geometry, energy, fluid, or module relevance.
-- The list is explicit so an unlisted modded type is an unknown rather than a
-- silently incomplete fact.
local ENTITY_TYPES = {
  accumulator = true,
  ["assembling-machine"] = true,
  beacon = true,
  boiler = true,
  ["burner-generator"] = true,
  container = true,
  ["electric-pole"] = true,
  furnace = true,
  generator = true,
  ["heat-pipe"] = true,
  inserter = true,
  lab = true,
  loader = true,
  ["loader-1x1"] = true,
  ["logistic-container"] = true,
  ["mining-drill"] = true,
  ["offshore-pump"] = true,
  pipe = true,
  ["pipe-to-ground"] = true,
  pump = true,
  reactor = true,
  roboport = true,
  ["rocket-silo"] = true,
  ["solar-panel"] = true,
  splitter = true,
  ["storage-tank"] = true,
  ["transport-belt"] = true,
  ["underground-belt"] = true,
}

-- Documented LuaForce numeric bonus attributes. Keeping an explicit list means
-- a new engine field is not silently treated as a bonus.
local FORCE_BONUS_FIELDS = {
  "artillery_range_modifier",
  "beacon_distribution_modifier",
  "belt_stack_size_bonus",
  "bulk_inserter_capacity_bonus",
  "character_build_distance_bonus",
  "character_health_bonus",
  "character_inventory_slots_bonus",
  "character_item_drop_distance_bonus",
  "character_item_pickup_distance_bonus",
  "character_loot_pickup_distance_bonus",
  "character_reach_distance_bonus",
  "character_resource_reach_distance_bonus",
  "character_running_speed_modifier",
  "character_trash_slot_count",
  "following_robots_lifetime_modifier",
  "inserter_stack_size_bonus",
  "laboratory_productivity_bonus",
  "laboratory_speed_modifier",
  "manual_crafting_speed_modifier",
  "manual_mining_speed_modifier",
  "maximum_following_robot_count",
  "mining_drill_productivity_bonus",
  "train_braking_force_bonus",
  "worker_robots_battery_modifier",
  "worker_robots_speed_modifier",
  "worker_robots_storage_bonus",
}

-- helpers.table_to_json serialises an empty Lua table as {} (object). Every
-- key below is always an array, so restore [] after serialisation.
local ARRAY_KEYS = {
  "allowed_effects",
  "allowed_module_categories",
  "categories",
  "connection_category",
  "effects",
  "entities",
  "entity_types",
  "fluidboxes",
  "fluids",
  "fuel_categories",
  "ingredients",
  "items",
  "pipe_connections",
  "positions",
  "prerequisites",
  "products",
  "recipe_fluid_boxes",
  "research_queue",
  "successors",
  "technologies",
  "unlocked_recipes",
  "unknown_fields",
  "unknowns",
}

local function trim(value)
  return string.match(value or "", "^%s*(.-)%s*$")
end

local function valid_request_id(value)
  return type(value) == "string"
    and value ~= ""
    and #value <= MAX_REQUEST_ID_LENGTH
    and value:match("^[%w_-]+$") ~= nil
end

local function as_number(value)
  if type(value) ~= "number" then return nil end
  if value ~= value or value == math.huge or value == -math.huge then return nil end
  return value
end

local function as_integer(value)
  local number = as_number(value)
  if not number then return nil end
  return math.floor(number)
end

local function as_string(value)
  if type(value) ~= "string" or value == "" then return nil end
  return value
end

local function as_boolean(value)
  if type(value) ~= "boolean" then return nil end
  return value
end

local function safe_read(object, key)
  if object == nil then return nil, "unavailable" end
  local ok, value = pcall(function() return object[key] end)
  if not ok then return nil, "unavailable" end
  return value, nil
end

local function safe_call(object, method)
  if object == nil then return nil end
  local ok, value = pcall(function()
    local callable = object[method]
    if type(callable) ~= "function" then return nil end
    return callable()
  end)
  if not ok then return nil end
  return value
end

-- The live API returns LuaCustomTable collections (prototypes.entity, force.technologies)
-- and prototype objects as userdata, not Lua tables; stub tests use tables.
local function is_object(value)
  local kind = type(value)
  return kind == "table" or kind == "userdata"
end

local function iterable(value)
  if type(value) == "table" then return true end
  return type(value) == "userdata" and (pcall(pairs, value))
end

local function sorted_keys(map)
  local keys = {}
  if not iterable(map) then return keys end
  for key, _ in pairs(map) do
    if type(key) == "string" and key ~= "" then keys[#keys + 1] = key end
  end
  table.sort(keys)
  return keys
end

local function enabled_keys(map)
  local keys = {}
  if not iterable(map) then return keys end
  for key, value in pairs(map) do
    if type(key) == "string" and key ~= "" and value == true then keys[#keys + 1] = key end
  end
  table.sort(keys)
  return keys
end

local function ordered_values(array)
  local indexed = {}
  if not iterable(array) then return {} end
  for key, value in pairs(array) do
    indexed[#indexed + 1] = { key = key, value = value }
  end
  table.sort(indexed, function(left, right)
    if type(left.key) == "number" and type(right.key) == "number" then
      return left.key < right.key
    end
    return tostring(left.key) < tostring(right.key)
  end)
  local values = {}
  for _, entry in ipairs(indexed) do values[#values + 1] = entry.value end
  return values
end

local function add_unknown(list, message)
  if message and message ~= "" then list[#list + 1] = message end
end

local function read_name_map(map)
  return sorted_keys(map)
end

-- Old exports silently treated an unreadable prerequisite map as empty.
-- Record whether the complete force map and every named edge were readable.
local function technology_graph_status(force, exported_count)
  local ok, complete = pcall(function()
    local technologies = force.technologies
    if not iterable(technologies) then return false end
    local count = 0
    for name, technology in pairs(technologies) do
      if type(name) ~= "string" or name == "" or not technology or technology.name ~= name then return false end
      count = count + 1
      local prerequisites = technology.prerequisites
      if not iterable(prerequisites) then return false end
      for dependency, target in pairs(prerequisites) do
        if type(dependency) ~= "string" or dependency == "" or not target or target.name ~= dependency
            or not technologies[dependency] or technologies[dependency].name ~= dependency then return false end
      end
    end
    return count > 0 and count == exported_count
  end)
  return { version = "runtime-technology-graph-v1", complete = ok and complete == true,
           technology_count = exported_count }
end

local function read_bbox(value)
  if type(value) ~= "table" then return nil end
  local left_top = value.left_top or value[1]
  local right_bottom = value.right_bottom or value[2]
  if type(left_top) ~= "table" or type(right_bottom) ~= "table" then return nil end
  local min_x = as_number(left_top.x or left_top[1])
  local min_y = as_number(left_top.y or left_top[2])
  local max_x = as_number(right_bottom.x or right_bottom[1])
  local max_y = as_number(right_bottom.y or right_bottom[2])
  if not min_x or not min_y or not max_x or not max_y then return nil end
  return { min_x = min_x, min_y = min_y, max_x = max_x, max_y = max_y }
end

local function direction_name(value)
  local directions = defines and defines.direction
  if type(directions) ~= "table" then return nil end
  local matches = {}
  for name, number in pairs(directions) do
    if number == value and type(name) == "string" and name ~= "" then
      matches[#matches + 1] = name
    end
  end
  table.sort(matches)
  return matches[1]
end

local function read_scalar_details(source, omitted)
  local details = {}
  local unknowns = {}
  if type(source) ~= "table" then return details, unknowns end
  for key, value in pairs(source) do
    if type(key) == "string" and not (omitted and omitted[key]) then
      local kind = type(value)
      if kind == "string" or kind == "number" or kind == "boolean" then
        details[key] = value
      else
        add_unknown(unknowns, "field " .. key .. " is not a scalar and was not exported")
      end
    end
  end
  return details, unknowns
end

local function read_trigger(trigger)
  if type(trigger) ~= "table" then return nil, nil end
  local details, unknowns = read_scalar_details(trigger, { type = true })
  local trigger_type = as_string(trigger.type) or "unknown"
  return { type = trigger_type, details = details }, unknowns
end

local function read_research_ingredients(list, unknowns)
  local ingredients = {}
  for _, ingredient in ipairs(ordered_values(list)) do
    if type(ingredient) == "table" then
      local name = as_string(ingredient.name)
      local amount = as_number(ingredient.amount)
      if name and amount then
        ingredients[#ingredients + 1] = { name = name, kind = "item", amount = amount }
      elseif name then
        add_unknown(unknowns, "research ingredient " .. name .. " amount unavailable")
      else
        add_unknown(unknowns, "research ingredient identity unavailable")
      end
    else
      add_unknown(unknowns, "research ingredient is not a table")
    end
  end
  table.sort(ingredients, function(left, right)
    if left.name == right.name then return left.amount < right.amount end
    return left.name < right.name
  end)
  return ingredients
end

local function read_effect(effect, force)
  if type(effect) ~= "table" then
    return {
      type = "unknown",
      handled = false,
      details = {},
      unsupported_reason = "effect entry is not a table",
    }
  end
  local effect_type = as_string(effect.type) or "unknown"
  local details, unknowns = read_scalar_details(effect, { type = true })
  if effect_type == "unlock-recipe" then details.recipe = nil end
  local result = {
    type = effect_type,
    handled = false,
    details = details,
  }
  local reason = "effect type is preserved as scalar details and not interpreted"
  if #unknowns > 0 then reason = reason .. "; " .. table.concat(unknowns, "; ") end
  result.unsupported_reason = reason

  if effect_type == "unlock-recipe" then
    local recipe = as_string(effect.recipe)
    if recipe then
      result.handled = true
      result.recipe = recipe
      result.unsupported_reason = nil
      local force_recipe = force and force.recipes and force.recipes[recipe] or nil
      if force_recipe ~= nil then
        result.recipe_enabled = force_recipe.enabled == true
      end
    else
      result.unsupported_reason = "unlock-recipe effect has no recipe name"
    end
  end
  return result
end

local function read_technology(technology, force)
  local unknowns = {}
  local prototype = technology.prototype
  local prototype_ok = prototype ~= nil

  local name = as_string(technology.name) or "unknown"
  local prerequisites = read_name_map(technology.prerequisites)
  local successors = read_name_map(technology.successors)

  local effects = {}
  local unlocked_recipes = {}
  local seen_recipes = {}
  if prototype_ok then
    for _, effect in ipairs(ordered_values(prototype.effects)) do
      local exported = read_effect(effect, force)
      effects[#effects + 1] = exported
      if exported.recipe and not seen_recipes[exported.recipe] then
        seen_recipes[exported.recipe] = true
        unlocked_recipes[#unlocked_recipes + 1] = exported.recipe
      end
    end
  else
    add_unknown(unknowns, "technology prototype unavailable")
  end
  table.sort(unlocked_recipes)

  local unit_count = as_integer(technology.research_unit_count)
  local unit_energy = as_number(technology.research_unit_energy)
  local unit_formula = as_string(technology.research_unit_count_formula)
  if unit_count == nil and prototype_ok then
    unit_count = as_integer(prototype.research_unit_count)
  end
  if unit_energy == nil and prototype_ok then
    unit_energy = as_number(prototype.research_unit_energy)
  end
  if unit_formula == nil and prototype_ok then
    unit_formula = as_string(prototype.research_unit_count_formula)
  end

  -- LuaTechnologyPrototype does not document a unit.time field at runtime.
  -- Probe it only when present; never derive seconds from energy.
  local unit_time = nil
  if prototype_ok then
    local unit, _ = safe_read(prototype, "unit")
    if type(unit) == "table" then
      unit_time = as_number(unit.time)
    end
  end

  local ingredient_source = technology.research_unit_ingredients
  if type(ingredient_source) ~= "table" and prototype_ok then
    ingredient_source = prototype.research_unit_ingredients
  end
  local ingredients = read_research_ingredients(ingredient_source, unknowns)
  if type(ingredient_source) ~= "table" then
    add_unknown(unknowns, "research ingredients unavailable")
  end
  if unit_count == nil then add_unknown(unknowns, "research unit count unavailable") end
  if unit_energy == nil then add_unknown(unknowns, "research unit energy unavailable") end

  local trigger, trigger_unknowns = nil, nil
  if prototype_ok then
    trigger, trigger_unknowns = read_trigger(prototype.research_trigger)
  end
  for _, message in ipairs(trigger_unknowns or {}) do add_unknown(unknowns, message) end

  local max_level = nil
  local upgrade = nil
  local allows_productivity = nil
  local hidden = nil
  local essential = nil
  local visible_when_disabled = nil
  if prototype_ok then
    max_level = as_integer(prototype.max_level)
    upgrade = prototype.upgrade
    if type(upgrade) ~= "boolean" then upgrade = nil end
    allows_productivity = prototype.allows_productivity
    if type(allows_productivity) ~= "boolean" then allows_productivity = nil end
    hidden = prototype.hidden
    if type(hidden) ~= "boolean" then hidden = nil end
    essential = prototype.essential
    if type(essential) ~= "boolean" then essential = nil end
    visible_when_disabled = prototype.visible_when_disabled
    if type(visible_when_disabled) ~= "boolean" then visible_when_disabled = nil end
  end

  return {
    name = name,
    researched = technology.researched == true,
    enabled = technology.enabled == true,
    level = as_integer(technology.level),
    max_level = max_level,
    upgrade = upgrade,
    allows_productivity = allows_productivity,
    hidden = hidden,
    essential = essential,
    visible_when_disabled = visible_when_disabled,
    prerequisites = prerequisites,
    successors = successors,
    research = {
      unit_count = unit_count,
      unit_count_formula = unit_formula,
      unit_energy = unit_energy,
      unit_time_seconds = unit_time,
      ingredients = ingredients,
      trigger = trigger,
    },
    effects = effects,
    unlocked_recipes = unlocked_recipes,
    unknowns = unknowns,
  }
end

local function read_fluid_ref(fluid)
  if fluid == nil then return nil end
  if type(fluid) == "string" then
    return { name = fluid, kind = "fluid" }
  end
  local name = as_string(fluid.name)
  if not name then return nil end
  return { name = name, kind = "fluid" }
end

local function read_pipe_connection(connection, unknowns)
  if type(connection) ~= "table" then return {} end
  local result = {}
  local positions = {}
  local source = connection.positions
  local valid_positions = type(source) == "table"
  if valid_positions then
    for key, _ in pairs(source) do
      if type(key) ~= "number" or key % 1 ~= 0 or key < 1 or key > 4 then
        valid_positions = false
      end
    end
  end
  for index = 1, 4 do
    local position = valid_positions and source[index] or nil
    if type(position) ~= "table" then
      positions = {}
      break
    end
    local x = as_number(position.x or position[1])
    local y = as_number(position.y or position[2])
    if not x or not y then
      positions = {}
      break
    end
    positions[#positions + 1] = { x = x, y = y }
  end
  if #positions == 4 then result.positions = positions
  else add_unknown(unknowns, "pipe connection positions unavailable or incomplete") end
  local direction = as_integer(connection.direction)
  if direction then
    result.direction = direction
    result.direction_name = direction_name(direction)
  end
  local flow_direction = as_string(connection.flow_direction)
  if flow_direction then result.flow_direction = flow_direction end
  local connection_type = as_string(connection.connection_type)
  if connection_type then result.connection_type = connection_type end
  local category = connection.connection_category
  if type(category) == "string" then
    result.connection_category = { category }
  elseif type(category) == "table" then
    local categories = {}
    for _, value in pairs(category) do
      local item = as_string(value)
      if item then categories[#categories + 1] = item end
    end
    table.sort(categories)
    result.connection_category = categories
  end
  local max_distance = as_integer(connection.max_underground_distance)
  if max_distance then result.max_underground_distance = max_distance end
  return result
end

local function read_fluidbox(box, unknowns)
  if type(box) ~= "table" then
    add_unknown(unknowns, "fluidbox entry is not a table")
    return nil
  end
  local result = {}
  local index = as_integer(box.index)
  if index then result.index = index end
  local production_type = as_string(box.production_type)
  if production_type then result.production_type = production_type end
  local volume = as_number(safe_read(box, "volume"))
  if volume then result.volume = volume end
  local filter = read_fluid_ref(box.filter)
  if filter then result.filter = filter end
  local minimum_temperature = as_number(box.minimum_temperature)
  if minimum_temperature then result.minimum_temperature = minimum_temperature end
  local maximum_temperature = as_number(box.maximum_temperature)
  if maximum_temperature then result.maximum_temperature = maximum_temperature end
  result.pipe_connections = {}
  for _, connection in ipairs(ordered_values(box.pipe_connections)) do
    result.pipe_connections[#result.pipe_connections + 1] = read_pipe_connection(connection, unknowns)
  end
  if volume == nil then add_unknown(unknowns, "fluidbox volume unavailable") end
  return result
end

local function read_energy(prototype, unknowns)
  local energy = {}
  local usage = as_number(prototype.energy_usage)
  if usage then energy.usage = usage end
  local max_usage = as_number(safe_call(prototype, "get_max_energy_usage"))
  if max_usage then energy.max_usage = max_usage end
  local passive_usage = as_number(safe_read(prototype, "passive_usage"))
  if passive_usage then energy.passive_usage = passive_usage end
  local max_power_output = as_number(safe_call(prototype, "get_max_power_output"))
  if max_power_output then energy.max_power_output = max_power_output end

  local electric = safe_read(prototype, "electric_energy_source_prototype")
  if electric then
    energy.source = "electric"
    energy.electric = {
      buffer_capacity = as_number(electric.buffer_capacity),
      drain = as_number(electric.drain),
      usage_priority = as_string(electric.usage_priority),
    }
    return energy
  end

  local burner = safe_read(prototype, "burner_prototype")
  if burner then
    energy.source = "burner"
    energy.burner = {
      fuel_categories = sorted_keys(burner.fuel_categories),
      fuel_inventory_size = as_integer(burner.fuel_inventory_size),
      effectivity = as_number(burner.effectivity),
    }
    return energy
  end

  local fluid = safe_read(prototype, "fluid_energy_source_prototype")
  if fluid then
    energy.source = "fluid"
    local fluid_box = is_object(fluid.fluid_box) and fluid.fluid_box or nil
    energy.fluid = {
      burns_fluid = as_boolean(fluid.burns_fluid),
      fluid_usage_per_tick = as_number(fluid.fluid_usage_per_tick),
      effectivity = as_number(fluid.effectivity),
      maximum_temperature = as_number(fluid.maximum_temperature),
      scale_fluid_usage = as_boolean(fluid.scale_fluid_usage),
      filter = fluid_box and read_fluid_ref(fluid_box.filter) or nil,
    }
    return energy
  end

  local heat = safe_read(prototype, "heat_energy_source_prototype")
  if heat then
    energy.source = "heat"
    energy.heat = {
      connection_count = #ordered_values(heat.connections),
      default_temperature = as_number(heat.default_temperature),
      max_temperature = as_number(heat.max_temperature),
      max_transfer = as_number(heat.max_transfer),
      specific_heat = as_number(heat.specific_heat),
    }
    return energy
  end

  local void_source = safe_read(prototype, "void_energy_source_prototype")
  if void_source then
    energy.source = "void"
    energy.void = true
    return energy
  end

  if next(energy) == nil then return nil end
  energy.source = nil
  add_unknown(unknowns, "energy source unavailable while direct energy usage is present")
  return energy
end

local function read_effect_values(effect)
  if type(effect) ~= "table" then return nil end
  local result = {}
  for key, value in pairs(effect) do
    local number = as_number(value)
    if type(key) == "string" and number then result[key] = number end
  end
  if next(result) == nil then return nil end
  return result
end

local function read_modules(prototype, unknowns)
  local inventory_size = as_integer(safe_read(prototype, "module_inventory_size"))
  local allowed_effects = enabled_keys(safe_read(prototype, "allowed_effects"))
  local allowed_categories = enabled_keys(safe_read(prototype, "allowed_module_categories"))
  local receiver = safe_read(prototype, "effect_receiver")
  local receiver_data = nil
  if type(receiver) == "table" then
    local uses_beacon = receiver.uses_beacon_effects
    local uses_module = receiver.uses_module_effects
    local uses_surface = receiver.uses_surface_effects
    receiver_data = {
      base_effect = read_effect_values(receiver.base_effect) or {},
      uses_beacon_effects = as_boolean(uses_beacon),
      uses_module_effects = as_boolean(uses_module),
      uses_surface_effects = as_boolean(uses_surface),
    }
  end
  if inventory_size == nil and #allowed_effects == 0 and #allowed_categories == 0
    and receiver_data == nil then
    return nil
  end
  if inventory_size == nil then
    add_unknown(unknowns, "module inventory size unavailable")
  end
  return {
    inventory_size = inventory_size,
    inventory_size_supported = inventory_size ~= nil,
    allowed_effects = allowed_effects,
    allowed_module_categories = allowed_categories,
    effect_receiver = receiver_data,
  }
end

local function read_placement(prototype, force, unknowns)
  local items = {}
  local source = safe_read(prototype, "items_to_place_this")
  local valid = type(source) == "table"
  local count = 0
  if valid then
    for key, _ in pairs(source) do
      if type(key) ~= "number" or key < 1 or key % 1 ~= 0 then valid = false end
      count = count + 1
    end
    for index = 1, count do
      local item = source[index]
      local name = type(item) == "table" and as_string(item.name) or nil
      local quantity = type(item) == "table" and as_number(item.count) or nil
      if not name or not quantity or quantity < 1 or quantity % 1 ~= 0 then
        valid = false
      else
        items[#items + 1] = { name = name, count = quantity }
      end
    end
  end
  if not valid then
    items = {}
    add_unknown(unknowns, "placement items unavailable or malformed")
  end
  local placement = { items = items }
  if #items > 0 and force and force.recipes then
    local first = items[1]
    local recipe = force.recipes[first.name]
    if recipe ~= nil then
      placement.recipe = { name = first.name, enabled = recipe.enabled == true }
    end
  end
  return placement
end

local function read_entity(prototype, force)
  local unknowns = {}
  local entity_type = as_string(prototype.type) or "unknown"
  local result = {
    name = as_string(prototype.name) or "unknown",
    type = entity_type,
    placement = read_placement(prototype, force, unknowns),
    fluidboxes = {},
    unknown_fields = unknowns,
  }

  local tile_width = as_integer(prototype.tile_width)
  local tile_height = as_integer(prototype.tile_height)
  if tile_width then result.tile_width = tile_width else add_unknown(unknowns, "tile_width unavailable") end
  if tile_height then result.tile_height = tile_height else add_unknown(unknowns, "tile_height unavailable") end
  local collision_box = read_bbox(prototype.collision_box)
  if collision_box then result.collision_box = collision_box
  else add_unknown(unknowns, "collision_box unavailable") end
  local selection_box = read_bbox(prototype.selection_box)
  if selection_box then result.selection_box = selection_box end

  local supports_direction = prototype.supports_direction
  if type(supports_direction) == "boolean" then result.supports_direction = supports_direction end
  local max_underground_distance = as_integer(safe_read(prototype, "max_underground_distance"))
  if max_underground_distance then result.max_underground_distance = max_underground_distance end
  local fast_replaceable_group = as_string(prototype.fast_replaceable_group)
  if fast_replaceable_group then result.fast_replaceable_group = fast_replaceable_group end
  local next_upgrade = safe_read(prototype, "next_upgrade")
  if next_upgrade ~= nil then
    local name = as_string(safe_read(next_upgrade, "name"))
    if name then result.next_upgrade = name end
  end

  local categories = safe_read(prototype, "crafting_categories")
  if type(categories) == "table" then
    local speed = as_number(safe_call(prototype, "get_crafting_speed"))
    if speed == nil then add_unknown(unknowns, "crafting speed unavailable") end
    result.crafting = {
      categories = sorted_keys(categories),
      speed = speed,
      ingredient_count = as_integer(safe_read(prototype, "ingredient_count")),
      fixed_recipe = as_string(safe_read(safe_read(prototype, "fixed_recipe"), "name")),
    }
  end

  local modules = read_modules(prototype, unknowns)
  if modules then result.modules = modules end

  local energy = read_energy(prototype, unknowns)
  if energy then result.energy = energy end

  for _, box in ipairs(ordered_values(prototype.fluidbox_prototypes)) do
    local exported = read_fluidbox(box, unknowns)
    if exported then result.fluidboxes[#result.fluidboxes + 1] = exported end
  end
  table.sort(result.fluidboxes, function(left, right)
    if left.index and right.index then return left.index < right.index end
    return (left.index or 0) < (right.index or 0)
  end)

  local lab_inputs = safe_read(prototype, "lab_inputs")
  if entity_type == "lab" or type(lab_inputs) == "table" then
    local inputs = {}
    for _, input in ipairs(ordered_values(lab_inputs)) do
      local name = as_string(input)
      if name then inputs[#inputs + 1] = name end
    end
    table.sort(inputs)
    result.lab = {
      inputs = inputs,
      researching_speed = as_number(safe_call(prototype, "get_researching_speed")),
    }
  end

  if entity_type == "beacon" then
    result.beacon = {
      supply_area_distance = as_number(safe_call(prototype, "get_supply_area_distance")),
      distribution_effectivity = as_number(prototype.distribution_effectivity),
    }
  end

  if entity_type == "electric-pole" then
    -- State the quality used: pole reach can depend on quality. A missing or
    -- unreadable fact remains unknown rather than falling back to base values.
    local wire_ok, wire = pcall(function() return prototype.get_max_wire_distance("normal") end)
    local supply_ok, supply = pcall(function() return prototype.get_supply_area_distance("normal") end)
    wire = wire_ok and as_number(wire) or nil
    supply = supply_ok and as_number(supply) or nil
    if wire and wire >= 0 and supply and supply >= 0 then
      result.electric_pole = { quality = "normal", max_wire_distance = wire, supply_area_distance = supply }
    else
      add_unknown(unknowns, "normal-quality electric pole reach unavailable")
    end
  end

  if entity_type == "mining-drill" then
    result.mining = {
      mining_speed = as_number(prototype.mining_speed),
      resource_categories = sorted_keys(prototype.resource_categories),
    }
  end

  if entity_type == "offshore-pump" or entity_type == "pump" then
    result.pump = { pumping_speed = as_number(safe_call(prototype, "get_pumping_speed")) }
  end

  if #unknowns == 0 then result.unknown_fields = {} end
  return result
end

-- Engine-ordered fluid parts let consumers derive crafting-machine port
-- identity. Any unreadable recipe omits the whole section, so consumers see
-- "unknown" rather than "no fluid recipes". A zero/absent fluidbox_index is
-- the engine's automatic assignment and is exported as absent.
local function read_fluid_parts(parts)
  if parts == nil then return {} end
  if type(parts) ~= "table" then error("recipe parts unreadable") end
  local count = #parts
  for key, _ in pairs(parts) do
    if type(key) ~= "number" or key % 1 ~= 0 or key < 1 or key > count then
      error("recipe parts are not an array")
    end
  end
  local result = {}
  for index = 1, count do
    local part = parts[index]
    local kind = type(part) == "table" and part.type or nil
    if kind ~= "item" and kind ~= "fluid" and kind ~= "research-progress" then
      error("recipe part type unreadable")
    end
    if kind == "fluid" then
      local name = as_string(part.name)
      if not name then error("fluid part name unreadable") end
      local entry = { name = name }
      local fluidbox_index = part.fluidbox_index
      if fluidbox_index ~= nil then
        if type(fluidbox_index) ~= "number" or fluidbox_index % 1 ~= 0 or fluidbox_index < 0 then
          error("fluidbox_index unreadable")
        end
        if fluidbox_index > 0 then entry.fluidbox_index = fluidbox_index end
      end
      result[#result + 1] = entry
    end
  end
  return result
end

local function read_recipe_fluid_boxes(force)
  local names = {}
  for name, _ in pairs(force.recipes) do
    if type(name) ~= "string" or name == "" then error("recipe name unreadable") end
    names[#names + 1] = name
  end
  table.sort(names)
  local rows = {}
  for _, name in ipairs(names) do
    local recipe = force.recipes[name]
    local ingredients = read_fluid_parts(recipe.ingredients)
    local products = read_fluid_parts(recipe.products)
    if #ingredients > 0 or #products > 0 then
      rows[#rows + 1] = { name = name, ingredients = ingredients, products = products }
    end
  end
  return rows
end

local function read_fluids(prototypes_table)
  local fluids = {}
  if not iterable(prototypes_table) then return fluids end
  for name, prototype in pairs(prototypes_table) do
    if type(name) == "string" and name ~= "" and is_object(prototype) then
      fluids[#fluids + 1] = {
        name = name,
        default_temperature = as_number(prototype.default_temperature),
        max_temperature = as_number(prototype.max_temperature),
        heat_capacity = as_number(prototype.heat_capacity),
        fuel_value = as_number(prototype.fuel_value),
        gas_temperature = as_number(prototype.gas_temperature),
        emissions_multiplier = as_number(prototype.emissions_multiplier),
        hidden = as_boolean(prototype.hidden),
      }
    end
  end
  table.sort(fluids, function(left, right) return left.name < right.name end)
  return fluids
end

local function read_active_mods()
  local active_mods = script.active_mods
  if type(active_mods) ~= "table" then
    error("Active mod list is unavailable")
  end
  local mods = {}
  local names = {}
  for name, version in pairs(active_mods) do
    if type(name) ~= "string" or name == "" or type(version) ~= "string" or version == "" then
      error("Active mod list is malformed")
    end
    mods[name] = version
    names[#names + 1] = name
  end
  table.sort(names)
  local signature_parts = {}
  for _, name in ipairs(names) do signature_parts[#signature_parts + 1] = name .. "@" .. mods[name] end
  return mods, #names, table.concat(signature_parts, ";"), mods["base"]
end

local function read_research_ref(technology)
  if technology == nil then return nil end
  local name = as_string(technology.name)
  if not name then return nil end
  return { name = name, level = as_integer(technology.level) }
end

local function build_force_state(force)
  local queue = {}
  for _, technology in ipairs(ordered_values(force.research_queue)) do
    local entry = read_research_ref(technology)
    if entry then queue[#queue + 1] = entry end
  end
  local bonuses = {}
  for _, field in ipairs(FORCE_BONUS_FIELDS) do
    local value = as_number(force[field])
    if value then bonuses[field] = value end
  end
  return {
    name = as_string(force.name) or "unknown",
    current_research = read_research_ref(force.current_research),
    previous_research = read_research_ref(force.previous_research),
    research_progress = as_number(force.research_progress) or 0,
    research_queue = queue,
    research_enabled = force.research_enabled == true,
    bonuses = bonuses,
  }
end

local function build_catalog(payload)
  local force_name = as_string(payload.force) or "player"
  local force = game.forces[force_name]
  if not force or force.valid == false then
    error("Unknown force: " .. force_name)
  end
  local surface_name = as_string(payload.surface)
  if surface_name then
    local surface = game.surfaces[surface_name]
    if not surface or surface.valid == false then
      error("Unknown surface: " .. surface_name)
    end
  end
  if type(game.tick) ~= "number" then
    error("Runtime tick is unavailable")
  end

  local active_mods, active_mods_count, mod_signature, base_mod_version = read_active_mods()
  local technologies = {}
  for _, technology in ipairs(ordered_values(force.technologies)) do
    technologies[#technologies + 1] = read_technology(technology, force)
  end
  table.sort(technologies, function(left, right) return left.name < right.name end)

  local entities = {}
  for _, prototype in ipairs(ordered_values(prototypes.entity)) do
    local entity_type = prototype and prototype.type
    if ENTITY_TYPES[entity_type] then
      entities[#entities + 1] = read_entity(prototype, force)
    end
  end
  table.sort(entities, function(left, right)
    if left.type == right.type then return left.name < right.name end
    return left.type < right.type
  end)

  local recipe_ok, recipe_fluid_boxes = pcall(read_recipe_fluid_boxes, force)
  if not recipe_ok then recipe_fluid_boxes = nil end

  local entity_types = {}
  for entity_type, _ in pairs(ENTITY_TYPES) do entity_types[#entity_types + 1] = entity_type end
  table.sort(entity_types)

  local unknowns = {
    "Effects other than unlock-recipe are preserved as scalar details and marked handled=false; they are not interpreted.",
    "Energy values are exported as runtime API doubles; this exporter does not assert joule/second conversion.",
    "Engine version is not exposed to Lua; base_mod_version is the base mod version, not the engine version.",
    "Entity scope is the explicit selected prototype type list; unlisted entity types are not exported.",
    "Placement availability is only the first placement item with a force recipe; it is not a full buildability check.",
    "Quality-specific prototype variants are not exported; runtime attributes describe normal quality unless a get_* method was called.",
    "Recipe definitions are not duplicated; only engine-ordered fluid parts and fluidbox_index are exported, use export_recipe_catalog for recipe definitions.",
    "Recipe-level mechanics such as catalysts, probabilistic or variable products, and temperature constraints are not interpreted here.",
    "Research unit wall-clock time is exported only when the runtime prototype exposes unit.time; unit_energy is captured separately.",
    "Technology and prototype facts are global to the loaded mod set; surface is recorded only as the requested scope.",
  }
  if not recipe_ok then
    unknowns[#unknowns + 1] = "Recipe fluid parts were unreadable; recipe_fluid_boxes is omitted and port assignment is unknown."
  end
  table.sort(unknowns)

  return {
    schema_version = SCHEMA_VERSION,
    kind = KIND,
    ok = true,
    tick = game.tick,
    provenance = {
      export_command = "export_runtime_catalog",
      export_tick = game.tick,
      active_mods = active_mods,
      active_mods_count = active_mods_count,
      mod_signature = mod_signature,
      scope = {
        force = force.name,
        surface = surface_name or "unspecified",
        surface_requested = surface_name ~= nil,
        technology_scope = "all_force_technologies",
        entity_scope = "selected_prototype_types",
        entity_types = entity_types,
        fluid_scope = "all_loaded_fluids",
        recipe_scope = recipe_ok and "unlocks_and_fluid_ports" or "unlocks_only",
      },
      engine_version = {
        available = false,
        reason = "Factorio does not expose the engine version to Lua",
        base_mod_version = base_mod_version,
      },
    },
    force_state = build_force_state(force),
    technology_graph = technology_graph_status(force, #technologies),
    technologies = technologies,
    entities = entities,
    fluids = read_fluids(prototypes.fluid),
    recipe_fluid_boxes = recipe_fluid_boxes,
    unknowns = unknowns,
  }
end

local function parse_payload(parameter)
  local text = trim(parameter)
  if text == "" then return {}, nil end
  if text:sub(1, 1) ~= "{" then
    return { force = text }, nil
  end

  local ok, payload = pcall(function()
    return helpers.json_to_table(text)
  end)
  if not ok or type(payload) ~= "table" then
    return nil, "Invalid runtime-catalog payload JSON"
  end

  local allowed = { force = true, surface = true, request_id = true }
  for key, _ in pairs(payload) do
    if type(key) ~= "string" or not allowed[key] then
      return payload, "Runtime-catalog payload has an unsupported field"
    end
  end
  if payload.force ~= nil and as_string(payload.force) == nil then
    return payload, "Runtime-catalog force must be a non-empty string"
  end
  if payload.surface ~= nil and as_string(payload.surface) == nil then
    return payload, "Runtime-catalog surface must be a non-empty string"
  end
  if payload.request_id ~= nil and not valid_request_id(payload.request_id) then
    return payload, "Runtime-catalog request_id must contain 1-64 filename-safe characters"
  end
  return payload, nil
end

local function failure_report(payload, message)
  return {
    schema_version = SCHEMA_VERSION,
    kind = KIND,
    ok = false,
    tick = type(game.tick) == "number" and game.tick or 0,
    error = tostring(message),
  }
end

local function write_report(payload, report)
  local suffix = valid_request_id(payload and payload.request_id)
    and payload.request_id or tostring(game.tick)
  if valid_request_id(payload and payload.request_id) then
    report.request_id = payload.request_id
  end
  local path = REPORT_DIRECTORY .. "runtime_catalog_" .. suffix .. ".json"
  local json = helpers.table_to_json(report)
  for _, key in ipairs(ARRAY_KEYS) do
    json = json:gsub('"' .. key .. '":{}', '"' .. key .. '":[]')
  end
  helpers.write_file(path, json, false)
  rcon.print("runtime_catalog=" .. path)
end

commands.add_command(
  "export_runtime_catalog",
  "Export read-only runtime technologies, unlocks, and selected entity facts. Parameter: optional existing force name or JSON {force?,surface?,request_id?}.",
  function(command)
    local payload, parse_err = parse_payload(command.parameter)
    local report
    if parse_err then
      report = failure_report(payload, parse_err)
    else
      local ok, result = pcall(function()
        return build_catalog(payload)
      end)
      if ok then
        report = result
      else
        report = failure_report(payload, result)
      end
    end
    write_report(payload, report)
  end
)
