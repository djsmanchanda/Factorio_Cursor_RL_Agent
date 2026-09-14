# Path: tests/test_extraction_route.py | Purpose: Exercise real bounded route search against surveyed occupancy.
from types import SimpleNamespace

from orchestrator import extraction_route as routes
from orchestrator.stage_extraction import LocalExtractionRouteBudget
from planners.transport_occupancy import Occupant, RouteOccupancy


def setup(monkeypatch, occupancy=RouteOccupancy()):
    monkeypatch.setattr(routes.live_base, "transport_belt_direction_at", lambda *_: "east")
    monkeypatch.setattr(routes.live_base, "transport_occupancy_snapshot",
                        lambda *_: SimpleNamespace(occupancy=occupancy))
    monkeypatch.setattr(routes.live_base, "belt_underground_reach", lambda *_: 5)
    return routes.live_route_preflight(object(), "nauvis", "player", belt_type="transport-belt")


def test_real_route_search_accepts_long_clear_link(monkeypatch):
    check = setup(monkeypatch)
    result = check((0.5, 0.5), (401.5, 0.5), LocalExtractionRouteBudget())
    assert result.legal and result.route_tiles == 401
    assert result.material_bill == {"transport-belt": 401}


def test_unowned_destination_is_not_erased_for_route(monkeypatch):
    occupied = RouteOccupancy((Occupant("live_entity", frozenset({(15, 0)}), name="steel-chest"),))
    result = setup(monkeypatch, occupied)((0.5, 0.5), (15.5, 0.5), LocalExtractionRouteBudget())
    assert not result.legal


def test_node_budget_is_enforced_during_real_search(monkeypatch):
    result = setup(monkeypatch)((0.5, 0.5), (15.5, 0.5), LocalExtractionRouteBudget(max_search_nodes=1))
    assert not result.legal and "search_limit" in result.reason


def test_existing_reverse_flow_is_not_reoriented(monkeypatch):
    check = setup(monkeypatch)
    monkeypatch.setattr(routes.live_base, "transport_belt_direction_at", lambda *_: "west")
    assert not check((0.5, 0.5), (15.5, 0.5), LocalExtractionRouteBudget()).legal


def test_existing_eastbound_haul_head_is_preserved_and_extended(monkeypatch):
    head = Occupant('live_entity', frozenset({(0, 0)}), name='transport-belt', direction='east')
    result = setup(monkeypatch, RouteOccupancy((head,)))((0.5, 0.5), (15.5, 0.5), LocalExtractionRouteBudget())
    assert result.legal
    assert result.route_tiles == 15  # starts downstream, never rewrites the head


def test_maximum_detour_rectangle_is_surveyed_and_route_stays_inside(monkeypatch):
    check = setup(monkeypatch)
    bounds = []
    planned = []
    occupancy = RouteOccupancy((Occupant('terrain', frozenset({(6, y) for y in range(-4, 5)})),))
    def survey(_client, _surface, low, high):
        bounds.append((low, high))
        return SimpleNamespace(occupancy=occupancy)
    monkeypatch.setattr(routes.live_base, 'transport_occupancy_snapshot', survey)
    original = routes.plan_belt_route
    def plan(*args, **kwargs):
        result = original(*args, **kwargs)
        planned.append(result)
        return result
    monkeypatch.setattr(routes, 'plan_belt_route', plan)
    result = check((0.5, 0.5), (15.5, 0.5), LocalExtractionRouteBudget(max_route_tiles=40))
    assert result.legal
    low, high = bounds[0]
    assert low[1] < -4 and high[1] > 4
    assert all(low[0] <= x <= high[0] and low[1] <= y <= high[1]
               for x, y in planned[0].route_tiles)
