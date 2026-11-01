-- Path: training/laya_launch_rocket.lua | Purpose: Order one explicitly identified silo launch on a paused disposable copy.
-- The caller supplies request and owns the durable no-retry journal. This
-- script does not create a silo, grant items, set recipes or claim an event.

assert(game.tick_paused and game.ticks_to_run == 0, "launch requires a paused world")
assert(game.tick == request.expected_tick, "launch observation tick changed")
local surface = assert(game.surfaces[request.surface], "launch surface is unavailable")
local force = assert(game.forces[request.force], "launch force is unavailable")
local matches = {}
for _, entity in pairs(surface.find_entities_filtered({type="rocket-silo", force=force.name})) do
  if entity.valid and entity.unit_number == request.unit_number then
    matches[#matches + 1] = entity
  end
end
assert(#matches == 1, "launch target is missing or ambiguous")
local silo = matches[1]
assert(silo.name == request.name and silo.position.x == request.position.x
  and silo.position.y == request.position.y, "launch target identity changed")
local ordered = silo.launch_rocket()
assert(type(ordered) == "boolean", "launch returned an unreadable result")
return {version="disposable-rocket-order-v1", request_id=request.request_id,
        tick=game.tick, surface=surface.name, force=force.name,
        unit_number=silo.unit_number, ordered=ordered}
