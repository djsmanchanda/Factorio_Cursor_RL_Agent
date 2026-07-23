-- Path: factorio_mod/control.lua
-- Purpose: Load planner command modules and register lifecycle event handlers.

local shared = require("sandbox_shared")
require("world_generation")
require("snapshot")
require("recipe_catalog")
require("ghost_plans")
require("construction")
require("upgrades")
require("deconstruction")
require("sandbox_topology")
require("scaffolding")
require("layout_executor")
require("research")
require("live_execution")

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
end)

script.on_init(function()
  shared.ensure_storage()
end)

script.on_configuration_changed(function()
  shared.ensure_storage()
end)
