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

local function export_request(command)
  local text = string.match(command.parameter or "", "^%s*(.-)%s*$")
  if text:sub(1, 1) ~= "{" then return text, nil end
  local ok, payload = pcall(function() return helpers.json_to_table(text) end)
  if not ok or type(payload) ~= "table" then error("invalid recipe catalog request") end
  for key, _ in pairs(payload) do
    if key ~= "force" and key ~= "request_id" then error("unsupported recipe catalog field") end
  end
  if type(payload.force) ~= "string" or payload.force == "" then
    error("recipe catalog request requires an existing force")
  end
  if type(payload.request_id) ~= "string" or #payload.request_id < 1 or #payload.request_id > 64
      or not payload.request_id:match("^[%w_-]+$") then
    error("recipe catalog request_id must contain 1-64 filename-safe characters")
  end
  return payload.force, payload.request_id
end

local function export_force(name)
  if name == "" then return shared.get_or_create_planner_force() end
  local force = game.forces[name]
  if not force then error("force does not exist: " .. name) end
  return force
end

local function primary_recipe_category(recipe)
  -- Factorio 2.1 exposes categories (plural) on LuaRecipe; the old singular
  -- LuaRecipe.category field raises and aborts the whole server command.
  local categories = recipe.categories
  if (not categories) and recipe.prototype then
    categories = recipe.prototype.categories
  end
  if type(categories) ~= "table" then return nil end
  local sorted = {}
  local seen = {}
  local count = 0
  for index, category in pairs(categories) do
    if type(index) ~= "number" or index < 1 or index % 1 ~= 0
        or type(category) ~= "string" or category == "" then return nil end
    if seen[category] then return nil end
    seen[category] = true
    table.insert(sorted, category)
    count = count + 1
  end
  for index = 1, count do if categories[index] == nil then return nil end end
  table.sort(sorted)
  return sorted[1], sorted
end

commands.add_command("export_recipe_catalog", "Export recipes; optional existing force or JSON force/request_id.", function(command)
  local force_name, request_id = export_request(command)
  local force = export_force(force_name)
  local active_mods = {}
  local active_mods_count = 0
  if type(script.active_mods) ~= "table" then error("active mod list unavailable") end
  for name, version in pairs(script.active_mods) do
    if type(name) ~= "string" or name == "" or type(version) ~= "string" or version == "" then
      error("active mod list malformed")
    end
    active_mods[name] = version
    active_mods_count = active_mods_count + 1
  end
  if active_mods_count == 0 then error("active mod list empty") end
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
    local category, categories = primary_recipe_category(recipe)
    if not category then table.insert(reasons, "recipe category unavailable or malformed") end
    local ticks = math.max(1, math.floor((recipe.energy or 0.5) * 60 + 0.5))
    local entry = {
      name = name, enabled = recipe.enabled, category = category or "unavailable", categories = categories or {},
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
  -- Stack sizes for everything a recipe produces. "Fill the chest" is only a
  -- real number if the stack size is the game's, not a guess.
  local stack_sizes = {}
  for _, recipe in pairs(recipes) do
    for _, product in pairs(recipe.products or {}) do
      if product.type == "item" and not stack_sizes[product.name] then
        local item = prototypes.item[product.name]
        if item then stack_sizes[product.name] = item.stack_size end
      end
    end
  end
  local payload = { version = "1.3.0", force = force.name, tick = game.tick, active_mods = active_mods, active_mods_count = active_mods_count, raw_resources = raw_resources, recipes = recipes, machines = machines, stack_sizes = stack_sizes }
  if request_id then payload.request_id = request_id end
  local path = "factorio_mod/recipe_catalogs/recipe_catalog_" .. (request_id or game.tick) .. ".json"
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
