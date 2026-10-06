# Path: tests/test_mall_cell_reservation.py | Purpose: Protect the reservation of future mall cell sites from power and coverage routing.

import itertools

from orchestrator.mall_builder import _cell_footprint_tiles, _cell_origins, planned_cell_reserved_tiles


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
