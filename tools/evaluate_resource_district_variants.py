# Path: tools/evaluate_resource_district_variants.py
# Purpose: Compare deterministic resource-district growth variants over fixed offline scenarios.

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from orchestrator.resource_district import (
    PhaseDecision,
    ResourceDistrictState,
    create_initial_envelope,
    plan_phase,
    validate_transport_continuity,
)
from planners.transport_occupancy import Occupant, OccupantIdentity, RouteOccupancy

VARIANTS = ("A", "B", "C")
_SCENARIOS = (
    "clear",
    "long_pipeline_crossing",
    "foreign_infrastructure",
    "obstacle_beyond_all_tiers",
    "obstacle_within_tier_reach",
    "blocked_endpoint",
    "ghosts_pending_partial_phase",
    "conflicting_future_reservation",
    "retry_recovery",
)


@dataclass(frozen=True)
class EvaluationResult:
    """One stable, offline-only row of the variant comparison matrix."""

    scenario: str
    variant: str
    outcome: str
    refusal_reason: str | None
    usable_drills: int
    district_count: int
    refinery_sites: int
    declared_connectivity: bool
    physical_continuity_verified: bool
    physical_continuity_reason: str
    geometry_source: str
    capacity_valid: bool
    collision_count: int
    raw_t_merges: tuple[tuple[int, int], ...]
    planned_actions: int
    submitted_actions: int
    throughput_headroom: float
    mine_outputs: int
    splitters: int
    additional_belt_entities: int
    occupied_area: int
    underground_pairs: int
    maximum_span_reach_ratio: float
    route_tiles: int
    route_excess: float
    reused_owned_tiles: int
    reused_foreign_tiles: int
    deterministic_key: str


@dataclass(frozen=True)
class EvaluationReport:
    """A byte-stable collection of comparison rows."""

    results: tuple[EvaluationResult, ...]

    def to_json(self) -> str:
        return json.dumps(
            {"results": [asdict(result) for result in self.results]},
            sort_keys=True,
            separators=(",", ":"),
        )


def _district(variant: str) -> ResourceDistrictState:
    # B deliberately exceeds one yellow lane at full growth, forcing separate
    # refinery interfaces. A and C remain on the merged manifold.
    capacity = 15.0 if variant == "B" else 45.0
    return create_initial_envelope(
        episode_id="episode-district-variants",
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
        belt_capacity_items_per_second=capacity,
        drill_items_per_second=0.5,
    )


def _identity(district_id: str, entity_id: str) -> OccupantIdentity:
    return OccupantIdentity(district_id=district_id, entity_id=entity_id)


def _wall(name: str, xs: range, ys: range) -> Occupant:
    return Occupant(
        category="live_entity",
        tiles=frozenset((x, y) for x in xs for y in ys),
        name=name,
    )


def _scenario_occupancy(
    scenario: str, state: ResourceDistrictState,
) -> tuple[RouteOccupancy, int | None]:
    primary = next(band for band in state.collector_bands if band.order == 0)
    if scenario == "long_pipeline_crossing":
        # Twelve tiles defeat a yellow tunnel while the explicit route bound keeps
        # this a local-capacity refusal rather than an unbounded global detour.
        return RouteOccupancy((_wall("pipe", range(64, 76), range(-40, 41)),)), 150
    if scenario == "obstacle_within_tier_reach":
        return RouteOccupancy((_wall("pipe", range(68, 69), range(-24, 25)),)), None
    if scenario == "obstacle_beyond_all_tiers":
        return RouteOccupancy((_wall("pipe", range(64, 76), range(-30, 31)),)), 240
    if scenario == "foreign_infrastructure":
        return RouteOccupancy((
            # Two exact, tier/direction-compatible belts owned by this district.
            # They exercise identity-scoped reuse while the unrelated foreign
            # belt remains an ordinary blocker.
            Occupant(
                category="live_entity",
                tiles=frozenset({(30, -15)}),
                name="transport-belt",
                direction="east",
                identity=_identity(state.district_id, "owned-trunk-a"),
            ),
            Occupant(
                category="live_entity",
                tiles=frozenset({(31, -15)}),
                name="transport-belt",
                direction="east",
                identity=_identity(state.district_id, "owned-trunk-b"),
            ),
            _wall("pipe", range(68, 69), range(-24, 25)),
            Occupant(
                category="live_entity",
                tiles=frozenset({(72, 0)}),
                name="transport-belt",
                direction="east",
                identity=_identity("foreign-district", "unrelated-belt"),
            ),
        )), None
    if scenario == "blocked_endpoint":
        return RouteOccupancy((
            Occupant(
                category="live_entity",
                tiles=frozenset({(78, -16)}),
                name="stone-furnace",
            ),
        )), None
    if scenario == "conflicting_future_reservation":
        parallel = next(band for band in state.collector_bands if band.order == 1)
        x = parallel.column_xs[len(primary.built_column_xs)]
        return RouteOccupancy((
            Occupant(
                category="district_reservation",
                tiles=frozenset({(int(x - 1.5), int(parallel.belt_y - 3.5))}),
                identity=_identity("future-power-district", "reserved-unit"),
            ),
        )), None
    return RouteOccupancy(()), None


def _actions(plan: dict | None) -> list[dict]:
    if plan is None:
        return []
    return [
        action
        for phase in plan.get("phases", ())
        for action in phase.get("actions", ())
    ]


def _belt_entity_count(plan: dict | None, *, projected_extra: int = 0) -> int:
    count = sum(
        isinstance(action.get("entity"), str) and "belt" in action["entity"]
        for action in _actions(plan)
    )
    return count + projected_extra


def _occupied_area(plan: dict | None, *, projected_extra: int = 0) -> int:
    from planners.plan_validation import entity_footprint_tiles

    tiles = {
        tile
        for action in _actions(plan)
        if action.get("action_type") == "place_ghost"
        for tile in entity_footprint_tiles(action)
    }
    return len(tiles) + projected_extra


def _route_metrics(
    state: ResourceDistrictState,
) -> tuple[int, int, float, int]:
    route_tiles = 0
    underground_pairs = 0
    maximum_ratio = 0.0
    reused_owned = 0
    for path in state.transport_paths:
        route_tiles += len(path.route_tiles)
        underground_pairs += len(path.underground_spans)
        for start, end in path.underground_spans:
            ratio = (
                abs(end[0] - start[0]) + abs(end[1] - start[1])
            ) / path.underground_reach
            maximum_ratio = max(maximum_ratio, ratio)
        reused_owned += len(path.reused_tiles)
    return route_tiles, underground_pairs, maximum_ratio, reused_owned


def _route_excess(state: ResourceDistrictState) -> int:
    """Report geometry beyond the endpoints' Manhattan interface distance."""
    return max(
        0,
        sum(max(0, len(path.route_tiles) - 1) for path in state.transport_paths)
        - sum(
            abs(path.destination[0] - path.source[0])
            + abs(path.destination[1] - path.source[1])
            for path in state.transport_paths
        ),
    )


def _projected_metrics(
    payload: dict[str, object], base_plan: dict | None,
) -> dict[str, object]:
    # Variant C is a comparison fixture, never submitted as proof. Projected
    # overhead is reported separately from the underlying production A plan.
    payload["geometry_source"] = "fixture_projection"
    payload["physical_continuity_reason"] = "variant_c_fixture_projection"
    payload["splitters"] = int(payload["splitters"]) + 1
    payload["additional_belt_entities"] = _belt_entity_count(
        base_plan, projected_extra=2,
    )
    payload["occupied_area"] = _occupied_area(base_plan, projected_extra=6)
    return payload


def _evaluate_one(
    scenario: str,
    variant: str,
) -> EvaluationResult:
    state = _district(variant)
    occupancy, route_limit = _scenario_occupancy(scenario, state)
    decision = plan_phase(
        state,
        target_drills=20,
        occupancy=occupancy,
        max_transport_route_tiles=route_limit,
    )
    if decision.outcome == "build":
        state = decision.next_state
        occupancy, route_limit = _scenario_occupancy(scenario, state)
        if scenario == "retry_recovery":
            state = ResourceDistrictState.from_json(state.to_json())
        decision = plan_phase(
            state,
            target_drills=50,
            occupancy=occupancy,
            max_transport_route_tiles=route_limit,
        )

    build = decision.outcome == "build"
    final_state = decision.next_state
    plan = decision.plan
    connected, connectivity_reason = (
        validate_transport_continuity(final_state)
        if build else (False, "phase refused")
    )
    route_tiles, underground_pairs, maximum_ratio, reused_owned = (
        _route_metrics(final_state) if build else (0, 0, 0.0, 0)
    )

    lanes = final_state.transport_lanes if build else ()
    capacity_valid = build and all(
        lane.declared_items_per_second <= lane.capacity_items_per_second + 1e-9
        for lane in lanes
    )
    throughput_headroom = (
        sum(
            lane.capacity_items_per_second - lane.declared_items_per_second
            for lane in lanes
        )
        if build else 0.0
    )
    mine_outputs = len({
        lane.refinery_input for lane in lanes if lane.refinery_input is not None
    })
    splitters = (
        sum(placement.entity == "splitter" for placement in final_state.owned_placements)
        if build else 0
    )
    # Exact persisted path identity is the only ownership-compatible reuse
    # counted here; nearby geometry and prototype families do not qualify.
    payload: dict[str, object] = {
        "scenario": scenario,
        "variant": variant,
        "outcome": decision.outcome,
        "refusal_reason": (
            None if decision.reason is None
            else f"{decision.reason.code}: {decision.reason.detail}"
        ),
        "usable_drills": decision.resulting_drill_count if build else 0,
        "district_count": 1 if build else 0,
        "refinery_sites": 1 if build else 0,
        "declared_connectivity": bool(connected),
        "physical_continuity_verified": False,
        "physical_continuity_reason": (
            "collector_to_manifold_geometry_not_emitted" if variant != "C" else ""
        ),
        "geometry_source": "production_phase_plan" if variant != "C" else "",
        "capacity_valid": bool(capacity_valid),
        "collision_count": 0 if build else 1,
        "raw_t_merges": decision.raw_t_merges,
        "planned_actions": len(_actions(plan)),
        "submitted_actions": len(_actions(plan)) if build else 0,
        "throughput_headroom": throughput_headroom,
        "mine_outputs": mine_outputs,
        "splitters": splitters,
        "additional_belt_entities": _belt_entity_count(plan),
        "occupied_area": _occupied_area(plan),
        "underground_pairs": underground_pairs,
        "maximum_span_reach_ratio": maximum_ratio,
        "route_tiles": route_tiles,
        "route_excess": 0.0,
        "reused_owned_tiles": reused_owned,
        "reused_foreign_tiles": 0,
        "deterministic_key": "",
    }
    if build and variant == "C":
        payload = _projected_metrics(payload, plan)
    elif variant == "C":
        payload["geometry_source"] = "fixture_projection"
        payload["physical_continuity_reason"] = "variant_c_fixture_projection"

    invalid = build and (
        connectivity_reason is not None or not capacity_valid
    )
    payload["collision_count"] = 1 if invalid else payload["collision_count"]
    payload["raw_t_merges"] = len(decision.raw_t_merges)
    payload["outcome"] = "refuse" if decision.outcome != "build" else "build"
    payload["route_excess"] = float(_route_excess(final_state) if build else 0)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["deterministic_key"] = hashlib.sha256(canonical.encode()).hexdigest()[:24]
    return EvaluationResult(**payload)


def evaluate_all() -> EvaluationReport:
    baselines_checked = 0
    for variant in VARIANTS:
        state = _district(variant)
        first = plan_phase(state, target_drills=20, occupancy=RouteOccupancy(()))
        if first.outcome != "build":
            raise RuntimeError(f"clear baseline failed for variant {variant}")
        second = plan_phase(
            first.next_state,
            target_drills=50,
            occupancy=RouteOccupancy(()),
        )
        if second.outcome != "build":
            raise RuntimeError(f"full baseline failed for variant {variant}")
        baselines_checked += 1

    assert baselines_checked == len(VARIANTS)
    return EvaluationReport(tuple(
        _evaluate_one(scenario, variant)
        for scenario in _SCENARIOS
        for variant in VARIANTS
    ))
