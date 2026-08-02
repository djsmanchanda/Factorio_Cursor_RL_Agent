# Path: tests/test_smelter_siting.py
# Purpose: Prove a smelter is sited beside the ore it consumes rather than beside the base, and that the ranking agrees with what a belt actually costs.

from __future__ import annotations

import inspect
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator.stage_extraction import (  # noqa: E402
    ore_reservation,
    plan_local_extraction,
    smelter_search_anchors,
)
from planners.zoning_geometry import Rect  # noqa: E402

# The observed iron shape: a long east-west patch whose ore leaves at the west
# end, with the base reference just beyond it.
_PATCH_MIN, _PATCH_MAX = (10.0, -10.0), (90.0, 10.0)
_FOOTPRINT = (13.0, 9.0)
_ORE_OUTPUT = (12.5, -3.5)
_REFERENCE = (3.0, -1.0)


def _belt_tiles(anchor, to) -> float:
    centre = (anchor[0] + _FOOTPRINT[0] / 2, anchor[1] + _FOOTPRINT[1] / 2)
    return abs(centre[0] - to[0]) + abs(centre[1] - to[1])


def _anchors(ore_output=_ORE_OUTPUT):
    return smelter_search_anchors(
        _PATCH_MIN, _PATCH_MAX, _FOOTPRINT, _REFERENCE, ore_output,
    )


def test_the_search_starts_where_the_ore_leaves() -> None:
    """The four cardinal anchors are edges of the whole PATCH, so on a long one
    none of them is near the belt the smelter has to meet."""
    costs = [_belt_tiles(anchor, _ORE_OUTPUT) for anchor in _anchors()]

    assert costs[0] == min(costs)
    assert costs[0] < _belt_tiles(_anchors(ore_output=None)[0], _ORE_OUTPUT)


def test_anchors_are_ranked_by_what_a_belt_actually_costs() -> None:
    """Manhattan, not straight-line: ranking by Euclidean distance disagreed
    with the quantity being minimised and put a 16.5-tile site behind an
    18.0-tile one."""
    costs = [_belt_tiles(anchor, _ORE_OUTPUT) for anchor in _anchors()]

    assert costs == sorted(costs)


def test_no_anchor_sits_on_the_ore_reservation() -> None:
    """The added anchors must obey the same rule as the cardinal ones."""
    reserved = Rect(*ore_reservation(_PATCH_MIN, _PATCH_MAX)[0],
                    *ore_reservation(_PATCH_MIN, _PATCH_MAX)[1])

    for anchor in _anchors():
        footprint = Rect(
            anchor[0], anchor[1],
            anchor[0] + _FOOTPRINT[0], anchor[1] + _FOOTPRINT[1],
        )
        assert not footprint.overlaps(reserved), anchor


def test_the_ore_side_is_chosen_from_where_the_output_actually_is() -> None:
    """An output at the east end must not pull the search to the west edge."""
    east_output = (88.0, 8.0)
    nearest = smelter_search_anchors(
        _PATCH_MIN, _PATCH_MAX, _FOOTPRINT, _REFERENCE, east_output,
    )[0]

    assert _belt_tiles(nearest, east_output) < _belt_tiles(nearest, _ORE_OUTPUT)


def test_the_ranking_is_deterministic() -> None:
    assert _anchors() == _anchors()


def test_without_an_ore_output_the_old_ordering_still_applies() -> None:
    """Callers that do not know where the ore leaves must keep working."""
    by_base = _anchors(ore_output=None)
    costs = [_belt_tiles(anchor, _REFERENCE) for anchor in by_base]

    assert costs == sorted(costs)


def test_the_ore_haul_outranks_the_plate_haul_in_scoring() -> None:
    """Summing the two let a site far from the mine win because it happened to
    sit near the base. The ore belt is re-laid every time the drill row grows
    6 -> 20 -> 50 -> 100; the plate belt out is built once."""
    source = inspect.getsource(plan_local_extraction)
    block = source[source.index("candidates.append(("):]
    block = block[:block.index("))")]
    ranked = [line.strip() for line in block.splitlines() if line.strip().endswith(",")]

    assert ranked.index("input_tiles,") < ranked.index("input_tiles + output_tiles,")


def test_the_scoring_unpacks_in_the_order_it_ranks() -> None:
    """A reordered tuple that is unpacked the old way silently mislabels the
    chosen direction and origin."""
    source = inspect.getsource(plan_local_extraction)

    assert "_input_wrong_way, _output_wrong_way, _input, _total," in source
