# Path: tests/test_fluid_underground.py | Purpose: Check intended pipe-to-ground partners without claiming runtime pairing.
import pytest

from planners.fluid_underground import validate_planned_underground_pairs
from planners.fluid_routing import fluid_chain_link_segments, generate_fluid_chain_link


def _plan(*endpoints):
    return {"phases": [{"name": "pipes", "actions": [
        {"action_type": "place_ghost", "entity": "pipe-to-ground",
         "position": {"x": x + .5, "y": y + .5}, "direction": direction}
        for x, y, direction in endpoints
    ]}]}


def _segments(*pairs):
    return [{"fluid": "water", "tiles": [point for pair in pairs for point in pair],
             "tunnel_endpoints": list(pairs)}]


def test_intended_partners_can_coexist_with_more_distant_endpoints():
    plan = _plan((0, 0, "west"), (2, 0, "east"), (4, 0, "west"), (6, 0, "east"))
    validate_planned_underground_pairs(plan, _segments(((0, 0), (2, 0)), ((4, 0), (6, 0))))


def test_closer_opposite_endpoint_rejects_intended_pair():
    plan = _plan((0, 0, "west"), (8, 0, "east"), (-1, 0, "west"), (3, 0, "east"))
    with pytest.raises(ValueError, match="ambiguous underground"):
        validate_planned_underground_pairs(plan, _segments(((0, 0), (8, 0)), ((-1, 0), (3, 0))))


@pytest.mark.parametrize("plan, segments", [
    (_plan((0, 0, "west"), (3, 0, "east")), _segments()),
    (_plan((0, 0, "west")), _segments(((0, 0), (3, 0)))),
    (_plan((0, 0, "east"), (3, 0, "west")), _segments(((0, 0), (3, 0)))),
    (_plan((0, 0, "west"), (3, 0, "east")),
     _segments(((0, 0), (3, 0)), ((0, 0), (3, 0)))),
    (_plan((0, 0, "west"), (11, 0, "east")), _segments(((0, 0), (11, 0)))),
])
def test_missing_invalid_or_overlong_pairs_fail_closed(plan, segments):
    with pytest.raises(ValueError):
        validate_planned_underground_pairs(plan, segments)


def test_trunk_route_reports_its_tunnel_pairs():
    foreign = [{"fluid": "crude-oil", "tiles": [(5, 0)], "separated_by_pump": False}]
    args = ((10, 0), [(10, 20)], "water", 0)
    plan = generate_fluid_chain_link(*args, foreign=foreign)
    segments = fluid_chain_link_segments(*args, foreign=foreign)
    assert segments[0]["tunnel_endpoints"] == [((3, 0), (7, 0))]
    validate_planned_underground_pairs(plan, segments)
