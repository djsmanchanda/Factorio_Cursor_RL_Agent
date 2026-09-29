# Path: training/candidates/connected_workflow.py
# Purpose: Realize one validated plan per connected-workflow branch; ordering between branches is Laya's choice.

from __future__ import annotations

from planners.plan_validation import validate_build_plan
from training.canonical import plan_hash
from training.contracts import validate_scenario

# Layout offsets from the ore-source chest (x+3, y). Integer positions snap to
# tile centres, as in the furnace family. Inserters pick up on the side they face.
#   ore -> furnace -> plate chest P -> gear assembler (north) -> gear chest Q -> belt assembler (south) -> sink
_CELLS_L1 = {
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

# Level 2 (demand-scaling) only: a second furnace, fed by its own ore-source-2
# fixture, that drops plate directly onto a free edge of the gear assembler's
# footprint (4,-3), lifting the plate/gear/belt ceiling so a high randomized
# target is actually reachable. It has no chest of its own -- the gear
# assembler already has spare craft-rate headroom over what two furnaces can
# supply, so the shortest legal path is straight into it, one tile at a time
# like every other inserter here. Measured live on 2.1.20: one furnace alone
# plateaus around 0.6/0.2/0.4 plate/gear/belt per second; adding this cell
# roughly doubles plate and gear and lifts belts comfortably above that.
_CELLS_L2_EXTRA = {
    "iron2": [
        ("fast-inserter", (4, -9), "north", None),   # ore-source-2 -> furnace2
        ("electric-furnace", (4, -7), None, None),
        ("fast-inserter", (4, -5), "north", None),   # furnace2 -> gear assembler (4,-4)
        ("small-electric-pole", (3, -9), None, None),
        ("small-electric-pole", (3, -5), None, None),
    ],
}

# Level 3 (second product) only: a copper -> cable -> circuits chain built in
# space free of every other branch (verified offline, see
# tests/test_connected_workflow_level3.py), plus a dedicated furnace1 tap so
# the circuit assembler gets iron-plate without competing geometrically with
# gears/belts for P's own inserters.
#   ore-source-copper -> copper furnace -> copper-plate chest U
#   U -> cable assembler (copper-cable) -> cable chest S
#   furnace1 (south tap) -> iron-plate chest P2 --\
#   S -----------------------------------------------> circuit assembler -> circuit-sink
_CELLS_L3_EXTRA = {
    "copper": [
        ("fast-inserter", (-14, 6), "west", None),   # ore-source-copper -> furnace
        ("electric-furnace", (-12, 6), None, None),
        ("fast-inserter", (-10, 6), "west", None),   # furnace -> copper-plate chest U
        ("iron-chest", (-9, 6), None, None),
        ("small-electric-pole", (-14, 5), None, None),
        ("small-electric-pole", (-10, 5), None, None),
        # Copper has no prerequisite branch, so it can be the very first thing
        # built (measured live: it was). Its own furnace/inserters are too far
        # from the power source for its two local poles alone, so these two
        # relay poles bridge back on their own -- copper never depends on
        # iron/cable/circuits existing first to stay powered.
        ("small-electric-pole", (-4, 1), None, None),
        ("small-electric-pole", (-10, 4), None, None),
    ],
    "cable": [
        ("fast-inserter", (-8, 6), "west", None),    # U -> cable assembler
        ("assembling-machine-2", (-6, 6), None, "copper-cable"),
        ("fast-inserter", (-4, 6), "west", None),    # cable assembler -> cable chest S
        ("iron-chest", (-3, 6), None, None),
        ("small-electric-pole", (-6, 4), None, None),
    ],
    "circuits": [
        ("fast-inserter", (0, 2), "north", None),    # furnace1 -> iron-plate chest P2
        ("iron-chest", (0, 3), None, None),
        ("fast-inserter", (-1, 3), "east", None),    # P2 -> circuit assembler (east side)
        ("assembling-machine-2", (-3, 3), None, "electronic-circuit"),
        ("fast-inserter", (-3, 5), "south", None),   # cable chest S -> circuit assembler (south side)
        ("fast-inserter", (-5, 3), "east", None),    # circuit assembler -> circuit-sink (west side)
        ("small-electric-pole", (-3, 1), None, None),
        ("small-electric-pole", (1, 2), None, None),
        ("small-electric-pole", (-4, 5), None, None),
    ],
}

BRANCH_IDS = tuple(_CELLS_L1)


def _cells_for(scenario: dict) -> dict:
    """The cell set for this scenario's curriculum level; level 1 is untouched."""
    level = int((scenario.get("curriculum") or {}).get("level", 1))
    if level >= 3:
        return {**_CELLS_L1, **_CELLS_L2_EXTRA, **_CELLS_L3_EXTRA}
    return {**_CELLS_L1, **_CELLS_L2_EXTRA} if level >= 2 else _CELLS_L1

# Variants keep the verified geometry and swap machine/inserter tiers: a real
# cost-versus-throughput choice. Rates are items per second per entity.
MACHINES = {"am2": ("assembling-machine-2", 0.75), "am1": ("assembling-machine-1", 0.5)}
INSERTERS = {"fast": ("fast-inserter", 2.31), "basic": ("inserter", 0.83)}
# Rough raw-iron cost per entity, for the model's cost features only.
_ITEM_COST = {
    "assembling-machine-2": 42, "assembling-machine-1": 22, "fast-inserter": 6.5, "inserter": 4,
    "electric-furnace": 70, "iron-chest": 8, "small-electric-pole": 1, "medium-electric-pole": 12,
}


def branch_variants(branch_id: str) -> tuple[tuple[str, str | None, str | None], ...]:
    """(variant_id, machine key, inserter key) choices for one branch."""
    if branch_id == "construction":
        return (("construction.line", None, None),)
    machines = ("am2", "am1") if branch_id in {"gears", "belts", "cable", "circuits"} else (None,)
    return tuple(
        (f"{branch_id}.{'-'.join(part for part in (machine, inserter) if part)}", machine, inserter)
        for machine in machines for inserter in ("fast", "basic")
    )


def branch_specs(scenario: dict) -> tuple[dict, ...]:
    """Measured completion targets for the work graph; no ordering or ranking."""
    belt_target = float(scenario["objective"]["target_rate_per_tick"])
    level = int((scenario.get("curriculum") or {}).get("level", 1))
    specs = [
        {"branch_id": "iron", "parent_goal_id": "goal.belts", "item": "iron-plate",
         "metric": "production_rate", "target_minimum": 0.4 / 60.0, "required_sustained_ticks": 600},
        {"branch_id": "gears", "parent_goal_id": "goal.belts", "item": "iron-gear-wheel",
         "metric": "production_rate", "target_minimum": 0.05 / 60.0, "required_sustained_ticks": 600,
         "requires": ["iron"]},
        {"branch_id": "belts", "parent_goal_id": "goal.belts", "item": "transport-belt",
         "metric": "production_rate", "target_minimum": belt_target, "required_sustained_ticks": 600,
         "requires": ["gears"]},
        {"branch_id": "construction", "parent_goal_id": "goal.remote-power", "item": "medium-electric-pole",
         "metric": "built_count", "target_minimum": len(_CELLS_L1["construction"])},
    ]
    if level == 2:
        # Offered only once "iron" is closed (furnace 1 measured producing).
        # Gated on a *count* of electric-furnace, not a rate, so it closes the
        # moment the second furnace exists rather than re-measuring plate rate
        # under a threshold that would already be true from furnace 1 alone.
        # (Level 2 has no other furnace-building branch, so a count of 2
        # unambiguously means iron2's own furnace exists.)
        specs.append({
            "branch_id": "iron2", "parent_goal_id": "goal.iron-scale", "item": "electric-furnace",
            "metric": "built_count", "target_minimum": 2, "requires": ["iron"],
        })
    elif level >= 3:
        # Level 3 also has a "copper" branch that builds its own
        # electric-furnace, so a raw count of 2 is satisfied by iron+copper
        # alone -- measured live: iron2 showed "closed" the instant copper was
        # built, was never actually offered again, and iron-plate stayed
        # pinned at one furnace's ~0.6/s for the rest of the episode. Counting
        # to 3 (iron + copper + iron2) requires iron2's own furnace to exist
        # too; copper is a hard physical prerequisite for a completing
        # episode anyway (circuits needs it), so this never blocks closure.
        specs.append({
            "branch_id": "iron2", "parent_goal_id": "goal.iron-scale", "item": "electric-furnace",
            "metric": "built_count", "target_minimum": 3, "requires": ["iron"],
        })
    if level >= 3:
        requirements = scenario["objective"]["requirements"]
        circuit_target = float(next(r for r in requirements if r["id"] == "circuits")["target_rate_per_tick"])
        specs.extend([
            {"branch_id": "copper", "parent_goal_id": "goal.circuits", "item": "copper-plate",
             "metric": "production_rate", "target_minimum": 0.4 / 60.0, "required_sustained_ticks": 600},
            {"branch_id": "cable", "parent_goal_id": "goal.circuits", "item": "copper-cable",
             "metric": "production_rate", "target_minimum": 0.05 / 60.0, "required_sustained_ticks": 600,
             "requires": ["copper"]},
            {"branch_id": "circuits", "parent_goal_id": "goal.circuits", "item": "electronic-circuit",
             "metric": "production_rate", "target_minimum": circuit_target, "required_sustained_ticks": 600,
             "requires": ["iron", "cable"]},
        ])
    return tuple(specs)


def branch_plan(scenario: dict, branch_id: str, *, machine: str | None = "am2", inserter: str | None = "fast") -> dict:
    cells = _cells_for(scenario)
    if branch_id not in cells:
        raise ValueError(f"unknown connected-workflow branch: {branch_id}")
    swap = {}
    if machine:
        swap["assembling-machine-2"] = MACHINES[machine][0]
    if inserter:
        swap["fast-inserter"] = INSERTERS[inserter][0]
    source = next(f for f in scenario["fixtures"] if f["kind"] == "item_source")["position"]
    origin_x, origin_y = source[0] + 3, source[1]
    actions = []
    for entity, (dx, dy), direction, recipe in cells[branch_id]:
        entity = swap.get(entity, entity)
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
    """Every variant of every branch; the runner offers only runnable, unbuilt branches."""
    if validate_contract:
        validate_scenario(scenario)
    candidates = []
    for branch_id in _cells_for(scenario):
        for variant_id, machine, inserter in branch_variants(branch_id):
            plan = branch_plan(scenario, branch_id, machine=machine, inserter=inserter)
            digest = plan_hash(plan)
            counts: dict[str, int] = {}
            for action in plan["phases"][0]["actions"]:
                counts[action["entity"]] = counts.get(action["entity"], 0) + 1
            features = {
                "entity_count": float(sum(counts.values())),
                "pole_count": float(sum(v for k, v in counts.items() if k.endswith("electric-pole"))),
                "material_cost": float(sum(_ITEM_COST.get(k, 0) * v for k, v in counts.items())),
            }
            if machine:
                features["machine_speed"] = MACHINES[machine][1]
            if inserter:
                features["inserter_items_per_second"] = INSERTERS[inserter][1]
            candidates.append({
                "action_id": variant_id,
                "branch_id": branch_id,
                "variant_id": variant_id,
                "plan_hash": digest,
                "features": features,
                "plan": plan,
            })
    return candidates


__all__ = ["BRANCH_IDS", "INSERTERS", "MACHINES", "branch_plan", "branch_specs", "branch_variants",
           "connected_workflow_candidates"]
