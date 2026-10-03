-- Path: training/laya_mall_stream.lua | Purpose: Keep a bounded live clock running while atomically delivering tick-earned supply without full-world scans.
local cfg=request.scenario
local s=storage.laya_mall
assert(cfg.version=="mall-world-v1" and s and not s.partial and not s.pending_operation,"mall stream state")
assert(string.sub(cfg.surface,1,10)=="laya-mall/" and string.sub(cfg.force,1,10)=="laya-mall-","mall stream scope")
assert(s.scenario_hash==cfg.scenario_hash and s.surface==cfg.surface and s.force==cfg.force,"mall stream identity")
assert(request.nonce and request.operation_hash,"stream receipt identity")
local old=s.receipts[request.nonce]
if old then assert(old.operation_hash==request.operation_hash,"stream nonce reused"); return old end
assert(request.revision==s.revision,"stale stream revision")
local function copy(value) return helpers.json_to_table(helpers.table_to_json(value)) end
local receipt={receipt_id=request.nonce,operation_hash=request.operation_hash,operation=request.op,tick=game.tick,inserted={}}
if request.op=="live_start" then
  assert(game.tick_paused and game.ticks_to_run==0 and s.ledger.tick==game.tick,"stream requires a reconciled paused clock")
  assert(not s.live_view or s.live_view.closed,"stream already owns clock")
  assert(type(request.ticks)=="number" and request.ticks%1==0 and request.ticks>=1 and request.ticks<=36000,"stream tick budget")
  local p=game.get_player(request.observer)
  assert(p and p.connected and p.surface.name==s.surface and p.force.name==s.force and p.controller_type==defines.controllers.spectator,"stream requires focused observer")
  s.pending_operation=request.nonce
  s.viewing_only=true
  s.live_view={id=request.nonce,start_tick=game.tick,end_tick=game.tick+request.ticks,observer=p.name,closed=false}
  game.speed=1; game.tick_paused=true; game.ticks_to_run=request.ticks
elseif request.op=="live_pump" or request.op=="live_finish" then
  local view=s.live_view
  assert(view and not view.closed and view.id==request.view_id,"not the active viewing window")
  assert(game.tick_paused and game.speed==1 and game.tick<=view.end_tick,"stream clock drift")
  assert(game.tick+game.ticks_to_run==view.end_tick,"stream endpoint drift")
  assert(game.tick>=s.ledger.tick,"stream ledger clock reversed")
  local surface=assert(game.surfaces[s.surface],"stream surface missing")
  local starter=nil
  for _,f in ipairs(cfg.fixtures) do if f.id=="starter" then
    for _,e in pairs(surface.find_entities_filtered{name=f.entity,position=f.position,force=s.force}) do
      if e.unit_number==s.fixtures.starter and e.position.x==f.position.x and e.position.y==f.position.y then starter=e end
    end
  end end
  assert(starter,"stream starter fixture replaced")
  local inv=starter.get_inventory(defines.inventory.chest)
  s.pending_operation=request.nonce
  for _,item in ipairs({"iron-plate","copper-plate"}) do
    local credit=s.ledger.accrued_120ths[item]+(game.tick-s.ledger.tick)*s.ledger.rate_halves_per_second[item]
    local wanted=math.floor(credit/120)
    local inserted=wanted>0 and inv.insert{name=item,count=wanted,quality="normal"} or 0
    s.ledger.accrued_120ths[item]=credit-inserted*120
    s.ledger.fixture_delivered[item]=s.ledger.fixture_delivered[item]+inserted
    receipt.inserted[item]=inserted
  end
  s.ledger.tick=game.tick
  local p=game.get_player(view.observer)
  if request.op=="live_finish" or game.ticks_to_run==0 or not p or not p.connected then
    game.ticks_to_run=0;view.closed=true
    view.reason=(not p or not p.connected) and "observer_disconnected" or (game.tick==view.end_tick and "complete" or "stopped")
  end
else error("unsupported live stream operation") end
s.revision=s.revision+1;s.pending_operation=nil
receipt.revision=s.revision;receipt.ledger=copy(s.ledger);receipt.view=copy(s.live_view)
s.receipts[request.nonce]=receipt
return receipt
