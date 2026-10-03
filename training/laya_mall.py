# Path: training/laya_mall.py | Purpose: Define seeded mall fixtures, manufacturing credit, priority observations and unranked actions.

from copy import deepcopy
import math
import random

from orchestrator.laya_operations import sha256_hash
from orchestrator.laya_state import validate_state
from planners.laya_quad_cell import generate_quad_slot, quad_reservation_reason
from planners.plan_validation import validate_build_plan
from planners.quad_mall_layout import DENSE_MALL_MODULE_OFFSETS, QUAD_MALL_MACHINE_OFFSETS
from training.laya_input_profile import MALL_PROFILE
from training.laya_mall_supply import MALL_KIT, PLATES, UPGRADE_BATCH, plan_upgrade
from training.laya_saved_world import SavedWorldError

RECIPES = ('iron-gear-wheel', 'copper-cable', 'electronic-circuit', 'transport-belt',
           'inserter', 'long-handed-inserter', 'electric-mining-drill', 'splitter', 'assembling-machine-1')
BUILD_ITEMS = frozenset((*MALL_KIT, 'long-handed-inserter'))
BOUNDS = {'x_min': -32, 'y_min': -32, 'x_max_exclusive': 32, 'y_max_exclusive': 32}


def scenario(seed, identity):
    if type(seed) is not int or not 0 <= seed <= 65535:
        raise ValueError('mall seed must be 0..65535')
    rng = random.Random(seed)
    ox, oy = rng.choice((-4, 0, 4)), rng.choice((-4, 0, 4))
    pos = lambda x, y: {'x': x + ox, 'y': y + oy}
    centers = [pos(-4.5 + x, -4.5 + y) for x, y in DENSE_MALL_MODULE_OFFSETS]
    fixtures = [{'id': 'power', 'entity': 'electric-energy-interface', 'position': pos(-17, -4)},
                {'id': 'roboport', 'entity': 'roboport', 'position': pos(-12, -1)},
                {'id': 'starter', 'entity': 'passive-provider-chest', 'position': pos(-12.5, -5.5)},
                {'id': 'returns', 'entity': 'storage-chest', 'position': pos(-9.5, -5.5)}]
    fixtures += [{'id': f'power-{i}', 'entity': 'substation', 'position': p}
                 for i, p in enumerate([pos(-1, -2), pos(5, 3), pos(-4, 8), pos(-13, -4)])]
    demands = [{'id': 'circuits', 'item': 'electronic-circuit', 'count': rng.choice((12, 16, 24)),
                'release_ticks': 0, 'due_ticks': 12000},
               {'id': 'belts', 'item': 'transport-belt', 'count': rng.choice((24, 32, 48)),
                'release_ticks': rng.choice((0, 1200, 2400)), 'due_ticks': 18000}]
    queue = ['expansion', 'circuits', 'belts']
    rng.shuffle(queue)
    configuration = {'version': MALL_PROFILE, 'seed': seed, 'bounds': dict(BOUNDS), 'centers': centers,
                     'fixtures': fixtures, 'kit': dict(MALL_KIT), 'robots': {'construction': 50, 'logistic': 50},
                     'research_fixture': 'all_technologies', 'recipes': list(RECIPES), 'demands': demands,
                     'initial_queue': queue, 'required_upgrades': 1, 'max_episode_ticks': 36000,
                     'supply_rule': {'initial_per_second': dict.fromkeys(PLATES, 1),
                                     'increment_per_second': 1.5, 'batch': dict(UPGRADE_BATCH), 'cap': None}}
    # Replays of one scenario retain its split identity, regardless of server/menu IDs.
    configuration['scenario_hash'] = sha256_hash({k:v for k,v in configuration.items() if k!='seed'})
    configuration['surface'] = 'laya-mall/' + identity
    configuration['force'] = 'laya-mall-' + identity
    return configuration


def manufactured_stock(facts):
    """Attribute ordinary use to seeded stock first; upgrade/customer use spends new output."""
    result = {}
    ledger = facts['ledger']
    for item, count in facts['production'].items():
        produced = count - ledger['baseline_production'].get(item, 0)
        if produced < 0:
            raise SavedWorldError('mall production counters reset')
        ordinary_use = facts['consumption'].get(item, 0) + facts['construction_committed'].get(item, 0)
        seed_remaining = max(0, facts['scenario']['kit'].get(item, 0) - ordinary_use)
        new_use = max(0, ordinary_use - facts['scenario']['kit'].get(item, 0))
        spent = ledger['spent_crafting'].get(item, 0) + facts['customer_removed'].get(item, 0)
        free = max(0, facts['provider_stock'].get(item, 0) - facts['reserved'].get(item, 0))
        result[item] = max(0, min(produced - new_use - spent, free - seed_remaining))
    return result


def plate_requirements(goal, facts):
    stock = dict(facts['provider_stock'])
    # Deliveries/upgrades require new output, even while seed items remain free.
    fresh=manufactured_stock(facts)
    roots=UPGRADE_BATCH if goal=='expansion' else {
        next(d['item'] for d in facts['scenario']['demands'] if d['id']==goal):1}
    for item in roots:stock[item]=fresh.get(item,0)+facts['reserved'].get(item,0)
    required = dict.fromkeys(PLATES, 0)
    def expand(item, count, visiting=()):
        used = min(count, max(0, stock.get(item, 0) - facts['reserved'].get(item, 0)))
        stock[item] = stock.get(item, 0) - used
        count -= used
        if count <= 0:
            return
        if item in PLATES:
            required[item] += count
            return
        if item in visiting or item not in facts['recipes']:
            raise SavedWorldError('mall recipe dependency is unavailable or cyclic')
        recipe = facts['recipes'][item]
        crafts = math.ceil(count / recipe['product_amount'])
        for part in recipe['ingredients']:
            expand(part['name'], crafts * part['amount'], (*visiting, item))
    if goal == 'expansion':
        for item, count in UPGRADE_BATCH.items(): expand(item, count)
    else:
        task = next(d for d in facts['scenario']['demands'] if d['id'] == goal)
        expand(task['item'], max(0, task['count'] - facts['served'].get(goal, 0)))
    return required


def priority_goal(facts):
    for goal in facts['queue']:
        if goal == 'expansion':
            if facts['ledger']['upgrades'] < facts['scenario']['required_upgrades']: return goal
        else:
            task = next(d for d in facts['scenario']['demands'] if d['id'] == goal)
            if facts['elapsed_ticks'] >= task['release_ticks'] and facts['served'].get(goal, 0) < task['count']: return goal
    return 'expansion'


def plate_priority(facts):
    goal = priority_goal(facts)
    needs = plate_requirements(goal, facts)
    if not any(needs.values()):
        # Forecast the queued product's next batch when current stock covers it.
        forecast = deepcopy(facts)
        forecast['provider_stock'] = {}
        forecast['reserved'] = {}
        needs = plate_requirements(goal, forecast)
    rates = facts['ledger']['rate_halves_per_second']
    return sorted(PLATES, key=lambda item: (-needs[item] / (rates[item] / 2), PLATES.index(item))), needs


def compile_candidates(facts):
    candidates = []
    sc = facts['scenario']
    def add(family, summary, **fields):
        candidate = {'family': family, 'summary': summary, **fields,
                     'at_revision': facts['revision'], 'at_tick': facts['tick']}
        candidate['option_id'] = family + '.' + sha256_hash({k:v for k,v in candidate.items() if k not in {'at_revision','at_tick'}})[7:19]
        candidates.append(candidate)
    for index, center in enumerate(sc['centers']):
        for side in ('west', 'east', 'north', 'south'):
            for product, spec in facts['recipes'].items():
                placements = generate_quad_slot(product, spec, (center['x'], center['y']), side)
                target = next(a['position'] for a in placements if a['entity'] == 'assembling-machine-1')
                if any(m['position'] == target and m.get('recipe') == product for m in facts['machines']): continue
                plan = {'surface': sc['surface'], 'force': sc['force'], 'atomic': True,
                        'phases': [{'name': 'mall-slot', 'actions': placements}]}
                validate_build_plan(plan)
                if quad_reservation_reason(plan, sc['centers']): continue
                add('assemble', f'{product} {side} module {index}', product=product, module=index, side=side, plan=plan)
    fresh = manufactured_stock(facts)
    for machine in facts['machines']:
        add('toggle', ('Pause ' if machine['active'] else 'Resume ')+(machine.get('recipe') or 'empty machine'),
            product=machine.get('recipe'), position=machine['position'], active=not machine['active'])
    for task in sc['demands']:
        gap = task['count'] - facts['served'].get(task['id'], 0)
        if facts['elapsed_ticks'] >= task['release_ticks'] and gap > 0 and fresh.get(task['item'], 0) > 0:
            add('deliver', f"Deliver {task['item']} to {task['id']}", goal=task['id'],
                count=min(gap, fresh[task['item']]), product=task['item'])
    for goal in facts['queue'][1:]: add('priority', f'Prioritize {goal}', goal=goal)
    queue, needs = plate_priority(facts)
    try:
        upgrade = plan_upgrade(facts['ledger'], facts['production'], fresh, queue)
    except ValueError:
        pass
    else:
        add('upgrade', f"Consume expansion batch; +1.5 {queue[0]}/s", upgrade=upgrade, plate_needs=needs)
    for ticks in (300, 1800):
        if ticks<=sc['max_episode_ticks']-facts['elapsed_ticks']:
            add('wait', f'Wait {ticks // 60} game seconds', wait_ticks=ticks)
    return candidates


def bounded_menu(legal, seed, limit=8):
    rng = random.Random(seed)
    groups = {}
    for c in legal: groups.setdefault(c['family'], []).append(c)
    families = sorted(groups); rng.shuffle(families)
    for group in groups.values(): rng.shuffle(group)
    chosen = [groups[k].pop() for k in families]
    rest = [c for group in groups.values() for c in group]; rng.shuffle(rest)
    return (chosen + rest)[:limit]


def decision_state(facts, candidates, run_id, model_ref, history=(), measurement=None):
    sc = facts['scenario']; scope = {k:sc[k] for k in ('surface','force','bounds')}
    goal = priority_goal(facts); queue, needs = plate_priority(facts)
    available = sum(max(0, n - facts['reserved'].get(k, 0)) for k,n in facts['provider_stock'].items())
    options = [{'option_id': c['option_id'], 'operation_family': c['family'], 'tool_id': 'mall.'+c['family'],
                'signature': sha256_hash(c), 'plan_hash': sha256_hash(c['plan']) if c.get('plan') else None,
                'summary': c['summary'], 'features': {'new_items': sum(c.get('bill',{}).values()),
                  'wait_ticks': c.get('wait_ticks',0)}, 'scope': scope,
                'risk_tier': 'ghost_submit' if c.get('plan') else ('destructive_mutation' if c['family'] in {'deliver','upgrade'}
                    else 'reversible_mutation' if c['family'] in {'toggle','priority'} else 'read_only')} for c in candidates]
    gap = sum(max(0, d['count']-facts['served'].get(d['id'],0)) for d in sc['demands'])
    mall = {'version': MALL_PROFILE, 'queue': facts['queue'], 'priority': goal,
            'demands': [{**d,'served':facts['served'].get(d['id'],0)} for d in sc['demands']],
            'stock': facts['provider_stock'], 'reserved': facts['reserved'], 'wip': facts['wip'],
            'supply_per_second': {k:v/2 for k,v in facts['ledger']['rate_halves_per_second'].items()},
            'fixture_delivered': facts['ledger']['fixture_delivered'], 'upgrade_count': facts['ledger']['upgrades'],
            'batch_available': {k:manufactured_stock(facts).get(k,0) for k in UPGRADE_BATCH},
            'needed_plates': needs, 'upgrade_supply': queue[0],
            'machines': [{'module':i,'side':side,'recipe':m.get('recipe'),'status':m['status'],'active':m['active']}
                for m in facts['machines'] for i,center in enumerate(sc['centers'])
                for side,(dx,dy) in QUAD_MALL_MACHINE_OFFSETS.items()
                if m['position']=={'x':center['x']+dx,'y':center['y']+dy}],
            'vertical_slots_require': {'long-handed-inserter':2}, 'robots': facts['robots']}
    if measurement: mall['flow']={k:measurement[k] for k in ('production_per_second','fixture_input','customer_delivered')}
    state={'version':'1.0.0','run':{'run_id':run_id,'episode_id':run_id,'tick':facts['tick'],
       'elapsed_ticks':facts['elapsed_ticks'],'bootstrap_profile':'supplied-input-mall','surface':sc['surface'],
       'force':sc['force'],'runtime':'disposable_training'},
      'objective':{'family':'mall','description':'Serve queued products and earn a consumed-batch supply upgrade.', 'target':1,'unit':'joint_service'},
      'goal_stack':[{'goal_id':'mall.'+goal,'kind':'produce','objective':'Serve '+goal,'status':'active','gap':gap,
                    'age_ticks':facts['elapsed_ticks'],'retry_count':0,'last_failure_kind':'none'}],
      'acceptance_gap':{'metric':'queued_products','current':sum(facts['served'].values()),'target':sum(d['count'] for d in sc['demands']),
                        'remaining':gap,'unit':'items','sustained_ticks':0,'required_sustained_ticks':0},
      'current_transaction':None,'state_digest':{
       'power':{'generation_kw':None,'firm_generation_kw':None,'accumulator_mj':None,'unpowered_consumers':None,'poles':4},
       'logistics':{'robots':facts['robots']['construction'],'providers_available':None,'pending_ghosts':facts['pending_ghosts'],'ghost_blockages_top3':[]},
       'stock':{'total':None,'accessible':available,'allocated':sum(facts['reserved'].values()),'top_shortages':[]},
       'production':{'rate_now':None,'rate_target':None,'sustained_ticks':None,'bottleneck_hint':None},
       'resource':{'remaining':None,'patch_bounds_hint':sc['bounds']},
       'space':{'free_area_tiles':None,'reserved_area_tiles':None,'occupied_footprint_tiles':None,'candidate_site_count':len(options)}},
      'recent_history':list(history)[-2:],'active_failures':[],
      'guidelines':[{'rule_id':'mall.funding','kind':'hard','statement':'Use funded ghosts; preserve fixtures and vertical space until long arms are available.'}],
      'options':options,'provenance':{'renderer_version':MALL_PROFILE,'state_snapshot_hash':sha256_hash(facts),
       'guideline_pack':{'id':'mall','version':'1.0.0','hash':sha256_hash(sc['kit'])},
       'tool_registry':{'id':'mall','version':'1.0.0','hash':sha256_hash(RECIPES)},'model':model_ref},
      'budget':{'materials_remaining':available,'construction_budget_remaining':None,'action_count_remaining':None,
                'elapsed_ticks_remaining':max(0,sc['max_episode_ticks']-facts['elapsed_ticks'])},
      'observation_digest':{'mall':mall}}
    validate_state(state)
    return state


def outcome(before, after):
    ticks=after['tick']-before['tick']
    if ticks<=0: raise SavedWorldError('mall measurement requires a positive tick window')
    production={k:after['production'][k]-v for k,v in before['production'].items()}
    if any(v<0 for v in production.values()): raise SavedWorldError('mall counters reset')
    return {'window_ticks':ticks,'production_per_second':{k:v*60/ticks for k,v in production.items() if v},
            'fixture_input':{k:after['ledger']['fixture_delivered'][k]-before['ledger']['fixture_delivered'][k] for k in PLATES},
            'customer_delivered':{k:after['served'].get(k,0)-before['served'].get(k,0) for k in after['served']},
            'supply_upgrades':after['ledger']['upgrades']-before['ledger']['upgrades'],
            'completed':all(after['served'].get(d['id'],0)>=d['count'] for d in after['scenario']['demands'])
               and after['ledger']['upgrades']>=after['scenario']['required_upgrades'] and after['pending_ghosts']==0,
            'elapsed_ticks':after['elapsed_ticks'],'material_cost':sum(after['construction_committed'].values()),
            'late_ticks':sum(max(0,after.get('completed_at',{}).get(d['id'],after['elapsed_ticks'])-d['due_ticks'])
                             for d in after['scenario']['demands'])}
