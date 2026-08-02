# Path: tests/test_belt_continuity.py
# Purpose: Prove a belted route is never emitted severed -- every tunnel entrance has its exit, whatever the obstacles do.

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from planners.belt_bridge import (  # noqa: E402
    UNDERGROUND_REACH,
    _belt_run,
    _route_points,
    _tunnelled_points,
    bridge_chest_to_chest,
)

_WEST_RUN = [(0.5, 0.5), (-16.5, 0.5)]


def _tunnels(actions):
    ins = [a for a in actions if a.get("underground_type") == "input"]
    outs = [a for a in actions if a.get("underground_type") == "output"]
    return ins, outs


def _assert_continuous(actions) -> None:
    """Every entrance is answered by an exit, and they alternate."""
    ins, outs = _tunnels(actions)
    assert len(ins) == len(outs), "a tunnel entrance with no exit severs the belt"
    kinds = [a["underground_type"] for a in actions if a.get("underground_type")]
    assert kinds == ["input", "output"] * len(ins), "tunnel halves out of order"
    occupied = [(a["position"]["x"], a["position"]["y"]) for a in actions]
    assert len(occupied) == len(set(occupied)), "two entities on one tile"


def test_a_single_free_tile_between_obstacles_does_not_sever_the_belt() -> None:
    """The live fault: the second tunnel's entrance overwrote the first's exit,
    so items went underground and never came back up. Seen on the iron-plate
    haul from (12.5,-3.5), which arrived as three disconnected belts."""
    blocked = {(-2, 0), (-3, 0), (-5, 0), (-6, 0)}

    actions = _belt_run(_WEST_RUN, "fast-transport-belt", blocked)

    _assert_continuous(actions)
    ins, _ = _tunnels(actions)
    assert len(ins) == 1, "the trapped tile should merge both runs into one tunnel"


def test_a_trapped_tile_is_swallowed_rather_than_used_as_an_exit() -> None:
    points = _route_points(_WEST_RUN)
    tunnelled = _tunnelled_points(points, {(-2, 0), (-4, 0)})
    at = {int(point[0] - 0.5): flag for (point, _d, _l), flag in zip(points, tunnelled)}

    assert at[-2] and at[-4], "the blocked tiles themselves"
    assert at[-3], "the tile between them cannot be exit and entrance at once"
    assert not at[-1] and not at[-5], "tiles outside the run stay on the surface"


def test_a_chain_of_trapped_tiles_collapses_to_one_tunnel() -> None:
    blocked = {(-2, 0), (-4, 0), (-6, 0), (-8, 0)}

    actions = _belt_run(_WEST_RUN, "express-transport-belt", blocked)

    _assert_continuous(actions)
    ins, _ = _tunnels(actions)
    assert len(ins) == 1


def test_two_free_tiles_still_get_two_separate_tunnels() -> None:
    """Merging must not be over-eager: two free tiles are enough for an exit
    and a following entrance, and a shorter pair is cheaper."""
    blocked = {(-2, 0), (-3, 0), (-6, 0), (-7, 0)}

    actions = _belt_run(_WEST_RUN, "transport-belt", blocked)

    _assert_continuous(actions)
    ins, _ = _tunnels(actions)
    assert len(ins) == 2


def test_a_merged_span_past_the_tier_reach_is_reported_not_severed() -> None:
    """Failing loudly is what lets the caller detour or buy a longer tier."""
    blocked = {(-2, 0), (-3, 0), (-4, 0), (-6, 0), (-7, 0), (-8, 0)}

    with pytest.raises(ValueError, match="beyond .* reach"):
        _belt_run(_WEST_RUN, "transport-belt", blocked)


def test_a_longer_tier_spans_what_the_cheap_one_could_not() -> None:
    blocked = {(-2, 0), (-3, 0), (-5, 0), (-6, 0)}

    with pytest.raises(ValueError):
        _belt_run(_WEST_RUN, "transport-belt", blocked)
    _assert_continuous(_belt_run(_WEST_RUN, "fast-transport-belt", blocked))


@pytest.mark.parametrize("belt_type", sorted(UNDERGROUND_REACH))
def test_no_obstacle_pattern_ever_yields_a_severed_belt(belt_type: str) -> None:
    """Exhaustive over every obstacle layout on a 12-tile run: the emitted belt
    is either continuous or refused, never silently broken."""
    checked = 0
    for mask in range(1 << 10):
        blocked = {(-1 - bit, 0) for bit in range(10) if mask >> bit & 1}
        try:
            actions = _belt_run([(0.5, 0.5), (-12.5, 0.5)], belt_type, blocked)
        except ValueError:
            continue
        _assert_continuous(actions)
        checked += 1
    assert checked > 100, f"only {checked} layouts were routable -- too weak a check"


def test_a_real_bridge_is_continuous_through_scattered_obstacles() -> None:
    blocked = {(-5, 0), (-6, 0), (-8, 0), (-9, 0)}

    plan = bridge_chest_to_chest(
        (0.5, 0.5), (-16.5, 0.5), exit_direction="west", entry_direction="east",
        belt_type="fast-transport-belt", inserter_type="inserter",
        blocked_tiles=blocked,
    )

    _assert_continuous([a for a in plan if "underground" in a.get("entity", "")
                        or a.get("entity", "").endswith("transport-belt")])
