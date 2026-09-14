# Path: orchestrator/extraction_route.py | Purpose: Bound live mine-link siting searches without claiming construction ownership.
from __future__ import annotations

import math

from orchestrator import live_base
from orchestrator.stage_extraction import (
    LocalExtractionRouteAssessment, route_assessment_from_actions,
)
from planners.belt_bridge import plan_belt_route, RouteFailure


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
