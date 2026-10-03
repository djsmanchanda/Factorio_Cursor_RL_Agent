-- Path: training/laya_mall_world.lua | Purpose: Own one isolated mall fixture, atomic input/consumption receipts and live funded-plan checks.
assert(game.tick_paused and game.ticks_to_run==0, "mall clock must be paused")
local cfg=request.scenario
assert(cfg.version=="mall-world-v1" and string.sub(cfg.surface,1,10)=="laya-mall/", "mall surface identity")
assert(string.sub(cfg.force,1,10)=="laya-mall-", "mall force identity")
local function contents(inv)
  local out={}
  if inv then for _,v in pairs(inv.get_contents()) do if v.quality=="normal" then out[v.name]=(out[v.name] or 0)+v.count end end end
  return out
end
local function add(to,from) for k,v in pairs(from) do to[k]=(to[k] or 0)+v end end
local s=storage.laya_mall
if request.op=="initialize" then
  if s then assert(s.scenario_hash==cfg.scenario_hash and s.surface==cfg.surface and not s.partial,"existing mall mismatch")
  else
    assert(not game.surfaces[cfg.surface] and not game.forces[cfg.force],"mall identity already exists")
    s={scenario_hash=cfg.scenario_hash,surface=cfg.surface,force=cfg.force,revision=0,partial=true,
       start_tick=game.tick,fixtures={},served={},completed_at={},customer_removed={},queue=cfg.initial_queue,receipts={}}
    storage.laya_mall=s
    local surface=game.create_surface(cfg.surface,{width=96,height=96,autoplace_controls={},autoplace_settings={entity={treat_missing_as_default=false},tile={treat_missing_as_default=false},decorative={treat_missing_as_default=false}}})
    surface.request_to_generate_chunks({0,0},2); surface.force_generate_chunk_requests(); surface.always_day=true
    local tiles={}
    for x=cfg.bounds.x_min,cfg.bounds.x_max_exclusive-1 do for y=cfg.bounds.y_min,cfg.bounds.y_max_exclusive-1 do
      table.insert(tiles,{name="refined-concrete",position={x,y}})
    end end
    surface.set_tiles(tiles)
    local force=game.create_force(cfg.force); force.research_all_technologies()
    for _,f in ipairs(cfg.fixtures) do
      local e=assert(surface.create_entity{name=f.entity,position=f.position,force=force},"fixture placement failed")
      e.minable_flag=false; e.destructible=false; e.rotatable=false
      s.fixtures[f.id]=e.unit_number
      if f.id=="power" then e.electric_buffer_size=1000000000; e.power_production=1000000000; e.power_usage=0; e.energy=1000000000 end
      if f.id=="roboport" then
        e.energy=100000000
        local inv=e.get_inventory(defines.inventory.roboport_robot)
        assert(inv.insert{name="construction-robot",count=cfg.robots.construction}==cfg.robots.construction,"construction bots")
        assert(inv.insert{name="logistic-robot",count=cfg.robots.logistic}==cfg.robots.logistic,"logistic bots")
      end
      if f.id=="starter" then
        local inv=e.get_inventory(defines.inventory.chest)
        for item,count in pairs(cfg.kit) do assert(inv.insert{name=item,count=count}==count,"starter kit insertion") end
      end
    end
    s.ledger=request.initial_ledger; s.ledger.tick=game.tick
    for _,d in ipairs(cfg.demands) do s.served[d.id]=0 end
    s.partial=false
  end
end
assert(s and not s.partial and s.scenario_hash==cfg.scenario_hash and s.surface==cfg.surface and s.force==cfg.force,"not this mall fixture")
local surface=assert(game.surfaces[s.surface],"mall surface missing"); local force=assert(game.forces[s.force],"mall force missing")
local allowed={}
for item,_ in pairs(cfg.kit) do allowed[item]=true end
allowed["long-handed-inserter"]=true
local fixtures={}; for _,id in pairs(s.fixtures) do fixtures[id]=true end
local function fixture_entity(id)
  for _,f in ipairs(cfg.fixtures) do if f.id==id then
    for _,e in pairs(surface.find_entities_filtered{name=f.entity,position=f.position,force=force}) do
      if e.position.x==f.position.x and e.position.y==f.position.y and e.unit_number==s.fixtures[id] then return e end
    end
  end end
  error("fixture missing or replaced: "..id)
end
local starter=fixture_entity("starter")
local port=fixture_entity("roboport")
local network=assert(port.logistic_network,"mall logistics unavailable")
local stats=force.get_item_production_statistics(surface)
local facts={version=cfg.version,tick=game.tick,elapsed_ticks=game.tick-s.start_tick,revision=s.revision,scenario=cfg,
             surface=s.surface,force=s.force,mods=script.active_mods,provider_stock={},reserved={},wip={},machines={},
             production={},consumption={},recipes={},construction_committed={},pending_ghosts=0,
             ledger=s.ledger,served=s.served,completed_at=s.completed_at or {},customer_removed=s.customer_removed,queue=s.queue,
             robots={construction=network.all_construction_robots,logistic=network.all_logistic_robots}}
local providers={}
local statuses={}; for name,id in pairs(defines.entity_status) do statuses[id]=name end
local function read_inventory(e,index)
  local ok,inv=pcall(function() return e.get_inventory(index) end)
  assert(ok,"mall inventory read: "..e.name..": "..tostring(inv))
  return contents(inv)
end
for _,e in pairs(surface.find_entities_filtered{force=force}) do
  local name=e.type=="entity-ghost" and e.ghost_name or e.name
  if not fixtures[e.unit_number] and allowed[name] then
    facts.construction_committed[name]=(facts.construction_committed[name] or 0)+1
  end
  if e.type=="entity-ghost" then
    facts.pending_ghosts=facts.pending_ghosts+1
    if allowed[e.ghost_name] then facts.reserved[e.ghost_name]=(facts.reserved[e.ghost_name] or 0)+1 end
  elseif e.type=="logistic-container" then
    local mode=e.prototype.logistic_mode
    local inv=e.get_inventory(defines.inventory.chest)
    if mode=="passive-provider" or mode=="active-provider" or mode=="storage" then
      assert(surface.find_logistic_network_by_position(e.position,force)==network,"provider is not in mall network")
      table.insert(providers,e); add(facts.provider_stock,contents(inv))
    else add(facts.wip,contents(inv)) end
  elseif e.type=="assembling-machine" then
    local input=read_inventory(e,defines.inventory.crafter_input)
    local output=read_inventory(e,defines.inventory.crafter_output)
    add(facts.wip,input); add(facts.wip,output)
    local ok,recipe=pcall(function() return e.get_recipe() end)
    assert(ok,"mall recipe read: "..e.name..": "..tostring(recipe))
    table.insert(facts.machines,{name=e.name,position=e.position,energy=e.energy,active=e.active,status=statuses[e.status] or "unknown",
                               recipe=recipe and recipe.name,input=input,output=output})
  elseif e.type=="inserter" then
    if e.held_stack.valid_for_read and e.held_stack.quality.name=="normal" then
      facts.wip[e.held_stack.name]=(facts.wip[e.held_stack.name] or 0)+e.held_stack.count
    end
  end
end
for _,item in ipairs(cfg.recipes) do
  local r=assert(force.recipes[item],"declared recipe missing")
  assert(r.enabled,"declared recipe disabled")
  local parts={}; local product=0
  for _,part in pairs(r.ingredients) do assert(part.type~="fluid","mall solid recipes only"); table.insert(parts,{name=part.name,amount=part.amount}) end
  for _,part in pairs(r.products) do if part.name==item then product=part.amount or 0 end end
  facts.recipes[item]={ingredients=parts,energy_ticks=math.floor(r.energy*60+.5),product_amount=product}
end
local tracked={"iron-plate","copper-plate","requester-chest","passive-provider-chest"}
for _,name in ipairs(cfg.recipes) do table.insert(tracked,name) end
for _,name in ipairs(tracked) do
  facts.production[name]=stats.get_input_count(name); facts.consumption[name]=stats.get_output_count(name)
end
local function available_new(item)
  local ordinary=(facts.consumption[item] or 0)+(facts.construction_committed[item] or 0)
  local seeded=cfg.kit[item] or 0
  local seed_left=math.max(0,seeded-ordinary)
  local produced=(facts.production[item] or 0)-(s.ledger.baseline_production[item] or 0)
  local remaining=produced-math.max(0,ordinary-seeded)-(s.ledger.spent_crafting[item] or 0)-(s.customer_removed[item] or 0)
  local free=(facts.provider_stock[item] or 0)-(facts.reserved[item] or 0)
  return math.max(0,math.min(remaining,free-seed_left))
end
local function remove(item,count)
  assert(available_new(item)>=count,"manufactured provider stock shortfall: "..item)
  local remaining=count
  for _,e in ipairs(providers) do
    local taken=e.get_inventory(defines.inventory.chest).remove{name=item,count=remaining,quality="normal"}
    remaining=remaining-taken; if remaining==0 then break end
  end
  assert(remaining==0,"physical removal incomplete")
end
local function inside(p)
  return p.x>=cfg.bounds.x_min+2 and p.x<cfg.bounds.x_max_exclusive-2 and p.y>=cfg.bounds.y_min+2 and p.y<cfg.bounds.y_max_exclusive-2
end
local function validate(candidate)
  local plan=candidate.plan
  if plan.surface~=s.surface or plan.force~=s.force or plan.atomic~=true then return "mall_scope" end
  local bill={}
  for _,phase in ipairs(plan.phases) do for _,a in ipairs(phase.actions) do
    if a.action_type~="place_ghost" or not allowed[a.entity] or not inside(a.position) then return "mall_build_scope" end
    for _,f in ipairs(cfg.fixtures) do if a.position.x==f.position.x and a.position.y==f.position.y then return "protected_fixture" end end
    if math.abs(a.position.x-port.position.x)>port.prototype.construction_radius-2 or math.abs(a.position.y-port.position.y)>port.prototype.construction_radius-2 then return "construction_coverage" end
    local proto=prototypes.entity[a.entity]
    if proto.type=="logistic-container" and surface.find_logistic_network_by_position(a.position,force)~=network then return "logistic_coverage" end
    local exists=false
    for _,e in pairs(surface.find_entities_filtered{name=a.entity,position=a.position,force=force}) do
      if e.position.x==a.position.x and e.position.y==a.position.y then exists=true end
    end
    if not exists then
      if not surface.can_place_entity{name=a.entity,position=a.position,direction=defines.direction[a.direction or "north"],force=force} then return "collision" end
      bill[a.entity]=(bill[a.entity] or 0)+1
    end
    if a.recipe then
      if not facts.recipes[a.recipe] then return "recipe_unavailable" end
      local r=force.recipes[a.recipe]; local supported=false
      for _,category in pairs(r.categories) do if proto.crafting_categories and proto.crafting_categories[category] then supported=true end end
      if not supported then return "recipe_category" end
    end
  end end
  for item,count in pairs(bill) do if (facts.provider_stock[item] or 0)-(facts.reserved[item] or 0)<count then return "item_shortfall:"..item end end
  return nil,bill
end
if request.op=="validate" then
  local out={}
  for _,candidate in ipairs(request.candidates) do local reason,bill=validate(candidate); out[candidate.option_id]={reason=reason,bill=bill} end
  return {validations=out}
end
if request.op=="inspect" then
  local out={built=0,pending=0,missing=0}
  for _,phase in ipairs(request.plan.phases) do for _,a in ipairs(phase.actions) do
    local built=false
    for _,e in pairs(surface.find_entities_filtered{name=a.entity,position=a.position,force=force}) do if e.position.x==a.position.x and e.position.y==a.position.y then built=true end end
    if built then out.built=out.built+1
    else
      local ghost=false
      for _,e in pairs(surface.find_entities_filtered{type="entity-ghost",position=a.position,force=force}) do if e.ghost_name==a.entity and e.position.x==a.position.x and e.position.y==a.position.y then ghost=true end end
      if ghost then out.pending=out.pending+1 else out.missing=out.missing+1 end
    end
  end end
  return out
end
if request.op=="observe" or request.op=="initialize" then return facts end
if request.op=="view_status" then
  local viewers={}
  for _,p in pairs(game.connected_players) do
    table.insert(viewers,{name=p.name,surface=p.surface.name,force=p.force.name,spectator=p.controller_type==defines.controllers.spectator})
  end
  return {players=viewers,tick=game.tick,paused=game.tick_paused}
end
if request.op=="view" then
  assert(type(request.observer)=="string" and #request.observer>0 and #request.observer<=128,"named observer required")
  local player=game.get_player(request.observer)
  assert(player and player.connected,"observer is not connected")
  player.set_controller{type=defines.controllers.spectator}; player.force=force
  force.chart(surface,{{cfg.bounds.x_min,cfg.bounds.y_min},{cfg.bounds.x_max_exclusive,cfg.bounds.y_max_exclusive}})
  assert(player.teleport({x=-4,y=0},surface),"observer teleport failed")
  player.zoom=.8
  player.print("Laya mall live view: supplied-input mechanics, spectator only. No trained mall policy is running.")
  return {observer=player.name,surface=player.surface.name,force=player.force.name,spectator=player.controller_type==defines.controllers.spectator}
end
assert(request.nonce and request.operation_hash,"mutation identity required")
local previous=s.receipts[request.nonce]
if previous then assert(previous.operation_hash==request.operation_hash,"nonce reused for another intent"); return previous end
assert(not s.pending_operation,"interrupted mall mutation needs review")
assert(request.revision==s.revision and request.tick==game.tick,"stale mall mutation")
assert(facts.elapsed_ticks<=cfg.max_episode_ticks,"mall episode budget exhausted")
local receipt={receipt_id=request.nonce,operation_hash=request.operation_hash,operation=request.op,tick=game.tick}
if request.op=="tick" then
  assert(game.tick>=s.ledger.tick and game.tick-s.ledger.tick<=60,"invalid supply quantum")
  s.pending_operation=request.nonce
  local inv=starter.get_inventory(defines.inventory.chest)
  local inserted={}
  for _,item in ipairs({"iron-plate","copper-plate"}) do
    local credit=s.ledger.accrued_120ths[item]+(game.tick-s.ledger.tick)*s.ledger.rate_halves_per_second[item]
    local wanted=math.floor(credit/120)
    local count=wanted>0 and inv.insert{name=item,count=wanted,quality="normal"} or 0
    s.ledger.accrued_120ths[item]=credit-count*120
    s.ledger.fixture_delivered[item]=s.ledger.fixture_delivered[item]+count
    inserted[item]=count
  end
  s.ledger.tick=game.tick; receipt.inserted=inserted
elseif request.op=="priority" then
  local found=false
  for _,goal in ipairs(s.queue) do if goal==request.goal then found=true end end
  assert(found,"unknown goal")
  s.pending_operation=request.nonce
  local order={request.goal}; for _,goal in ipairs(s.queue) do if goal~=request.goal then table.insert(order,goal) end end
  s.queue=order
elseif request.op=="toggle" then
  local machine=nil
  for _,m in ipairs(facts.machines) do
    if m.position.x==request.position.x and m.position.y==request.position.y then machine=m end
  end
  assert(machine and type(request.active)=="boolean" and request.active~=machine.active,"stale machine control")
  local entity=nil
  for _,e in pairs(surface.find_entities_filtered{name="assembling-machine-1",position=request.position,force=force}) do
    if e.position.x==request.position.x and e.position.y==request.position.y then entity=e end
  end
  assert(entity and not fixtures[entity.unit_number],"machine control scope")
  s.pending_operation=request.nonce
  entity.disabled_by_script=not request.active
  receipt.position=request.position; receipt.active=not entity.disabled_by_script
  assert(receipt.active==request.active,"machine control was not applied")
elseif request.op=="deliver" then
  local task=nil
  for _,d in ipairs(cfg.demands) do if d.id==request.goal then task=d end end
  assert(task and facts.elapsed_ticks>=task.release_ticks,"customer job is unavailable")
  local count=math.min(task.count-s.served[task.id],available_new(task.item))
  assert(count>0 and count==request.count,"stale customer delivery")
  s.pending_operation=request.nonce
  remove(task.item,count); s.served[task.id]=s.served[task.id]+count
  if s.served[task.id]==task.count then
    s.completed_at=s.completed_at or {}; s.completed_at[task.id]=facts.elapsed_ticks
  end
  s.customer_removed[task.item]=(s.customer_removed[task.item] or 0)+count
  receipt.removed={[task.item]=count}; receipt.goal=task.id
elseif request.op=="upgrade" then
  local plan=request.upgrade
  assert(plan.version==s.ledger.version and plan.increment_per_second==1.5 and plan.sequence==s.ledger.upgrades+1,"invalid upgrade plan")
  assert(plan.supply=="iron-plate" or plan.supply=="copper-plate","invalid upgrade supply")
  local batch=cfg.supply_rule.batch
  for item,count in pairs(batch) do assert(plan.consumption[item]==count and available_new(item)>=count,"incomplete newly crafted batch") end
  for item,_ in pairs(plan.consumption) do assert(batch[item],"unknown batch item") end
  s.pending_operation=request.nonce
  local removed={}
  for item,count in pairs(batch) do remove(item,count); removed[item]=count end
  for item,count in pairs(batch) do s.ledger.spent_crafting[item]=s.ledger.spent_crafting[item]+count end
  s.ledger.rate_halves_per_second[plan.supply]=s.ledger.rate_halves_per_second[plan.supply]+3
  s.ledger.upgrades=s.ledger.upgrades+1; table.insert(s.ledger.consumption_receipts,request.nonce)
  receipt.removed=removed; receipt.plan_hash=request.plan_hash; receipt.supply=plan.supply
else error("unsupported mall operation") end
s.revision=s.revision+1; s.pending_operation=nil
receipt.revision=s.revision; s.receipts[request.nonce]=receipt
return receipt
