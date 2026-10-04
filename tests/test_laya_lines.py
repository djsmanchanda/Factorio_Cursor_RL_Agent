# Path: tests/test_laya_lines.py | Purpose: Protect Laya smelting lines: approved unit counts, continuous hauls into the line input, powered mines, sized taps.

import math
from collections import Counter

import pytest

from planners.plan_validation import validate_build_plan
from training.laya_lines import haul, line_candidates, line_units, mine_unit, output_taps
from training.laya_power import power_variants


def entities(actions):
    return Counter(a["entity"] for a in actions)


@pytest.mark.parametrize("rows, furnaces", [(1, 6), (3, 18)])
def test_a_line_is_one_column_of_start_middles_and_end(rows, furnaces):
    acts = line_units("iron-plate", rows, 10, 20)
    assert entities(acts)["electric-furnace"] == furnaces
    assert {"x": 9.5, "y": 21.5} in [a["position"] for a in acts if a["entity"] == "transport-belt"]  # ore input
    taps = output_taps(rows, 10, 20)
    assert entities(taps)["inserter"] == math.ceil(furnaces * 0.625 / 0.83)


@pytest.mark.parametrize("flow", ["east", "west"])
def test_haul_is_continuous_and_enters_the_line_input_heading_east(flow):
    mine, head = mine_unit(0, 0, 3, flow)
    drills = [a for a in mine if a["entity"] == "electric-mining-drill"]
    assert len(drills) == 6 and {a["direction"] for a in drills} == {"south", "north"}
    line_x = 30 if flow == "east" else -20
    route = haul(head, flow, line_x, 12)
    tiles = [(a["position"]["x"], a["position"]["y"]) for a in route]
    assert tiles[0] == head and route[-1]["position"] == {"x": line_x - 1.5, "y": 13.5} and route[-1]["direction"] == "east"
    assert all(abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1 for a, b in zip(tiles, tiles[1:]))
    assert haul(head, flow, -line_x, 12) is None  # The wrong side cannot be reached without a U-turn.


def test_long_mines_get_substations_along_the_row():
    mine, _ = mine_unit(0, 0, 9, "east")
    assert entities(mine)["electric-mining-drill"] == 18 and entities(mine)["substation"] == 2


def test_line_candidates_are_valid_powered_and_sit_on_planned_land():
    from training.laya_site_plan import reservation_reason
    ore = [{"item": "iron-ore", "position": {"x": 0.5 + 4 * i, "y": 0.5 + 4 * j}} for i in range(12) for j in range(4)]
    plan = {"zones": [{"zone_id": "o", "purpose": "ore:iron-ore", "rect": [-2, -2, 48, 16]},
                      {"zone_id": "s", "purpose": "smelt:iron-plate", "rect": [0, 21, 48, 55]}]}
    facts = {"resources": ore, "ports": [{"position": {"x": 24, "y": 20}, "energy": 1, "network": "1",
                                          "construction_radius": 55, "logistic_radius": 25}],
             "poles": [{"name": "substation", "generated": True, "position": {"x": 24, "y": 24}, "wire": 18,
                        "supply": 9, "grid": "1"}], "machines": []}
    diagnostics={}
    lines = line_candidates(facts, plan, {"iron-plate"},diagnostics=diagnostics)
    assert diagnostics['iron-plate']['candidates']==len(lines)
    remote={**facts,'ports':[{**facts['ports'][0],'position':{'x':500,'y':500}}]}
    rejected={};assert not line_candidates(remote,plan,{'iron-plate'},diagnostics=rejected)
    assert rejected['iron-plate']['outside_construction']>0
    assert lines and {c["furnaces"] for c in lines} <= {6, 18}
    for line in lines:
        validate_build_plan(line["plan"])
        assert reservation_reason(plan, line) is None
        assert power_variants(line, facts)


def test_line_sites_must_clear_entities_already_placed():
    from training.laya_lines import clear_of
    acts = line_units("iron-plate", 1, 10, 20)
    furnace = next(a for a in acts if a["entity"] == "electric-furnace")
    assert clear_of(acts, [])
    assert not clear_of(acts, [{"entity": "roboport", "position": dict(furnace["position"])}])
    assert clear_of(acts, [{"entity": "roboport", "position": {"x": 500, "y": 500}}])
