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

  surface = game.create_surface("planner-sandbox")
  -- Deterministic buildable canvas: default mapgen can produce alien terrain
  -- (ice, oil ocean) where bots cannot place entities.
  surface.generate_with_lab_tiles = true
  return surface
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
