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
  infrastructure_counts={}, recipes={}, recipe_observations={}, recipe_product_index={version="1",products={}}, enabled_recipes={}, fluid_recipes={}}
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
    local q_ok,q = pcall(function() return e.quality.name end)
    local quality = q_ok and type(q)=="string" and q or nil
    local pole={name=e.name, position=e.position, wire=e.prototype.get_max_wire_distance(quality or "normal"),
      supply=e.prototype.get_supply_area_distance(quality or "normal"), grid=tostring(e.electric_network_id),
      generated=generation[tostring(e.electric_network_id)] == true}
    if quality then pole.quality=quality end
    local unit_ok, unit=pcall(function() return e.unit_number end)
    if unit_ok and type(unit)=="number" and unit>0 and unit%1==0 then pole.unit_number=unit end
    table.insert(facts.poles, pole)
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
-- Full force recipe inventory. This is read-only observation metadata: it adds
-- no operation recipe, chooses no route and grants no construction authority.
-- LuaRecipe.categories is an array of category names, not a name-keyed map.
-- Reading keys would emit numeric categories, break machine matching and
-- violate the observation contract. Reject any other shape so the survey marks
-- itself incomplete instead of inventing a category or a machine match.
local function recipe_categories(recipe)
  local categories=recipe.categories
  if categories==nil and recipe.prototype then categories=recipe.prototype.categories end
  if type(categories)~="table" then return nil end
  local result={}
  for _,category in pairs(categories) do
    if type(category)~="string" or category=="" then return nil end
    result[#result+1]=category
  end
  if #result==0 then return nil end
  table.sort(result)
  return result
end
-- Index crafting prototypes once so category-compatible speeds are observed.
local crafting_machines={}
for machine,p in pairs(prototypes.entity) do
  if p.type=="assembling-machine" or p.type=="furnace" then
    local ok,speed=pcall(function() return p.get_crafting_speed("normal") end)
    if not ok then speed=p.crafting_speed end
    if type(speed)=="number" and speed>0 then
      crafting_machines[machine]={categories=p.crafting_categories or {},
        ingredient_count=p.ingredient_count or 0,speed=speed}
    end
  end
end
local unlocks={}
for tech_name,tech in pairs(force.technologies) do
  for _,effect in pairs(tech.effects or {}) do
    if effect.type=="unlock-recipe" and effect.recipe then
      unlocks[effect.recipe]=unlocks[effect.recipe] or {}
      table.insert(unlocks[effect.recipe],tech_name)
    end
  end
end
local observation_count=0
local unsupported_categories=0
for name,r in pairs(force.recipes) do
  local ingredients={}; local products={}
  local quality_state="normal"
  local function check_quality(part)
    -- Recipe bounds/changes are not item-stack quality. Non-default bounds
    -- require quality-aware planning, which this normal-quality operation lacks.
    if (part.quality_min~=nil and part.quality_min~="normal")
      or (part.quality_max~=nil and part.quality_max~="normal") or (part.quality_change or 0)~=0 then
      quality_state="unsupported"
    end
  end
  for _,part in pairs(r.ingredients or {}) do
    check_quality(part)
    table.insert(ingredients,{name=part.name,type=part.type,amount=part.amount,
      quality_min=part.quality_min,quality_max=part.quality_max,quality_change=part.quality_change})
  end
  for _,part in pairs(r.products or {}) do
    check_quality(part)
    table.insert(products,{name=part.name,type=part.type,amount=part.amount,
      amount_min=part.amount_min,amount_max=part.amount_max,probability=part.probability,
      independent_probability=part.independent_probability,shared_probability=part.shared_probability,
      extra_count_fraction=part.extra_count_fraction,
      quality_min=part.quality_min,quality_max=part.quality_max,quality_change=part.quality_change,
      ignored_by_productivity=part.ignored_by_productivity})
    -- Item counters include science packs and every other item product.
    -- Fluid counters stay out of item production and provider inventory.
    if part.type=="item" then
      facts.production[part.name]=stats.get_input_count(part.name)
      facts.consumption[part.name]=stats.get_output_count(part.name)
    end
  end
  local category_list=recipe_categories(r)
  if category_list==nil then
    unsupported_categories=unsupported_categories+1
    category_list={}
  end
  local speeds={}
  for machine,p in pairs(crafting_machines) do
    local compatible=false
    for _,category in pairs(category_list) do
      if p.categories[category] then compatible=true end
    end
    if compatible and (p.ingredient_count==0 or #ingredients<=p.ingredient_count) then
      speeds[machine]=p.speed
    end
  end
  local energy=(r.energy or .5)*60
  local observation={enabled=r.enabled,ingredients=ingredients,products=products,
    categories=category_list,machine_speeds=speeds,quality_state=quality_state,
    energy_ticks=math.max(1/60,energy),productivity_bonus=r.productivity_bonus}
  if category_list[1] then observation.category=category_list[1] end
  if unlocks[name] then table.sort(unlocks[name]); observation.unlock_technologies=unlocks[name] end
  facts.recipe_observations[name]=observation
  for _,part in ipairs(products) do
    local key=part.type .. ":" .. part.name
    local entries=facts.recipe_product_index.products[key] or {}
    entries[#entries+1]=name
    facts.recipe_product_index.products[key]=entries
  end
  if r.enabled then facts.enabled_recipes[name]=true end
  -- Enabled recipes touching a fluid get their own typed key; facts.recipes
  -- (Laya's solid build menu) never sees them. Products carry their expected
  -- amount so multi-product and probabilistic outputs read as plain rates.
  local fluid=false
  for _,part in ipairs(ingredients) do if part.type=="fluid" then fluid=true end end
  for _,part in ipairs(products) do if part.type=="fluid" then fluid=true end end
  if r.enabled and fluid then
    local typed_products={}
    for _,part in ipairs(products) do
      local amount=part.amount or ((part.amount_min or 0)+(part.amount_max or 0))/2
      table.insert(typed_products,{name=part.name,type=part.type,amount=amount*(part.probability or 1)})
    end
    local entry={ingredients=ingredients,products=typed_products,energy_ticks=math.max(1/60,energy),
      machine_speeds=speeds,productivity_bonus=r.productivity_bonus}
    if category_list[1] then entry.category=category_list[1] end
    facts.fluid_recipes[name]=entry
  end
  observation_count=observation_count+1
end
for _,entries in pairs(facts.recipe_product_index.products) do table.sort(entries) end
-- The count covers exactly force.recipes at this tick, enabled or not. Other
-- forces and prototype-only recipes are outside this survey's declared scope.
-- This envelope is survey-local, not the /export_recipe_catalog document. It
-- reuses the consumer's version token only and carries none of that document's
-- recipes, machines, raw_resources or stack_sizes fields.
facts.recipe_catalog={version="1.2.0",force=force.name,tick=game.tick,surface="nauvis",
  recipe_count=observation_count,source="force.recipes",complete=unsupported_categories==0,
  document="saved-world-recipe-survey",metadata_only=true,
  category_metadata_unsupported=unsupported_categories}
-- Build the solid operation view from the complete force survey. The current
-- Single-output solid assembly operations use observed recipe identity.
-- Other outputs remain in the product index and observation facts.
local names={}
-- These are the existing automatic-recipe furnace operations, not discovery
-- limits: every force recipe still appears in the typed product index above.
local furnace_operations={["iron-plate"]=true,["copper-plate"]=true,
  ["stone-brick"]=true,["steel-plate"]=true}
for name in pairs(force.recipes) do names[#names+1]=name end
table.sort(names)
for _,name in ipairs(names) do
  local r=force.recipes[name]
  local obs=facts.recipe_observations[name]
  local product=obs.products[1]
  local machine=obs.machine_speeds["assembling-machine-1"] and "assembling-machine-1"
    or (product and furnace_operations[product.name] and obs.machine_speeds["electric-furnace"] and "electric-furnace")
  local cause=nil
  if obs.quality_state~="normal" then cause="quality_unsupported"
  elseif #obs.products~=1 then cause="multiple_outputs"
  elseif product.type~="item" then cause="fluid_operation"
  elseif not obs.categories[1] then cause="category_unknown"
  elseif not machine then cause="category_unsupported"
  elseif type(product.amount)~="number" or product.amount<=0 then cause="nondeterministic_output"
  elseif (product.probability~=nil and product.probability~=1)
    or (product.independent_probability~=nil and product.independent_probability~=1)
    or (product.shared_probability~=nil and (product.shared_probability.min~=0 or product.shared_probability.max~=1))
    or (product.extra_count_fraction~=nil and product.extra_count_fraction~=0)
    or product.amount_min~=nil or product.amount_max~=nil then cause="nondeterministic_output" end
  for _,part in ipairs(obs.ingredients) do
    if part.type~="item" then cause="fluid_operation"
    elseif type(part.amount)~="number" or part.amount<=0 then cause=cause or "ingredient_unsupported" end
  end
  obs.solid_classification={version="1",supported=cause==nil,cause=cause,machine=machine or nil}
  local solid=r.enabled and cause==nil and facts.recipes[product.name]==nil

  if solid then
    facts.recipes[product.name]={recipe=name,ingredients=obs.ingredients,product_amount=product.amount,
      energy_ticks=math.max(1,math.floor(obs.energy_ticks+.5)),machine_speeds=obs.machine_speeds}
  end
end
for _, name in ipairs({"iron-ore","copper-ore","coal","stone"}) do
  facts.production[name]=stats.get_input_count(name); facts.consumption[name]=stats.get_output_count(name)
end
for _,name in ipairs(recipe_names) do
  facts.production[name]=stats.get_input_count(name); facts.consumption[name]=stats.get_output_count(name)
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
  -- targets records each planned entity's state and unit number, so the adapter
  -- can tell an entity that already stood before submission from one built by it.
  local result={built=0,pending=0,missing=0,targets={}}
  for _, phase in ipairs(request.inspect.phases) do for _, a in ipairs(phase.actions) do
    local found=nil
    for _,e in pairs(surface.find_entities_filtered{position=a.position,name=a.entity,force=force}) do
      if e.position.x==a.position.x and e.position.y==a.position.y then found=e end
    end
    local target={entity=a.entity,position={x=a.position.x,y=a.position.y}}
    if found then result.built=result.built+1; target.state="built"; target.unit_number=found.unit_number
    else
      local ghost=false
      for _,e in pairs(surface.find_entities_filtered{position=a.position,type="entity-ghost",force=force}) do
        if e.ghost_name==a.entity and e.position.x==a.position.x and e.position.y==a.position.y then ghost=true end
      end
      if ghost then result.pending=result.pending+1; target.state="ghost" else result.missing=result.missing+1; target.state="missing" end
    end
    result.targets[#result.targets+1]=target
  end end
  return result
end
-- A diagnostic only: prototype capability is not a fluid-topology or purity
-- check. Missing/unsupported API facts stay unknown and never authorize a plan.
local function underground_competitors(plan)
  local result={planned=0,conflicts=0,unknown=0,complete=true}
  local names={north={0,-1},east={1,0},south={0,1},west={-1,0}}
  local directions={[0]={0,-1},[4]={1,0},[8]={0,1},[12]={-1,0}}
  for _,phase in ipairs(plan.phases or {}) do
    for _,a in ipairs(phase.actions or {}) do
      if a.entity=="pipe-to-ground" then
        result.planned=result.planned+1
        if result.planned>256 then result.complete=false; return result end
        local normal=names[a.direction or "north"]
        local p=a.position
        if not normal or type(p)~="table" or type(p.x)~="number" or type(p.y)~="number"
          or p.x~=p.x or p.y~=p.y or math.abs(p.x)==math.huge or math.abs(p.y)==math.huge
          or p.x%1~=0.5 or p.y%1~=0.5 then
          result.unknown=result.unknown+1
        else
          local area={{p.x-10.5,p.y-10.5},{p.x+10.5,p.y+10.5}}
          local ok,entities,ghosts=pcall(function()
            return surface.find_entities_filtered{name="pipe-to-ground",area=area,limit=4097},
              surface.find_entities_filtered{type="entity-ghost",area=area,limit=4097}
          end)
          if not ok or type(entities)~="table" or type(ghosts)~="table" then
            result.unknown=result.unknown+1
          elseif #entities>=4097 or #ghosts>=4097 then
            result.complete=false; return result
          else
            local function check(e, ghost)
              local read_ok, name, position, direction = pcall(function()
                if ghost then return e.ghost_name, e.position, e.direction end
                return e.name, e.position, e.direction
              end)
              if not read_ok or type(name)~="string" then result.unknown=result.unknown+1; return end
              if name~="pipe-to-ground" then return end
              local other=directions[direction]
              if not other or type(position)~="table" or type(position.x)~="number" or type(position.y)~="number"
                or position.x~=position.x or position.y~=position.y
                or math.abs(position.x)==math.huge or math.abs(position.y)==math.huge
                or position.x%1~=0.5 or position.y%1~=0.5 then
                result.unknown=result.unknown+1; return
              end
              local dx,dy=position.x-p.x,position.y-p.y
              local distance=math.abs(dx)+math.abs(dy)
              if distance>=1 and distance<=10 and dx==-normal[1]*distance and dy==-normal[2]*distance
                and other[1]==-normal[1] and other[2]==-normal[2] then
                result.conflicts=result.conflicts+1
              end
            end
            for _,e in pairs(entities) do check(e,false) end
            for _,e in pairs(ghosts) do check(e,true) end
          end
        end
      end
    end
  end
  return result
end

-- Existing endpoints may carry an unrelated or unknown fluid. Until named
-- attachment ports are verified, even a same-fluid connection is rejected.
local function surface_pipe_contacts(plan)
  local result={planned=0,contacts=0,unknown=0,complete=true}
  for _,phase in ipairs(plan.phases or {}) do
    for _,a in ipairs(phase.actions or {}) do
      if a.entity=="pipe" or a.entity=="pipe-to-ground" then
        result.planned=result.planned+1
        if result.planned>2048 then result.complete=false; return result end
        local p=a.position
        if type(p)~="table" or type(p.x)~="number" or type(p.y)~="number"
          or p.x~=p.x or p.y~=p.y or math.abs(p.x)==math.huge or math.abs(p.y)==math.huge
          or p.x%1~=0.5 or p.y%1~=0.5 then
          result.unknown=result.unknown+1
        else
          local area={{p.x-1.5,p.y-1.5},{p.x+1.5,p.y+1.5}}
          local ok,entities,ghosts=pcall(function()
            return surface.find_entities_filtered{name={"pipe","pipe-to-ground"},area=area,limit=4097},
              surface.find_entities_filtered{type="entity-ghost",area=area,limit=4097}
          end)
          if not ok or type(entities)~="table" or type(ghosts)~="table" then
            result.unknown=result.unknown+1
          elseif #entities>=4097 or #ghosts>=4097 then
            result.complete=false; return result
          else
            local function check(e, ghost)
              local read_ok,name,position=pcall(function()
                if ghost then return e.ghost_name,e.position end
                return e.name,e.position
              end)
              if not read_ok or type(name)~="string" then result.unknown=result.unknown+1; return end
              if name~="pipe" and name~="pipe-to-ground" then return end
              if type(position)~="table" or type(position.x)~="number" or type(position.y)~="number"
                or position.x~=position.x or position.y~=position.y
                or math.abs(position.x)==math.huge or math.abs(position.y)==math.huge
                or position.x%1~=0.5 or position.y%1~=0.5 then
                result.unknown=result.unknown+1; return
              end
              if math.abs(position.x-p.x)+math.abs(position.y-p.y)<=1 then
                result.contacts=result.contacts+1
              end
            end
            for _,e in pairs(entities) do check(e,false) end
            for _,e in pairs(ghosts) do check(e,true) end
          end
        end
      end
    end
  end
  return result
end

local function fluid_prototype_survey(plan)
  local result={actions=0,without_fluidboxes=0,with_fluidboxes=0,unknown=0}
  for _,phase in ipairs(plan.phases or {}) do
    for _,a in ipairs(phase.actions or {}) do
      result.actions=result.actions+1
      local prototype=prototypes.entity[a.entity]
      if not prototype then
        result.unknown=result.unknown+1
      else
        local ok,boxes=pcall(function() return prototype.fluidbox_prototypes end)
        if not ok or type(boxes)~="table" then
          result.unknown=result.unknown+1
        elseif next(boxes) then
          result.with_fluidboxes=result.with_fluidboxes+1
        else
          result.without_fluidboxes=result.without_fluidboxes+1
        end
      end
    end
  end
  return result
end
-- Read-only observation for the Python fluid-topology validator; it judges
-- nothing itself. Built segment fluids/filters are observed identity, ghosts
-- carry only declared names/recipes. Any unreadable fact or cap overflow sets
-- complete=false so the validator fails closed.
local FLUID_DIRECTIONS={[0]="north",[4]="east",[8]="south",[12]="west"}
local FLUID_SNAPSHOT_MARGIN=16
local FLUID_SNAPSHOT_LIMIT=20000
local CRAFTING_TYPES={["assembling-machine"]=true,furnace=true,["rocket-silo"]=true}
local fluid_types_cache=nil
local function fluid_entity_types()
  if fluid_types_cache then return fluid_types_cache end
  local seen,list={},{}
  for _,prototype in pairs(prototypes.entity) do
    local ok,kind,boxes=pcall(function() return prototype.type,prototype.fluidbox_prototypes end)
    if not ok or type(kind)~="string" or type(boxes)~="table" then return nil end
    if next(boxes) and not seen[kind] then seen[kind]=true; list[#list+1]=kind end
  end
  table.sort(list)
  fluid_types_cache=list
  return list
end
local function fluid_snapshot_entity(e, ghost, snapshot)
  local ok,name,kind,position,direction,force_name,mirroring=pcall(function()
    if ghost then return e.ghost_name,e.ghost_type,e.position,e.direction,e.force.name,e.mirroring end
    return e.name,e.type,e.position,e.direction,e.force.name,e.mirroring
  end)
  if not ok or type(name)~="string" or type(position)~="table" then snapshot.complete=false; return end
  local entry={name=name,ghost=ghost,force=force_name,position={x=position.x,y=position.y},
    direction=FLUID_DIRECTIONS[direction] or tostring(direction),mirroring=mirroring}
  if CRAFTING_TYPES[kind] then
    local recipe_ok,recipe=pcall(function() return e.get_recipe() end)
    if recipe_ok and recipe then entry.recipe=recipe.name end
  end
  if not ghost then
    entry.unit_number=e.unit_number
    entry.fluidboxes={}
    local count_ok,boxes=pcall(function() return e.prototype.fluidbox_prototypes end)
    if not count_ok or type(boxes)~="table" then snapshot.complete=false; return end
    for index=1,#boxes do
      local box={index=index,readable=false}
      local read_ok,proto_index,fluid,filter=pcall(function()
        local prototype=e.get_fluid_box_prototype(index)
        local segment=e.has_fluid_segment(index) and e.get_fluid_segment_fluid(index) or e.get_fluid(index)
        return prototype.index,segment,e.get_fluid_filter(index)
      end)
      if read_ok and proto_index==index then
        box.readable=true
        box.segment_fluid=fluid and fluid.name or nil
        box.filter=filter and filter.name or nil
      end
      entry.fluidboxes[#entry.fluidboxes+1]=box
    end
  end
  snapshot.entities[#snapshot.entities+1]=entry
end
-- Observed terrain for underground-span checks: run-length rows of palette
-- indices (0-based), each palette tile's runtime collision layers, and tiles
-- with a pending tile ghost or tile deconstruction. Its own complete flag is
-- false on any unreadable tile, ungenerated chunk or size overflow; entity
-- snapshot completeness is unaffected.
local FLUID_TILE_SCAN_LIMIT=262144
local function fluid_tile_scan(area)
  local scan={version="fluid-tile-scan-v1",tick=game.tick,area=area,complete=false,palette={},rows={},
    prototypes={},pending={}}
  local x0,y0,x1,y1=area.x_min,area.y_min,area.x_max_exclusive,area.y_max_exclusive
  if (x1-x0)*(y1-y0)>FLUID_TILE_SCAN_LIMIT then return scan end
  local index,palette,rows,layers_by_name,pending={},{},{},{},{}
  local ok=pcall(function()
    for cx=math.floor(x0/32),math.floor((x1-1)/32) do
      for cy=math.floor(y0/32),math.floor((y1-1)/32) do
        if surface.is_chunk_generated({x=cx,y=cy})~=true then error("chunk not generated") end
      end
    end
    for y=y0,y1-1 do
      local row,last,run={},nil,0
      for x=x0,x1-1 do
        local tile=surface.get_tile(x,y)
        local name=tile.name
        local i=index[name]
        if i==nil then
          local mask=prototypes.tile[name].collision_mask
          if type(mask)~="table" or type(mask.layers)~="table" then error("tile mask unreadable") end
          local layers={}
          for layer,value in pairs(mask.layers) do
            if type(layer)~="string" or value~=true then error("tile mask malformed") end
            layers[#layers+1]=layer
          end
          table.sort(layers)
          palette[#palette+1]=name; i=#palette-1; index[name]=i
          layers_by_name[name]={layers=layers}
        end
        if tile.has_tile_ghost()==true or tile.to_be_deconstructed()==true then pending[#pending+1]={x=x,y=y} end
        if i==last then run=run+1 else
          if last~=nil then row[#row+1]=last; row[#row+1]=run end
          last,run=i,1
        end
      end
      row[#row+1]=last; row[#row+1]=run
      rows[#rows+1]=row
    end
  end)
  if ok then
    scan.complete=true; scan.palette=palette; scan.rows=rows; scan.prototypes=layers_by_name; scan.pending=pending
  end
  return scan
end
local function fluid_topology_snapshot(plan, include_tiles)
  local snapshot={version="fluid-topology-snapshot-v1",surface="nauvis",force="player",tick=game.tick,
    forces="all",complete=true,entities={}}
  local x_min,y_min,x_max,y_max
  for _,phase in ipairs(plan.phases or {}) do
    for _,a in ipairs(phase.actions or {}) do
      local p=a.position
      if type(p)~="table" or type(p.x)~="number" or type(p.y)~="number" or p.x~=p.x or p.y~=p.y
        or math.abs(p.x)==math.huge or math.abs(p.y)==math.huge then
        snapshot.complete=false
      else
        x_min=math.min(x_min or p.x,p.x); y_min=math.min(y_min or p.y,p.y)
        x_max=math.max(x_max or p.x,p.x); y_max=math.max(y_max or p.y,p.y)
      end
    end
  end
  if not x_min then
    snapshot.complete=false
    snapshot.area={x_min=0,y_min=0,x_max_exclusive=1,y_max_exclusive=1}
    return snapshot
  end
  local m=FLUID_SNAPSHOT_MARGIN
  snapshot.area={x_min=math.floor(x_min)-m,y_min=math.floor(y_min)-m,
    x_max_exclusive=math.floor(x_max)+1+m,y_max_exclusive=math.floor(y_max)+1+m}
  if include_tiles then snapshot.tiles=fluid_tile_scan(snapshot.area) end
  local types=fluid_entity_types()
  if not types then snapshot.complete=false; return snapshot end
  local area={{snapshot.area.x_min,snapshot.area.y_min},{snapshot.area.x_max_exclusive,snapshot.area.y_max_exclusive}}
  local ok,built,ghosts=pcall(function()
    local found={}
    if #types>0 then found=surface.find_entities_filtered{area=area,type=types,limit=FLUID_SNAPSHOT_LIMIT+1} end
    return found,surface.find_entities_filtered{area=area,type="entity-ghost",limit=FLUID_SNAPSHOT_LIMIT+1}
  end)
  if not ok or type(built)~="table" or type(ghosts)~="table" or #built+#ghosts>FLUID_SNAPSHOT_LIMIT then
    snapshot.complete=false; return snapshot
  end
  for _,e in pairs(built) do fluid_snapshot_entity(e,false,snapshot) end
  for _,e in pairs(ghosts) do
    local proto_ok,boxes=pcall(function() return e.ghost_prototype.fluidbox_prototypes end)
    if not proto_ok or type(boxes)~="table" then snapshot.complete=false
    elseif next(boxes) then fluid_snapshot_entity(e,true,snapshot) end
  end
  return snapshot
end
-- A player's blueprint builds ghosts over trees and rocks and bots clear them. A tree or a
-- minable rock (simple-entity) is therefore not a collision; anything else still is.
-- Mirrored by factorio_mod/layout_executor.lua, which orders their deconstruction before the ghost.
local function clearable_obstacle(e)
  local ok,kind,minable=pcall(function() return e.type,e.minable end)
  return ok and (kind=="tree" or (kind=="simple-entity" and minable==true))
end
local function mask_layers(prototype)
  local mask=prototype and prototype.collision_mask
  return type(mask)=="table" and (mask.layers or mask) or nil
end
-- True when the footprint is refused only by clearable obstacles: terrain does not collide, at
-- least one overlapping entity exists, and every overlapping entity is a tree or minable rock.
-- Fails closed (false) on anything unreadable, on ghosts, and on entities of unknown mask.
local function footprint_blocked_only_by_clearable(prototype, a)
  local ok,result=pcall(function()
    local layers,box=mask_layers(prototype),prototype.collision_box
    if not layers or not box then return false end
    local lt,rb=box.left_top or box[1],box.right_bottom or box[2]
    local l,t,r,b=lt.x or lt[1],lt.y or lt[2],rb.x or rb[1],rb.y or rb[2]
    local direction=defines.direction[a.direction or "north"]
    if direction==defines.direction.east or direction==defines.direction.west then
      l,t,r,b=-b,l,-t,r
    elseif direction~=defines.direction.north and direction~=defines.direction.south then
      local radius=math.max(math.abs(l),math.abs(t),math.abs(r),math.abs(b))
      l,t,r,b=-radius,-radius,radius,radius
    end
    local p=a.position
    local area={{p.x+l+0.001,p.y+t+0.001},{p.x+r-0.001,p.y+b-0.001}}
    for x=math.floor(area[1][1]),math.ceil(area[2][1])-1 do
      for y=math.floor(area[1][2]),math.ceil(area[2][2])-1 do
        local tile=surface.get_tile(x,y)
        if not tile or not tile.valid then return false end
        for layer,enabled in pairs(layers) do
          if enabled==true and tile.collides_with(layer) then return false end
        end
      end
    end
    local saw=false
    for _,e in pairs(surface.find_entities_filtered{area=area}) do
      if e.valid then
        if e.type=="entity-ghost" or e.type=="tile-ghost" then return false end
        local other=mask_layers(e.prototype)
        local overlaps=other==nil
        for layer,enabled in pairs(layers) do
          if enabled==true and other and other[layer]==true then overlaps=true end
        end
        if overlaps then
          if not clearable_obstacle(e) then return false end
          saw=true
        end
      end
    end
    return saw
  end)
  return ok and result==true
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
  -- A declared per-option scope (checked by the adapter against the oil survey) replaces the request bounds.
  local scope=candidate.scope_bounds or bounds
  for _, phase in ipairs(candidate.plan.phases) do
    for _, a in ipairs(phase.actions) do
      if a.action_type~="place_ghost" then return "ghosts_only" end
      local p=a.position
      if p.x<scope.x_min+2 or p.y<scope.y_min+2 or p.x>=scope.x_max_exclusive-2 or p.y>=scope.y_max_exclusive-2 then return "scope" end
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
        if not surface.can_place_entity{name=a.entity,position=p,direction=defines.direction[a.direction or "north"],type=a.underground_type,force=force} and not footprint_blocked_only_by_clearable(prototype,a) then return "collision" end
      end
      if a.recipe then
        local r=force.recipes[a.recipe]
        if not r or not r.enabled then return "recipe_unavailable" end
        local supported=false
        for _,category in pairs(recipe_categories(r) or {}) do
          if prototype.crafting_categories and prototype.crafting_categories[category] then supported=true end
        end
        if not supported then return "recipe_category" end
        if prototype.ingredient_count and prototype.ingredient_count>0 and #r.ingredients>prototype.ingredient_count then return "recipe_ingredients" end
      end
    end
  end
  -- Every item the network cannot cover, so a blocked build shows its whole shortage at once.
  local names,missing,first={},nil,nil
  for name in pairs(bill) do names[#names+1]=name end
  table.sort(names)
  for _,name in ipairs(names) do
    local lacking=bill[name]-((net.items[name] or 0)-(net.reserved[name] or 0))
    if lacking>0 then missing=missing or {}; missing[name]=lacking; first=first or name end
  end
  if first then return "item_shortfall:"..first,nil,missing end
  return nil,bill
end
if request.candidates then
  local results={}
  for _, c in ipairs(request.candidates) do
    local reason,bill,missing=validate(c)
    local result={reason=reason,bill=bill,missing=missing}
    if c.inspect_power_topology==true then
      local poles={}
      for i=1,math.min(#facts.poles,5000) do poles[i]=facts.poles[i] end
      result.power_topology_snapshot={version="optimizer-power-snapshot-v1",tick=game.tick,
        surface=surface.name,force=force.name,mods=script.active_mods,complete=#facts.poles<=5000,poles=poles}
    end
    for _,check in ipairs(c.required_checks or {}) do
      if check=="fluid_purity" then
        result.fluid_prototype_survey=fluid_prototype_survey(c.plan)
        result.underground_competitors=underground_competitors(c.plan)
        result.surface_pipe_contacts=surface_pipe_contacts(c.plan)
        result.fluid_topology_snapshot=fluid_topology_snapshot(c.plan, c.inspect_underground_tiles==true)
        if not result.reason then
          if result.underground_competitors.conflicts>0 then
            result.reason="underground_world_competitor"
          elseif result.surface_pipe_contacts.contacts>0 then
            result.reason="surface_pipe_world_contact"
          elseif not result.underground_competitors.complete or result.underground_competitors.unknown>0 then
            result.reason="underground_world_unverified"
          elseif not result.surface_pipe_contacts.complete or result.surface_pipe_contacts.unknown>0 then
            result.reason="surface_pipe_world_unverified"
          end
        end
        break
      end
    end
    results[c.option_id]=result
  end
  return {tick=game.tick, validations=results}
end
-- Read-only oil facts for the base: the nearest crude-oil patch and water tile
-- to the base anchor (lowest roboport, else the force spawn). Searches only
-- generated chunks; creates nothing and generates no chunks.
if request.oil_search_radius then
  local anchor,source=nil,"spawn"
  for _,port in ipairs(facts.ports) do
    if not anchor or port.position.x<anchor.x or (port.position.x==anchor.x and port.position.y<anchor.y) then
      anchor=port.position; source="roboport"
    end
  end
  anchor=anchor or force.get_spawn_position(surface)
  local ax,ay,radius=anchor.x,anchor.y,request.oil_search_radius
  local field={anchor={x=ax,y=ay},anchor_source=source,search_radius=radius,found=false,wells=0}
  local wells={}
  if prototypes.entity["crude-oil"] then
    wells=surface.find_entities_filtered{type="resource",name="crude-oil",area={{ax-radius,ay-radius},{ax+radius,ay+radius}}}
  end
  local nearest,best=nil,math.huge
  for _,e in pairs(wells) do
    local d=math.sqrt((e.position.x-ax)^2+(e.position.y-ay)^2)
    if d<=radius and d<best then nearest,best=e,d end
  end
  if nearest then
    -- One patch: wells within 32 tiles of the nearest well.
    local count,amount,sx,sy=0,0,0,0
    for _,e in pairs(wells) do
      if math.abs(e.position.x-nearest.position.x)<=32 and math.abs(e.position.y-nearest.position.y)<=32 then
        count=count+1; amount=amount+e.amount; sx=sx+e.position.x; sy=sy+e.position.y
      end
    end
    local cx,cy=sx/count,sy/count
    field.found=true; field.wells=count; field.total_amount=amount
    field.center={x=cx,y=cy}; field.distance=math.sqrt((cx-ax)^2+(cy-ay)^2); field.nearest_well_distance=best
    -- Yield: amount over the prototype's normal amount (100% each); crude/s if every well had a pumpjack.
    local ok,per_amount=pcall(function()
      local p=prototypes.entity["crude-oil"]; local pump=prototypes.entity["pumpjack"]
      local mined=p.mineable_properties
      return pump.mining_speed*mined.products[1].amount/mined.mining_time/p.normal_resource_amount
    end)
    local ok_normal,normal=pcall(function() return prototypes.entity["crude-oil"].normal_resource_amount end)
    if ok_normal and type(normal)=="number" and normal>0 then field.yield_percent=100*amount/normal end
    if ok and type(per_amount)=="number" and per_amount>0 then field.max_crude_per_s=amount*per_amount end
  end
  -- Nearest water: tiles whose prototype yields water, by bisecting the search radius.
  local names={}
  for name,tile in pairs(prototypes.tile or {}) do
    local ok,fluid=pcall(function() return tile.fluid end)
    if ok and fluid and fluid.name=="water" then names[#names+1]=name end
  end
  local function water_within(r)
    return surface.count_tiles_filtered{position={ax,ay},radius=r,name=names,limit=1}>0
  end
  local ok_water,water=pcall(function()
    if #names==0 or not water_within(radius) then return {found=false} end
    local lo,hi=0,radius
    while hi-lo>1 do
      local mid=math.floor((lo+hi)/2)
      if water_within(mid) then hi=mid else lo=mid end
    end
    local tile=surface.find_tiles_filtered{position={ax,ay},radius=hi,name=names,limit=1}[1]
    local x,y=tile.position.x+.5,tile.position.y+.5
    return {found=true,position={x=x,y=y},distance=math.sqrt((x-ax)^2+(y-ay)^2)}
  end)
  field.water=ok_water and water or {found=false,unknown=true}
  facts.oil_field=field
end
return facts
