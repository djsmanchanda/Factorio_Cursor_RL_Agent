-- Path: factorio_training_lab/control.lua
-- Purpose: Register the isolated training laboratory lifecycle and sampler.

local shared = require("training_shared")
local world = require("episode_world")
local measurement = require("episode_measurement")
local plan_execution = require("plan_execution")

world.register_commands()
measurement.register_commands()
plan_execution.register_commands()

script.on_init(function()
  shared.ensure_storage()
end)

script.on_configuration_changed(function()
  shared.ensure_storage()
end)

script.on_nth_tick(60, function(event)
  measurement.sample_all(event.tick)
end)

script.on_event(defines.events.on_forces_merged, function(event)
  world.complete_force_merge(event)
end)
