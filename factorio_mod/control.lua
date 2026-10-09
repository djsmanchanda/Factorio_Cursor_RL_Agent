-- Path: factorio_mod/control.lua
-- Purpose: Load planner command modules and register lifecycle event handlers.
--
-- The runtime identity (deterministic or Laya) is resolved from namespace.lua
-- before the shared modules load, then the engine globals are shadowed so every
-- module registers prefixed commands and writes under this runtime's
-- script-output root. Both runtime mods therefore load together safely.

local namespace = require("namespace")
commands, helpers = namespace.install(commands, helpers)

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
require("logistic_inventory")
require("live_execution")
require("spidertron_builder")

-- The human Laya guidance window belongs only to the Laya runtime mod. Its
-- event handlers are inert unless the Laya runtime is the active one.
local laya_coach = nil
if namespace.runtime == "laya" then
  laya_coach = require("laya_coach")
end

namespace.install_remote(remote)

local function activate(active)
  local ok, err = namespace.set_active(active)
  if ok then
    game.print("runtime " .. (active and "activated: " or "deactivated: ") .. namespace.mod_name)
    -- Refresh the Laya guidance window immediately so toggling the setting
    -- takes effect without reloading the save.
    if laya_coach then
      laya_coach.on_configuration_changed()
    end
  else
    game.print("runtime setting failed: " .. tostring(err))
  end
end

commands.add_command("activate_runtime", "Mark this runtime active in the save.", function()
  activate(true)
end)

commands.add_command("deactivate_runtime", "Mark this runtime inactive in the save.", function()
  activate(false)
end)

-- Only the selected runtime applies gameplay side effects. The guard is
-- evaluated per event (not at load) so the runtime-global setting can be
-- changed in-game without a restart.
script.on_event(defines.events.on_robot_built_entity, function(event)
  if not namespace.is_active() then
    return
  end
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

  -- Bot-built requester ghosts cannot carry the blueprint trash flag. Apply
  -- the same setting to deterministic player/planner chests immediately after
  -- construction; training forces are excluded by trash_requesters.
  if entity.valid and entity.name == "requester-chest" then
    local force_name = entity.force and entity.force.name
    if trash_requesters.managed_force(force_name) then
      trash_requesters.revive_with_trash(entity.surface, entity)
    end
  end
end)

script.on_init(function()
  shared.ensure_storage()
  if laya_coach and namespace.is_active() then
    laya_coach.on_init()
  end
end)

script.on_configuration_changed(function()
  shared.ensure_storage()
  if laya_coach and namespace.is_active() then
    laya_coach.on_configuration_changed()
  end
end)
