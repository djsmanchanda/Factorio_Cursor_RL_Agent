-- Path: training/laya_saved_world.lua | Purpose: Read facts and validate funded plans on an owned, paused saved-world copy.
-- Executed by the Python adapter through guarded RCON; this is not a deployed mod.
local surface = assert(game.surfaces.nauvis)
local force = assert(game.forces.player)
assert(game.tick_paused and game.ticks_to_run == 0, "saved-world clock must be paused")

local function contents(inventory)
  local result = {}
  for key, v in pairs(inventory.get_contents()) do
    if type(v) == "number" then result[key] = v
    elseif v.quality == "normal" then result[v.name] = (result[v.name] or 0) + v.count end
  end
  return result
end

local facts = {version="saved-world-v1", tick=game.tick, surface="nauvis", force="player",
  mods=script.active_mods, networks={}, ports={}, poles={}, machines={}, resources={},
  player_stock={}, production={}, consumption={}, researched={}, pending_ghosts=0,
  infrastructure_counts={}, recipes={}, enabled_recipes={}}
local generation = {}
for _, e in pairs(surface.find_entities_filtered{force=force}) do
  facts.infrastructure_counts[e.name]=(facts.infrastructure_counts[e.name] or 0)+1
  if e.type == "generator" or e.type == "solar-panel" or e.type == "electric-energy-interface" then
    local ok, id = pcall(function() return e.electric_network_id end)
    if ok and id then generation[tostring(id)] = true end
  end
end
local status_names = {}
for name, id in pairs(defines.entity_status) do status_names[id] = name end
for _, e in pairs(surface.find_entities_filtered{force=force}) do
  if e.type == "roboport" and e.logistic_network then
    local net = e.logistic_network; local id = tostring(net.network_id)
    if not facts.networks[id] then facts.networks[id] = {items={}, reserved={},
      construction_robots=net.all_construction_robots, available_construction_robots=net.available_construction_robots,
      logistic_robots=net.all_logistic_robots} end
    table.insert(facts.ports, {position=e.position, network=id, energy=e.energy,
      construction_radius=e.prototype.construction_radius, logistic_radius=e.prototype.logistic_radius})
  elseif e.type == "electric-pole" then
    table.insert(facts.poles, {name=e.name, position=e.position, wire=e.prototype.get_max_wire_distance(),
      supply=e.prototype.get_supply_area_distance(), grid=tostring(e.electric_network_id),
      generated=generation[tostring(e.electric_network_id)] == true})
  elseif e.type == "mining-drill" or e.type == "furnace" or e.type == "assembling-machine" then
    local m={name=e.name, position=e.position, energy=e.energy,status=status_names[e.status] or "unknown"}
    if e.type~="mining-drill" then local recipe=e.get_recipe(); m.recipe=recipe and recipe.name end
    table.insert(facts.machines,m)
  end
end
-- Provider/storage inventories are transferable. Requester/buffer inventories,
-- machine WIP, player inventories and flying-bot cargo stay separate.
for _, e in pairs(surface.find_entities_filtered{type="logistic-container",force=force}) do
  local mode=e.prototype.logistic_mode
  local net=surface.find_logistic_network_by_position(e.position,force)
  local entry=net and facts.networks[tostring(net.network_id)]
  if entry and (mode=="passive-provider" or mode=="active-provider" or mode=="storage") then
    for name,count in pairs(contents(e.get_inventory(defines.inventory.chest))) do
      entry.items[name]=(entry.items[name] or 0)+count
    end
  end
end
-- Iterate ghosts only after collecting every network, independent of entity order.
for _, e in pairs(surface.find_entities_filtered{type="entity-ghost",force=force}) do
  facts.pending_ghosts=facts.pending_ghosts+1
  for _, net in pairs(surface.find_logistic_networks_by_construction_area(e.position,force)) do
    local entry=facts.networks[tostring(net.network_id)]
    if entry then for _, item in pairs(e.ghost_prototype.items_to_place_this or {}) do
      entry.reserved[item.name]=(entry.reserved[item.name] or 0)+item.count
    end end
  end
end
for _, p in pairs(game.players) do
  local inv=p.get_main_inventory()
  if inv then facts.player_stock[p.name]=contents(inv) end
end
for name, tech in pairs(force.technologies) do if tech.researched then facts.researched[name]=true end end
local stats=force.get_item_production_statistics(surface)
local recipe_names={"iron-plate","copper-plate","stone-brick","steel-plate","iron-gear-wheel","copper-cable",
  "electronic-circuit","transport-belt","inserter","long-handed-inserter","medium-electric-pole",
  "assembling-machine-1","electric-mining-drill","electric-furnace","requester-chest","passive-provider-chest",
  "roboport","pipe","underground-belt","splitter","assembling-machine-2","chemical-plant","oil-refinery","pumpjack"}
-- Query observed demand and its enabled item-recipe closure, including recipes machines already use.
-- Fluid recipes are enabled capabilities, but stay out of the solid-only geometry recipe map.
local seen={}
for _,name in ipairs(recipe_names) do seen[name]=true end
local function include(name)
  if name and not seen[name] then seen[name]=true; table.insert(recipe_names,name) end
end
for _,name in ipairs(request.extra_recipes or {}) do include(name) end
for _,machine in ipairs(facts.machines) do include(machine.recipe) end
local index=1
while index<=#recipe_names do
  local name=recipe_names[index]; index=index+1
  local r=force.recipes[name]
  if r and r.enabled then
    facts.enabled_recipes[name]=true
    local ingredients={}; local solid=true
    for _, part in pairs(r.ingredients) do
      if part.type=="fluid" then solid=false end
      if part.type=="item" then include(part.name) end
      table.insert(ingredients,{name=part.name,amount=part.amount})
    end
    if solid then
      local amount=0
      for _,part in pairs(r.products) do if part.name==name then amount=part.amount or 0 end end
      if amount>0 then facts.recipes[name]={ingredients=ingredients,product_amount=amount,
        energy_ticks=math.max(1,math.floor(r.energy*60+.5))} end
    end
  end
end
for _, name in ipairs({"iron-ore","copper-ore","coal","stone"}) do
  facts.production[name]=stats.get_input_count(name); facts.consumption[name]=stats.get_output_count(name)
end
for _,name in ipairs(recipe_names) do
  if prototypes.item[name] then
    facts.production[name]=stats.get_input_count(name); facts.consumption[name]=stats.get_output_count(name)
  end
end
local bounds=request.bounds
-- Survey generated terrain only. No fixture, resource or item creation.
for x=bounds.x_min+2.5,bounds.x_max_exclusive-2,4 do
  for y=bounds.y_min+2.5,bounds.y_max_exclusive-2,4 do
    local resources=surface.find_entities_filtered{type="resource",area={{x-1.4,y-1.4},{x+1.4,y+1.4}}}
    local amount={}
    for _, e in pairs(resources) do
      if e.name=="iron-ore" or e.name=="copper-ore" or e.name=="coal" or e.name=="stone" then
        amount[e.name]=(amount[e.name] or 0)+e.amount
      end
    end
    for name, count in pairs(amount) do table.insert(facts.resources,{item=name,position={x=x,y=y},remaining=count}) end
  end
end

local function in_network(pos, id)
  for _, port in ipairs(facts.ports) do
    if port.network==id and port.energy>0 and math.abs(port.position.x-pos.x)<=port.construction_radius-2
      and math.abs(port.position.y-pos.y)<=port.construction_radius-2 then return true end
  end
  return false
end
if request.inspect then
  local result={built=0,pending=0,missing=0}
  for _, phase in ipairs(request.inspect.phases) do for _, a in ipairs(phase.actions) do
    local found=false
    for _,e in pairs(surface.find_entities_filtered{position=a.position,name=a.entity,force=force}) do
      if e.position.x==a.position.x and e.position.y==a.position.y then found=true end
    end
    if found then result.built=result.built+1
    else
      local ghost=false
      for _,e in pairs(surface.find_entities_filtered{position=a.position,type="entity-ghost",force=force}) do
        if e.ghost_name==a.entity and e.position.x==a.position.x and e.position.y==a.position.y then ghost=true end
      end
      if ghost then result.pending=result.pending+1 else result.missing=result.missing+1 end
    end
  end end
  return result
end
local function validate(candidate)
  local network=candidate.network
  if candidate.network_anchor then
    network=nil
    for _,port in ipairs(facts.ports) do
      if port.position.x==candidate.network_anchor.x and port.position.y==candidate.network_anchor.y then network=port.network end
    end
  end
  local net=network and facts.networks[network]
  if not net or net.construction_robots==0 then return "no_construction_network" end
  local bill={}
  for _, phase in ipairs(candidate.plan.phases) do
    for _, a in ipairs(phase.actions) do
      if a.action_type~="place_ghost" then return "ghosts_only" end
      local p=a.position
      if p.x<bounds.x_min+2 or p.y<bounds.y_min+2 or p.x>=bounds.x_max_exclusive-2 or p.y>=bounds.y_max_exclusive-2 then return "scope" end
      if not in_network(p,network) then return "construction_coverage" end
      local prototype=prototypes.entity[a.entity]
      if not prototype then return "prototype" end
      if prototype.type=="logistic-container" then
        local serving=surface.find_logistic_network_by_position(p,force)
        if not serving or tostring(serving.network_id)~=network then return "logistic_coverage" end
        if net.logistic_robots==0 then return "no_logistic_robots" end
      end
      local item=(prototype.items_to_place_this or {})[1]
      if not item then return "unfundable_prototype" end
      local exists=false
      for _,e in pairs(surface.find_entities_filtered{position=p,name=a.entity,force=force}) do
        if e.position.x==p.x and e.position.y==p.y then exists=true end
      end
      if not exists then
        bill[item.name]=(bill[item.name] or 0)+item.count
        if not surface.can_place_entity{name=a.entity,position=p,direction=defines.direction[a.direction or "north"],force=force} then return "collision" end
      end
      if a.recipe then
        local r=force.recipes[a.recipe]
        if not r or not r.enabled then return "recipe_unavailable" end
        local supported=false
        for _,category in pairs(r.categories or r.prototype.categories or {}) do
          if prototype.crafting_categories and prototype.crafting_categories[category] then supported=true end
        end
        if not supported then return "recipe_category" end
        if prototype.ingredient_count and prototype.ingredient_count>0 and #r.ingredients>prototype.ingredient_count then return "recipe_ingredients" end
      end
    end
  end
  for name,count in pairs(bill) do
    if (net.items[name] or 0)-(net.reserved[name] or 0)<count then return "item_shortfall:"..name end
  end
  return nil,bill
end
if request.candidates then
  local results={}
  for _, c in ipairs(request.candidates) do local reason,bill=validate(c); results[c.option_id]={reason=reason,bill=bill} end
  return {tick=game.tick, validations=results}
end
return facts
