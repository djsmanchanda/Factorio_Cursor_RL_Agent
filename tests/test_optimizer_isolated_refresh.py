# Path: tests/test_optimizer_isolated_refresh.py | Purpose: Protect stock refreshes from crossing untracked ledger scopes.

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from orchestrator.material_reservations import MaterialReservationError, MaterialReservationLedger
from tests.test_optimizer_saved_world import offer, world
from tools.optimizer_saved_world import optimizer_observation_candidate


def _refresh(ledger, bound, stock, **overrides):
    settings = {"binding": bound.plan.binding, "required": dict(bound.plan.required_materials),
                "target_item": bound.plan.throughput_output, "expected_revision": ledger.revision}
    settings.update(overrides)
    return ledger.refresh_isolated_bound_project("optimizer:iron-block", stock, **settings)


def test_isolated_refresh_tracks_shortage_and_recovery_without_releasing_hold(world):
    adapter, bound, ledger = world
    original = offer(world)
    required = dict(bound.plan.required_materials)
    item = next(iter(required))
    revision = ledger.revision
    project = _refresh(ledger, bound, {**required, item: 0})
    assert project.state == "supply_wait" and project.hold_until_producing and item not in project.reserved
    assert ledger.revision == revision + 1
    waiting = optimizer_observation_candidate(adapter, bound, ledger, episode_id="episode-1", network_anchor={"x": 3, "y": -1})
    assert waiting["funding_status"]["shortfalls"][item] == required[item]
    before = ledger.path.read_bytes()
    _refresh(ledger, bound, {**required, item: 0})
    assert ledger.revision == revision + 1 and ledger.path.read_bytes() == before
    project = _refresh(ledger, bound, required)
    assert project.state == "ready" and project.reserved == required and project.hold_until_producing
    assert ledger.revision == revision + 2 and offer(world)["option_id"] != original["option_id"]
    reloaded = MaterialReservationLedger(adapter.root / "server/script-output", episode_id="episode-1", surface="nauvis", force="player")
    assert reloaded.revision == ledger.revision and reloaded.projects == ledger.projects


@pytest.mark.parametrize("mutation", ["missing", "extra", "boolean", "negative", "fractional", "revision", "binding", "bill", "target"])
def test_isolated_refresh_rejects_incomplete_or_mismatched_inputs_without_mutation(world, mutation):
    _adapter, bound, ledger = world
    stock = dict(bound.plan.required_materials)
    item = next(iter(stock))
    settings = {}
    if mutation == "missing": stock.pop(item)
    elif mutation == "extra": stock["unrelated"] = 1
    elif mutation == "boolean": stock[item] = True
    elif mutation == "negative": stock[item] = -1
    elif mutation == "fractional": stock[item] = 0.5
    elif mutation == "revision": settings["expected_revision"] = ledger.revision + 1
    elif mutation == "binding": settings["binding"] = replace(bound.plan.binding, transaction_id="b" * 32)
    elif mutation == "bill": settings["required"] = {**stock, item: stock[item] + 1}
    else: settings["target_item"] = "other"
    before = ledger.path.read_bytes(), dict(ledger.projects), ledger.revision
    with pytest.raises(MaterialReservationError):
        _refresh(ledger, bound, stock, **settings)
    assert (ledger.path.read_bytes(), ledger.projects, ledger.revision) == before


@pytest.mark.parametrize("other_state", ["active", "constructing"])
def test_isolated_refresh_refuses_other_projects_without_network_attribution(world, other_state):
    _adapter, bound, ledger = world
    required = dict(bound.plan.required_materials)
    stock = {**required, "extra": 1}
    ledger.declare("other", {"extra": 1}, stock, hold_until_producing=False)
    if other_state == "constructing":
        ledger.mark_constructing("other", stock)
    before = ledger.path.read_bytes(), dict(ledger.projects), ledger.revision
    with pytest.raises(MaterialReservationError, match="other active or constructing"):
        _refresh(ledger, bound, required)
    assert (ledger.path.read_bytes(), ledger.projects, ledger.revision) == before


def test_isolated_refresh_allows_terminal_projects_without_rewriting_them(world):
    _adapter, bound, ledger = world
    required = dict(bound.plan.required_materials)
    ledger.declare("other", {"extra": 1}, {**required, "extra": 1})
    ledger.fail("other")
    terminal = ledger.projects["other"]
    _refresh(ledger, bound, {item: 0 for item in required})
    assert ledger.projects["other"] == terminal and ledger.projects["other"].state == "failed"


def test_isolated_refresh_rejects_other_writer_before_any_change(world):
    adapter, bound, ledger = world
    writer = MaterialReservationLedger(adapter.root / "server/script-output", episode_id="episode-1", surface="nauvis", force="player")
    writer.declare("other", {"extra": 1}, {**dict(bound.plan.required_materials), "extra": 1})
    before = writer.path.read_bytes(), dict(ledger.projects), ledger.revision
    with pytest.raises(MaterialReservationError, match="changed since it was loaded"):
        _refresh(ledger, bound, dict(bound.plan.required_materials))
    assert (writer.path.read_bytes(), ledger.projects, ledger.revision) == before


def test_concurrent_refreshes_from_same_revision_cannot_both_commit(world):
    _adapter, bound, ledger = world
    required = dict(bound.plan.required_materials)
    revision = ledger.revision
    barrier = Barrier(2)
    items = list(required)
    assert len(items) >= 2
    snapshots = [{**required, items[0]: 0}, {**required, items[1]: 0}]
    def refresh(stock):
        barrier.wait(timeout=10)
        try:
            _refresh(ledger, bound, stock, expected_revision=revision)
            return "saved"
        except MaterialReservationError as error:
            assert "revision changed" in str(error)
            return "stale"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(refresh, snapshots))
    assert sorted(outcomes) == ["saved", "stale"]
    assert ledger.revision == revision + 1
    assert ledger.projects["optimizer:iron-block"].state == "supply_wait"
