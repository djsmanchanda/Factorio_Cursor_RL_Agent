# Path: tests/test_mining_geometry.py | Purpose: Preserve pre-extraction drill coordinates and their use in Laya mines.

import pytest

from planners.mining_geometry import mining_drill_positions, paired_mining_drill_positions
from training.laya_lines import mine_unit

# Captured from stage_extraction at eeb3900 before moving either function.
# Keep literal coordinates so arithmetic, row order, and rounding cannot drift together.
_BASELINE_CASES = [
    ((0, 0), -2, []),
    ((0, 0), 0, []),
    ((10, 20), 1, [(11.5, 18.5), (11.5, 22.5)]),
    ((0, 0), 3, [
        (1.5, -1.5), (4.5, -1.5), (7.5, -1.5),
        (1.5, 2.5), (4.5, 2.5), (7.5, 2.5),
    ]),
    ((-13, -7), 6, [
        (-11.5, -8.5), (-8.5, -8.5), (-5.5, -8.5),
        (-2.5, -8.5), (0.5, -8.5), (3.5, -8.5),
        (-11.5, -4.5), (-8.5, -4.5), (-5.5, -4.5),
        (-2.5, -4.5), (0.5, -4.5), (3.5, -4.5),
    ]),
    ((0.25, -0.75), 3, [
        (1.75, -2.25), (4.75, -2.25), (7.75, -2.25),
        (1.75, 1.75), (4.75, 1.75), (7.75, 1.75),
    ]),
    ((1e16, 1e16), 2, [
        (1.0000000000000002e16, 9999999999999998.0),
        (1.0000000000000004e16, 9999999999999998.0),
        (1.0000000000000002e16, 1.0000000000000002e16),
        (1.0000000000000004e16, 1.0000000000000002e16),
    ]),
]


@pytest.mark.parametrize("origin,count,expected", _BASELINE_CASES)
def test_drill_positions_match_pre_extraction_geometry(origin, count, expected):
    assert paired_mining_drill_positions(origin, count) == expected
    assert mining_drill_positions(origin, count) == expected[:max(0, count)]


@pytest.mark.parametrize("flow", ["east", "west"])
@pytest.mark.parametrize("origin,count,expected", _BASELINE_CASES[2:6])
def test_laya_mine_keeps_pre_extraction_drill_geometry(origin, count, expected, flow):
    actions, _head = mine_unit(*origin, count, flow)
    drills = [action for action in actions if action["entity"] == "electric-mining-drill"]
    assert [(a["position"]["x"], a["position"]["y"]) for a in drills] == expected
    assert [a["direction"] for a in drills] == ["south"] * count + ["north"] * count
