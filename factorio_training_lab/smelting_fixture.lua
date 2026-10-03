-- Path: factorio_training_lab/smelting_fixture.lua | Purpose: Explicit, inert-by-default belt smelting mechanics fixture on private training surfaces.
local M = {}
local palette={['transport-belt']=true,['express-transport-belt']=true,splitter=true,['express-splitter']=true,
  ['electric-furnace']=true,inserter=true,['fast-inserter']=true,['medium-electric-pole']=true}
local function state() return storage.smelting_fixture end
local function scope(s)
  assert(s and string.find(s.surface,"^training/smelting%-") and string.find(s.force,"^training%-smelting%-"),"invalid smelting fixture identity")
  return assert(game.surfaces[s.surface]),assert(game.forces[s.force])
end
local function create(surface, force, name, x, y)
  assert(surface.can_place_entity{name=name,position={x,y},force=force},"fixture collision: "..name.." "..x..","..y)
  local e=assert(surface.create_entity{name=name,position={x,y},force=force})
  e.minable_flag=false; e.destructible=false
  return e
end
local function init(p)
  assert(not state(),"existing smelting fixture must be preserved")
  assert(type(p.surface)=="string" and string.find(p.surface,"^training/smelting%-[a-z0-9%-]+$"))
  assert(type(p.force)=="string" and string.find(p.force,"^training%-smelting%-[a-z0-9%-]+$"))
  assert(not game.surfaces[p.surface] and not game.forces[p.force],"fixture identity already exists")
  assert(p.recipe=="iron-plate" or p.recipe=="copper-plate")
  for name,count in pairs(p.budget or {}) do
    assert(palette[name] and type(count)=="number" and count>0 and count<=10000 and count==math.floor(count),"invalid fixture construction bill")
  end
  local surface=game.create_surface(p.surface,{width=768,height=256,default_enable_all_autoplace_controls=false,
    autoplace_settings={entity={treat_missing_as_default=false,settings={}},tile={treat_missing_as_default=false,settings={}},decorative={treat_missing_as_default=false,settings={}}}})
  surface.request_to_generate_chunks({128,16},7); surface.force_generate_chunk_requests()
  local tiles={}; for y=-64,95 do for x=-32,303 do
    tiles[#tiles+1]={name="lab-dark-1",position={x,y}}
    if #tiles==1024 then surface.set_tiles(tiles);tiles={} end
  end end; surface.set_tiles(tiles)
  local force=game.create_force(p.force);force.research_all_technologies();force.inserter_stack_size_bonus=0
  local s={owner="factorio_training_lab.smelting_fixture",surface=p.surface,force=p.force,recipe=p.recipe,ore=p.recipe=="iron-plate" and "iron-ore" or "copper-ore",
    budget=p.budget,supplied={0,0,0,0},delivered={0,0,0,0},denied={0,0,0,0},rates={0,0,0,0},credit={},fixture_units={},ports={},providers={}}
  storage.smelting_fixture=s
  local function fixture(name,x,y)
    local e=create(surface,force,name,x,y);s.fixture_units[e.unit_number]=true;return e
  end
  local ports={}; for _,y in ipairs({-40,64}) do for x=24,264,48 do ports[#ports+1]={x,y} end end
  for _,x in ipairs({-16,288}) do for _,y in ipairs({8,40}) do ports[#ports+1]={x,y} end end
  for _,pos in ipairs(ports) do
    local x,y=pos[1],pos[2];local port=fixture("roboport",x,y)
    assert(port.get_inventory(defines.inventory.roboport_robot).insert{name="construction-robot",count=200}==200)
    port.energy=100000000; s.ports[#s.ports+1]=port
    local generator=fixture("electric-energy-interface",x+4,y-2)
    generator.electric_buffer_size=1000000000; generator.power_production=1000000000;generator.energy=1000000000
    fixture("medium-electric-pole",x+3.5,y+.5)
    local provider=fixture("passive-provider-chest",x+5.5,y+.5);s.providers[#s.providers+1]=provider
  end
  -- Declared power scaffold, outside the line bodies; template poles remain bot-built.
  for x=6.5,262.5,8 do fixture("medium-electric-pole",x,-4.5) end
  -- Link the scaffold to powered upper roboports with medium poles only.
  for x=27.5,267.5,48 do for y=-34.5,-10.5,8 do fixture("medium-electric-pole",x,y) end end
  local n=1
  for name,count in pairs(p.budget) do
    assert(type(count)=="number" and count>0 and count==math.floor(count),"invalid fixture construction bill")
    local left=count
    for k=1,#s.providers do
      local inv=s.providers[n].get_inventory(defines.inventory.chest)
      local put=inv.insert{name=name,count=left};left=left-put;n=n%#s.providers+1
      if left==0 then break end
    end
    assert(left==0,"construction fixture capacity exceeded")
  end
  return {surface=s.surface,force=s.force,budget=s.budget,fixture_supply=true,construction_robots=#ports*200,
    furnace_speed=prototypes.entity['electric-furnace'].get_crafting_speed('normal'),recipe_seconds=prototypes.recipe[p.recipe].energy,
    express_belt_speed=prototypes.entity['express-transport-belt'].belt_speed,
    power_fixture={generators=#ports,production_watts_each=1000000000},
    inserter_capacity_bonus=force.inserter_stack_size_bonus}
end
local function snapshot()
  local s=state();local surface,force=scope(s);local stats=force.get_item_production_statistics(surface)
  local result={tick=game.tick,surface=s.surface,force=s.force,supplied=s.supplied,delivered=s.delivered,denied=s.denied,
    recipe=s.recipe,ore=s.ore,
    produced=stats.get_input_count(s.recipe),consumed=stats.get_output_count(s.ore),ghosts=0,path_chests=0,
    line_finished={},line_status={},line_input={},line_output={},belt_ore=0,furnaces=0,stock={},pending_bill={},ports={},built={}}
  for i=1,16 do result.line_finished[i]=0;result.line_status[i]={};result.line_input[i]=0;result.line_output[i]=0 end
  local status_names={};for name,id in pairs(defines.entity_status) do status_names[id]=name end
  for _,e in pairs(surface.find_entities_filtered{force=force}) do
    if not s.fixture_units[e.unit_number] then
      if e.type=="entity-ghost" then result.ghosts=result.ghosts+1;result.pending_bill[e.ghost_name]=(result.pending_bill[e.ghost_name] or 0)+1
      elseif e.type=="logistic-container" or e.type=="container" then result.path_chests=result.path_chests+1
      elseif e.type=="furnace" then
        local i=math.floor(e.position.x/16)+1; result.furnaces=result.furnaces+1
        result.line_finished[i]=result.line_finished[i]+e.products_finished
        local status=status_names[e.status] or "unknown";result.line_status[i][status]=(result.line_status[i][status] or 0)+1
        result.line_input[i]=result.line_input[i]+e.get_inventory(defines.inventory.crafter_input).get_item_count(s.ore)
        result.line_output[i]=result.line_output[i]+e.get_inventory(defines.inventory.crafter_output).get_item_count(s.recipe)
      elseif e.type=="transport-belt" then
        for lane=1,2 do result.belt_ore=result.belt_ore+e.get_transport_line(lane).get_item_count(s.ore) end
      end
      if e.type~='entity-ghost' and e.type~='item-entity' and e.type~='construction-robot' then
        result.built[e.name]=(result.built[e.name] or 0)+1
      end
    end
  end
  for _,provider in ipairs(s.providers) do for _,v in pairs(provider.get_inventory(defines.inventory.chest).get_contents()) do
    result.stock[v.name]=(result.stock[v.name] or 0)+v.count
  end end
  for _,port in ipairs(s.ports) do
    local net=port.logistic_network
    result.ports[#result.ports+1]={position=port.position,energy=port.energy,network=net and tostring(net.network_id),robots=net and net.all_construction_robots}
  end
  return result
end
local function validate(p)
  local s=state();local surface,force=scope(s);local bill={};local occupied={}
  for _,a in ipairs(p.actions) do
    assert(a.action_type=="place_ghost" and s.budget[a.entity],"undeclared construction item")
    assert(a.position.x>=-8 and a.position.x<280 and a.position.y>=-28 and a.position.y<=64,"outside declared production area")
    assert(surface.can_place_entity{name=a.entity,position=a.position,force=force,direction=defines.direction[a.direction or "north"]},"live collision: "..a.entity.." "..a.position.x..","..a.position.y)
    local covered=false
    for _,net in pairs(surface.find_logistic_networks_by_construction_area(a.position,force)) do
      if net.all_construction_robots>0 and net.get_item_count(a.entity)>0 then covered=true end
    end
    assert(covered,"no funded bot coverage: "..a.entity.." "..a.position.x..","..a.position.y)
    bill[a.entity]=(bill[a.entity] or 0)+1
  end
  local facts=snapshot()
  for name,count in pairs(bill) do assert((facts.stock[name] or 0)-(facts.pending_bill[name] or 0)>=count,"unfunded build: "..name) end
  return {ok=true,bill=bill}
end
function M.tick()
  local s=state();if not s then return end
  local surface=game.surfaces[s.surface];if not surface then return end
  for g=1,4 do
    local input=surface.find_entity("express-transport-belt",{(g-1)*64+21.5,-25.5})
    if input and s.rates[g]>0 then
      for lane=1,2 do
        local key=g..":"..lane;s.credit[key]=(s.credit[key] or 0)+s.rates[g]/120
        if s.credit[key]>=1 then
          s.credit[key]=s.credit[key]-1
          if input.get_transport_line(lane).insert_at_back{name=s.ore,count=1} then s.supplied[g]=s.supplied[g]+1 else s.denied[g]=s.denied[g]+1 end
        end
      end
    end
    local output=surface.find_entity("express-transport-belt",{(g-1)*64+38.5,58.5})
    if output then for lane=1,2 do
      s.delivered[g]=s.delivered[g]+output.get_transport_line(lane).remove_item{name=s.recipe,count=100}
    end end
  end
end
function M.register()
  commands.add_command("rl_smelting_fixture","Private belt smelting mechanics fixture; RCON only",function(command)
    assert(not command.player_index,"RCON only")
    local p=helpers.json_to_table(command.parameter or "{}")
    local ok,result=pcall(function()
      assert(game.tick_paused and game.ticks_to_run==0,"fixture commands require paused host")
      if p.op=="init" then return init(p) end
      if p.op=="snapshot" then return snapshot() end
      if p.op=="validate" then return validate(p) end
      if p.op=="rates" then
        scope(state());assert(#p.rates==4)
        for _,v in ipairs(p.rates) do assert(v>=0 and v<=45) end
        state().rates=p.rates;return {rates=p.rates}
      end
      error("unknown fixture operation")
    end)
    local report={ok=ok,value=ok and result or nil,error=not ok and tostring(result) or nil}
    assert(type(p.report)=="string" and string.match(p.report,"^smelting%-fixture/[a-z0-9%-]+%.json$"),"invalid report path")
    helpers.write_file(p.report,helpers.table_to_json(report),false);rcon.print("smelting-fixture-report")
  end)
end
return M
