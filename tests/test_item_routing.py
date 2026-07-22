# Path: tests/test_item_routing.py
# Purpose: Verify declared item endpoints compose deterministic collision-free belt routes.

from __future__ import annotations

import pytest

from planners.item_routing import ItemEndpoint, ItemRoute, route_declared_items
from planners.plan_validation import actions


def _endpoints() -> list[ItemEndpoint]:
    return [
        ItemEndpoint("cable_out", "copper-cable", "producer", (10, 5), "east"),
        ItemEndpoint("cable_aux", "copper-cable", "consumer", (20, 15), "east"),
    ]


def test_declared_z_route_is_deterministic_and_never_deletes_collectors() -> None:
    route = ItemRoute(
        "cable_to_aux", "copper-cable", "cable_out", "cable_aux",
        waypoints=((16, 5), (16, 15)),
    )
    first = route_declared_items(_endpoints(), [route])
    second = route_declared_items(_endpoints(), [route])

    assert first == second
    routed = list(actions(first[0][1]))
    assert routed[0]["position"] == {"x": 10.5, "y": 5.5}
    assert routed[-1]["position"] == {"x": 20.5, "y": 15.5}
    assert all(action["action_type"] == "place_ghost" for action in routed)
    assert all(action["entity"] == "express-transport-belt" for action in routed)


def test_item_router_rejects_undeclared_items_and_collisions() -> None:
    mismatch = ItemRoute("bad", "iron-plate", "cable_out", "cable_aux")
    with pytest.raises(ValueError, match="item does not match"):
        route_declared_items(_endpoints(), [mismatch])

    route = ItemRoute("blocked", "copper-cable", "cable_out", "cable_aux",
                      waypoints=((10, 15),))
    with pytest.raises(ValueError, match="collides"):
        route_declared_items(_endpoints(), [route], occupied_tiles={(10, 10)})


def test_contiguous_tunnel_run_emits_exactly_one_underground_pair() -> None:
    crossings = tuple((x, 5) for x in range(11, 19))
    route = ItemRoute(
        "long_crossing", "copper-cable", "cable_out", "cable_aux",
        waypoints=((20, 5),), tunnel_crossings=crossings,
    )
    endpoints = [
        ItemEndpoint("cable_out", "copper-cable", "producer", (10, 5), "east"),
        ItemEndpoint("cable_aux", "copper-cable", "consumer", (20, 5), "east"),
    ]

    routed = list(actions(route_declared_items(endpoints, [route])[0][1]))

    underground = [action for action in routed if action["entity"] == "express-underground-belt"]
    assert [action["position"] for action in underground] == [
        {"x": 10.5, "y": 5.5}, {"x": 19.5, "y": 5.5},
    ]
    assert [action["underground_type"] for action in underground] == ["input", "output"]
    assert not {tuple((x + 0.5, 5.5)) for x in range(11, 19)} & {
        (action["position"]["x"], action["position"]["y"]) for action in routed
    }


def test_tunnel_span_is_validated_against_selected_belt_tier() -> None:
    crossings = tuple((x, 5) for x in range(11, 19))
    route = ItemRoute(
        "too_long_for_yellow", "copper-cable", "cable_out", "cable_aux",
        waypoints=((20, 5),), tunnel_crossings=crossings,
    )
    endpoints = [
        ItemEndpoint("cable_out", "copper-cable", "producer", (10, 5), "east"),
        ItemEndpoint("cable_aux", "copper-cable", "consumer", (20, 5), "east"),
    ]

    with pytest.raises(ValueError, match="transport-belt maximum 5"):
        route_declared_items(endpoints, [route], belt_type="transport-belt")


def test_separate_tunnel_runs_cannot_share_an_endpoint() -> None:
    route = ItemRoute(
        "shared_endpoint", "copper-cable", "cable_out", "cable_aux",
        waypoints=((20, 5),), tunnel_crossings=((11, 5), (13, 5)),
    )
    endpoints = [
        ItemEndpoint("cable_out", "copper-cable", "producer", (10, 5), "east"),
        ItemEndpoint("cable_aux", "copper-cable", "consumer", (20, 5), "east"),
    ]

    with pytest.raises(ValueError, match="cannot share an endpoint"):
        route_declared_items(endpoints, [route])