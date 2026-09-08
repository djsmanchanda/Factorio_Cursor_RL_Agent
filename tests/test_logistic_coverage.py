# Path: tests/test_logistic_coverage.py
# Purpose: Deterministic offline tests pinning that logistic chests are checked against the roboport LOGISTIC supply radius (25), not the construction radius (55), and that the roboports chosen to fix a gap genuinely close it.

from __future__ import annotations

import math

import pytest

from orchestrator import live_base
from orchestrator import autonomous_builder as builder
from orchestrator.autonomous_builder import _deliver_cell_ingredients, _diagnose_blockage
from orchestrator import roboport_placement
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


def test_transfer_stock_uses_mall_providers_and_storage_as_sources() -> None:
    client = _FakeRcon("12")
    moved = live_base.transfer_stock(
        client, "nauvis", "iron-plate", 20, (4.0, 5.0),
    )

    assert moved == 12
    lua = client.commands[0]
    assert "type={'container','logistic-container'}" in lua
    assert "c.prototype.logistic_mode" in lua
    assert "mode=='passive-provider'" in lua
    assert "mode=='storage'" in lua


def test_cell_delivery_reuses_a_nearby_provider_without_a_new_plan(monkeypatch) -> None:
    submitted: list[str] = []
    transfers: list[tuple[str, int, tuple[float, float]]] = []

    def fake_requester(_client, _surface, _item, _near):
        return (10.0, 10.0)

    def fake_count(*_args):
        return 0

    def fake_provider(*_args, **_kwargs):
        return (13.0, 10.0)

    def fake_transfer(_client, _surface, item, count, destination):
        transfers.append((item, count, destination))
        return count

    monkeypatch.setattr(builder.live_base, "requester_requesting", fake_requester)
    monkeypatch.setattr(builder.live_base, "network_item_count", fake_count)
    monkeypatch.setattr(builder.live_base, "nearest_container", fake_provider)
    monkeypatch.setattr(builder.live_base, "transfer_stock", fake_transfer)
    monkeypatch.setattr(
        builder, "_submit",
        lambda *_args, **_kwargs: submitted.append("submitted"),
    )

    assert _deliver_cell_ingredients(
        object(), object(), "nauvis", "player", "inserter", (0.0, 0.0),
        lambda _message: None,
    ) is True
    assert submitted == []
    assert transfers


def test_cell_delivery_does_not_submit_when_an_existing_transfer_is_empty(
    monkeypatch,
) -> None:
    submitted: list[str] = []
    monkeypatch.setattr(
        builder.live_base, "requester_requesting", lambda *_a: (0.0, 0.0),
    )
    monkeypatch.setattr(builder.live_base, "network_item_count", lambda *_a: 0)
    monkeypatch.setattr(
        builder.live_base, "nearest_container", lambda *_a, **_k: (3.0, 0.0),
    )
    monkeypatch.setattr(builder.live_base, "transfer_stock", lambda *_a: 0)
    monkeypatch.setattr(
        builder, "_submit",
        lambda *_args, **_kwargs: submitted.append("submitted"),
    )

    assert _deliver_cell_ingredients(
        object(), object(), "nauvis", "player", "inserter", (0.0, 0.0),
        lambda _message: None,
    ) is False
    assert submitted == []


def test_cell_delivery_can_pin_a_rotating_loan_to_its_exact_requester(
    monkeypatch,
) -> None:
    requesters: list[tuple[float, float]] = []
    monkeypatch.setattr(
        builder.live_base, "requester_requesting",
        lambda *_a: pytest.fail("an explicit loan requester needs no search"),
    )
    monkeypatch.setattr(builder.live_base, "network_item_count", lambda *_a: 0)
    monkeypatch.setattr(
        builder.live_base, "nearest_container",
        lambda _c, _s, _f, chest, **_k: requesters.append(chest) or (52.5, 32.5),
    )
    monkeypatch.setattr(builder.live_base, "transfer_stock", lambda *_a: 1)

    assert _deliver_cell_ingredients(
        object(), object(), "nauvis", "player", "electronic-circuit",
        (47.5, 32.5), lambda _message: None,
        requester_position=(50.5, 32.5),
    ) is True
    assert requesters == [(50.5, 32.5), (50.5, 32.5)]


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


# --- chain spacing: the live waste of 2026-08-21 ---------------------------
# Ports at (45,-1), (47,-5), (49,-9) each sat a few tiles from their
# predecessor, stacking supply areas on top of each other, because every hop
# was capped at "just past where coverage begins". A port that barely extends
# reach costs a full roboport's worth of power and robots for nothing.

@pytest.mark.parametrize(
    "target,radius,square",
    [((101.0, -21.0), _ROBOPORT_CONSTRUCTION_RADIUS, False),
     ((84.5, -41.5), _ROBOPORT_CONSTRUCTION_RADIUS, False),
     ((39.5, 31.5), _ROBOPORT_LOGISTIC_RADIUS, True)],
)
def test_one_needed_port_is_pushed_toward_the_gap_not_clustered(
    target: tuple[float, float], radius: float, square: bool,
) -> None:
    """A gap just past one roboport's radius warrants ONE useful port, not a
    cluster beside the existing network edge."""
    source = (45.0, -1.0)
    total = math.dist(source, target)
    assert service_distance(source, target, square=square) > radius
    chain = roboport_chain(source, target, radius)
    assert len(chain) == 1
    # The port travels most of the way to the gap instead of hugging `source`.
    assert math.dist(source, chain[0]) >= min(30.0, total - radius)


@pytest.mark.parametrize("target", [(100.0, 0.0), (200.0, -80.0), (-140.0, 95.0)])
def test_chain_ports_are_spread_along_the_route(target: tuple[float, float]) -> None:
    """Consecutive ports sit near one link apart -- never several ports inside
    one another's supply area."""
    source = (0.0, 0.0)
    chain = roboport_chain(source, target, _ROBOPORT_CONSTRUCTION_RADIUS)
    previous = source
    for hop in chain:
        gap = math.dist(previous, hop)
        assert gap <= _ROBOPORT_LINK_DISTANCE
        assert gap >= _ROBOPORT_LINK_DISTANCE - 12 or hop is chain[-1], (
            "an interior hop wasted a port by sitting on its predecessor"
        )
        previous = hop
    assert math.dist(chain[-1], target) <= _ROBOPORT_CONSTRUCTION_RADIUS


def test_chain_relocates_a_roboport_off_a_stage_footprint(monkeypatch) -> None:
    ideal = (17.0, 0.0)
    monkeypatch.setattr(
        live_base, "area_clear",
        lambda _c, _s, lo, hi, **_k:
            ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2) != ideal,
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


def test_chain_relocates_roboports_off_resource_patches(monkeypatch) -> None:
    options = []
    monkeypatch.setattr(
        live_base, "area_clear",
        lambda *_a, **kwargs: options.append(kwargs) or True,
    )

    placed = clear_chain_positions(
        None, "nauvis", (0.0, 0.0), (40.0, 0.0), [(17.0, 0.0)],
        service_radius=25.0, service_square=True, link_distance=46.0,
    )

    assert placed
    assert options
    assert all(option.get("avoid_resources") is True for option in options)


def test_blocked_local_roboport_ideal_uses_an_alternate_corridor(monkeypatch) -> None:
    """A 10-tile local failure must not terminate a remote coverage task.

    This models the live oil-cell failure near (-67, 18): the direct ideal is
    blocked, but a nearby connected site is valid and keeps the final target
    covered.
    """
    ideals = roboport_chain((0.0, 0.0), (100.0, 0.0), _ROBOPORT_CONSTRUCTION_RADIUS)
    blocked = footprint_tile_indices(ideals[0], 4)
    # Use an explicit local failure rather than probing the game for it; the
    # fallback itself still receives the surveyed obstacle.
    monkeypatch.setattr(
        roboport_placement, "_local_chain_positions",
        lambda *_a, **_k: (_ for _ in ()).throw(ValueError("local blocked")),
    )
    occupied_options = []
    monkeypatch.setattr(
        live_base, "occupied_tiles",
        lambda *_a, **kwargs: occupied_options.append(kwargs) or blocked,
    )

    placed = clear_chain_positions(
        None, "nauvis", (0.0, 0.0), (100.0, 0.0), ideals,
        service_radius=_ROBOPORT_CONSTRUCTION_RADIUS,
        service_square=False,
        link_distance=_ROBOPORT_LINK_DISTANCE,
    )

    assert placed[0] != ideals[0]
    assert all(
        math.dist(previous, current) <= _ROBOPORT_LINK_DISTANCE
        for previous, current in zip([(0.0, 0.0), *placed], placed)
    )
    assert math.dist(placed[-1], (100.0, 0.0)) <= _ROBOPORT_CONSTRUCTION_RADIUS
    assert occupied_options == [{
        "include_clutter": True,
        "include_resources": True,
    }]


def test_low_power_roboport_is_given_a_power_hookup(monkeypatch) -> None:
    from orchestrator import stage_services

    powered = []
    calls = {"n": 0}
    def _nearest(*a, **k):
        calls["n"] += 1
        return (0.0, 0.0) if calls["n"] == 1 else (30.0, 0.0)
    monkeypatch.setattr(live_base, "nearest_roboport", _nearest)
    monkeypatch.setattr(live_base, "area_clear", lambda *a, **k: True)
    monkeypatch.setattr(
        stage_services, "_repair_existing_roboport_power", lambda *_args: (),
    )
    monkeypatch.setattr(live_base, "entity_status_name", lambda *a, **k: "low_power")
    monkeypatch.setattr("orchestrator.stage_services._submit", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(
        stage_services, "_await_built_status", lambda *_args, **_kwargs: "low_power",
    )
    monkeypatch.setattr(stage_services, "_await_roboport_charge", lambda *_args: None)
    monkeypatch.setattr(
        "orchestrator.stage_services.extend_power",
        lambda _c, _b, _s, _f, position, _emit, **_kwargs:
            powered.append(position) or True,
    )
    with pytest.raises(builder.ProductionPrerequisiteDeferred):
        extend_roboport_coverage(
            object(), object(), "nauvis", "player", (30.0, 0.0), lambda _m: None,
            purpose="logistic",
        )
    assert powered


# --- wave batching: a charging roboport is a multi-megawatt load ----------

def test_charge_wait_is_skipped_when_the_grid_can_generate_the_threshold(monkeypatch) -> None:
    from orchestrator import stage_services

    monkeypatch.setattr(
        live_base, "network_generation_kw", lambda *_a: stage_services._ROBOPORT_WAVE_GENERATION_KW,
    )
    slept: list[float] = []
    monkeypatch.setattr(stage_services.time, "sleep", slept.append)
    stage_services._await_roboport_charge(
        None, "nauvis", "player", (0.0, 0.0), [(1.0, 1.0)], lambda _m: None,
    )
    assert slept == []


def test_charge_check_accepts_an_already_charged_wave(monkeypatch) -> None:
    from orchestrator import stage_services

    monkeypatch.setattr(live_base, "network_generation_kw", lambda *_a: 10_000.0)
    monkeypatch.setattr(
        live_base, "roboport_energy", lambda *_a: 95_000_000.0,
    )
    polls: list[float] = []
    monkeypatch.setattr(stage_services.time, "sleep", polls.append)
    stage_services._await_roboport_charge(
        None, "nauvis", "player", (0.0, 0.0), [(1.0, 1.0)], lambda _m: None,
    )
    assert polls == []


def test_charging_wave_does_not_sleep_or_stop_coverage(monkeypatch) -> None:
    from orchestrator import stage_services

    monkeypatch.setattr(live_base, "network_generation_kw", lambda *_a: 167.0)
    monkeypatch.setattr(live_base, "roboport_energy", lambda *_a: 50_000_000.0)
    slept: list[float] = []
    monkeypatch.setattr(stage_services.time, "sleep", slept.append)
    messages: list[str] = []

    stage_services._await_roboport_charge(
        None, "nauvis", "player", (0.0, 0.0), [(1.0, 1.0)],
        messages.append,
    )
    assert slept == []
    assert any("next bot-built coverage hop waits" in message for message in messages)


def test_existing_low_power_anchor_still_extends_required_coverage(monkeypatch) -> None:
    from orchestrator import stage_services

    monkeypatch.setattr(
        stage_services, "_repair_existing_roboport_power", lambda *_args: None,
    )
    calls = {"n": 0}
    def _nearest(*_args):
        calls["n"] += 1
        return (5.0, 5.0) if calls["n"] == 1 else (100.0, 5.0)
    monkeypatch.setattr(live_base, "nearest_roboport", _nearest)
    monkeypatch.setattr(
        live_base, "entity_status_name", lambda *_args: "low_power",
    )
    charge_checks: list[tuple] = []
    monkeypatch.setattr(
        stage_services, "_await_roboport_charge",
        lambda *_args: charge_checks.append(_args[4]),
    )
    monkeypatch.setattr(
        stage_services, "clear_chain_positions",
        lambda *_args, **_kwargs: [(45.0, 5.0)],
    )
    submitted: list[str] = []
    monkeypatch.setattr(
        stage_services, "_submit",
        lambda _c, _b, _s, _plan, name, _emit, **_kwargs:
            submitted.append(name) or {"ok": True},
    )
    monkeypatch.setattr(
        stage_services, "_await_built_status",
        lambda *_args, **_kwargs: "working",
    )

    assert stage_services.extend_roboport_coverage(
        object(), object(), "nauvis", "player", (100.0, 5.0),
        lambda _message: None,
    )
    assert charge_checks
    assert submitted == ["roboport_bridge"]


def test_coverage_waits_for_powered_source_before_next_ghost_hop(monkeypatch) -> None:
    """A mall-funded pole bridge cannot make its roboport usable in this pass.

    Cycle 5 placed the first construction-reachable port at (28,-35), then
    treated it as a live logistic source before its ghost-built power bridge
    had joined. The second hop was therefore unreachable and the controller
    terminally rejected ordinary construction work.
    """
    from orchestrator import stage_services

    source, target = (28.0, -35.0), (54.5, -70.5)
    monkeypatch.setattr(
        stage_services.live_base, "roboports_needing_power",
        lambda *_args: [(source, "low_power")],
    )
    bridges: list[Point] = []
    monkeypatch.setattr(
        stage_services, "extend_power",
        lambda *_args, **_kwargs: bridges.append(_args[4]) or True,
    )
    monkeypatch.setattr(
        stage_services, "clear_chain_positions",
        lambda *_args, **_kwargs: pytest.fail(
            "the next roboport must wait for the powered source",
        ),
    )
    monkeypatch.setattr(stage_services, "_await_roboport_charge", lambda *_args: None)

    with pytest.raises(builder.ProductionPrerequisiteDeferred) as deferred:
        stage_services.extend_roboport_coverage(
            object(), object(), "nauvis", "player", target, lambda _m: None,
            purpose="logistic",
        )

    assert bridges == [source]
    assert deferred.value.code == "roboport_coverage_construction_wait"
    assert deferred.value.state == "constructing"
    assert deferred.value.details["wave"] == [[28.0, -35.0]]


def test_new_roboport_ghost_defers_before_a_second_unreachable_hop(monkeypatch) -> None:
    """One submitted port is a construction wait, never permission to bulk-chain."""
    from orchestrator import stage_services

    source, first, second, target = (0.0, 0.0), (28.0, -35.0), (54.5, -70.5), (70.0, -90.0)
    monkeypatch.setattr(
        stage_services.live_base, "roboports_needing_power", lambda *_args: [],
    )
    monkeypatch.setattr(
        stage_services.live_base, "nearest_roboport", lambda *_args: source,
    )
    monkeypatch.setattr(
        stage_services.live_base, "entity_status_name", lambda *_args: "working",
    )
    monkeypatch.setattr(
        stage_services, "clear_chain_positions", lambda *_args, **_kwargs: [first, second],
    )
    submitted: list[list[dict]] = []
    monkeypatch.setattr(
        stage_services, "_submit",
        lambda _c, _b, _s, plan, *_args, **_kwargs:
            submitted.append(plan["phases"][0]["actions"]) or {},
    )
    monkeypatch.setattr(stage_services, "_await_built_status", lambda *_args, **_kwargs: None)

    with pytest.raises(builder.ProductionPrerequisiteDeferred) as deferred:
        stage_services.extend_roboport_coverage(
            object(), object(), "nauvis", "player", target, lambda _m: None,
            purpose="logistic",
        )

    assert submitted == [[{
        "action_type": "place_ghost", "entity": "roboport",
        "position": {"x": first[0], "y": first[1]},
    }]]
    assert deferred.value.code == "roboport_coverage_construction_wait"
    assert deferred.value.state == "constructing"


def test_offline_long_chains_retain_complete_geometric_plan(monkeypatch) -> None:
    """Offline geometry has no entities to re-observe, so it retains all hops."""
    from orchestrator import stage_services

    submitted_waves: list[int] = []
    waits: list[int] = []
    calls = {"n": 0}
    def _nearest(*a, **k):
        calls["n"] += 1
        return (0.0, 0.0) if calls["n"] == 1 else (400.0, 0.0)
    monkeypatch.setattr(live_base, "nearest_roboport", _nearest)
    monkeypatch.setattr(live_base, "area_clear", lambda *a, **k: True)
    monkeypatch.setattr(
        "orchestrator.stage_services._submit",
        lambda _c, _b, _s, plan, _name, _emit, **_k:
            submitted_waves.append(len(plan["phases"][0]["actions"])) or {"ok": True},
    )
    monkeypatch.setattr(
        "orchestrator.stage_services._await_built_status",
        lambda *_a, **_k: "working",
    )
    monkeypatch.setattr(
        "orchestrator.stage_services._await_roboport_charge",
        lambda _c, _s, _f, _near, wave, _emit: waits.append(len(wave)),
    )

    assert extend_roboport_coverage(
        None, None, "nauvis", "player", (400.0, 0.0), lambda _m: None,
    )

    assert sum(submitted_waves) >= 4
    assert set(submitted_waves) == {1}
    assert stage_services._ROBOPORT_WAVE == 1
    assert waits == []


def test_network_generation_query_sums_generators_on_one_network() -> None:
    client = _FakeRcon("42000.0")
    assert live_base.network_generation_kw(client, "nauvis", "player", (3.0, -1.0)) == 42000.0
    # Accumulators store rather than generate; they must not license a burst.
    assert "accumulator" not in client.commands[0]
    assert "get_max_energy_production" in client.commands[0]
    assert "type='electric-pole'" in client.commands[0]
    assert "name='roboport'" not in client.commands[0]


def test_primary_power_bridge_prefers_generation_over_proximity() -> None:
    client = _FakeRcon("20.0 10.0 substation")

    assert live_base.nearest_powered_pole(
        client, "nauvis", "player", (3.0, -1.0),
    ) == ((20.0, 10.0), "substation")
    lua = client.commands[0]
    assert "generation[id]" in lua
    assert "selected,best_kw" in lua
    assert "goto" not in lua
    assert "if d<bd then bd=d;best=e.position;bname=e.name end end end;" in lua


def test_primary_power_bridge_can_exclude_poles_inside_resource_patches() -> None:
    client = _FakeRcon("8.0 -8.0 substation")

    assert live_base.nearest_powered_pole(
        client, "nauvis", "player", (40.0, 26.0),
        avoid_resources=True,
    ) == ((8.0, -8.0), "substation")
    lua = client.commands[0]
    assert "local avoid=true" in lua
    assert "count_entities_filtered{type='resource',area=e.bounding_box}==0" in lua


def test_network_generation_trusts_entity_output_over_interface_prototype() -> None:
    """Live 2026-08-22: the save's electric-energy-interface reported prototype
    8_333_333_333 kW while the entity actually produced 166.7 kW -- the lie
    gated off every solar top-up and browned out the whole base. The query
    must read script-set output from the ENTITY for that type only."""
    client = _FakeRcon("166.7")
    assert live_base.network_generation_kw(client, "nauvis", "player", (3.0, -1.0)) == pytest.approx(166.7)
    lua = client.commands[0]
    assert "electric-energy-interface" in lua
    assert "power_production" in lua
    # Normal generators keep the proven nameplate path.
    assert "g.type=='electric-energy-interface'" in lua


def test_chained_clear_spots_probes_distinct_positions() -> None:
    """Live 2026-08-22: find_non_colliding_position returns the cursor tile
    itself whenever that tile is clear, so an eight-panel array collapsed onto
    ONE planned tile (mod report attempted=9 placed=2 already=7 ok=true) and
    the grid stayed starved at 167 kW. The generated Lua must skip spots it
    has already claimed before searching again."""
    client = _FakeRcon("")
    live_base.chained_clear_spots(
        client, "nauvis",
        [("medium-electric-pole", 1), ("solar-panel", 8)],
        (38.5, 47.5),
    )
    lua = client.commands[0]
    assert "used[spot_key(p.x,p.y)]" in lua
    assert "find_non_colliding_position" in lua


def test_unpowered_entity_scan_ignores_generation_sources() -> None:
    """Live runs 26-27 (2026-08-22): a night-dark solar panel reads no_power,
    so the stranded-support scan classified it as a wiring defect and every
    remediation round waited for a generator to 'charge' -- two STUCK endings
    on the same blockage. Sources must never enter this scan."""
    client = _FakeRcon("")
    live_base.unpowered_entities(client, "nauvis", ((30.0, 40.0), (50.0, 60.0)))
    lua = client.commands[0]
    assert "solar-panel" in lua
    assert "not gens[e.type]" in lua
    assert "electric-energy-interface" in lua


def test_roboport_energy_treats_a_bad_reply_as_no_reading() -> None:
    """Live run 29 (2026-08-22): one charge poll arrived truncated at the
    server and float() raised, killing the mission from an advisory poll. A
    bad sample must read as unavailable so the wait loop just repolls."""
    client = _FakeRcon("Cannot execute command. Error: [string \"...\"]:1: ')' expected near <eof>")
    assert live_base.roboport_energy(client, "nauvis", "player", (1.0, 1.0)) is None


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
    calls = {"n": 0}
    def _nearest(*a, **k):
        calls["n"] += 1
        return (0.0, 0.0) if calls["n"] == 1 else (30.5, -9.5)
    monkeypatch.setattr(live_base, "nearest_roboport", _nearest)
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


def test_roboport_chain_defers_after_a_live_wave_that_has_not_reached_target(monkeypatch) -> None:
    """A live one-hop wave waits for re-observation rather than terminally failing."""
    from orchestrator import stage_services
    from orchestrator.stage_services import extend_roboport_coverage
    far, near, target = (0.0, 0.0), (100.0, 100.0), (100.0, 100.0)
    remaining = [[far], [far], [far]]
    monkeypatch.setattr(
        stage_services.live_base, "roboports_needing_power", lambda *_a: [],
    )
    monkeypatch.setattr(
        stage_services.live_base, "nearest_roboport",
        lambda *_a: remaining.pop(0)[0],
    )
    monkeypatch.setattr(
        stage_services.live_base, "entity_status_name", lambda *_a: "active",
    )
    monkeypatch.setattr(
        stage_services, "clear_chain_positions", lambda *_a, **_k: [(50.0, 50.0)],
    )
    monkeypatch.setattr(stage_services, "_submit", lambda *_a, **_k: {})
    monkeypatch.setattr(
        stage_services, "_await_built_status", lambda *_a, **_k: "active",
    )
    monkeypatch.setattr(
        stage_services, "_await_roboport_charge", lambda *_a: None,
    )
    monkeypatch.setattr("time.sleep", lambda *_a: None)

    with pytest.raises(builder.ProductionPrerequisiteDeferred) as deferred:
        extend_roboport_coverage(
            object(), object(), "nauvis", "player", target,
            lambda _message: None, purpose="logistic",
        )
    assert deferred.value.code == "roboport_coverage_construction_wait"


def test_coverage_gap_verdict_files_a_typed_contract(monkeypatch) -> None:
    """Cycle 5 (+134s): the coverage-chain verdict fired as legacy
    `untyped_stuck` with empty details, so the next identical verdict could
    not discriminate a dropped final hop from a still-converging wave. The
    raise keeps its message but files code `coverage_gap` plus the geometry
    and wave facts the blocker record needs."""
    from orchestrator import stage_services
    from orchestrator.stage_services import StuckError, extend_roboport_coverage
    far, target = (0.0, 0.0), (100.0, 100.0)
    remaining = [[far], [far], [far]]
    monkeypatch.setattr(
        stage_services.live_base, "roboports_needing_power", lambda *_a: [],
    )
    monkeypatch.setattr(
        stage_services.live_base, "nearest_roboport",
        lambda *_a: remaining.pop(0)[0],
    )
    monkeypatch.setattr(
        stage_services.live_base, "entity_status_name", lambda *_a: "active",
    )
    monkeypatch.setattr(
        stage_services, "clear_chain_positions", lambda *_a, **_k: [(50.0, 50.0)],
    )
    monkeypatch.setattr(stage_services, "_submit", lambda *_a, **_k: {})
    monkeypatch.setattr(
        stage_services, "_await_built_status", lambda *_a, **_k: "active",
    )
    monkeypatch.setattr(
        stage_services, "_await_roboport_charge", lambda *_a: None,
    )
    monkeypatch.setattr("time.sleep", lambda *_a: None)

    with pytest.raises(StuckError, match="still outside logistic coverage") as error:
        extend_roboport_coverage(
            None, None, "nauvis", "player", target,
            lambda _message: None, purpose="logistic",
        )

    assert error.value.code == "coverage_gap"
    assert error.value.classification == "bug"
    assert error.value.state == "failed"
    details = error.value.details
    assert details["purpose"] == "logistic"
    assert details["target"] == [100.0, 100.0]
    assert details["nearest"] == [0.0, 0.0]
    assert details["gap"] == pytest.approx(100.0)
    assert details["radius"] == stage_services._ROBOPORT_LOGISTIC_RADIUS
    assert details["waves_placed"] == 1


def test_covered_target_skips_chaining_entirely(monkeypatch) -> None:
    """No gap, no work: covered chests cost one survey."""
    from orchestrator import stage_services
    from orchestrator.stage_services import extend_roboport_coverage
    monkeypatch.setattr(
        stage_services.live_base, "roboports_needing_power", lambda *_a: [],
    )
    monkeypatch.setattr(
        stage_services.live_base, "nearest_roboport",
        lambda *_a: (100.0, 100.0),
    )
    monkeypatch.setattr(
        stage_services.live_base, "entity_status_name", lambda *_a: "active",
    )
    monkeypatch.setattr(
        stage_services, "clear_chain_positions",
        lambda *_a, **_k: pytest.fail("covered target must not chain"),
    )

    assert extend_roboport_coverage(
        object(), object(), "nauvis", "player", (100.0, 100.0),
        lambda _message: None, purpose="logistic",
    ) is False


def test_pending_roboport_wave_is_credited_before_planning_a_near_duplicate(
    monkeypatch,
) -> None:
    """The (34,27) mall wave already covers the concurrent copper blueprint."""
    from orchestrator import stage_services

    class _LiveClient:
        def command(self, _text: str) -> str:
            return ""

    source = (3.0, -1.0)
    pending = (34.0, 27.0)
    target = (55.5, 24.5)
    monkeypatch.setattr(
        stage_services, "_repair_existing_roboport_power", lambda *_a: (),
    )
    monkeypatch.setattr(
        live_base, "nearest_roboport", lambda *_a: source,
    )
    monkeypatch.setattr(
        live_base, "roboport_ghost_positions", lambda *_a: [pending],
    )
    monkeypatch.setattr(
        stage_services, "clear_chain_positions",
        lambda *_a, **_k: pytest.fail("must wait for the pending wave"),
    )

    with pytest.raises(builder.ProductionPrerequisiteDeferred) as deferred:
        stage_services.extend_roboport_coverage(
            _LiveClient(), object(), "nauvis", "player", target,
            lambda _message: None,
        )

    assert deferred.value.details["wave"] == [[34.0, 27.0]]


def test_roboport_power_branch_is_submitted_before_the_port_ghost(monkeypatch) -> None:
    from orchestrator import stage_services

    class _LiveClient:
        def command(self, _text: str) -> str:
            return ""

    source, port, target = (3.0, -1.0), (34.0, 27.0), (39.5, 31.5)
    events: list[tuple[str, object]] = []
    nearest = iter((source, port))
    monkeypatch.setattr(
        stage_services, "_repair_existing_roboport_power", lambda *_a: (),
    )
    monkeypatch.setattr(
        live_base, "nearest_roboport", lambda *_a: next(nearest),
    )
    monkeypatch.setattr(live_base, "roboport_ghost_positions", lambda *_a: [])
    monkeypatch.setattr(live_base, "entity_status_name", lambda *_a: "working")
    monkeypatch.setattr(
        stage_services, "clear_chain_positions", lambda *_a, **_k: [port],
    )
    monkeypatch.setattr(
        stage_services, "extend_power",
        lambda *_a, **kwargs: events.append(("power", kwargs["reserved_tiles"])) or True,
    )
    monkeypatch.setattr(
        stage_services, "_submit",
        lambda *_a, **_k: events.append(("roboport", None)) or {},
    )
    monkeypatch.setattr(
        stage_services, "_await_built_status", lambda *_a, **_k: "working",
    )
    monkeypatch.setattr(stage_services, "_await_roboport_charge", lambda *_a: None)

    assert stage_services.extend_roboport_coverage(
        _LiveClient(), object(), "nauvis", "player", target,
        lambda _message: None, purpose="logistic", reserved_tiles={(1, 2)},
    )
    assert [event[0] for event in events] == ["power", "roboport"]
    power_reserved = events[0][1]
    assert (1, 2) in power_reserved
    assert footprint_tile_indices(port, 4).issubset(power_reserved)

def test_roboport_power_hookup_routes_around_reserved_corridor(monkeypatch) -> None:
    """2026-09-05: the bridged roboport dodged the pipe corridor but its
    power chain marched through it, and the crude pipeline died on the pole
    at (-297.5, -57.5). The hookup inherits the corridor reservation."""
    from orchestrator import stage_services

    monkeypatch.setattr(
        stage_services, "_repair_existing_roboport_power", lambda *_args: None,
    )
    calls = {"n": 0}
    def _nearest(*_args):
        calls["n"] += 1
        return (5.0, 5.0) if calls["n"] == 1 else (100.0, 5.0)
    monkeypatch.setattr(live_base, "nearest_roboport", _nearest)
    monkeypatch.setattr(
        live_base, "entity_status_name", lambda *_args: "low_power",
    )
    monkeypatch.setattr(
        stage_services, "_await_roboport_charge", lambda *_args: None,
    )
    monkeypatch.setattr(
        stage_services, "clear_chain_positions",
        lambda *_args, **_kwargs: [(45.0, 5.0)],
    )
    monkeypatch.setattr(
        stage_services, "_submit",
        lambda _c, _b, _s, _plan, name, _emit, **_kwargs: {"ok": True},
    )
    monkeypatch.setattr(
        stage_services, "_await_built_status",
        lambda *_args, **_kwargs: "low_power",
    )
    powered: list[dict] = []
    def _extend(_c, _b, _s, _f, position, _emit, **kwargs):
        powered.append({"position": position, **kwargs})
        return True
    monkeypatch.setattr(stage_services, "extend_power", _extend)

    with pytest.raises(builder.ProductionPrerequisiteDeferred) as deferred:
        stage_services.extend_roboport_coverage(
            object(), object(), "nauvis", "player", (100.0, 5.0),
            lambda _message: None, reserved_tiles={(1, 2), (3, 4)},
        )
    assert powered and powered[0]["position"] == (45.0, 5.0)
    assert powered[0]["reserved_tiles"] == {(1, 2), (3, 4)}
    assert deferred.value.code == "roboport_coverage_construction_wait"
