-- Path: factorio_mod/sandbox_shared.lua
-- Purpose: Own canonical sandbox constants and shared deterministic helpers.



local CANONICAL_POWER_SOURCE = { x = -160, y = -160 }
local CANONICAL_ROBOPORT_HUB = { x = -128, y = -128 }

local function ensure_storage()
  return storage
end

local function get_or_create_sandbox_surface()
  local surface = game.surfaces["planner-sandbox"]
  if surface then
    return surface
  end

  error("planner-sandbox does not exist; run the confirmed WorldSpec command first")
end

local function get_or_create_planner_force()
  local force = game.forces.planner
  if not force then
    force = game.create_force("planner")
  end

  local human_force = game.forces.player
  if human_force then
    force.set_friend(human_force, true)
    human_force.set_friend(force, true)
    -- Sync research from the human force so the sandbox's bots, machines and
    -- inserters run at the SAME speed as the rest of the game. A fresh force
    -- has zero research: bots ~10x slower, machines slower, inserters base
    -- capacity - which the user observed as the sandbox being "out of sync".
    for name, tech in pairs(human_force.technologies) do
      if tech.researched and force.technologies[name] and not force.technologies[name].researched then
        force.technologies[name].researched = true
      end
    end
    -- Infinite-research effects (e.g. worker robot speed) stack past the finite
    -- techs; copy the resulting force modifiers directly to fully match.
    for _, modifier in pairs({
      "worker_robots_speed_modifier", "worker_robots_battery_modifier",
      "worker_robots_storage_bonus",
    }) do
      pcall(function() force[modifier] = human_force[modifier] end)
    end
  end

  local bootstrap = force.technologies["automation-science-pack"]
  if bootstrap and not bootstrap.researched then
    bootstrap.researched = true
  end
  return force
end

local function find_exact_entity(surface, force, name, position)
  local candidates = surface.find_entities_filtered({
    name = name,
    area = {
      { position.x - 0.01, position.y - 0.01 },
      { position.x + 0.01, position.y + 0.01 }
    },
    force = force
  })
  for _, entity in pairs(candidates) do
    if entity.position.x == position.x and entity.position.y == position.y then
      return entity
    end
  end
  return nil
end


local function is_factory_entity(entity)
  return entity.valid and entity.type ~= "character" and entity.type ~= "resource"
end

return {
  CANONICAL_POWER_SOURCE = CANONICAL_POWER_SOURCE,
  CANONICAL_ROBOPORT_HUB = CANONICAL_ROBOPORT_HUB,
  ensure_storage = ensure_storage,
  get_or_create_sandbox_surface = get_or_create_sandbox_surface,
  get_or_create_planner_force = get_or_create_planner_force,
  find_exact_entity = find_exact_entity,
  is_factory_entity = is_factory_entity
}
