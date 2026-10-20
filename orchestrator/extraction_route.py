# Path: orchestrator/extraction_route.py | Purpose: Bound live mine-link siting searches without claiming construction ownership.
from __future__ import annotations

import math

from orchestrator import live_base
from orchestrator.stage_extraction import (
    LocalExtractionRouteAssessment, LocalExtractionRouteBudget,
    route_assessment_from_actions, route_budget_failure,
)
from planners.belt_bridge import plan_belt_route, RouteFailure
from planners.transport_occupancy import Occupant, OccupantIdentity


def live_route_preflight(client, surface: str, force: str, *, belt_type: str):
    """Create one-pass candidate route checks; final combined plan still validates.

    New managed collectors run east. For an existing terminal we start on its
    downstream tile, preserving the observed belt. We claim no live occupancy;
    an obstructed endpoint must therefore fail rather than be silently erased.
    """
    cache = {}

    def check(source, destination, budget):
        key = (source, destination, budget)
        if key in cache:
            return cache[key]
        direction = live_base.transport_belt_direction_at(client, surface, source)
        if direction not in (None, "east"):
            result = LocalExtractionRouteAssessment(False, reason="mine interface is not eastbound")
            cache[key] = result
            return result
        start = (source[0] + 1, source[1])
        # The planner may spend its full route budget detouring. Observe the
        # entire reachable rectangle, not just an unsurveyed thin corridor.
        direct = abs(start[0] - destination[0]) + abs(start[1] - destination[1]) + 1
        if direct > budget.max_route_tiles:
            return LocalExtractionRouteAssessment(False, reason="mine link exceeds route tile budget")
        margin = math.ceil((budget.max_route_tiles - direct) / 2) + 2
        low = (min(start[0], destination[0]) - margin, min(start[1], destination[1]) - margin)
        high = (max(start[0], destination[0]) + margin, max(start[1], destination[1]) + margin)
        snapshot = live_base.transport_occupancy_snapshot(client, surface, low, high)
        reach = live_base.belt_underground_reach(client, belt_type)
        route = plan_belt_route(
            start, destination, belt_type=belt_type, occupancy=snapshot.occupancy,
            district_id=f"siting:{surface}:{force}", source_heading="east", destination_heading="east",
            max_route_tiles=budget.max_route_tiles, max_actions=budget.max_actions,
            max_search_nodes=budget.max_search_nodes, underground_reach=reach,
        )
        if isinstance(route, RouteFailure):
            result = LocalExtractionRouteAssessment(False, reason=f"{route.reason}: {route.detail}")
        else:
            result = route_assessment_from_actions(route.actions, route_tiles=len(route.route_tiles))
        cache[key] = result
        return result

    return check


# A second collector feeds a nearby owned tail; a link longer than this is a
# different district, not a module of the owned one.
SECOND_COLLECTOR_LINK_BUDGET = LocalExtractionRouteBudget(
    max_route_tiles=200, max_actions=400, max_material_items=400,
    max_search_nodes=120_000,
)


def live_collector_link(
    client, surface: str, force: str, *, belt_type: str,
    budget: LocalExtractionRouteBudget = SECOND_COLLECTOR_LINK_BUDGET,
):
    """Plan an eastbound link from a new collector head to an owned tail.

    Live occupancy supplies built and ghosted entities; ``avoid`` supplies the
    new row's own footprint and corridor plus recorded reservations, which are
    not entities yet. Returns route actions or a rejection reason.
    """
    del force

    def link(source, destination, avoid):
        start = (source[0] + 1, source[1])
        direct = abs(start[0] - destination[0]) + abs(start[1] - destination[1]) + 1
        if direct > budget.max_route_tiles:
            return "link exceeds the second-collector route budget"
        margin = math.ceil((budget.max_route_tiles - direct) / 2) + 2
        low = (min(start[0], destination[0]) - margin, min(start[1], destination[1]) - margin)
        high = (max(start[0], destination[0]) + margin, max(start[1], destination[1]) + margin)
        claimed = frozenset(
            tile for tile in avoid
            if low[0] - 1 <= tile[0] <= high[0] + 1 and low[1] - 1 <= tile[1] <= high[1] + 1
        )
        snapshot = live_base.transport_occupancy_snapshot(
            client, surface, low, high,
            # The survey requires planner ownership on every claim; a district id distinct from the
            # route's own keeps these reserved tiles blocked (C10: an identity-less claim raised
            # TelemetryError on every second-collector attempt).
            pending_claims=(Occupant("pending_plan", claimed, identity=OccupantIdentity(
                district_id=f"second-collector-reserved:{surface}", entity_id="corridor-and-reservations",
            )),) if claimed else (),
        )
        route = plan_belt_route(
            start, destination, belt_type=belt_type, occupancy=snapshot.occupancy,
            district_id=f"second-collector:{surface}", source_heading="east",
            destination_heading="east", max_route_tiles=budget.max_route_tiles,
            max_actions=budget.max_actions, max_search_nodes=budget.max_search_nodes,
            underground_reach=live_base.belt_underground_reach(client, belt_type),
        )
        if isinstance(route, RouteFailure):
            return f"{route.reason}: {route.detail}"
        failure = route_budget_failure(
            route_assessment_from_actions(route.actions, route_tiles=len(route.route_tiles)),
            budget,
        )
        return failure if failure is not None else tuple(route.actions)

    return link
