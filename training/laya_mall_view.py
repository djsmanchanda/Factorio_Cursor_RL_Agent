# Path: training/laya_mall_view.py | Purpose: Run a bounded spectator-only mall session at visible engine speed without policy decisions.

from copy import deepcopy
import json
import uuid
import time
from training.laya_saved_world import SavedWorldError
from training.laya_mall_supply import accrue_supply, record_delivery


def watch(world, *, seconds=600, observer=None, connect_timeout=300):
    if type(seconds) is not int or not 1<=seconds<=600:
        raise SavedWorldError('view duration must be 1..600 game seconds')
    if type(connect_timeout) is not int or not 1<=connect_timeout<=600:
        raise SavedWorldError('observer wait must be 1..600 seconds')
    if observer is not None and (not isinstance(observer,str) or not 1<=len(observer)<=128):
        raise SavedWorldError('named observer must be 1..128 characters')
    world.require_clear_mutations()
    initial=world.query()
    budget=seconds*60
    path=world.root/'live-view.json'
    state={'authority':'spectator_view','comparison_eligible':False,'policy_running':False,
           'surface':world.surface,'force':world.force,'requested_seconds':seconds,
           'budget_ticks':budget,'advanced_ticks':0,'observer':observer,'initial_tick':initial['tick']}
    def record(status):
        state['status']=status;path.write_text(json.dumps(state,indent=2)+'\n')
    record('waiting_for_observer');deadline=time.monotonic()+connect_timeout;focused=False
    view_id=None;revision=None
    try:
        while state['advanced_ticks']<budget:
            players=world.rpc('view_status')['players']
            if players=={}:players=[]
            if observer is None:
                if len(players)>1:raise SavedWorldError('multiple connected players: specify --observer')
                if players:observer=players[0]['name'];state['observer']=observer
            player=next((p for p in players if p['name']==observer),None)
            if player is None:
                record('waiting_for_observer' if not focused else 'observer_disconnected')
                if time.monotonic()>=deadline:
                    record('paused_observer_timeout');return state
                time.sleep(.25);continue
            deadline=time.monotonic()+connect_timeout
            if not focused or player['surface']!=world.surface or not player['spectator'] or player['force']!=world.force:
                view=world.rpc('view',observer=observer)
                if view['surface']!=world.surface or view['force']!=world.force or not view['spectator']:
                    raise SavedWorldError('spectator focus failed')
                focused=True;state['view']=view
            break
        world.mark_viewing_only()
        receipt=world.stream_rpc('live_start',nonce=uuid.uuid4().hex,revision=initial['revision'],ticks=budget,observer=observer)
        view_id=receipt['view']['id'];revision=receipt['revision'];ledger=deepcopy(initial['ledger'])
        while not receipt['view']['closed']:
            record('live')
            time.sleep(.1)
            receipt=world.stream_rpc('live_pump',nonce=uuid.uuid4().hex,revision=revision,view_id=view_id)
            revision=receipt['revision']
            expected=deepcopy(ledger);accrue_supply(expected,receipt['tick']);record_delivery(expected,receipt['inserted'])
            actual=receipt['ledger']
            if actual.get('consumption_receipts')=={}:actual['consumption_receipts']=[]
            if actual!=expected:raise SavedWorldError('live engine/Python supply ledger mismatch')
            ledger=actual;revision=receipt['revision']
            state['advanced_ticks']=receipt['tick']-initial['tick'];state['latest_tick']=receipt['tick']
        reason=receipt['view']['reason']
        record('observer_disconnected' if reason=='observer_disconnected' else 'paused_view_complete')
        return state
    except (Exception,KeyboardInterrupt) as error:
        state['error']=str(error)
        if view_id is not None:
            try:
                stopped=world.stream_rpc('live_finish',nonce=uuid.uuid4().hex,revision=revision,view_id=view_id)
                state['latest_tick']=stopped['tick'];state['advanced_ticks']=stopped['tick']-initial['tick']
            except Exception as cleanup_error:state['cleanup_error']=str(cleanup_error)
        record('stopped_interrupted' if isinstance(error,KeyboardInterrupt) else 'stopped_error')
        raise
