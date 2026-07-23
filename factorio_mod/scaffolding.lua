-- Path: factorio_mod/scaffolding.lua
-- Purpose: Provision idempotent bot/material scaffolding around preplanned infrastructure.



local shared = require("sandbox_shared")
local get_or_create_sandbox_surface = shared.get_or_create_sandbox_surface
local get_or_create_planner_force = shared.get_or_create_planner_force
local find_exact_entity = shared.find_exact_entity

local function ensure_entity(surface, force, name, position)
  local point = { x = position.x or position[1], y = position.y or position[2] }
  local existing = find_exact_entity(surface, force, name, point)
  if existing then
    return existing, false
  end
  return surface.create_entity({ name = name, position = point, force = force }), true
end

local function ensure_scaffolding(payload)
  local surface = get_or_create_sandbox_surface()
  local force = get_or_create_planner_force()
  local anchors = payload.anchors
  if type(anchors) ~= "table" or #anchors == 0 then
    error("Scaffolding payload must include anchors array")
  end

  local managed = payload.managed_infrastructure == true
  if not managed and payload.legacy_infrastructure ~= true then
    error("Scaffolding mode must be explicit: managed_infrastructure or legacy_infrastructure")
  end
  local created = 0
  local infrastructure_created = 0
  local bots_inserted = 0
  local bots_target = tonumber(payload.bots_per_roboport) or 30
  local inserted = {}

  for _, anchor in ipairs(anchors) do
    local x = tonumber(anchor.x or anchor)
    if x == nil then
      error("Scaffolding anchor must be a number or an object with x")
    end
    local y = -3
    if type(anchor) == "table" and anchor.y ~= nil then
      y = tonumber(anchor.y)
      if y == nil then
        error("Scaffolding anchor y must be a number")
      end
    end

    local roboport
    if managed then
      roboport = find_exact_entity(surface, force, "roboport", { x = x, y = y })
      if not roboport then
        error("Managed infrastructure requires planner roboport at exact anchor " .. x .. "," .. y)
      end
    else
      local _, new_eei = ensure_entity(
        surface, force, "electric-energy-interface", { x = x - 4, y = y - 5 }
      )
      local _, new_sub = ensure_entity(surface, force, "substation", { x = x, y = y - 4 })
      local new_rp
      roboport, new_rp = ensure_entity(surface, force, "roboport", { x = x, y = y })
      infrastructure_created = infrastructure_created
        + (new_eei and 1 or 0) + (new_sub and 1 or 0) + (new_rp and 1 or 0)
      created = created + (new_eei and 1 or 0) + (new_sub and 1 or 0) + (new_rp and 1 or 0)
    end

    local robot_inventory = roboport.get_inventory(defines.inventory.roboport_robot)
    local have_bots = robot_inventory.get_item_count("construction-robot")
    if have_bots < bots_target then
      local added = roboport.insert({ name = "construction-robot", count = bots_target - have_bots })
      bots_inserted = bots_inserted + added
    end

    local materials = type(anchor) == "table" and anchor.materials or nil
    local needs_chests = not managed or materials ~= nil
      or anchor.provider_position ~= nil or anchor.storage_position ~= nil
    if needs_chests then
      local provider_position = anchor.provider_position or { x = x + 4, y = y - 5 }
      local storage_position = anchor.storage_position or { x = x + 6, y = y - 5 }
      local provider, new_chest = ensure_entity(
        surface, force, "passive-provider-chest", provider_position
      )
      local _, new_storage = ensure_entity(surface, force, "storage-chest", storage_position)
      created = created + (new_chest and 1 or 0) + (new_storage and 1 or 0)

      if materials then
        local network = surface.find_logistic_network_by_position({ x, y }, force)
        if not network then
          error("No planner logistic network at scaffolding anchor " .. x .. "," .. y)
        end
        for item, count in pairs(materials) do
          local target = tonumber(count) or 0
          local have = network.get_item_count(item)
          if target > have then
            local added = provider.insert({ name = item, count = target - have })
            inserted[item] = (inserted[item] or 0) + added
          end
        end
      end
    end
  end

  -- Legacy test-world support. Milestone 3 replaces this with WorldSpec.
  local seeded = 0
  for _, patch in ipairs(payload.ore_patches or {}) do
    local amount = tonumber(patch.amount) or 100000
    for x = math.floor(patch.x1), math.floor(patch.x2) do
      for y = math.floor(patch.y1), math.floor(patch.y2) do
        local existing = surface.find_entities_filtered({
          name = patch.item,
          area = { { x, y }, { x + 1, y + 1 } },
          force = "neutral",
          limit = 1
        })
        if #existing == 0 then
          surface.create_entity({ name = patch.item, position = { x + 0.5, y + 0.5 }, amount = amount })
          seeded = seeded + 1
        end
      end
    end
  end

  return {
    mode = managed and "managed_infrastructure" or "legacy",
    created_entities = created,
    infrastructure_created = infrastructure_created,
    bots_inserted = bots_inserted,
    inserted = inserted,
    seeded_ore_tiles = seeded
  }
end
local function seed_ore_patches(payload)
  local surface = get_or_create_sandbox_surface()
  local patches = payload.ore_patches
  if type(patches) ~= "table" or #patches == 0 then
    error("Ore seeding payload must include a non-empty ore_patches array")
  end

  local seeded = 0
  local by_resource = {}
  for _, patch in ipairs(patches) do
    local item = patch.item
    local x1 = tonumber(patch.x1)
    local y1 = tonumber(patch.y1)
    local x2 = tonumber(patch.x2)
    local y2 = tonumber(patch.y2)
    if type(item) ~= "string" or x1 == nil or y1 == nil or x2 == nil or y2 == nil then
      error("Ore patch entries need item, x1, y1, x2, y2")
    end
    local amount = tonumber(patch.amount) or 100000
    for x = math.floor(x1), math.floor(x2) do
      for y = math.floor(y1), math.floor(y2) do
        local existing = surface.find_entities_filtered({
          name = item,
          area = { { x, y }, { x + 1, y + 1 } },
          force = "neutral",
          limit = 1
        })
        if #existing == 0 then
          surface.create_entity({ name = item, position = { x + 0.5, y + 0.5 }, amount = amount })
          seeded = seeded + 1
          by_resource[item] = (by_resource[item] or 0) + 1
        end
      end
    end
  end

  return { seeded_ore_tiles = seeded, seeded_by_resource = by_resource }
end

commands.add_command("seed_ore_patches", "Idempotently seed resource-entity tiles for surveyed WorldSpec ore patches on planner-sandbox, exactly under the mining rows that reference them.", function(command)
  local ok, payload = pcall(function()
    return helpers.json_to_table(command.parameter or "")
  end)
  if not ok or type(payload) ~= "table" then
    game.print("Ore seeding error: invalid JSON payload")
    return
  end

  local run_ok, result = pcall(function()
    return seed_ore_patches(payload)
  end)

  local report = { tick = game.tick, ok = run_ok }
  if run_ok then
    report.seeded_ore_tiles = result.seeded_ore_tiles
    report.seeded_by_resource = result.seeded_by_resource
  else
    report.error = tostring(result)
  end
  local json = helpers.table_to_json(report)
  helpers.write_file("factorio_mod/ore_seed_reports/ore_seed_" .. game.tick .. ".json", json, false)
end)

commands.add_command("ensure_sandbox_scaffolding", "Idempotently provision power, roboports, bots, and materials on planner-sandbox.", function(command)
  local ok, payload = pcall(function()
    return helpers.json_to_table(command.parameter or "")
  end)
  if not ok or type(payload) ~= "table" then
    game.print("Scaffolding error: invalid JSON payload")
    return
  end

  local run_ok, result = pcall(function()
    return ensure_scaffolding(payload)
  end)

  local report = { tick = game.tick, ok = run_ok }
  if run_ok then
    report.created_entities = result.created_entities
    report.infrastructure_created = result.infrastructure_created
    report.bots_inserted = result.bots_inserted
    report.mode = result.mode
    report.inserted = result.inserted
  else
    report.error = tostring(result)
  end
  local json = helpers.table_to_json(report)
  helpers.write_file("factorio_mod/scaffold_reports/scaffold_" .. game.tick .. ".json", json, false)
end)
