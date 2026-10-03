# Path: training/laya_mall_view.py | Purpose: Run a bounded spectator-only mall session at visible engine speed without policy decisions.

import json
import time
from training.laya_saved_world import SavedWorldError


def watch(world, *, seconds=600, observer=None, connect_timeout=300):
    if type(seconds) is not int or not 1<=seconds<=600:
        raise SavedWorldError('view duration must be 1..600 game seconds')
    if type(connect_timeout) is not int or not 1<=connect_timeout<=600:
        raise SavedWorldError('observer wait must be 1..600 seconds')
    if observer is not None and (not isinstance(observer,str) or not 1<=len(observer)<=128):
        raise SavedWorldError('named observer must be 1..128 characters')
    world.require_clear_mutations()
    initial=world.query()
    remaining=world.scenario['max_episode_ticks']-initial['elapsed_ticks']
    budget=min(seconds*60,remaining)
    if budget<=0:raise SavedWorldError('mall episode budget exhausted; inspect the paused world')
    path=world.root/'live-view.json'
    state={'authority':'spectator_view','comparison_eligible':False,'policy_running':False,
           'surface':world.surface,'force':world.force,'requested_seconds':seconds,
           'budget_ticks':budget,'advanced_ticks':0,'observer':observer,'initial_tick':initial['tick']}
    def record(status):
        state['status']=status;path.write_text(json.dumps(state,indent=2)+'\n')
    record('waiting_for_observer');deadline=time.monotonic()+connect_timeout;focused=False
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
            record('live')
            quantum=min(60,budget-state['advanced_ticks'])
            world.advance(quantum,speed=1)
            state['advanced_ticks']+=quantum;state['latest_tick']=world.query()['tick']
        record('paused_episode_budget' if budget==remaining else 'paused_view_complete')
        return state
    except (Exception,KeyboardInterrupt) as error:
        state['error']=str(error)
        record('stopped_interrupted' if isinstance(error,KeyboardInterrupt) else 'stopped_error')
        raise
