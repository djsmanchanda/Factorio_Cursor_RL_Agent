# Path: training/laya_mall_population.py | Purpose: Seeded population RL bootstrap for measured mall growth, with contextual action scores and no scripted build sequence.

import math
import numpy as np
from training.laya_mall import RECIPES,manufactured_stock
from training.laya_mall_supply import MALL_KIT,UPGRADE_BATCH,PLATES

VERSION='mall-population-growth-v1'
FAMILIES=('assemble','toggle','priority','deliver','upgrade','tier','buffer','wait')
PRODUCTS=(*RECIPES,'steel-plate','stone','coal')


def action_vector(facts,c):
    stock=facts['provider_stock'];fresh=manufactured_stock(facts)
    context=[1,facts['elapsed_ticks']/facts['scenario']['max_episode_ticks'],facts['ledger']['upgrades']/10,
             *[math.log1p(facts['ledger']['rate_halves_per_second'][p]/2)/3 for p in PLATES],
             *[min(2,fresh.get(p,0)/n) for p,n in UPGRADE_BATCH.items()],
             *[math.log1p(stock.get(p,0))/6 for p in PRODUCTS],
             *[math.log1p(facts['wip'].get(p,0))/6 for p in PRODUCTS],
             *[min(2,max(0,stock.get(p,0)-facts['reserved'].get(p,0))/max(1,n)) for p,n in MALL_KIT.items()],
             *[float(facts['queue'][0]==g) for g in ('expansion','circuits','belts')],
             len(facts['machines'])/14,facts['pending_ghosts']/20,facts.get('pending_upgrades',0)/10,
             *[sum(m.get('recipe')==p and m.get('active',True) for m in facts['machines'])/4 for p in PRODUCTS]]
    labels=[*[float(c['family']==f) for f in FAMILIES],*[float(c.get('product')==p) for p in PRODUCTS],
            *[float(c.get('module')==i) for i in range(3)],*[float(c.get('side')==s) for s in ('west','east','north','south')],
            *[float(c.get('goal')==g) for g in ('expansion','circuits','belts')],float(c.get('active',False)),
            float(c.get('machine')=='assembling-machine-2')]
    numeric=[sum(c.get('bill',{}).values())/10,c.get('wait_ticks',0)/1800,c.get('multiplier',0)/15]
    # Interactions expose shortages/queue/time to every operation and product; weights learn their effect.
    return np.array([*numeric,*labels,*(a*b for a in labels for b in context)],dtype=np.float64)


def choose(weights,facts,candidates,rng,epsilon=.2):
    if not candidates:raise ValueError('empty legal menu')
    rows=np.stack([action_vector(facts,c) for c in candidates]);scores=rows@np.asarray(weights)
    if rng.random()<epsilon:return int(rng.integers(len(candidates)))
    probabilities=np.exp(np.clip(scores-scores.max(),-40,0));probabilities/=probabilities.sum()
    return int(rng.choice(len(candidates),p=probabilities))


def initial_population(width,seed,count=20):
    rng=np.random.default_rng(seed)
    return [rng.normal(0,.08,width).tolist() for _ in range(count)]


def evolve(population,fitness,seed):
    if len(population)!=20 or len(fitness)!=20:raise ValueError('twenty matched population outcomes required')
    rng=np.random.default_rng(seed);ranked=sorted(range(20),key=lambda i:(-fitness[i],i));elites=[population[i] for i in ranked[:4]]
    result=[list(w) for w in elites]
    while len(result)<16:
        a=np.asarray(elites[int(rng.integers(4))]);b=np.asarray(elites[int(rng.integers(4))])
        child=np.where(rng.random(len(a))<.5,a,b)+rng.normal(0,.04,len(a));result.append(child.tolist())
    result.extend(initial_population(len(elites[0]),seed+1,4))
    return result
