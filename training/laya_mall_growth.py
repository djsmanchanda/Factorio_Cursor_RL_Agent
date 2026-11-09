# Path: training/laya_mall_growth.py | Purpose: Reward earlier earned supply growth and bounded manufacturing progress using actual engine counters.

from copy import deepcopy
from training.laya_mall_supply import UPGRADE_BATCH, PLATES
from training.laya_saved_world import SavedWorldError

VERSION='mall-growth-reward-v2'


def expansion_bill(recipes):
    totals={}
    def expand(item,count,path=()):
        totals[item]=totals.get(item,0)+count
        if item not in recipes:return
        if item in path:raise ValueError('cyclic manufacturing recipe')
        spec=recipes[item];crafts=count/spec['product_amount']
        for part in spec['ingredients']:expand(part['name'],crafts*part['amount'],(*path,item))
    for item,count in UPGRADE_BATCH.items():expand(item,count)
    return {k:v for k,v in totals.items() if k not in PLATES and k in recipes}


def manufacturing_potential(facts):
    bill=expansion_bill(facts['recipes']);target_batches=facts['ledger']['upgrades']+1
    # Necessary intermediates provide bounded cold-start credit. Infinite cable cannot keep earning it.
    progress=sum(min(1,max(0,facts['production'].get(item,0)-facts['ledger']['baseline_production'].get(item,0))
                     /max(1,count*target_batches)) for item,count in bill.items())/max(1,len(bill))
    return facts['ledger']['upgrades']+progress


def reward(before,after,previous_rate=0,upgrade_tick=None):
    if before.get('viewing_only') or after.get('viewing_only'):raise SavedWorldError('viewer outcomes are not training evidence')
    ticks=after['tick']-before['tick']
    if ticks<=0:raise ValueError('reward requires a positive engine window')
    horizon=after['scenario']['max_episode_ticks']
    old_extra=sum(before['ledger']['rate_halves_per_second'].values())/2-2
    new_extra=sum(after['ledger']['rate_halves_per_second'].values())/2-2
    if new_extra<old_extra:raise SavedWorldError('earned supply decreased')
    changed=before['ledger']['upgrades']!=after['ledger']['upgrades']
    if changed and (upgrade_tick is None or not before['tick']<=upgrade_tick<=after['tick']):
        raise ValueError('supply growth needs its exact physical consumption tick')
    area=(old_extra*ticks+(new_extra-old_extra)*(after['tick']-upgrade_tick if changed else 0))/horizon
    bill=expansion_bill(after['recipes'])
    produced={k:after['production'].get(k,0)-before['production'].get(k,0) for k in bill}
    if any(v<0 for v in produced.values()):raise SavedWorldError('manufacturing counters reset')
    potential_before=manufacturing_potential(before);potential_after=manufacturing_potential(after)
    rate=sum(min(produced[k],max(0,bill[k]*(after['ledger']['upgrades']+1)-before['production'].get(k,0)))
             /max(1,bill[k]) for k in bill)*60/ticks/max(1,len(bill))
    gain=rate-previous_rate
    shaping=.05*(potential_after-potential_before)
    return {'version':VERSION,'reward':area+shaping+.01*gain,'earned_supply_area':area,
            'manufacturing_progress':shaping,'needed_production_rate':rate,'production_rate_gain':gain,
            'upgrades':after['ledger']['upgrades']-before['ledger']['upgrades'],'window_ticks':ticks}
