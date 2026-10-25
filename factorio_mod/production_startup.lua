-- Path: factorio_mod/production_startup.lua
-- Purpose: Retain game-clock production startup samples for explicitly observed scopes.

local VERSION = "production-startup-v2"
local PERIOD = 30 * 60
local WINDOW = 120 * 60
local function watches()
  storage.production_startup_v2 = storage.production_startup_v2 or {}
  return storage.production_startup_v2
end

local function counter(watch)
  local surface, force = game.surfaces[watch.surface], game.forces[watch.force]
  if not surface or not force then return nil end
  return force.get_item_production_statistics(surface).get_input_count(watch.item)
end

local function sample(watch, tick)
  local count = counter(watch)
  if count == nil then watch.error = "scope_unavailable"; return end
  if count < watch.last_count then watch.error = "production_counter_reset"; return end
  watch.last_count = count
  if not watch.start_tick and count > watch.baseline_count then
    -- The first observed new output is resolved to one game second; inventory
    -- stock, including stock already in the save, never starts this clock.
    watch.start_tick = tick
    watch.samples = {{tick = tick, produced = count}}
  elseif watch.start_tick and tick - watch.start_tick >= #watch.samples * PERIOD then
    table.insert(watch.samples, {tick = tick, produced = count})
    if tick - watch.start_tick >= WINDOW then watch.complete = true end
  end
end

script.on_nth_tick(60, function(event)
  for _, watch in pairs(watches()) do
    if not watch.complete and not watch.error then sample(watch, event.tick) end
  end
end)

local startup = {
  arm = function(surface, force, item, run_id)
    assert(type(run_id) == "string" and #run_id > 0, "run identity required")
    assert(type(surface) == "string" and type(force) == "string" and type(item) == "string")
    -- One bounded record per item/scope. A new logical run establishes a new
    -- baseline; re-reading or resuming the same run preserves its first window.
    local key = surface .. "\0" .. force .. "\0" .. item
    local watch = watches()[key]
    if not watch or watch.run_id ~= run_id then
      watch = {version = VERSION, surface = surface, force = force, item = item,
        run_id = run_id, baseline_tick = game.tick, samples = {}, complete = false,
        armed_at_episode_start = true,
        source = "force.get_item_production_statistics(surface).get_input_count"}
      local count = counter(watch)
      assert(count ~= nil, "startup scope unavailable")
      watch.baseline_count, watch.last_count = count, count
      watches()[key] = watch
    end
    watch.observed_tick = game.tick
    return watch
  end,
  read = function(surface, force, item, run_id)
    -- Read/resume never creates a new window after production already ran.
    local watch = watches()[surface .. "\0" .. force .. "\0" .. item]
    if not watch or watch.run_id ~= run_id then return nil end
    watch.observed_tick = game.tick
    return watch
  end
}

return startup
