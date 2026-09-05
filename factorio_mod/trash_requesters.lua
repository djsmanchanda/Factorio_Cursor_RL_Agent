-- Path: factorio_mod/trash_requesters.lua
-- Purpose: Give bot-built requester chests blueprint trash behavior the runtime API cannot set.

local M = {}

-- Forces whose bot-built requesters are trash-enabled. Player-force builds
-- are the deterministic mission's own mall cells; planner-force is the
-- sandbox reference. Anything else (including training forces, which run
-- under a separate mod and contract) is left exactly as built.
M.TRASH_MANAGED_FORCES = { player = true, planner = true }

-- The exact request_filters payload a hand-exported blueprint carries for a
-- trash-enabled requester chest (verified live on 2.1.17: create_entity
-- accepts it and bots then remove unrequested items; no runtime getter or
-- setter exists, there is no section field for it, and the engine offers no
-- blueprint-stamping API, so creation time is the only moment it can land).
function M.trash_request_filters()
  return { trash_not_requested = true }
end

-- Extra create_entity parameters for a directly placed entity: the trash
-- payload for requester chests, nothing for anything else. Pure table merge
-- input so the executor stays testable without a running game.
function M.creation_params(entity_name)
  if entity_name == "requester-chest" then
    return { request_filters = M.trash_request_filters() }
  end
  return {}
end

function M.managed_force(force_name)
  return M.TRASH_MANAGED_FORCES[force_name] == true
end

-- Rebuild a freshly bot-revived requester with trash enabled, preserving
-- position, direction, force, and contents. Ghosts carry no settings and no
-- setter exists, so destroy + recreate at the same tick -- before any
-- configure flow or bot delivery touches the chest -- is the only path.
-- create_entity is called WITHOUT raise_built so this hook never retriggers
-- on its own replacement. Returns true when a trash-enabled chest stands at
-- the position afterwards, false when only a rebuild ghost could be placed.
function M.revive_with_trash(surface, entity)
  if entity == nil or not entity.valid then
    return false, "invalid"
  end
  local position = { entity.position.x, entity.position.y }
  local direction = entity.direction
  local force = entity.force
  local stacks = {}
  local ok_inv, inventory = pcall(function()
    return entity.get_inventory(defines.inventory.chest)
  end)
  if ok_inv and inventory then
    for index = 1, #inventory do
      local stack = inventory[index]
      if stack.valid_for_read then
        stacks[#stacks + 1] = { name = stack.name, count = stack.count }
      end
    end
  end
  entity.destroy()
  local created_ok, created = pcall(function()
    return surface.create_entity({
      name = "requester-chest",
      position = position,
      direction = direction,
      force = force,
      request_filters = M.trash_request_filters(),
    })
  end)
  if created_ok and created and created.valid then
    for _, stack in ipairs(stacks) do
      created.insert(stack)
    end
    return true
  end
  surface.create_entity({
    name = "entity-ghost",
    ghost_name = "requester-chest",
    position = position,
    direction = direction,
    force = force,
  })
  return false, "recreate_failed"
end

return M
