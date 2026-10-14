# Path: tests/test_mall_cell_reservation.py | Purpose: Protect the reservation of future mall cell sites from power and coverage routing.

import itertools

from orchestrator.mall_builder import (
    LINE_RECIPES, _cell_footprint_tiles, _cell_origins, compact_cell_reserved_tiles, planned_cell_reserved_tiles,
)
from planners.mall_layout import generate_paired_mall_layout


def test_the_next_cell_sites_are_reserved_but_their_gaps_stay_routable():
    origins = _cell_origins((0.0, 0.0))
    current = origins[4]
    reserved = planned_cell_reserved_tiles((0.0, 0.0), current)
    for origin in origins[5:13]:  # the next eight sites
        assert _cell_footprint_tiles(origin) <= reserved
    assert not _cell_footprint_tiles(current) & reserved  # the cell being built routes its own power
    assert not any(_cell_footprint_tiles(a) & _cell_footprint_tiles(b) for a, b in itertools.combinations(origins[:10], 2))
    substation = (current[0] + 4, current[1] + 5)
    free = [(x, y) for x in range(substation[0] - 9, substation[0] + 10) for y in range(substation[1] - 9, substation[1] + 10)
            if (x, y) not in reserved and (x, y) not in _cell_footprint_tiles(current)]
    assert len(free) > 50  # poles can still reach the cell through the gaps


def test_routing_one_half_keeps_the_other_half_of_its_cell_clear():
    origin = (54, 32)  # live 2026-10-15: a bridge pole took the right half's chest column
    spec = LINE_RECIPES["copper-cable"]
    left = generate_paired_mall_layout(
        "copper-cable", spec["machine"], spec["ingredients"], spec["amounts"], origin, "left",
        craft_time=spec["craft_time"], set_recipe=False, include_substation=False,
    )
    reserved = compact_cell_reserved_tiles(left, (0.0, 0.0), origin)
    assert {(58, 34), (59, 34)} <= reserved  # right-half provider and output inserter
    substation = (origin[0] + 4, origin[1] + 5)
    big_pole_spots = [(x, y) for x in range(substation[0] - 5, substation[0] + 5)
                      for y in range(substation[1] - 5, substation[1] + 5)
                      if not {(x, y), (x + 1, y), (x, y + 1), (x + 1, y + 1)} & reserved]
    assert big_pole_spots  # the cell's power can still land in the gap row
