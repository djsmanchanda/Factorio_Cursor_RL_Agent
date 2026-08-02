-- Path: factorio_mod/recipe_catalog.lua
-- Purpose: Export all recipe knowledge plus force unlock state and environmental acquisition leaves in stable order.

local shared = require("sandbox_shared")

local function part_contract(part)
  local amount = part.amount
  local unsupported = nil
  if part.probability and part.probability ~= 1 then
    unsupported = "probabilistic product"
  elseif part.amount_min or part.amount_max then
    unsupported = "variable product amount"
  elseif part.temperature or part.minimum_temperature or part.maximum_temperature then
    unsupported = "temperature-constrained fluid"
  elseif part.catalyst_amount and part.catalyst_amount ~= 0 then
    unsupported = "catalyst amount"
  elseif not amount or amount <= 0 then
    unsupported = "missing positive deterministic amount"
  end
  return { name = part.name, type = part.type or "item", amount = amount or 0 }, unsupported
end

local function sorted_parts(parts)
  local result = {}
  local reasons = {}
  for _, part in pairs(parts or {}) do
    local contract, reason = part_contract(part)
    table.insert(result, contract)
    if reason then table.insert(reasons, reason .. ": " .. tostring(part.name)) end
  end
  table.sort(result, function(a, b)
    if a.name == b.name then return a.type < b.type end
    return a.name < b.name
  end)
  table.sort(reasons)
  return result, reasons
end

local function export_force(command)
  local name = string.match(command.parameter or "", "^%s*(.-)%s*$")
  if name == "" then return shared.get_or_create_planner_force() end
  local force = game.forces[name]
  if not force then error("force does not exist: " .. name) end
  return force
end

commands.add_command("export_recipe_catalog", "Export all recipes with unlock state; optional existing force name.", function(command)
  local force = export_force(command)
  local recipes = {}
  local names = {}
  for name, recipe in pairs(force.recipes) do
    -- Skip product-less entries: Factorio's blueprint-parameter placeholders
    -- (parameter-0..9) and recipe-unknown are enabled but craft nothing, so
    -- they can never contribute to a production chain and only break the
    -- catalog's non-empty products contract.
    if #recipe.products > 0 then table.insert(names, name) end
  end
  table.sort(names)
  for _, name in ipairs(names) do
    local recipe = force.recipes[name]
    local ingredients, ingredient_reasons = sorted_parts(recipe.ingredients)
    local products, product_reasons = sorted_parts(recipe.products)
    local reasons = {}
    for _, reason in ipairs(ingredient_reasons) do table.insert(reasons, reason) end
    for _, reason in ipairs(product_reasons) do table.insert(reasons, reason) end
    local ticks = math.max(1, math.floor((recipe.energy or 0.5) * 60 + 0.5))
    local entry = {
      name = name, enabled = recipe.enabled, category = recipe.category or "crafting",
      energy_ticks = ticks, ingredients = ingredients, products = products,
      supported = #reasons == 0,
    }
    if #reasons > 0 then entry.unsupported_reason = table.concat(reasons, "; ") end
    table.insert(recipes, entry)
  end
  local raw_set = { water = true, wood = true }
  local environmental_source_types = {
    resource = true, tree = true, plant = true, fish = true,
  }
  local function add_mineable_products(prototype)
    if not prototype.mineable_properties then return end
    for _, product in pairs(prototype.mineable_properties.products or {}) do
      raw_set[product.name] = true
    end
  end
  for _, prototype in pairs(prototypes.entity) do
    if environmental_source_types[prototype.type] then
      add_mineable_products(prototype)
    end
  end
  for _, prototype in pairs(prototypes.asteroid_chunk) do
    add_mineable_products(prototype)
  end
  local raw_resources = {}
  for name, _ in pairs(raw_set) do table.insert(raw_resources, name) end
  table.sort(raw_resources)
  -- Crafting machines, with the two facts that decide which tier a recipe can
  -- run on: how many ingredient slots it has, and which categories it accepts.
  -- Exported rather than assumed because guessing the slot count assigns a
  -- three-ingredient recipe to a machine that cannot hold it, and the whole
  -- line then fails to set its recipe.
  local machines = {}
  for _, prototype in pairs(prototypes.entity) do
    if prototype.type == "assembling-machine" or prototype.type == "furnace" then
      local categories = {}
      for category, _ in pairs(prototype.crafting_categories or {}) do
        table.insert(categories, category)
      end
      table.sort(categories)
      table.insert(machines, {
        name = prototype.name,
        type = prototype.type,
        ingredient_count = prototype.ingredient_count or 0,
        crafting_speed = prototype.get_crafting_speed and prototype.get_crafting_speed() or 0,
        categories = categories,
      })
    end
  end
  table.sort(machines, function(a, b) return a.name < b.name end)
  local payload = { version = "1.1.0", force = force.name, tick = game.tick, raw_resources = raw_resources, recipes = recipes, machines = machines }
  local path = "factorio_mod/recipe_catalogs/recipe_catalog_" .. game.tick .. ".json"
  local json = helpers.table_to_json(payload)
  -- An EMPTY Lua table serialises to `{}` (object), not `[]` (array), so any
  -- ingredient-less or product-less recipe (biter-egg, the parameter-N
  -- placeholders) breaks schema validation. Same fix as layout_executor.lua
  -- and live_execution.lua already apply to their own list fields.
  json = json:gsub('"ingredients":{}', '"ingredients":[]')
  json = json:gsub('"products":{}', '"products":[]')
  json = json:gsub('"categories":{}', '"categories":[]')
  json = json:gsub('"machines":{}', '"machines":[]')
  helpers.write_file(path, json, false)
  rcon.print("recipe_catalog=" .. path)
end)
