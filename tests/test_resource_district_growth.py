# Path: tests/test_resource_district_growth.py
# Purpose: Specify cohesive phased mine growth, capacity-aware manifolds, and structured defers.

from __future__ import annotations

import json
import math

from orchestrator.resource_district import (
    ResourceDistrictState,
    create_initial_envelope,
    plan_phase,
    validate_transport_continuity,
)
from planners.transport_occupancy import Occupant, OccupantIdentity, RouteOccupancy
from planners.infrastructure_geometry import footprint_tile_indices
from planners.plan_validation import ENTITY_FOOTPRINTS


def _district(*, belt_capacity: float = 45.0) -> ResourceDistrictState:
    return create_initial_envelope(
        episode_id="episode-growth",
        surface="nauvis",
        force="player",
        ore="iron-ore",
        recipe="iron-plate",
        root=(4.5, 0.5),
        output=(0.5, 0.5),
        expansion_direction="east",
        initial_drills=6,
        longitudinal_drill_limit=20,
        maximum_drills=50,
        parallel_band_pitch=8.0,
        refinery_origin=(80.0, -16.0),
        refinery_variant="standard",
        refinery_generation=1,
        belt_tier="transport-belt",
        belt_capacity_items_per_second=belt_capacity,
        drill_items_per_second=0.5,
    )


def _actions(plan: dict | None) -> list[dict]:
    if plan is None:
        return []
    return [
        action
        for phase in plan.get("phases", ())
        for action in phase.get("actions", ())
    ]


def _drills(plan: dict | None) -> list[dict]:
    return [action for action in _actions(plan) if action.get("entity") == "electric-mining-drill"]


def _primary(state: ResourceDistrictState):
    return next(band for band in state.collector_bands if band.order == 0)


def _through_twenty(state: ResourceDistrictState):
    decision = plan_phase(state, target_drills=20, occupancy=RouteOccupancy(()))
    assert decision.outcome == "build"
    return decision.next_state


def _owned_occupancy(state: ResourceDistrictState) -> RouteOccupancy:
    return RouteOccupancy(tuple(
        Occupant(
            category="entity_ghost",
            tiles=frozenset(footprint_tile_indices(
                placement.position,
                ENTITY_FOOTPRINTS.get(placement.entity, 1),
            )),
            name=placement.entity,
            direction=placement.direction,
            underground_type=placement.underground_type,
            identity=OccupantIdentity(
                district_id=state.district_id,
                entity_id=placement.action_id,
            ),
        )
        for placement in state.owned_placements
    ))


def test_six_to_twenty_adds_exactly_fourteen_drills_to_the_fixed_output_row() -> None:
    initial = _district()

    decision = plan_phase(initial, target_drills=20, occupancy=RouteOccupancy(()))
    drills = _drills(decision.plan)

    assert decision.outcome == "build"
    assert decision.resulting_drill_count == 20
    assert len(drills) == 14
    assert decision.plan["atomic"] is True
    assert decision.next_state.district_id == initial.district_id
    assert decision.next_state.output == initial.output
    assert decision.next_state.refinery.site_id == initial.refinery.site_id
    assert decision.next_state.refinery.origin == initial.refinery.origin

    primary = _primary(initial)
    expected = {
        (x, primary.belt_y + dy)
        for x in primary.column_xs[3:]
        for dy in (-2.0, 2.0)
    }
    actual = {
        (action["position"]["x"], action["position"]["y"])
        for action in drills
    }
    assert actual == expected


def test_fifty_drills_use_deterministic_fixed_pitch_parallel_bands() -> None:
    twenty = _through_twenty(_district())

    decision = plan_phase(
        twenty, target_drills=50, occupancy=_owned_occupancy(twenty),
    )
    active = [band for band in decision.next_state.collector_bands if band.planned_column_xs]
    primary = _primary(decision.next_state)

    assert decision.outcome == "build"
    assert decision.resulting_drill_count == 50
    assert len(_drills(decision.plan)) == 30
    assert {band.order for band in active} >= {-1, 0, 1}
    assert all(
        math.isclose(
            band.belt_y,
            primary.belt_y + band.order * decision.next_state.parallel_band_pitch,
        )
        for band in active
    )
    assert decision.next_state.refinery.site_id == twenty.refinery.site_id
    assert decision.next_state.refinery.origin == twenty.refinery.origin


def test_capacity_selects_merge_a_only_when_the_trunk_fits() -> None:
    wide = _through_twenty(_district(belt_capacity=45.0))
    narrow = _through_twenty(_district(belt_capacity=15.0))

    merged = plan_phase(wide, target_drills=50, occupancy=_owned_occupancy(wide))
    separate = plan_phase(
        narrow, target_drills=50, occupancy=_owned_occupancy(narrow),
    )

    assert merged.variant == "A"
    assert len(merged.next_state.transport_lanes) == 1
    assert merged.next_state.transport_lanes[0].declared_items_per_second <= 45.0
    assert separate.variant == "B"
    assert len(separate.next_state.transport_lanes) > 1
    assert all(
        lane.declared_items_per_second <= lane.capacity_items_per_second
        for lane in separate.next_state.transport_lanes
    )


def test_every_multi_band_merge_has_an_explicit_splitter_and_no_raw_t() -> None:
    twenty = _through_twenty(_district(belt_capacity=45.0))
    decision = plan_phase(
        twenty, target_drills=50, occupancy=_owned_occupancy(twenty),
    )
    splitters = {
        placement.action_id
        for placement in decision.next_state.owned_placements
        if placement.entity == "splitter"
    }

    merged_lanes = [
        lane for lane in decision.next_state.transport_lanes
        if len(lane.source_band_orders) > 1
    ]
    assert merged_lanes
    for lane in merged_lanes:
        assert len(lane.splitter_action_ids) == len(lane.source_band_orders) - 1
        assert set(lane.splitter_action_ids) <= splitters
    assert decision.raw_t_merges == ()


def test_blocked_longitudinal_corridor_defers_with_zero_actions() -> None:
    state = _district()
    primary = _primary(state)
    future_x = primary.column_xs[len(primary.built_column_xs)]
    blocked_tile = (math.floor(future_x - 1.5), math.floor(primary.belt_y - 3.5))
    occupancy = RouteOccupancy((
        Occupant(
            category="live_entity",
            tiles=frozenset({blocked_tile}),
            name="pipeline",
        ),
    ))

    decision = plan_phase(state, target_drills=20, occupancy=occupancy)

    assert decision.outcome == "defer"
    assert decision.reason.code == "longitudinal_corridor_blocked"
    assert decision.reason.tile == blocked_tile
    assert _actions(decision.plan) == []
    assert decision.next_state == state


def test_foreign_future_reservation_defers_before_parallel_growth() -> None:
    state = _through_twenty(_district())
    parallel = next(band for band in state.collector_bands if band.order == 1)
    reserved_tile = (
        math.floor(parallel.column_xs[0] - 1.5),
        math.floor(parallel.belt_y - 3.5),
    )
    occupancy = RouteOccupancy((
        Occupant(
            category="district_reservation",
            tiles=frozenset({reserved_tile}),
            identity=OccupantIdentity(
                district_id="foreign-district",
                entity_id="future-power-unit",
            ),
        ),
    ))

    decision = plan_phase(state, target_drills=50, occupancy=occupancy)

    assert decision.outcome == "defer"
    assert decision.reason.code == "district_reservation_conflict"
    assert decision.reason.tile == reserved_tile
    assert _actions(decision.plan) == []
    assert decision.next_state == state


def test_identical_phase_inputs_produce_identical_output() -> None:
    state = _through_twenty(_district())
    occupancy = _owned_occupancy(state)

    first = plan_phase(state, target_drills=50, occupancy=occupancy)
    second = plan_phase(state, target_drills=50, occupancy=occupancy)

    assert first == second
    assert json.dumps(first.plan, sort_keys=True) == json.dumps(second.plan, sort_keys=True)


def test_emitted_transport_geometry_traces_every_active_band_to_the_refinery() -> None:
    twenty = _through_twenty(_district(belt_capacity=45.0))
    decision = plan_phase(
        twenty, target_drills=50, occupancy=_owned_occupancy(twenty),
    )

    valid, reason = validate_transport_continuity(decision.next_state)
    route_by_id = {
        route.path_id: route for route in decision.next_state.transport_paths
    }

    assert decision.outcome == "build"
    assert valid, reason
    assert route_by_id
    assert all(lane.refinery_input for lane in decision.next_state.transport_lanes)
    assert all(lane.path_ids for lane in decision.next_state.transport_lanes)
    assert all(
        path_id in route_by_id
        for lane in decision.next_state.transport_lanes
        for path_id in lane.path_ids
    )
    assert all(
        abs(end[0] - start[0]) + abs(end[1] - start[1])
        <= route.underground_reach
        for route in route_by_id.values()
        for start, end in route.underground_spans
    )


def test_transport_obstacle_that_cannot_be_routed_defers_the_whole_phase() -> None:
    state = _district(belt_capacity=45.0)
    wall = tuple(
        Occupant(
            category="live_entity",
            tiles=frozenset({(x, y) for y in range(-80, 81)}),
            name="pipe",
        )
        for x in range(40, 48)
    )

    decision = plan_phase(
        state,
        target_drills=20,
        occupancy=RouteOccupancy(wall),
        max_transport_route_tiles=140,
    )

    assert decision.outcome == "defer"
    assert decision.reason is not None
    assert decision.reason.code == "transport_route_unavailable"
    assert _actions(decision.plan) == []
    assert decision.next_state == state


def test_fresh_transport_conflict_invalidates_every_action_before_submission() -> None:
    state = _district(belt_capacity=45.0)
    baseline = plan_phase(
        state, target_drills=20, occupancy=RouteOccupancy(()),
    )
    selected = next(
        action for action in _actions(baseline.plan)
        if ":haul:action:" in action.get("action_id", "")
        and action.get("entity") == "transport-belt"
    )
    selected_tile = (
        math.floor(selected["position"]["x"]),
        math.floor(selected["position"]["y"]),
    )
    fresh = RouteOccupancy((
        Occupant(
            category="pending_plan",
            tiles=frozenset({selected_tile}),
            name="pipe",
            identity=OccupantIdentity("foreign-district", "late-pipe"),
        ),
    ))

    decision = plan_phase(
        state,
        target_drills=20,
        occupancy=RouteOccupancy(()),
        fresh_occupancy=fresh,
    )

    assert decision.outcome == "defer"
    assert decision.reason is not None
    assert decision.reason.code == "fresh_transport_conflict"
    assert _actions(decision.plan) == []


def test_parallel_phase_reuses_exact_shared_service_lane_from_prior_phases() -> None:
    state = _district()
    twenty = plan_phase(state, target_drills=20, occupancy=RouteOccupancy(()))
    occupants = tuple(
        Occupant(
            category="entity_ghost",
            tiles=frozenset(footprint_tile_indices(
                placement.position,
                ENTITY_FOOTPRINTS.get(placement.entity, 1),
            )),
            name=placement.entity,
            direction=placement.direction,
            identity=OccupantIdentity(
                district_id=twenty.next_state.district_id,
                entity_id=placement.action_id,
            ),
        )
        for placement in twenty.next_state.owned_placements
    )

    fifty = plan_phase(
        twenty.next_state,
        target_drills=50,
        occupancy=RouteOccupancy(occupants),
    )

    assert fifty.outcome == "build"
    action_ids = [action["action_id"] for action in _actions(fifty.plan)]
    assert len(action_ids) == len(set(action_ids))
    assert not set(action_ids) & {
        placement.action_id for placement in twenty.next_state.owned_placements
    }
