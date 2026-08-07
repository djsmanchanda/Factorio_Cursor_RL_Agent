# Path: tests/test_logistic_coverage.py
# Purpose: Deterministic offline tests pinning that logistic chests are checked against the roboport LOGISTIC supply radius (25), not the construction radius (55), and that the roboports chosen to fix a gap genuinely close it.

from __future__ import annotations

import math

import pytest

from orchestrator import live_base
from orchestrator.autonomous_builder import _diagnose_blockage
from orchestrator.roboport_placement import clear_chain_positions
from planners.infrastructure_geometry import footprint_tile_indices
from orchestrator.stage_services import (
    _LOGISTIC_CHEST_ENTITIES,
    _ROBOPORT_CONSTRUCTION_RADIUS,
    _ROBOPORT_LINK_DISTANCE,
    _ROBOPORT_LOGISTIC_RADIUS,
    _logistic_chest_positions,
    ensure_logistic_coverage,
    extend_roboport_coverage,
    roboport_chain,
    service_distance,
)


class _FakeRcon:
    """Records the single batched query and replays a canned reply."""

    def __init__(self, reply: str = "") -> None:
        self.reply = reply
        self.commands: list[str] = []

    def command(self, text: str) -> str:
        self.commands.append(text)
        return self.reply


def _chest(x: float, y: float, entity: str = "requester-chest") -> dict:
    return {"action_type": "place_ghost", "entity": entity, "position": {"x": x, "y": y}}


# --- the radii themselves -------------------------------------------------

def test_logistic_radius_is_less_than_half_the_construction_radius() -> None:
    """Live-verified on 2.0.77: logistic_radius 25, construction_radius 55.

    The whole bug is that these are different numbers; if someone ever
    collapses them back into one constant this test is the tripwire.
    """
    assert _ROBOPORT_LOGISTIC_RADIUS == 25.0
    assert _ROBOPORT_CONSTRUCTION_RADIUS == 55.0
    assert _ROBOPORT_LOGISTIC_RADIUS * 2 < _ROBOPORT_CONSTRUCTION_RADIUS


def test_service_distance_uses_square_area_for_logistic_only() -> None:
    # The supply area is a square, so the far corner is served even though it
    # is 35 tiles away as the crow flies.
    assert service_distance((0.0, 0.0), (24.0, 24.0), square=True) == 24.0
    assert service_distance((0.0, 0.0), (24.0, 24.0), square=False) == pytest.approx(33.94, abs=0.01)


# --- coverage decision: the exact live bug --------------------------------

def test_chest_inside_the_supply_radius_needs_no_new_roboport() -> None:
    assert roboport_chain((0.0, 0.0), (20.0, 0.0), _ROBOPORT_LOGISTIC_RADIUS) == []


@pytest.mark.parametrize("distance", [26.0, 30.0, 40.0, 54.0])
def test_chest_between_the_two_radii_is_buildable_but_not_served(distance: float) -> None:
    """THE BUG: these chests are inside construction range so the bots build
    them happily, and outside supply range so they join no network at all.
    Checking only the construction radius reports them as fine."""
    chest = (distance, 0.0)
    assert service_distance((0.0, 0.0), chest, square=False) <= _ROBOPORT_CONSTRUCTION_RADIUS
    assert roboport_chain((0.0, 0.0), chest, _ROBOPORT_CONSTRUCTION_RADIUS) == []
    assert roboport_chain((0.0, 0.0), chest, _ROBOPORT_LOGISTIC_RADIUS) != []


@pytest.mark.parametrize(
    "target",
    [(30.0, 0.0), (40.0, 0.0), (0.0, -38.0), (30.0, 30.0), (-27.5, 8.5), (200.0, 0.0),
     (-120.0, 90.0)],
)
def test_chain_ends_with_the_chest_actually_inside_the_supply_area(target: tuple[float, float]) -> None:
    """A remedy that does not close the gap is worse than none: it spends
    roboports and leaves the chest just as stranded."""
    source = (0.0, 0.0)
    chain = roboport_chain(source, target, _ROBOPORT_LOGISTIC_RADIUS)
    assert chain, "a gap was reported but no roboport was proposed"
    assert service_distance(chain[-1], target, square=True) <= _ROBOPORT_LOGISTIC_RADIUS
    # Euclidean too, so the result holds under the conservative measure as well.
    assert math.dist(chain[-1], target) <= _ROBOPORT_LOGISTIC_RADIUS


@pytest.mark.parametrize("target", [(40.0, 0.0), (200.0, 0.0), (-120.0, 90.0), (30.0, 30.0)])
def test_every_chain_hop_stays_within_roboport_link_distance(target: tuple[float, float]) -> None:
    """An unlinked roboport forms its own network and supplies nothing."""
    source = (0.0, 0.0)
    previous = source
    for hop in roboport_chain(source, target, _ROBOPORT_LOGISTIC_RADIUS):
        assert math.dist(previous, hop) <= _ROBOPORT_LINK_DISTANCE
        previous = hop


def test_chain_never_places_a_roboport_on_top_of_the_chest() -> None:
    chain = roboport_chain((0.0, 0.0), (40.0, 0.0), _ROBOPORT_LOGISTIC_RADIUS)
    assert (40.0, 0.0) not in chain


def test_chain_relocates_a_roboport_off_a_stage_footprint(monkeypatch) -> None:
    ideal = (17.0, 0.0)
    monkeypatch.setattr(
        live_base, "area_clear",
        lambda _c, _s, lo, hi: ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2) != ideal,
    )
    placed = clear_chain_positions(
        None, "nauvis", (0.0, 0.0), (40.0, 0.0), [ideal],
        service_radius=25.0, service_square=True, link_distance=46.0,
    )
    assert placed[0] != ideal
    assert math.dist((0.0, 0.0), placed[0]) <= 46.0
    assert service_distance(placed[0], (40.0, 0.0), square=True) <= 25.0


def test_chain_relocates_off_pending_plan_footprints(monkeypatch) -> None:
    """A clear centre can still overlap a pending machine or pole ghost."""
    ideal = (17.0, 0.0)
    reserved = {(x, y) for x in range(15, 19) for y in range(-2, 2)}
    monkeypatch.setattr(live_base, "area_clear", lambda *_a, **_k: True)
    placed = clear_chain_positions(
        None, "nauvis", (0.0, 0.0), (40.0, 0.0), [ideal],
        service_radius=25.0, service_square=True, link_distance=46.0,
        reserved_tiles=reserved,
    )
    assert not (footprint_tile_indices(placed[0], 4) & reserved)


def test_low_power_roboport_is_given_a_power_hookup(monkeypatch) -> None:
    powered = []
    monkeypatch.setattr(live_base, "nearest_roboport", lambda *a, **k: (0.0, 0.0))
    monkeypatch.setattr(live_base, "area_clear", lambda *a, **k: True)
    monkeypatch.setattr(live_base, "entity_status_name", lambda *a, **k: "low_power")
    monkeypatch.setattr("orchestrator.stage_services._submit", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(
        "orchestrator.stage_services.extend_power",
        lambda _c, _b, _s, _f, position, _emit: powered.append(position) or True,
    )
    assert extend_roboport_coverage(
        None, None, "nauvis", "player", (30.0, 0.0), lambda _m: None,
        purpose="logistic",
    )
    assert powered

# --- plan-time chest discovery -------------------------------------------

def test_logistic_chest_positions_finds_feeds_and_output_but_not_plain_chests() -> None:
    plan = {"phases": [{"name": "line", "actions": [
        _chest(1.0, 2.0, "requester-chest"),
        _chest(1.0, 6.0, "requester-chest"),
        _chest(9.0, 2.0, "passive-provider-chest"),
        _chest(4.0, 4.0, "steel-chest"),
        {"action_type": "place_ghost", "entity": "assembling-machine-2",
         "position": {"x": 5.0, "y": 5.0}},
    ]}]}
    assert _logistic_chest_positions(plan) == [(1.0, 2.0), (1.0, 6.0), (9.0, 2.0)]


def test_every_colour_of_logistic_chest_is_covered_by_the_scan() -> None:
    """The user's list: passive provider, active provider, requester, storage,
    buffer -- all inert outside a supply area."""
    assert _LOGISTIC_CHEST_ENTITIES == {
        "active-provider-chest", "buffer-chest", "passive-provider-chest",
        "requester-chest", "storage-chest",
    }


# --- live query shape -----------------------------------------------------

def test_logistic_network_query_is_one_round_trip_and_decodes_absence() -> None:
    client = _FakeRcon("1=24,2=-,3=-")
    served = live_base.logistic_network_ids(
        client, "nauvis", [(-0.5, -2.5), (30.5, -9.5), (-9.5, 30.5)],
    )
    assert len(client.commands) == 1
    assert "logistic_network" in client.commands[0]
    # The two live-observed stranded providers, and one healthy chest.
    assert served == {(-0.5, -2.5): 24, (30.5, -9.5): None, (-9.5, 30.5): None}


def test_a_chest_that_is_still_a_ghost_is_omitted_not_reported_as_stranded() -> None:
    """An unbuilt chest is the bots' business. Reporting it as "no network"
    would make every stage look permanently broken while it is being built."""
    served = live_base.logistic_network_ids(
        _FakeRcon("1=x,2=-,3=24"), "nauvis", [(0.0, 0.0), (30.5, -9.5), (1.0, 1.0)],
    )
    assert served == {(30.5, -9.5): None, (1.0, 1.0): 24}


def test_logistic_network_query_is_skipped_entirely_for_no_positions() -> None:
    client = _FakeRcon()
    assert live_base.logistic_network_ids(client, "nauvis", []) == {}
    assert client.commands == []


# --- wiring into the builder ---------------------------------------------

def test_extend_roboport_coverage_ignores_the_gap_when_asked_about_construction(monkeypatch) -> None:
    monkeypatch.setattr(live_base, "nearest_roboport", lambda *a, **k: (0.0, 0.0))
    monkeypatch.setattr(
        "orchestrator.stage_services._submit",
        lambda *a, **k: pytest.fail("construction coverage should be satisfied at 40 tiles"),
    )
    assert extend_roboport_coverage(
        None, None, "nauvis", "player", (40.0, 0.0), lambda _m: None,
    ) is False


def test_ensure_logistic_coverage_places_a_roboport_for_a_stranded_chest(monkeypatch) -> None:
    submitted: list[dict] = []
    monkeypatch.setattr(live_base, "nearest_roboport", lambda *a, **k: (0.0, 0.0))
    monkeypatch.setattr(live_base, "entity_status_name", lambda *a, **k: "working")
    monkeypatch.setattr(live_base, "area_clear", lambda *a, **k: True)
    monkeypatch.setattr(
        "orchestrator.stage_services._submit",
        lambda _c, _b, _s, plan, *a, **k: submitted.append(plan) or {"ok": True},
    )
    assert ensure_logistic_coverage(
        None, None, "nauvis", "player", [(30.5, -9.5)], lambda _m: None,
    ) is True
    placed = [
        (action["position"]["x"], action["position"]["y"])
        for plan in submitted for phase in plan["phases"] for action in phase["actions"]
    ]
    assert placed
    assert all(action["entity"] == "roboport"
               for plan in submitted for phase in plan["phases"] for action in phase["actions"])
    assert service_distance(placed[-1], (30.5, -9.5), square=True) <= _ROBOPORT_LOGISTIC_RADIUS


def test_ensure_logistic_coverage_is_a_no_op_for_chests_already_served(monkeypatch) -> None:
    monkeypatch.setattr(live_base, "nearest_roboport", lambda *a, **k: (0.0, 0.0))
    monkeypatch.setattr(
        "orchestrator.stage_services._submit",
        lambda *a, **k: pytest.fail("no roboport is needed inside the supply area"),
    )
    assert ensure_logistic_coverage(
        None, None, "nauvis", "player", [(20.0, 0.0), (-24.0, 24.0)], lambda _m: None,
    ) is False


# --- diagnosis ordering ---------------------------------------------------

def _patch_healthy_site(monkeypatch) -> None:
    monkeypatch.setattr(live_base, "nearest_roboport", lambda *a, **k: (0.0, 0.0))
    monkeypatch.setattr(live_base, "entity_status_name", lambda *a, **k: "working")
    monkeypatch.setattr(live_base, "area_clear", lambda *a, **k: True)
    monkeypatch.setattr(live_base, "entity_statuses", lambda _c, _s, ps: {tuple(p): "working" for p in ps})


def test_diagnose_blockage_reports_a_stranded_chest_with_a_remedy(monkeypatch) -> None:
    _patch_healthy_site(monkeypatch)
    monkeypatch.setattr(live_base, "logistic_network_ids",
                        lambda _c, _s, ps: {tuple(p): None for p in ps})
    issue = _diagnose_blockage(
        None, "nauvis", "player", (10.0, 10.0), (10.0, 12.0), [(11.0, 10.0)],
        [(30.5, -9.5)],
    )
    assert issue is not None
    description, remedy = issue
    assert remedy == "logistic_coverage"
    assert "no logistic network" in description


def test_diagnose_blockage_is_silent_when_every_chest_is_on_a_network(monkeypatch) -> None:
    _patch_healthy_site(monkeypatch)
    monkeypatch.setattr(live_base, "logistic_network_ids",
                        lambda _c, _s, ps: {tuple(p): 24 for p in ps})
    assert _diagnose_blockage(
        None, "nauvis", "player", (10.0, 10.0), (10.0, 12.0), [(11.0, 10.0)], [(12.0, 10.0)],
    ) is None


def test_construction_coverage_still_outranks_logistic_coverage(monkeypatch) -> None:
    """Nothing gets built at all outside construction range, so that stays the
    first thing reported -- the chest cannot be stranded before it exists."""
    monkeypatch.setattr(live_base, "nearest_roboport", lambda *a, **k: (0.0, 0.0))
    monkeypatch.setattr(live_base, "logistic_network_ids",
                        lambda _c, _s, ps: {tuple(p): None for p in ps})
    issue = _diagnose_blockage(
        None, "nauvis", "player", (300.0, 0.0), (300.0, 2.0), [(301.0, 0.0)], [(300.0, 1.0)],
    )
    assert issue is not None and issue[1] == "coverage"


def test_unbuilt_chests_do_not_look_like_a_coverage_fault(monkeypatch) -> None:
    """Regression guard for the remediation loop: while a stage's chests are
    still ghosts the live query returns nothing for them, and the loop must
    keep waiting on bots instead of burning all six rounds on a phantom."""
    _patch_healthy_site(monkeypatch)
    monkeypatch.setattr(live_base, "logistic_network_ids", lambda _c, _s, _ps: {})
    assert _diagnose_blockage(
        None, "nauvis", "player", (10.0, 10.0), (10.0, 12.0), [(11.0, 10.0)], [(12.0, 10.0)],
    ) is None


def test_diagnose_blockage_skips_the_logistic_query_when_no_chests_are_known(monkeypatch) -> None:
    _patch_healthy_site(monkeypatch)
    monkeypatch.setattr(
        live_base, "logistic_network_ids",
        lambda *a, **k: pytest.fail("no chest positions were supplied to check"),
    )
    assert _diagnose_blockage(
        None, "nauvis", "player", (10.0, 10.0), (10.0, 12.0), [(11.0, 10.0)],
    ) is None
