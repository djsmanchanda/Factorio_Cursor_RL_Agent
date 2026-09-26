# Path: training/candidates/connected_workflow.py
# Purpose: Realize one validated plan per connected-workflow branch; ordering between branches is Laya's choice.

from __future__ import annotations

from planners.plan_validation import validate_build_plan
from training.canonical import plan_hash
from training.contracts import validate_scenario

# Layout offsets from the ore-source chest (x+3, y). Integer positions snap to
# tile centres, as in the furnace family. Inserters pick up on the side they face.
#   ore -> furnace -> plate chest P -> gear assembler (north) -> gear chest Q -> belt assembler (south) -> sink
_CELLS = {
    "iron": [
        ("fast-inserter", (-2, 0), "west", None),
        ("electric-furnace", (0, 0), None, None),
        ("fast-inserter", (2, 0), "west", None),
        ("iron-chest", (3, 0), None, None),
        ("small-electric-pole", (-2, -1), None, None),
        ("small-electric-pole", (2, -1), None, None),
    ],
    "gears": [
        ("fast-inserter", (3, -1), "south", None),
        ("assembling-machine-2", (4, -3), None, "iron-gear-wheel"),
        ("fast-inserter", (5, -1), "north", None),
        ("iron-chest", (5, 0), None, None),
        # Each branch owns distinct poles, so no two plans list the same entity.
        ("small-electric-pole", (4, -1), None, None),
    ],
    "belts": [
        ("fast-inserter", (3, 1), "north", None),
        ("assembling-machine-2", (4, 3), None, "transport-belt"),
        ("fast-inserter", (5, 1), "north", None),
        ("fast-inserter", (6, 3), "west", None),
        ("small-electric-pole", (4, 1), None, None),
    ],
    # Independent of the production chain: carry power toward a remote site.
    "construction": [
        ("medium-electric-pole", (0, -5), None, None),
        ("medium-electric-pole", (0, -12), None, None),
        ("medium-electric-pole", (0, -19), None, None),
        ("medium-electric-pole", (0, -26), None, None),
    ],
}

BRANCH_IDS = tuple(_CELLS)


def branch_specs(scenario: dict) -> tuple[dict, ...]:
    """Measured completion targets for the work graph; no ordering or ranking."""
    belt_target = float(scenario["objective"]["target_rate_per_tick"])
    return (
        {"branch_id": "iron", "parent_goal_id": "goal.belts", "item": "iron-plate",
         "metric": "production_rate", "target_minimum": 0.4 / 60.0, "required_sustained_ticks": 600},
        {"branch_id": "gears", "parent_goal_id": "goal.belts", "item": "iron-gear-wheel",
         "metric": "production_rate", "target_minimum": 0.05 / 60.0, "required_sustained_ticks": 600,
         "requires": ["iron"]},
        {"branch_id": "belts", "parent_goal_id": "goal.belts", "item": "transport-belt",
         "metric": "production_rate", "target_minimum": belt_target, "required_sustained_ticks": 600,
         "requires": ["gears"]},
        {"branch_id": "construction", "parent_goal_id": "goal.remote-power", "item": "medium-electric-pole",
         "metric": "built_count", "target_minimum": len(_CELLS["construction"])},
    )


def branch_plan(scenario: dict, branch_id: str) -> dict:
    if branch_id not in _CELLS:
        raise ValueError(f"unknown connected-workflow branch: {branch_id}")
    source = next(f for f in scenario["fixtures"] if f["kind"] == "item_source")["position"]
    origin_x, origin_y = source[0] + 3, source[1]
    actions = []
    for entity, (dx, dy), direction, recipe in _CELLS[branch_id]:
        action = {"action_type": "place_entity", "entity": entity,
                  "position": {"x": float(origin_x + dx), "y": float(origin_y + dy)}}
        if direction:
            action["direction"] = direction
        if recipe:
            action["recipe"] = recipe
        actions.append(action)
    plan = {
        "surface": scenario["environment"]["surface_name"],
        "force": scenario["environment"]["force_name"],
        "phases": [{"name": f"connected_workflow.{branch_id}", "actions": actions}],
    }
    validate_build_plan(plan)
    return plan


def connected_workflow_candidates(scenario: dict, *, validate_contract: bool = True) -> list[dict]:
    """One candidate per branch, keyed by branch; the runner offers only runnable ones."""
    if validate_contract:
        validate_scenario(scenario)
    candidates = []
    for branch_id in BRANCH_IDS:
        plan = branch_plan(scenario, branch_id)
        digest = plan_hash(plan)
        counts: dict[str, int] = {}
        for action in plan["phases"][0]["actions"]:
            counts[action["entity"]] = counts.get(action["entity"], 0) + 1
        candidates.append({
            "action_id": f"branch-{branch_id}-{digest[7:19]}",
            "branch_id": branch_id,
            "plan_hash": digest,
            "features": {"entity_count": float(sum(counts.values())),
                         "pole_count": float(sum(v for k, v in counts.items() if k.endswith("electric-pole")))},
            "plan": plan,
        })
    return candidates


__all__ = ["BRANCH_IDS", "branch_plan", "branch_specs", "connected_workflow_candidates"]
