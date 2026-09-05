-- Path: factorio_mod/control.lua
-- Purpose: Load planner command modules and register lifecycle event handlers.

local shared = require("sandbox_shared")
local trash_requesters = require("trash_requesters")
require("world_generation")
require("snapshot")
require("recipe_catalog")
require("ghost_plans")
require("construction")
require("upgrades")
require("deconstruction")
require("sandbox_topology")
require("scaffolding")
require("water_seeding")
require("layout_executor")
require("research")
require("science_telemetry")
require("live_execution")
require("spidertron_builder")

script.on_event(defines.events.on_robot_built_entity, function(event)
  -- Factorio 2.0: event field renamed from created_entity to entity.
  if not event or not event.entity then
    return
  end

  local entity = event.entity
  if entity.surface and entity.surface.name == "planner-sandbox" and entity.force.name == "planner" then
    local storage = shared.ensure_storage()
    if storage.construction_session then
      storage.construction_session.completed = storage.construction_session.completed + 1
    end
  end

  -- Bot-revived requester chests cannot carry the blueprint trash flag
  -- (ghosts hold no settings and no setter exists), so swap each one for a
  -- trash-enabled copy at revive time, before any configure flow or delivery
  -- touches it. Scoped to bot builds: player hand placements keep whatever
  -- the player set. Training forces run under their own mod and contract.
  if entity.valid and entity.name == "requester-chest" then
    local force_name = entity.force and entity.force.name
    if trash_requesters.managed_force(force_name) then
      trash_requesters.revive_with_trash(entity.surface, entity)
    end
  end
end)

script.on_init(function()
  shared.ensure_storage()
end)

script.on_configuration_changed(function()
  shared.ensure_storage()
end)
