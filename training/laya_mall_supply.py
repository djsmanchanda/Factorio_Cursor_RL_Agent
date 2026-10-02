# Path: training/laya_mall_supply.py | Purpose: Account for tick-based fixture plates and consumed, newly crafted mall expansion batches.

from copy import deepcopy

from orchestrator.laya_operations import sha256_hash

VERSION = 'mall-earned-supply-v1'
MALL_KIT = {'requester-chest': 10, 'passive-provider-chest': 20,
            'assembling-machine-1': 6, 'inserter': 12}
UPGRADE_BATCH = {'electric-mining-drill': 2, 'transport-belt': 10,
                 'inserter': 4, 'splitter': 1}
PLATES = ('iron-plate', 'copper-plate')


def _counts(values, names):
    result = {name: values.get(name, 0) for name in names}
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in result.values()):
        raise ValueError('item counters must be nonnegative integers')
    return result


def new_supply(tick, production):
    if isinstance(tick, bool) or not isinstance(tick, int) or tick < 0:
        raise ValueError('initial tick must be a nonnegative integer')
    return {'version': VERSION, 'tick': tick, 'baseline_production': _counts(production, UPGRADE_BATCH),
            'spent_crafting': dict.fromkeys(UPGRADE_BATCH, 0), 'upgrades': 0,
            'rate_halves_per_second': dict.fromkeys(PLATES, 2),
            'accrued_120ths': dict.fromkeys(PLATES, 0),
            'fixture_delivered': dict.fromkeys(PLATES, 0), 'consumption_receipts': []}


def accrue_supply(state, tick):
    """Keep undelivered input credit, including fractions, across arbitrary step sizes."""
    if isinstance(tick, bool) or not isinstance(tick, int) or tick < state['tick']:
        raise ValueError('supply clock cannot move backwards')
    elapsed = tick - state['tick']
    for item in PLATES:
        state['accrued_120ths'][item] += elapsed * state['rate_halves_per_second'][item]
    state['tick'] = tick
    return {item: state['accrued_120ths'][item] // 120 for item in PLATES}


def record_delivery(state, inserted):
    """Only an observed inventory insertion counts as fixture delivery, never crafting."""
    counts = _counts(inserted, PLATES)
    if set(inserted) - set(PLATES):
        raise ValueError('fixture may supply iron and copper plates only')
    if any(counts[item] * 120 > state['accrued_120ths'][item] for item in PLATES):
        raise ValueError('delivery exceeds accrued plate supply')
    for item, count in counts.items():
        state['accrued_120ths'][item] -= count * 120
        state['fixture_delivered'][item] += count


def upgrade_progress(state, production):
    current = _counts(production, UPGRADE_BATCH)
    credits = {item: current[item] - state['baseline_production'][item] - state['spent_crafting'][item]
               for item in UPGRADE_BATCH}
    if any(count < 0 for count in credits.values()):
        raise ValueError('crafting counters reset or spent credit disappeared')
    return {'newly_crafted_remainder': credits,
            'earned_batches': min(credits[item] // count for item, count in UPGRADE_BATCH.items()),
            'rates_per_second': {item: value / 2 for item, value in state['rate_halves_per_second'].items()},
            'fixture_delivered': deepcopy(state['fixture_delivered'])}


def plan_upgrade(state, production, provider_stock, plate_priority_queue):
    """The external priority queue determines the supply; this seam chooses no build order."""
    queue = list(plate_priority_queue)
    if not queue or any(item not in PLATES for item in queue) or len(queue) != len(set(queue)):
        raise ValueError('upgrade requires a current unique plate priority queue')
    if upgrade_progress(state, production)['earned_batches'] < 1:
        raise ValueError('no newly crafted expansion batch')
    stock = _counts(provider_stock, UPGRADE_BATCH)
    if any(stock[item] < count for item, count in UPGRADE_BATCH.items()):
        raise ValueError('complete batch is not available in provider/storage stock')
    return {'version': VERSION, 'ledger_hash': sha256_hash(state), 'supply': queue[0],
            'consumption': dict(UPGRADE_BATCH), 'increment_per_second': 1.5,
            'sequence': state['upgrades'] + 1}


def commit_upgrade(state, plan, removal_receipt):
    """Apply an upgrade only after the complete physical batch was removed once."""
    if plan.get('ledger_hash') != sha256_hash(state) or plan.get('sequence') != state['upgrades'] + 1:
        raise ValueError('stale or replayed supply upgrade')
    if (plan.get('version') != VERSION or plan.get('supply') not in PLATES
            or plan.get('consumption') != UPGRADE_BATCH or plan.get('increment_per_second') != 1.5):
        raise ValueError('invalid supply upgrade contract')
    receipt_id = removal_receipt.get('receipt_id')
    if (not isinstance(receipt_id, str) or not receipt_id or receipt_id in state['consumption_receipts']
            or removal_receipt.get('removed') != UPGRADE_BATCH
            or removal_receipt.get('plan_hash') != sha256_hash(plan)):
        raise ValueError('upgrade requires a fresh complete consumption receipt')
    for item, count in UPGRADE_BATCH.items():
        state['spent_crafting'][item] += count
    state['rate_halves_per_second'][plan['supply']] += 3
    state['upgrades'] += 1
    state['consumption_receipts'].append(receipt_id)
