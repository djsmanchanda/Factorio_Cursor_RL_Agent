# Path: orchestrator/craft_boundary.py | Purpose: Finish one in-flight craft without admitting another before a recipe handoff.
from __future__ import annotations

import json

from orchestrator.execution_slice import runtime_checkpoint, persist_checkpoint


def _literal(value):
    return json.dumps(json.dumps(value))


def request_drain(client, surface, force, changes):
    """Stop feeding, return unused real inputs, and let the current craft finish.

    A stock gate may freeze an assembler midway through a craft. Sampling zero
    progress cannot resolve that state. Feeders remain disabled until the shared
    recipe transaction completes; no ingredient is granted, destroyed or spent
    twice, and no recipe is changed here. The runtime checkpoint is world-scoped.
    """
    state = runtime_checkpoint()
    if state is None:
        return
    records = [{"x": a["position"]["x"], "y": a["position"]["y"], "recipe": a["recipe"]}
               for a in changes]
    prior = state.setdefault('craft_drains', {})
    lua = ("local s=game.surfaces["+json.dumps(surface)+"];local prior=helpers.json_to_table("+_literal(prior)+");"
           "local function key(x,y) return string.format('%.2f:%.2f',x,y) end;"
           "local function inside(p,b) return p.x>=b.left_top.x and p.x<=b.right_bottom.x "
           "and p.y>=b.left_top.y and p.y<=b.right_bottom.y end;"
           "local function stack_item(st) return {name=st.name,count=st.count,quality=st.quality} end;"
           "for _,a in ipairs(helpers.json_to_table("+_literal(records)+")) do "
           "local e=s.find_entities_filtered{position={a.x,a.y},radius=0.1,type='assembling-machine',force="+
           json.dumps(force)+",limit=1}[1];if e then local r=e.get_recipe();"
           "if r and r.name~=a.recipe and e.crafting_progress>0.000001 then "
           "local k=key(a.x,a.y);local record=prior[k];local feeders={};local dst=nil;local box=e.bounding_box;"
           "for _,i in pairs(s.find_entities_filtered{type='inserter',force=e.force,"
           "area={{box.left_top.x-3,box.left_top.y-3},{box.right_bottom.x+3,box.right_bottom.y+3}}}) do "
           "if inside(i.drop_position,box) then feeders[#feeders+1]=i;"
           "dst=dst or s.find_entities_filtered{position=i.pickup_position,type={'container','logistic-container'},"
           "force=e.force,limit=1}[1] end end;"
           "local inv=e.get_inventory(defines.inventory.crafter_input or defines.inventory.assembling_machine_input);"
           "if dst and inv and #feeders>0 then local cb=e.get_control_behavior();"
           "record=record or {x=a.x,y=a.y,recipe=r.name,connected=cb.connect_to_logistic_network,feeders={}};"
           "if not prior[k] then for _,i in ipairs(feeders) do record.feeders[#record.feeders+1]="
           "{x=i.position.x,y=i.position.y,disabled=i.disabled_by_script} end end;"
           "local room=true;for n=1,#inv do local st=inv[n];if st.valid_for_read and not dst.can_insert(stack_item(st)) then room=false end end;"
           "if room then for _,i in ipairs(feeders) do i.disabled_by_script=true end;"
           "for n=1,#inv do local st=inv[n];if st.valid_for_read then local count=dst.insert(stack_item(st));st.count=st.count-count end end;"
           "for _,i in ipairs(feeders) do local st=i.held_stack;if st.valid_for_read then "
           "local count=dst.insert(stack_item(st));st.count=st.count-count end end;"
           "cb.connect_to_logistic_network=false;prior[k]=record end end end end end;"
           "rcon.print(helpers.table_to_json(prior))")
    reply = client.command('/sc '+lua).strip()
    try:
        state['craft_drains'] = json.loads(reply)
    except json.JSONDecodeError as error:
        raise RuntimeError(f'Craft drain command failed: {reply[:300]}') from error
    persist_checkpoint()


def release_drains(client, surface, force, held):
    """Restore owned feeder state after configuration, or a failed transaction."""
    state = runtime_checkpoint()
    if state is None:
        return
    drains = state.get('craft_drains', {})
    released = []
    for actor in held:
        key = f"{actor['x']:.2f}:{actor['y']:.2f}"
        record = drains.get(key)
        if record is None:
            continue
        lua = ("local s=game.surfaces["+json.dumps(surface)+"];local a=helpers.json_to_table("+_literal(record)+");"
               "for _,p in ipairs(a.feeders) do local i=s.find_entities_filtered{position={p.x,p.y},"
               "radius=0.1,type='inserter',force="+json.dumps(force)+",limit=1}[1];"
               "if i then i.disabled_by_script=p.disabled end end;"
               "local e=s.find_entities_filtered{position={a.x,a.y},radius=0.1,type='assembling-machine',force="+
               json.dumps(force)+",limit=1}[1];if e and e.get_recipe() and e.get_recipe().name==a.recipe then "
               "e.get_control_behavior().connect_to_logistic_network=a.connected end")
        client.command('/sc '+lua)
        released.append(key)
    for key in released:
        drains.pop(key)
    persist_checkpoint()


def recover_orphan_drains(client, surface, force, loans):
    """A released/reconfigured borrower must not keep its source feeder parked."""
    state = runtime_checkpoint()
    if state is None:
        return
    live = {f"{l.machine_position[0]:.2f}:{l.machine_position[1]:.2f}": l.current_recipe for l in loans}
    orphaned = [r for key, r in state.get('craft_drains', {}).items()
                if key not in live or live[key] != r['recipe']]
    release_drains(client, surface, force, orphaned)
