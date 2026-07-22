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
  return nil
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
  local surface = get_or_create_sandbox_surface()
  local force = get_or_create_planner_force()
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
          local mismatch = configuration_error(existing, action, direction)
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
          local mismatch = configuration_error(existing, action, direction)
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
