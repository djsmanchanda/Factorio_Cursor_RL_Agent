-- Path: factorio_mod/layout_executor.lua
-- Purpose: Execute authorized BuildPlan placements with exact idempotency checks.



local shared = require("sandbox_shared")
local get_or_create_sandbox_surface = shared.get_or_create_sandbox_surface
local get_or_create_planner_force = shared.get_or_create_planner_force
local find_exact_entity = shared.find_exact_entity

local function find_exact_ghost(surface, force, inner_name, position)
  local ghosts = surface.find_entities_filtered({
    name = "entity-ghost",
    area = {
      { position.x - 0.01, position.y - 0.01 },
      { position.x + 0.01, position.y + 0.01 }
    },
    force = force
  })
  for _, ghost in pairs(ghosts) do
    if ghost.position.x == position.x and ghost.position.y == position.y
      and ghost.ghost_name == inner_name then
      return ghost
    end
  end
  return nil
end

local function exact_position_occupants(surface, force, position)
  local exact = {}
  local candidates = surface.find_entities_filtered({
    area = {
      { position.x - 0.01, position.y - 0.01 },
      { position.x + 0.01, position.y + 0.01 }
    },
    force = force
  })
  for _, entity in pairs(candidates) do
    if entity.position.x == position.x and entity.position.y == position.y then
      table.insert(exact, entity)
    end
  end
  return exact
end

local function recipe_name(entity)
  local ok, recipe = pcall(function() return entity.get_recipe() end)
  if not ok then return nil, "recipe_read_failed" end
  return recipe and recipe.name or nil, nil
end

local function infinity_filter_configuration(entity)
  local ok, filter
  if entity.type == "infinity-pipe" then
    ok, filter = pcall(function() return entity.get_infinity_pipe_filter() end)
  else
    ok, filter = pcall(function() return entity.get_infinity_container_filter(1) end)
  end
  if not ok then return nil, "infinity_filter_read_failed" end
  if not filter then return nil, nil end
  return filter, nil
end

local function inventory_limit_details(entity, limit)
  local inventory = entity.get_inventory(defines.inventory.chest)
  local prototype = prototypes.item[limit.name]
  if not inventory then return nil, nil, "inventory_limit_missing_inventory" end
  if not prototype then return nil, nil, "inventory_limit_unknown_item" end
  local group_name = prototype.subgroup and prototype.subgroup.group
    and prototype.subgroup.group.name or ""
  local group_minimums = limit.minimum_stacks_by_group or {}
  local minimum_stacks = tonumber(group_minimums[group_name]) or 1
  local target_stacks = math.ceil(limit.count / prototype.stack_size)
  local growth_stacks = math.max(1, tonumber(limit.growth_stacks) or 2)
  local usable_slots = minimum_stacks
  while target_stacks * 2 > usable_slots and usable_slots < #inventory do
    usable_slots = math.min(#inventory, usable_slots + growth_stacks)
  end
  return inventory, math.min(#inventory + 1, usable_slots + 1), nil
end

-- One chest may feed several machines, so its requests are kept as one NAMED
-- SECTION per machine set rather than merged into a single slot list: the game
-- sums the sections itself, the label says which machines each set belongs to,
-- and re-running a build rewrites only that machine's own section instead of
-- accumulating onto whatever the chest already held.
local function find_section_by_group(sections, group)
  for _, section in pairs(sections.sections) do
    if section.valid and section.group == group then
      return section
    end
  end
  return nil
end

-- A chest placed by create_entity starts with one blank unnamed section. Claim
-- that before adding another, so a single-machine cell ends up with exactly one
-- section rather than an empty one trailing every labelled group.
local function claim_section_for_group(sections, group)
  for _, section in pairs(sections.sections) do
    if section.valid and section.group == "" and section.filters_count == 0 then
      section.group = group
      return section
    end
  end
  local section = sections.add_section()
  section.group = group
  return section
end

local function verify_section_slots(section, requests)
  for index, request in ipairs(requests) do
    local slot = section.get_slot(index)
    local name = slot and slot.value and (slot.value.name or slot.value)
    local count = slot and tonumber(slot.min) or nil
    if name ~= request.name or count ~= tonumber(request.count) then
      error("slot=" .. index .. ",expected=" .. request.name .. ":" .. request.count
        .. ",actual=" .. tostring(name) .. ":" .. tostring(count))
    end
  end
end

local function write_section_slots(section, requests)
  for index, request in ipairs(requests) do
    section.set_slot(index, { value = request.name, min = request.count })
  end
  for index = #requests + 1, section.filters_count do
    section.clear_slot(index)
  end
end

local function clear_logistic_groups(entity, groups)
  local sections = entity.get_logistic_sections()
  if not sections then error("no_logistic_sections") end
  for _, group in ipairs(groups) do
    local section = find_section_by_group(sections, group)
    if section then write_section_slots(section, {}) end
  end
end

-- Which fields mean "this action still has settings to apply to an entity that
-- ALREADY exists". Listed once, because an omission here is silent: the entity
-- is reported already_present, no failure is recorded, and the setting simply
-- never lands -- indistinguishable from the planner never asking for it. Adding
-- a configurable action field means adding it here.
local SETTING_FIELDS = {
  "logistic_request", "logistic_requests", "logistic_sections", "clear_logistic_groups", "inventory_limit",
  "infinity_filter",
}

local function has_settings(action)
  for _, field in ipairs(SETTING_FIELDS) do
    if action[field] ~= nil then return true end
  end
  return false
end

-- A ghost was configured when it was created, so only a real entity needs its
-- recipe reapplied; settings above are reapplied to either.
local function needs_reconfiguration(action, entity)
  if has_settings(action) then return true end
  return action.recipe ~= nil and entity.type ~= "entity-ghost"
end

local function configuration_error(entity, action, direction)
  if direction ~= nil and entity.direction ~= direction then
    return "direction_mismatch"
  end
  if action.underground_type then
    local ok, actual = pcall(function() return entity.belt_to_ground_type end)
    if not ok then return "underground_type_read_failed" end
    if actual ~= action.underground_type then
      return "underground_type_mismatch:expected=" .. action.underground_type
        .. ",actual=" .. tostring(actual)
    end
  end
  if action.recipe then
    local actual, read_error = recipe_name(entity)
    if read_error then return read_error end
    if actual ~= action.recipe then
      return "recipe_mismatch:expected=" .. action.recipe .. ",actual=" .. tostring(actual)
    end
  end
  if action.infinity_filter then
    local filter, read_error = infinity_filter_configuration(entity)
    if read_error then return read_error end
    local actual = filter and (filter.name or (filter.id and filter.id.name)) or nil
    if actual ~= action.infinity_filter then
      return "infinity_filter_mismatch:expected=" .. action.infinity_filter
        .. ",actual=" .. tostring(actual)
    end
    if entity.type == "infinity-pipe" then
      if not filter or filter.mode ~= "at-least" then
        return "infinity_pipe_mode_mismatch"
      end
      local expected_percentage = tonumber(action.fill_percentage) or 1.0
      local actual_percentage = tonumber(filter.percentage)
      if actual_percentage == nil
        or math.abs(actual_percentage - expected_percentage) > 0.000001 then
        return "infinity_filter_percentage_mismatch"
      end
    else
      if not filter or filter.mode ~= "exactly" then
        return "infinity_container_mode_mismatch"
      end
      if tonumber(filter.count) ~= 1000 then
        return "infinity_container_count_mismatch"
      end
      if entity.remove_unfiltered_items ~= true then
        return "infinity_container_remove_unfiltered_items_mismatch"
      end
    end
  end
  if action.inventory_limit then
    local inventory, expected, detail = inventory_limit_details(entity, action.inventory_limit)
    if detail then return detail end
    local ok, actual = pcall(function() return inventory.get_bar() end)
    if not ok then return "inventory_limit_read_failed" end
    if actual ~= expected then
      return "inventory_limit_mismatch:expected=" .. expected .. ",actual=" .. tostring(actual)
    end
  end
  local requests = action.logistic_requests
  if not requests and action.logistic_request then
    requests = { action.logistic_request }
  end
  if requests then
    local ok, detail = pcall(function()
      local sections = entity.get_logistic_sections()
      local section = sections and sections.sections[1] or nil
      if not section then error("missing_section") end
      if action.logistic_group and section.group ~= action.logistic_group then
        error("group_expected=" .. action.logistic_group .. ",actual=" .. tostring(section.group))
      end
      verify_section_slots(section, requests)
    end)
    if not ok then return "logistic_request_mismatch:" .. tostring(detail) end
  end
  if action.logistic_sections then
    local ok, detail = pcall(function()
      local sections = entity.get_logistic_sections()
      if not sections then error("missing_sections") end
      for _, spec in ipairs(action.logistic_sections) do
        local section = find_section_by_group(sections, spec.group)
        if not section then error("missing_group=" .. tostring(spec.group)) end
        verify_section_slots(section, spec.requests)
        local expected_multiplier = tonumber(spec.multiplier) or 1
        local actual_multiplier = tonumber(section.multiplier) or 1
        if math.abs(actual_multiplier - expected_multiplier) > 0.000001 then
          error("group=" .. tostring(spec.group) .. ",multiplier_expected="
            .. expected_multiplier .. ",actual=" .. tostring(actual_multiplier))
        end
      end
    end)
    if not ok then return "logistic_section_mismatch:" .. tostring(detail) end
  end
  return nil
end

local function configure_created_entity(entity, action)
  if action.recipe then
    local ok = pcall(function() entity.set_recipe(action.recipe) end)
    if not ok then return "recipe_set_failed" end
  end
  if action.infinity_filter then
    local ok
    if entity.type == "infinity-pipe" then
      ok = pcall(function()
        entity.set_infinity_pipe_filter({
          name = action.infinity_filter,
          percentage = tonumber(action.fill_percentage) or 1.0,
          mode = "at-least"
        })
      end)
    else
      ok = pcall(function()
        entity.set_infinity_container_filter(1, {
          name = action.infinity_filter, count = 1000, mode = "exactly", index = 1
        })
        entity.remove_unfiltered_items = true
      end)
    end
    if not ok then return "infinity_filter_set_failed" end
  end
  if action.inventory_limit then
    local inventory, bar, detail = inventory_limit_details(entity, action.inventory_limit)
    if detail then return detail end
    local ok = pcall(function()
      if bar <= #inventory then inventory.set_bar(bar) else inventory.clear_bar() end
    end)
    if not ok then return "inventory_limit_set_failed" end
  end
  local requests = action.logistic_requests
  if not requests and action.logistic_request then
    requests = { action.logistic_request }
  end
  if requests then
    -- One requester may hold several recipe-proportional item requests. The
    -- optional group makes copied mall cells share the same named settings.
    local ok = pcall(function()
      local sections = entity.get_logistic_sections()
      local section = sections.sections[1] or sections.add_section()
      if action.logistic_group then section.group = action.logistic_group end
      write_section_slots(section, requests)
    end)
    if not ok then return "logistic_request_set_failed" end
  end
  if action.clear_logistic_groups then
    local ok, detail = pcall(function()
      clear_logistic_groups(entity, action.clear_logistic_groups)
    end)
    if not ok then return "logistic_group_clear_failed:" .. tostring(detail) end
  end
  if action.logistic_sections then
    local ok, detail = pcall(function()
      local sections = entity.get_logistic_sections()
      if not sections then error("no_logistic_sections") end
      for _, spec in ipairs(action.logistic_sections) do
        -- Upsert by group: a section already carrying this label is rewritten
        -- in place, and any OTHER machine's section on the same chest is left
        -- exactly as it is. That is what lets one chest serve two machines
        -- without either build erasing the other's requests.
        -- The label must be applied BEFORE the slots. Assigning a group that
        -- already exists makes the section adopt that group's filters, which
        -- silently discards anything written first (observed live: mall
        -- requests came back empty). Naming it first means the slots below are
        -- written into the group itself.
        local section = find_section_by_group(sections, spec.group)
          or claim_section_for_group(sections, spec.group)
        write_section_slots(section, spec.requests)
        section.multiplier = tonumber(spec.multiplier) or 1
      end
    end)
    if not ok then return "logistic_section_set_failed:" .. tostring(detail) end
  end
  return nil
end

-- Factorio's create_entity auto-wires nearby poles unreliably once dozens of
-- poles already exist on the surface (verified live: two poles 5 tiles apart
-- always auto-connect, but the same distance silently fails to connect once
-- ~60 poles already exist nearby) -- so every place_entity/place_ghost pole
-- placement must be followed by an explicit wiring pass rather than trusting
-- the implicit auto-connect. Reach values come straight from the prototype
-- (get_max_wire_distance), matching planners/infrastructure.py's POLE_SPECS.
local function repair_existing_inserter_direction(entity, action, direction, mismatch)
  -- Mall cells are idempotent: an older deployment may have created the
  -- inserter facing the drop side. Inserters are the only existing entities we
  -- retarget in place; chests and machines must never be silently replaced.
  if mismatch ~= "direction_mismatch" or direction == nil
    or string.find(action.entity or "", "inserter", 1, true) == nil then
    return mismatch
  end
  local ok = pcall(function() entity.direction = direction end)
  if not ok then return mismatch end
  return configuration_error(entity, action, direction)
end

local function ensure_pole_wiring(surface, force, area)
  local poles = surface.find_entities_filtered({ type = "electric-pole", force = force, area = area })
  local reach_cache = {}
  local function reach(entity)
    local cached = reach_cache[entity.name]
    if cached then return cached end
    local value = prototypes.entity[entity.name].get_max_wire_distance()
    reach_cache[entity.name] = value
    return value
  end
  local connected = 0
  for i = 1, #poles do
    local a = poles[i]
    if a.valid then
      for j = i + 1, #poles do
        local b = poles[j]
        if b.valid and a.electric_network_id ~= b.electric_network_id then
          local dx = a.position.x - b.position.x
          local dy = a.position.y - b.position.y
          local distance = math.sqrt(dx * dx + dy * dy)
          if distance <= math.min(reach(a), reach(b)) then
            local wire_a = a.get_wire_connector(defines.wire_connector_id.pole_copper, true)
            local wire_b = b.get_wire_connector(defines.wire_connector_id.pole_copper, true)
            if wire_a.connect_to(wire_b, false, defines.wire_origin.script) then
              connected = connected + 1
            end
          end
        end
      end
    end
  end
  return connected
end

local function execute_build_plan(authorization, build_plan)
  local approved = {}
  for _, action in ipairs(authorization.approved_actions or {}) do
    approved[action] = true
  end
  if type(build_plan.phases) ~= "table" then
    error("BuildPlan must include phases")
  end

  local required_authorization = {
    place_ghost = "project_more_ghosts",
    place_entity = "place_core_infrastructure",
    remove_entity = "remove_entities"
  }
  -- Optional: target an existing surface/force (e.g. "nauvis"/"player") instead
  -- of the isolated sandbox, for building directly onto a real base.
  local surface = get_or_create_sandbox_surface(build_plan.surface)
  local force = get_or_create_planner_force(build_plan.force)
  local counts = {
    attempted_ghosts = 0,
    placed_ghosts = 0,
    already_present_ghosts = 0,
    failed_ghosts = 0,
    attempted_entities = 0,
    placed_entities = 0,
    already_present_entities = 0,
    failed_entities = 0
  }
  local placement_failures = {}
  local recipe_failures = 0
  local removed_entities = 0
  local placement_bounds = nil
  local pole_wire_margin = 0

  local function record_failure(phase, action, reason)
    if string.find(reason, "recipe", 1, true) then
      recipe_failures = recipe_failures + 1
    end
    table.insert(placement_failures, {
      phase = phase.name,
      action_type = action.action_type,
      entity = action.entity,
      position = { x = action.position.x, y = action.position.y },
      reason = reason
    })
  end

  for _, phase in ipairs(build_plan.phases) do
    for _, action in ipairs(phase.actions or {}) do
      local permission = required_authorization[action.action_type]
      if not permission then
        error("Unsupported build plan action: " .. tostring(action.action_type))
      end
      if not approved[permission] then
        error("Authorization does not permit " .. permission .. " for " .. action.action_type)
      end

      local position = action.position
      if type(position) ~= "table" or position.x == nil or position.y == nil then
        error("BuildPlan action requires position with x and y")
      end
      if action.action_type ~= "remove_entity" then
        if not placement_bounds then
          placement_bounds = {
            min_x = position.x, min_y = position.y,
            max_x = position.x, max_y = position.y
          }
        else
          placement_bounds.min_x = math.min(placement_bounds.min_x, position.x)
          placement_bounds.min_y = math.min(placement_bounds.min_y, position.y)
          placement_bounds.max_x = math.max(placement_bounds.max_x, position.x)
          placement_bounds.max_y = math.max(placement_bounds.max_y, position.y)
        end
        local prototype = prototypes.entity[action.entity]
        if prototype and prototype.type == "electric-pole" then
          pole_wire_margin = math.max(
            pole_wire_margin, prototype.get_max_wire_distance()
          )
        end
      end
      local direction = nil
      if action.direction then
        direction = defines.direction[action.direction]
        if direction == nil then
          error("Unknown direction: " .. tostring(action.direction))
        end
      end

      if action.action_type == "place_ghost" then
        counts.attempted_ghosts = counts.attempted_ghosts + 1
        local built = find_exact_entity(surface, force, action.entity, position)
        local ghost = find_exact_ghost(surface, force, action.entity, position)
        local existing = built or ghost
        if existing then
          local configure_error = nil
          if needs_reconfiguration(action, existing) then
            configure_error = configure_created_entity(existing, action)
          end
          local mismatch = configure_error or configuration_error(existing, action, direction)
          mismatch = repair_existing_inserter_direction(existing, action, direction, mismatch)
          if mismatch then
            counts.failed_ghosts = counts.failed_ghosts + 1
            record_failure(phase, action, mismatch)
          else
            counts.already_present_ghosts = counts.already_present_ghosts + 1
          end
        elseif #exact_position_occupants(surface, force, position) > 0 then
          counts.failed_ghosts = counts.failed_ghosts + 1
          record_failure(phase, action, "exact_position_occupied_by_different_entity")
        else
          ghost = surface.create_entity({
            name = "entity-ghost",
            inner_name = action.entity,
            position = { position.x, position.y },
            direction = direction,
            type = action.underground_type,
            force = force
          })
          if ghost and ghost.valid then
            local configure_error = configure_created_entity(ghost, action)
            local verify_error = configure_error or configuration_error(ghost, action, direction)
            if verify_error then
              counts.failed_ghosts = counts.failed_ghosts + 1
              record_failure(phase, action, verify_error)
              ghost.destroy()
            else
              counts.placed_ghosts = counts.placed_ghosts + 1
            end
          else
            counts.failed_ghosts = counts.failed_ghosts + 1
            record_failure(phase, action, "create_entity_returned_nil")
          end
        end
      elseif action.action_type == "place_entity" then
        counts.attempted_entities = counts.attempted_entities + 1
        local existing = find_exact_entity(surface, force, action.entity, position)
        if existing then
          local configure_error = nil
          if needs_reconfiguration(action, existing) then
            configure_error = configure_created_entity(existing, action)
          end
          local mismatch = configure_error or configuration_error(existing, action, direction)
          mismatch = repair_existing_inserter_direction(existing, action, direction, mismatch)
          if mismatch then
            counts.failed_entities = counts.failed_entities + 1
            record_failure(phase, action, mismatch)
          else
            counts.already_present_entities = counts.already_present_entities + 1
          end
        elseif #exact_position_occupants(surface, force, position) > 0 then
          counts.failed_entities = counts.failed_entities + 1
          record_failure(phase, action, "exact_position_occupied_by_different_entity")
        else
          local entity = surface.create_entity({
            name = action.entity,
            position = { position.x, position.y },
            direction = direction,
            type = action.underground_type,
            force = force
          })
          if entity and entity.valid then
            local configure_error = configure_created_entity(entity, action)
            local verify_error = configure_error or configuration_error(entity, action, direction)
            if verify_error then
              counts.failed_entities = counts.failed_entities + 1
              record_failure(phase, action, verify_error)
              entity.destroy()
            else
              counts.placed_entities = counts.placed_entities + 1
            end
          else
            counts.failed_entities = counts.failed_entities + 1
            record_failure(phase, action, "create_entity_returned_nil")
          end
        end
      else
        local doomed = surface.find_entities_filtered({
          name = action.entity,
          area = { { position.x - 0.6, position.y - 0.6 }, { position.x + 0.6, position.y + 0.6 } },
          force = force
        })
        for _, entity in pairs(doomed) do
          if entity.valid then
            entity.destroy()
            removed_entities = removed_entities + 1
          end
        end
        local ghosts = surface.find_entities_filtered({
          name = "entity-ghost",
          area = { { position.x - 0.6, position.y - 0.6 }, { position.x + 0.6, position.y + 0.6 } },
          force = force
        })
        for _, existing_ghost in pairs(ghosts) do
          if existing_ghost.valid and existing_ghost.ghost_name == action.entity then
            existing_ghost.destroy()
            removed_entities = removed_entities + 1
          end
        end
      end
    end
  end

  counts.wires_connected = 0
  if placement_bounds and pole_wire_margin > 0 then
    local area = {
      {
        placement_bounds.min_x - pole_wire_margin,
        placement_bounds.min_y - pole_wire_margin
      },
      {
        placement_bounds.max_x + pole_wire_margin,
        placement_bounds.max_y + pole_wire_margin
      }
    }
    counts.wires_connected = ensure_pole_wiring(surface, force, area)
  end
  counts.attempted_placements = counts.attempted_ghosts + counts.attempted_entities
  counts.succeeded_placements = counts.placed_ghosts + counts.placed_entities
  counts.already_present_placements = counts.already_present_ghosts
    + counts.already_present_entities
  counts.failed_placements = counts.failed_ghosts + counts.failed_entities
  counts.placement_failures = placement_failures
  counts.recipe_failures = recipe_failures
  counts.removed_entities = removed_entities
  return counts
end
commands.add_command("build_layout_plan", "Execute an authorized BuildPlan with explicit positions on planner-sandbox.", function(command)
  local ok, payload = pcall(function()
    return helpers.json_to_table(command.parameter or "")
  end)
  if not ok or type(payload) ~= "table" or type(payload.authorization) ~= "table" or type(payload.build_plan) ~= "table" then
    game.print("BuildPlan error: payload must include authorization and build_plan")
    return
  end

  local run_ok, result = pcall(function()
    return execute_build_plan(payload.authorization, payload.build_plan)
  end)

  local report = { tick = game.tick, ok = false }
  if run_ok then
    for key, value in pairs(result) do
      report[key] = value
    end
    report.ok = result.failed_placements == 0
    if not report.ok then
      report.error = tostring(result.failed_placements) .. " placement(s) failed"
    end
  else
    report.error = tostring(result)
  end

  local json = helpers.table_to_json(report)
  if run_ok and #result.placement_failures == 0 then
    json = json:gsub('"placement_failures":{}', '"placement_failures":[]')
  end
  helpers.write_file("factorio_mod/layout_reports/layout_" .. game.tick .. ".json", json, false)
end)
