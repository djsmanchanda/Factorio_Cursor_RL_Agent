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

-- Each episode owns its 60-tick cadence; global clock phase must not change
-- the measured window or reward an initial wait that shifts later polling.
script.on_event(defines.events.on_tick, function(event)
  measurement.sample_all(event.tick)
  if event.tick % 600 == 0 then
    world.cleanup_orphan_surfaces(event.tick)
  end
end)

script.on_event(defines.events.on_forces_merged, function(event)
  world.complete_force_merge(event)
end)

script.on_event(defines.events.on_player_joined_game, function(event)
  local player = game.get_player(event.player_index)
  if player then world.reveal_active_episodes(player) end
end)

-- Read-only observer access for the Laya Coach window in the companion mod.
remote.add_interface("factorio_training_lab", {
  active_worlds = world.active_worlds,
  view_world = world.view_world,
  leave_world = world.leave_world,
})
