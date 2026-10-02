# Path: tests/test_laya_mall_supply.py | Purpose: Protect mall supply conservation, crafting credit and exactly-once consumed upgrades.

from copy import deepcopy

import pytest

from orchestrator.laya_operations import sha256_hash
from training.laya_mall_supply import (
    UPGRADE_BATCH, new_supply, accrue_supply, record_delivery,
    upgrade_progress, plan_upgrade, commit_upgrade,
)


def receipt(plan, removed=None, receipt_id='consumed-1'):
    return {'receipt_id': receipt_id, 'plan_hash': sha256_hash(plan),
            'removed': dict(UPGRADE_BATCH if removed is None else removed)}


def test_seeded_items_and_transfers_cannot_earn_an_upgrade():
    baseline = {item: count * 100 for item, count in UPGRADE_BATCH.items()}
    state = new_supply(100, baseline)
    with pytest.raises(ValueError, match='newly crafted'):
        plan_upgrade(state, baseline, baseline, ['iron-plate', 'copper-plate'])
    assert upgrade_progress(state, baseline)['earned_batches'] == 0
    crafted = {item: baseline[item] + count for item, count in UPGRADE_BATCH.items()}
    with pytest.raises(ValueError, match='provider/storage'):
        plan_upgrade(state, crafted, {}, ['iron-plate'])


def test_consumption_and_queue_priority_preserve_remainders_and_prevent_double_credit():
    state = new_supply(0, {})
    crafted = {item: count * 2 + 1 for item, count in UPGRADE_BATCH.items()}
    plan = plan_upgrade(state, crafted, crafted, ['copper-plate', 'iron-plate'])
    before = deepcopy(state)
    with pytest.raises(ValueError, match='complete consumption'):
        commit_upgrade(state, plan, receipt(plan, {'inserter': 4}))
    assert state == before
    commit_upgrade(state, plan, receipt(plan))
    assert upgrade_progress(state, crafted)['rates_per_second'] == {'iron-plate': 1, 'copper-plate': 2.5}
    with pytest.raises(ValueError, match='stale or replayed'):
        commit_upgrade(state, plan, receipt(plan))
    next_plan = plan_upgrade(state, crafted, UPGRADE_BATCH, ['iron-plate'])
    with pytest.raises(ValueError, match='fresh complete'):
        commit_upgrade(state, next_plan, receipt(next_plan))
    commit_upgrade(state, next_plan, receipt(next_plan, receipt_id='consumed-2'))
    assert upgrade_progress(state, crafted)['newly_crafted_remainder'] == dict.fromkeys(UPGRADE_BATCH, 1)
    assert upgrade_progress(state, crafted)['earned_batches'] == 0


def test_fractional_tick_supply_is_segmentation_independent_and_blocked_inputs_remain_due():
    whole = new_supply(0, {})
    split = deepcopy(whole)
    crafted = dict(UPGRADE_BATCH)
    for state in (whole, split):
        plan = plan_upgrade(state, crafted, crafted, ['iron-plate'])
        commit_upgrade(state, plan, receipt(plan))
    due = accrue_supply(whole, 180)
    record_delivery(whole, due)
    for tick in (1, 59, 60, 121, 180):
        ready = accrue_supply(split, tick)
        record_delivery(split, ready)
    assert whole == split
    assert whole['fixture_delivered'] == {'iron-plate': 7, 'copper-plate': 3}
    accrue_supply(whole, 240)
    record_delivery(whole, {'iron-plate': 1})
    assert accrue_supply(whole, 240) == {'iron-plate': 2, 'copper-plate': 1}
    before = deepcopy(whole)
    with pytest.raises(ValueError, match='exceeds'):
        record_delivery(whole, {'iron-plate': 3})
    assert whole == before
