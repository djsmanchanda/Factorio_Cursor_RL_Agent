# Path: tests/test_optimizer_ledger_revision.py | Purpose: Preserve candidate identity when ledger stock allocations change.

import pytest

from orchestrator.material_reservations import MaterialReservationError
from tests.test_optimizer_saved_world import offer, world
from tools.optimizer_saved_world import optimizer_observation_candidate


def test_stock_refresh_changes_bound_candidate_identity_and_persists(world):
    adapter, bound, ledger = world
    first = offer(world)
    before = ledger.revision
    stock = dict(bound.plan.required_materials)
    item = next(iter(stock))
    reduced = {**stock, item: 0}
    ledger.shortage_targets("optimizer:iron-block", reduced)
    assert ledger.revision == before + 1
    with pytest.raises(MaterialReservationError, match="fully funded"):
        offer(world)
    waiting = optimizer_observation_candidate(adapter, bound, ledger, episode_id="episode-1", network_anchor={"x": 3, "y": -1})
    assert waiting["funding_status"]["shortfalls"][item] == stock[item]
    assert waiting["option_id"] != first["option_id"]
    ledger.shortage_targets("optimizer:iron-block", stock)
    restored = offer(world)
    assert ledger.revision == before + 2 and restored["option_id"] != first["option_id"]
