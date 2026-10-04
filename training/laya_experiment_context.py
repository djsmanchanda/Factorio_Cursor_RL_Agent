# Path: training/laya_experiment_context.py | Purpose: Explicit experiment-only requester horizons and dependency/stock-lock observations; no default strategy change.
from copy import deepcopy
from math import floor
from orchestrator.laya_operations import sha256_hash
from training.laya_factory_status import accessible_stock


def with_request_multiplier(candidate, multiplier):
    if type(multiplier) is not int or not 1 <= multiplier <= 15:
        raise ValueError('experimental request multiplier must be 1..15')
    result=deepcopy(candidate)
    changed=False
    for phase in result.get('plan',{}).get('phases',[]):
        for action in phase['actions']:
            for section in action.get('logistic_sections',[]):
                if section.get('multiplier',1)!=multiplier:
                    section['multiplier']=multiplier;changed=True
    if changed:result['option_id']=candidate['family']+'.'+sha256_hash(result['plan'])[7:19]
    return result


def dependency_context(facts, options, board):
    from training.laya_clean_runner import requested_items
    stock=accessible_stock(facts)
    made={n['item'] for n in board['needs'] if (n.get('measured_per_s',n.get('measured')) or 0)>0}
    rows={}
    for option in options:
        draws=requested_items(option);bill=option.get('bill') or {}
        left={n:max(0,stock.get(n,0)-draws.get(n,0)-bill.get(n,0)) for n in stock.keys()|draws.keys()|bill.keys()}
        blocked=[]
        for other in options:
            if other['option_id']==option['option_id'] or not other.get('bill'):continue
            if all(stock.get(n,0)>=v for n,v in other['bill'].items()) and any(left.get(n,0)<v for n,v in other['bill'].items()):blocked.append(other['option_id'])
        recipe=facts.get('recipes',{}).get(option.get('product'),{})
        ingredients=recipe.get('ingredients',[])
        ready=min((floor(stock.get(p['name'],0)/p['amount']) for p in ingredients),default=None)
        rows[option['option_id']]={'requested':draws,'unbacked_inputs':sorted(n for n in draws if stock.get(n,0)==0 and n not in made),
          'blocks_other_builds':len(blocked),'blocked_option_ids':blocked[:4], 'available_input_crafts':ready,
          'request_horizon_crafts':max((draws.get(p['name'],0)/p['amount'] for p in ingredients),default=0)}
    return rows


def apply_view(state, facts, options, board, history, mode):
    """Experimental policy views; execution validators always keep the full facts."""
    if mode=='full':return
    if mode not in {'inventory','bottleneck','dependency','history','combined'}:
        raise ValueError('unknown experimental context view')
    digest=state['observation_digest']
    digest['experiment_context_view']=mode
    if mode=='inventory':digest.pop('factory_status',None)
    if mode=='bottleneck':digest['factory_status']={'bottleneck':digest.get('factory_status',{}).get('bottleneck',[])}
    if mode in {'dependency','combined'}:
        digest['dependency_context']=dependency_context(facts,options,board)
        graph={n:sorted(p['name'] for p in r.get('ingredients',[])) for n,r in facts.get('recipes',{}).items()}
        digest['dependency_graph']=graph
    if mode in {'history','combined'}:
        digest['action_outcomes']=[h['experiment_outcome'] for h in history[-8:] if 'experiment_outcome' in h]


def reservation_view(plan, mode):
    if mode=='strict' or not plan:return plan
    if mode not in {'mall-open','corridor-open','soft-zones'}:raise ValueError('unknown reservation experiment')
    result=deepcopy(plan)
    result['zones']=[z for z in result['zones'] if not (mode=='soft-zones' or
                    mode=='mall-open' and z['purpose']=='mall' or mode=='corridor-open' and z['purpose']=='corridor')]
    return result
