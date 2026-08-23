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

local function find_exact_tile_ghost(surface, force, tile_name, position)
  local ghosts = surface.find_entities_filtered({
    name = "tile-ghost",
    area = {
      { position.x - 0.01, position.y - 0.01 },
      { position.x + 0.01, position.y + 0.01 }
    },
    force = force
  })
  for _, ghost in pairs(ghosts) do
    if ghost.position.x == position.x and ghost.position.y == position.y
      and ghost.ghost_name == tile_name then
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

local function atomic_placement_blocked(surface, force, action)
  local position = action.position
  if find_exact_entity(surface, force, action.entity, position) ~= nil then
    return false
  end
  if find_exact_ghost(surface, force, action.entity, position) ~= nil then
    return false
  end
  local check_type = defines.build_check_type.manual
    or defines.build_check_type.ghost_revive
  local ok, can_place = pcall(function()
    return surface.can_place_entity({
      name = action.entity,
      position = { position.x, position.y },
      force = force,
      build_check_type = check_type
    })
  end)
  return not ok or can_place ~= true
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
  if limit.fill_chest then
    return inventory, #inventory + 1, nil
  end
  local group_minimums = limit.minimum_stacks_by_group or {}
  local minimum_stacks = tonumber(group_minimums[group_name]) or 1
  local target_stacks = math.ceil(limit.count / prototype.stack_size)
  local usable_slots = math.max(minimum_stacks, target_stacks)
  return inventory, math.min(#inventory + 1, usable_slots + 1), nil
end

-- One chest may feed several machines, so its requests are kept as one NAMED
-- SECTION per machine set rather than merged into a single slot list. That
-- bookkeeping lives in logistic_sections.lua, where it can be tested without a
-- running game -- a chest holding no requests is indistinguishable from one
-- that was never asked for any, so the failure mode is silent.
local logistic_sections = require("logistic_sections")
local find_section_by_group = logistic_sections.find_section_by_group
local claim_section_for_group = logistic_sections.claim_section_for_group
local verify_section_slots = logistic_sections.verify_section_slots
local write_section_slots = logistic_sections.write_section_slots
local clear_logistic_groups = logistic_sections.clear_logistic_groups
local has_settings = logistic_sections.has_settings
local needs_reconfiguration = logistic_sections.needs_reconfiguration

local function splitter_priority_error(entity, action)
  if action.input_priority then
    local ok, actual = pcall(function() return entity.splitter_input_priority end)
    if not ok then return "splitter_input_priority_read_failed" end
    if actual ~= action.input_priority then
      return "splitter_input_priority_mismatch:expected=" .. action.input_priority
        .. ",actual=" .. tostring(actual)
    end
  end
  if action.output_priority then
    local ok, actual = pcall(function() return entity.splitter_output_priority end)
    if not ok then return "splitter_output_priority_read_failed" end
    if actual ~= action.output_priority then
      return "splitter_output_priority_mismatch:expected=" .. action.output_priority
        .. ",actual=" .. tostring(actual)
    end
  end
  return nil
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
  local priority_error = splitter_priority_error(entity, action)
  if priority_error then return priority_error end
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
  if action.clear_logistic_condition then
    local behavior = entity.get_or_create_control_behavior()
    if not behavior then return "logistic_condition_unsupported" end
    if behavior.connect_to_logistic_network == true then
      return "logistic_condition_not_cleared"
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
  if action.input_priority then
    local ok = pcall(function() entity.splitter_input_priority = action.input_priority end)
    if not ok then return "splitter_input_priority_set_failed" end
  end
  if action.output_priority then
    local ok = pcall(function() entity.splitter_output_priority = action.output_priority end)
    if not ok then return "splitter_output_priority_set_failed" end
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
    -- set_bar() with no argument CLEARS the limit; there is no clear_bar().
    -- The clearing branch only runs when the target needs the whole chest, so
    -- it went unexercised until a stock cap lifted to a full chest of belts and
    -- the first plan that reached it died on a nil method.
    local ok = pcall(function()
      if not inventory.supports_bar() then return end
      if bar <= #inventory then inventory.set_bar(bar) else inventory.set_bar() end
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
  if action.clear_logistic_condition then
    local behavior = entity.get_or_create_control_behavior()
    if not behavior then return "logistic_condition_unsupported" end
    local ok = pcall(function()
      behavior.connect_to_logistic_network = false
    end)
    if not ok or behavior.connect_to_logistic_network == true then
      return "logistic_condition_clear_failed"
    end
  end
  if action.logistic_condition then
    -- A machine reads the LOGISTIC network directly; it needs no wire, no
    -- roboport connection and no combinator, only to stand inside roboport
    -- coverage. Failure is reported rather than swallowed: a gate that never
    -- lands looks exactly like a machine nobody asked to gate, which is the
    -- silent-skip failure SETTING_FIELDS exists to prevent.
    local behavior = entity.get_or_create_control_behavior()
    if not behavior then return "logistic_condition_unsupported" end
    local ok = pcall(function()
      behavior.connect_to_logistic_network = true
      behavior.logistic_condition = {
        first_signal = { type = "item", name = action.logistic_condition.signal },
        comparator = action.logistic_condition.comparator,
        constant = action.logistic_condition.constant,
      }
    end)
    if not ok then return "logistic_condition_set_failed" end
    if behavior.connect_to_logistic_network ~= true then
      return "logistic_condition_not_applied"
    end
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
    place_tile_ghost = "project_more_ghosts",
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
      entity = action.entity or action.tile,
      position = { x = action.position.x, y = action.position.y },
      reason = reason
    })
  end

  if build_plan.atomic == true then
    for _, phase in ipairs(build_plan.phases) do
      for _, action in ipairs(phase.actions or {}) do
        if action.action_type == "place_entity" or action.action_type == "place_ghost" then
          counts.attempted_placements = counts.attempted_placements + 1
          if atomic_placement_blocked(surface, force, action) then
            record_failure(phase, action, "atomic_footprint_blocked")
          end
        end
      end
    end
    if #placement_failures > 0 then
      counts.failed_placements = #placement_failures
      counts.placement_failures = placement_failures
      counts.error = "atomic_footprint_blocked"
      return counts
    end
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
        local prototype = action.entity and prototypes.entity[action.entity] or nil
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

      if action.action_type == "place_tile_ghost" then
        counts.attempted_ghosts = counts.attempted_ghosts + 1
        local tile = surface.get_tile(position.x, position.y)
        local existing = find_exact_tile_ghost(surface, force, action.tile, position)
        if tile and tile.valid and tile.name == action.tile then
          counts.already_present_ghosts = counts.already_present_ghosts + 1
        elseif existing then
          counts.already_present_ghosts = counts.already_present_ghosts + 1
        elseif not tile or not tile.valid or not tile.collides_with("water_tile") then
          counts.failed_ghosts = counts.failed_ghosts + 1
          record_failure(phase, action, "landfill_target_is_not_water")
        else
          local ghost = surface.create_entity({
            name = "tile-ghost",
            inner_name = action.tile,
            position = { position.x, position.y },
            force = force
          })
          if ghost and ghost.valid then
            counts.placed_ghosts = counts.placed_ghosts + 1
          else
            counts.failed_ghosts = counts.failed_ghosts + 1
            record_failure(phase, action, "create_tile_ghost_returned_nil")
          end
        end
      elseif action.action_type == "place_ghost" then
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
