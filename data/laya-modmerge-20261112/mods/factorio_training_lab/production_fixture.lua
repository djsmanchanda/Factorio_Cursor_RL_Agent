-- Path: factorio_training_lab/production_fixture.lua | Purpose: Explicit isolated circuit/plastic experiment sources; never an autonomous production controller.
local shared=require('training_shared')
local M={}
local function scope()
  local s=assert(storage.production_fixture,'production fixture not initialized')
  assert(s.owner=='factorio_training_lab.production_fixture' and string.find(s.surface,'^training/mechanics%-'))
  return s,assert(game.surfaces[s.surface]),assert(game.forces[s.force])
end
local function init(p)
  assert(not storage.production_fixture,'existing fixture must be preserved')
  assert(string.find(p.surface,'^training/mechanics%-[a-z0-9%-]+$') and p.force=='training-'..string.sub(p.surface,10),'invalid lab identity')
  assert(not game.surfaces[p.surface] and not game.forces[p.force])
  local surface=game.create_surface(p.surface,{width=384,height=256,default_enable_all_autoplace_controls=false,
    autoplace_settings={entity={treat_missing_as_default=false,settings={}},tile={treat_missing_as_default=false,settings={}},decorative={treat_missing_as_default=false,settings={}}}})
  surface.request_to_generate_chunks({32,16},5);surface.force_generate_chunk_requests()
  local tiles={};for y=-64,95 do for x=-64,127 do tiles[#tiles+1]={name='lab-dark-1',position={x,y}};if #tiles==1024 then surface.set_tiles(tiles);tiles={} end end end;surface.set_tiles(tiles)
  local force=game.create_force(p.force);force.research_all_technologies()
  local s={owner='factorio_training_lab.production_fixture',surface=p.surface,force=p.force,spec=p.spec,started_tick=game.tick,
    fixture_units={},providers={},sources={},supplied={},denied={},delivered=0,credit={}}
  storage.production_fixture=s
  assert((p.spec.logistic_robots or 1100)>=11 and (p.spec.logistic_robots or 1100)<=2200,'invalid robot budget')
  local port_index=0
  local function robots(port)
    port_index=port_index+1
    local total=p.spec.logistic_robots or 1100
    local count=math.floor(total/11)+(port_index<=total%11 and 1 or 0)
    port.get_inventory(defines.inventory.roboport_robot).insert{name='construction-robot',count=200}
    port.get_inventory(defines.inventory.roboport_robot).insert{name='logistic-robot',count=count}
  end
  local function create(name,pos)
    assert(surface.can_place_entity{name=name,position=pos,force=force},'fixture collision '..name)
    local e=assert(surface.create_entity{name=name,position=pos,force=force});e.minable_flag=false;e.destructible=false;s.fixture_units[e.unit_number]=true;return e
  end
  for _,y in ipairs({-32,64}) do for _,x in ipairs({-32,16,64}) do
    local port=create('roboport',{x,y});port.energy=100000000
    robots(port)
    local gen=create('electric-energy-interface',{x+4,y-2});gen.electric_buffer_size=1000000000;gen.power_production=1000000000;gen.energy=1000000000
    create('medium-electric-pole',{x+3.5,y+.5})
    s.providers[#s.providers+1]=create('passive-provider-chest',{x+5.5,y+.5})
  end end
  -- Join both robot rows and power the template's medium-pole network.
  for _,pos in ipairs({{-48,16},{112,16},{16,-20},{16,40},{-24,16}}) do
    local x,y=pos[1],pos[2]
    local port=create('roboport',{x,y});port.energy=100000000
    robots(port)
    local gen=create('electric-energy-interface',{x+4,y-2});gen.electric_buffer_size=1000000000;gen.power_production=1000000000;gen.energy=1000000000
    create('medium-electric-pole',{x+3.5,y+.5})
  end
  for x=-24.5,95.5,8 do create('medium-electric-pole',{x,-12.5}) end
  for y=-28.5,-12.5,8 do create('medium-electric-pole',{19.5,y}) end
  if p.spec.fault_action then create('storage-chest',{10.5,-30.5}) end -- Destination for bot-cleared ground items.
  for name,count in pairs(p.spec.budget) do
    local left=count
    assert(prototypes.item[name] and type(count)=='number' and count>0 and count<=10000 and count==math.floor(count),'invalid construction budget')
    for _,provider in ipairs(s.providers) do left=left-provider.get_inventory(defines.inventory.chest).insert{name=name,count=left};if left==0 then break end end
    assert(left==0,'fixture item storage full')
  end
  for i,source in ipairs(p.spec.sources) do
    if source.kind~='belt' then
      local e=create(source.entity,source.position);s.sources[i]=e
      if source.kind=='fluid' then e.set_infinity_pipe_filter{name=source.fluid,percentage=1,mode='at-least'} end
    end
    s.supplied[i]=0;s.denied[i]=0
  end
  shared.ensure_storage().owned_fixtures=shared.ensure_storage().owned_fixtures or {}
  shared.ensure_storage().owned_fixtures[s.surface]={owner=s.owner,surface=s.surface,force=s.force,last_tick=game.tick}
  return {surface=s.surface,force=s.force,fixture_funded=true,budget=p.spec.budget,sources=p.spec.sources,sinks=p.spec.sinks,started_tick=s.started_tick}
end
function M.tick()
  if not storage.production_fixture then return end
  local s,surface,force=scope();local registered=(shared.ensure_storage().owned_fixtures or {})[s.surface];if registered then registered.last_tick=game.tick end
  if not s.first_output_observed_tick and (game.tick-s.started_tick)%60==0
      and force.get_item_production_statistics(surface).get_input_count(s.spec.recipe)>0 then
    s.first_output_observed_tick=game.tick
  end
  local elapsed=s.armed_tick and game.tick-s.armed_tick or -1
  local outage=elapsed>=3600 and elapsed<3600+(s.spec.interruption_ticks or 0)
  for i,source in ipairs(s.spec.sources) do
    local selector=s.spec.interruption_source or 'all'
    local interrupted=outage and (selector=='all' or selector==source.item or selector==source.fluid)
    if source.kind=='fluid' then
      local e=s.sources[i]
      if interrupted~=s.fluid_interrupted then
        e.set_infinity_pipe_filter{name=source.fluid,percentage=interrupted and 0 or 1,mode=interrupted and 'exactly' or 'at-least'}
        s.fluid_interrupted=interrupted
      end
    elseif not interrupted and game.tick-s.started_tick>=(source.delay_ticks or 0) then
      local e=s.sources[i] or surface.find_entity(source.entity,source.position)
      if e then
        local lanes=source.kind=='belt' and 2 or 1
        for lane=1,lanes do
          local key=i..':'..lane;s.credit[key]=(s.credit[key] or 0)+source.rate/(60*lanes)
          if s.credit[key]>=1 then
            s.credit[key]=s.credit[key]-1;local put
            if source.kind=='belt' then put=e.get_transport_line(lane).insert_at_back{name=source.item,count=1} and 1 or 0
            else put=e.get_inventory(defines.inventory.chest).insert{name=source.item,count=1} end
            s.supplied[i]=s.supplied[i]+put;s.denied[i]=s.denied[i]+1-put
          end
        end
      end
    end
  end
  if elapsed>=3600 and elapsed<3600+(s.spec.sink_pause_ticks or 0) then return end
  for _,sink in ipairs(s.spec.sinks) do
    local e=surface.find_entity(sink.entity,sink.position)
    if e then
      if sink.kind=='belt' then for lane=1,2 do s.delivered=s.delivered+e.get_transport_line(lane).remove_item{name=sink.item,count=100} end
      else s.delivered=s.delivered+e.get_inventory(defines.inventory.chest).remove{name=sink.item,count=100} end
    end
  end
end
local function snapshot()
  local s,surface,force=scope();local stats=force.get_fluid_production_statistics(surface)
  local r={tick=game.tick,surface=s.surface,force=s.force,fixture_funded=true,supplied=s.supplied,denied=s.denied,delivered=s.delivered,
    fluid_production={},fluid_consumption={},pending_bill={},stock={},ghosts=0,built={},fluid_contents={},
    inserter_capacity_bonus=force.inserter_stack_size_bonus,protected_entities={},recipe_productivity={},
    first_output_observed_tick=s.first_output_observed_tick,first_output_sampling_ticks=60,
    armed_tick=s.armed_tick,perturbations=s.perturbations or {},logistic_robots=s.spec.logistic_robots or 1100}
  for _,name in ipairs({'copper-cable','electronic-circuit','plastic-bar','basic-oil-processing'}) do
    r.recipe_productivity[name]=force.recipes[name].productivity_bonus
  end
  for _,fluid in ipairs({'crude-oil','petroleum-gas'}) do r.fluid_production[fluid]=stats.get_input_count(fluid);r.fluid_consumption[fluid]=stats.get_output_count(fluid) end
  for _,e in pairs(surface.find_entities_filtered{force=force}) do
    if e.type=='entity-ghost' then r.ghosts=r.ghosts+1;r.pending_bill[e.ghost_name]=(r.pending_bill[e.ghost_name] or 0)+1
    elseif not s.fixture_units[e.unit_number] and e.type~='construction-robot' and e.type~='logistic-robot' and e.type~='item-entity' then r.built[e.name]=(r.built[e.name] or 0)+1 end
    if e.type=='pipe' or e.type=='pipe-to-ground' or e.type=='oil-refinery' or e.type=='chemical-plant' then
      for name,amount in pairs(e.get_fluid_contents()) do r.fluid_contents[name]=(r.fluid_contents[name] or 0)+amount end
    end
    if e.type~='entity-ghost' and e.type~='construction-robot' and e.type~='logistic-robot' and e.type~='item-entity' then
      r.protected_entities[#r.protected_entities+1]={entity=e.name,position=e.position}
    end
  end
  for _,provider in ipairs(s.providers) do for _,v in pairs(provider.get_inventory(defines.inventory.chest).get_contents()) do r.stock[v.name]=(r.stock[v.name] or 0)+v.count end end
  if s.perturbations then
    local p=s.perturbations[1].position;r.fault_site_entities={}
    for _,e in pairs(surface.find_entities_filtered{area={{p.x-4,p.y-4},{p.x+4,p.y+4}}}) do
      r.fault_site_entities[#r.fault_site_entities+1]={name=e.name,type=e.type,position=e.position}
    end
  end
  return r
end
function M.register()
  commands.add_command('rl_production_fixture','Explicit production mechanics fixture; RCON only',function(command)
    local p=helpers.json_to_table(command.parameter or '{}')
    local ok,v=pcall(function()
      assert(not command.player_index and game.tick_paused and game.ticks_to_run==0,'paused RCON-only fixture')
      if p.op=='init' then return init(p) end
      if p.op=='snapshot' then return snapshot() end
      if p.op=='arm' then
        local s=scope();assert(not s.armed_tick,'already armed');s.armed_tick=game.tick
        return {armed_tick=game.tick,interruption_ticks=s.spec.interruption_ticks or 0,sink_pause_ticks=s.spec.sink_pause_ticks or 0}
      end
      if p.op=='perturb' then
        local s,surface=scope();local a=assert(s.spec.fault_action,'no declared fault')
        assert(not s.perturbations,'fault already applied')
        local e=assert(surface.find_entity(a.entity,a.position),'fault target absent')
        assert(e.force.name==s.force and not s.fixture_units[e.unit_number] and e.type~='entity-ghost','protected fault target')
        s.perturbations={{tick=game.tick,entity=e.name,position=e.position,kind='declared-removal',refund=0}}
        assert(e.destroy(),'fault removal failed');return s.perturbations[1]
      end
      if p.op=='clear-ground' then
        local s,surface,force=scope();assert(s.perturbations,'no declared damaged site')
        local a=s.spec.fault_action.position;local count=0
        for _,e in pairs(surface.find_entities_filtered{type='item-entity',area={{a.x-2,a.y-2},{a.x+2,a.y+2}}}) do
          if e.order_deconstruction(force) then count=count+1 end
        end
        s.cleanup_orders=s.cleanup_orders or {};s.cleanup_orders[#s.cleanup_orders+1]={tick=game.tick,count=count}
        return {tick=game.tick,count=count,method='construction-bot-deconstruction',extra_items_granted=0}
      end
      error('unknown production fixture operation')
    end)
    assert(type(p.report)=='string' and string.match(p.report,'^production%-fixture/[a-z0-9%-]+%.json$'))
    helpers.write_file(p.report,helpers.table_to_json({ok=ok,value=ok and v or nil,error=not ok and tostring(v) or nil}),false);rcon.print('production-fixture-report')
  end)
end
return M
