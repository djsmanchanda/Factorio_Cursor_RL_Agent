# Path: tests/test_three_input_layout.py
# Purpose: Verify advanced-circuit auxiliary-belt geometry and product-aware capacity math.

from __future__ import annotations

import math

from planners.local_layout_planner import LocalLayoutPlanner
from planners.plan_validation import actions
from planners.recipe_data import FEED_HEADROOM, feeder_rate


def test_advanced_circuit_row_has_two_main_inputs_and_auxiliary_cable_belt() -> None:
    planner = LocalLayoutPlanner()
    kwargs = {
        "recipe": "advanced-circuit",
        "machine_count": 2,
        "belt_type": "express-transport-belt",
        "inserter_type": "stack-inserter",
        "feed_style": "chained",
        "chained_ingredients": {0, 1, 2},
        "terminal_collector": False,
    }
    plan = planner.generate_line_layout(**kwargs)

    assert plan == planner.generate_line_layout(**kwargs)
    placed = list(actions(plan))
    assert not any(action["entity"].startswith("infinity-") for action in placed)
    assert sum(action["entity"] == "long-handed-inserter" for action in placed) == 2
    auxiliary = [
        action for action in placed
        if action["entity"] == "express-transport-belt" and action["position"]["y"] == 7.5
    ]
    assert len(auxiliary) == 9
    main = [
        action for action in placed
        if action["entity"] == "express-transport-belt" and action["position"]["y"] == 0.5
    ]
    assert len(main) == 9
    assert sum(
        action["entity"] == "stack-inserter" and action["position"]["y"] == 5.5
        for action in placed
    ) == 2


def test_product_amount_scales_output_collector_capacity() -> None:
    plan = LocalLayoutPlanner().generate_line_layout(
        "copper-cable", 6, inserter_type="stack-inserter"
    )
    steel_chests = [action for action in actions(plan) if action["entity"] == "steel-chest"]
    # 6 machines x 1.5 crafts/s x 2 cable per craft = 18/s to clear.
    assert len(steel_chests) == math.ceil(18 * FEED_HEADROOM / feeder_rate("stack-inserter"))
