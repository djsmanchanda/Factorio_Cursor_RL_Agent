-- Path: factorio_mod/upgrades.lua
-- Purpose: Validate and execute authorized deterministic upgrade plans.



local shared = require("sandbox_shared")
local get_or_create_sandbox_surface = shared.get_or_create_sandbox_surface
local get_or_create_planner_force = shared.get_or_create_planner_force
local find_exact_entity = shared.find_exact_entity

local function parse_upgrade_payload(json_text)
  if not json_text or json_text == "" then
    return nil, "Missing upgrade payload JSON"
  end

  local ok, payload = pcall(function()
    return helpers.json_to_table(json_text)
  end)
  if not ok or type(payload) ~= "table" then
    return nil, "Invalid upgrade payload JSON"
  end

  if type(payload.authorization) ~= "table" then
    return nil, "Upgrade payload must include authorization"
  end
  if type(payload.upgrade_plan) ~= "table" then
    return nil, "Upgrade payload must include upgrade_plan"
  end

  return payload, nil
end

local function validate_upgrade_payload(payload)
  local authorization = payload.authorization
  local upgrade_plan = payload.upgrade_plan

  if type(authorization.approved_actions) ~= "table" then
    return nil, "Authorization must include approved_actions"
  end
  if type(upgrade_plan.actions) ~= "table" then
    return nil, "UpgradePlan must include actions"
  end

  return { authorization = authorization, upgrade_plan = upgrade_plan }, nil
end

local function execute_upgrades(authorization, upgrade_plan)
  local approved = {}
  for _, action in ipairs(authorization.approved_actions) do
    approved[action] = true
  end

  if not approved["apply_upgrades"] then
    error("Authorization does not permit apply_upgrades")
  end

  local scope_limits = authorization.scope_limits or {}
  local max_count = scope_limits.max_count
  local block_filter = {}
  if type(scope_limits.block_filter) == "table" then
    for _, block in ipairs(scope_limits.block_filter) do
      block_filter[block] = true
    end
  end

  local surface = get_or_create_sandbox_surface()
  local force = get_or_create_planner_force()

  local results = {}
  local processed = 0

  for _, entry in ipairs(upgrade_plan.actions) do
    if max_count and processed >= max_count then
      break
    end

    if type(entry) ~= "table" then
      error("Upgrade action must be an object")
    end
    if entry.block and next(block_filter) ~= nil and not block_filter[entry.block] then
      goto continue
    end

    local position = entry.position
    if type(position) ~= "table" or position.x == nil or position.y == nil then
      error("Upgrade action must include position")
    end

    local upgraded = entry.to_name and find_exact_entity(surface, force, entry.to_name, position) or nil
    if upgraded then
      table.insert(results, { action = entry.action, status = "skipped", reason = "already_upgraded" })
      goto continue
    end
    local target = find_exact_entity(surface, force, entry.from_name, position)
    if not target then
      table.insert(results, { action = entry.action, status = "failed", reason = "target_missing" })
      goto continue
    end

    if entry.action == "assembler_tier_upgrade" or entry.action == "entity_tier_upgrade" then
      if not entry.from_name or not entry.to_name then
        error("Entity tier upgrade requires from_name and to_name")
      end
      if target.name == entry.to_name then
        table.insert(results, { action = entry.action, status = "skipped", reason = "already_upgraded" })
      else
        surface.create_entity({
          name = "entity-ghost",
          inner_name = entry.to_name,
          position = position,
          force = force,
          tags = { block = entry.block or "", phase = "upgrade", capacity_slice = "n/a" }
        })
        table.insert(results, { action = entry.action, status = "success" })
      end
    elseif entry.action == "module_upgrade" then
      local module_name = entry.module_to
      local module_count = entry.module_count
      if not module_name or not module_count then
        error("Module upgrade requires module_to and module_count")
      end
      -- Factorio 2.0: module requests are BlueprintInsertPlan entries targeting
      -- explicit module-inventory slots, not a name→count map.
      local insert_plans = {}
      for slot = 1, module_count do
        table.insert(insert_plans, {
          id = { name = module_name },
          items = {
            in_inventory = {
              { inventory = defines.inventory.crafter_modules, stack = slot - 1, count = 1 }
            }
          }
        })
      end
      surface.create_entity({
        name = "item-request-proxy",
        position = target.position,
        force = force,
        target = target,
        modules = insert_plans
      })
      table.insert(results, { action = entry.action, status = "success" })
    else
      error("Unsupported upgrade action: " .. tostring(entry.action))
    end

    processed = processed + 1
    ::continue::
  end

  return results
end

commands.add_command("execute_upgrade_plan", "Execute authorized upgrades in planner-sandbox.", function(command)
  local payload, err = parse_upgrade_payload(command.parameter)
  if err then
    if command.player_index then
      local player = game.get_player(command.player_index)
      if player then
        player.print("Upgrade error: " .. err)
      end
    else
      game.print("Upgrade error: " .. err)
    end
    return
  end

  local validated, validation_err = validate_upgrade_payload(payload)
  if validation_err then
    if command.player_index then
      local player = game.get_player(command.player_index)
      if player then
        player.print("Upgrade error: " .. validation_err)
      end
    else
      game.print("Upgrade error: " .. validation_err)
    end
    return
  end

  local ok, results = pcall(function()
    return execute_upgrades(validated.authorization, validated.upgrade_plan)
  end)

  if not ok then
    local message = "Upgrade error: " .. tostring(results)
    if command.player_index then
      local player = game.get_player(command.player_index)
      if player then
        player.print(message)
      end
    else
      game.print(message)
    end
    return
  end

  local report = {
    tick = game.tick,
    surface = get_or_create_sandbox_surface().name,
    actions = results
  }

  local json = helpers.table_to_json(report)
  local path = "factorio_mod/execution_reports/upgrade_report_" .. game.tick .. ".json"
  helpers.write_file(path, json, false)

  if command.player_index then
    local player = game.get_player(command.player_index)
    if player then
      player.print("Upgrade report written to script-output/" .. path)
    end
  else
    game.print("Upgrade report written to script-output/" .. path)
  end
end)
