# Path: planners/research_labs.py | Purpose: Reuse deterministic lab-row geometry for funded logistic supply.
# Ported verbatim from local laya/release 5a5ed28:planners/research_labs.py.
from __future__ import annotations
import math
from collections.abc import Mapping
from planners.local_layout_planner import LocalLayoutPlanner
from planners.plan_validation import validate_build_plan


def logistic_lab_row(count: int, packs: dict[str, float], *, origin=(0, 0), buffer_units=30) -> dict:
    if type(count) is not int or not 1 <= count <= 6:
        raise ValueError("lab packet requires 1..6 labs")
    if not isinstance(packs, Mapping) or not packs:
        raise ValueError("lab packet requires observed research packs")
    if any(not isinstance(name, str) or not name for name in packs):
        raise ValueError("invalid research pack names")
    if any(isinstance(v, bool) or not isinstance(v, (int, float))
           or not math.isfinite(v) or v <= 0 for v in packs.values()):
        raise ValueError("invalid research pack coefficients")
    if type(buffer_units) is not int or buffer_units < 1:
        raise ValueError("lab pack buffer must be a positive integer")
    row = LocalLayoutPlanner().generate_lab_row(count, *origin, inserter_type="inserter")
    # Sandbox power and belt injection are not part of funded lab construction.
    actions = [a for p in row["phases"] if p["name"] == "lab_row" for a in p["actions"]
               if a["entity"] != "transport-belt"]
    for a in list(actions):
        if a["entity"] == "inserter":
            actions.append({"action_type": "place_ghost", "entity": "requester-chest",
                            "position": {"x": a["position"]["x"], "y": a["position"]["y"] - 1},
                            "logistic_sections": [{"group": "research-lab-packs", "multiplier": 1,
                                "requests": [{"name": name, "count": math.ceil(amount * buffer_units)}
                                             for name, amount in sorted(packs.items())]}]})
    result = {"phases": [{"name": "funded_research_labs", "actions": actions}]}
    validate_build_plan(result)
    return result
