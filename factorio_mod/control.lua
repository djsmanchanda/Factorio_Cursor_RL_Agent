-- Path: factorio_mod/control.lua
-- Purpose: Load planner command modules and register lifecycle event handlers.

local shared = require("sandbox_shared")
local trash_requesters = require("trash_requesters")
local research_lab_ownership = require("research_lab_ownership")
local optimizer_build_evidence = require("optimizer_build_evidence")
require("world_generation")
require("snapshot")
require("recipe_catalog")
require("runtime_catalog")
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
require("optimizer_consumer_observation")
local rocket_launch_telemetry = require("rocket_launch_telemetry")
require("rocket_silo_observation")
local laya_coach = require("laya_coach")

script.on_event(defines.events.on_robot_built_entity, function(event)
  -- Factorio 2.0: event field renamed from created_entity to entity.
  if not event or not event.entity then
    return
  end

  local entity = event.entity
  local ownership = research_lab_ownership.capture(event)
  local optimizer_evidence = optimizer_build_evidence.capture(event)
  local ownership_entity = entity
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
      local replaced, _, replacement = trash_requesters.revive_with_trash(entity.surface, entity)
      ownership_entity = replaced and replacement or nil
    end
  end
  research_lab_ownership.record(ownership, ownership_entity)
  optimizer_build_evidence.record(optimizer_evidence, ownership_entity)
end)

script.on_event(defines.events.on_rocket_launched, rocket_launch_telemetry.on_rocket_launched)

script.on_init(function()
  shared.ensure_storage()
  laya_coach.on_init()
end)

script.on_configuration_changed(function()
  shared.ensure_storage()
  laya_coach.on_configuration_changed()
end)
