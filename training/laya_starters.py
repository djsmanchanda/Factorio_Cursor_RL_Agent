# Path: training/laya_starters.py
# Purpose: Direct starter options from the deterministic geometry: the first mine of each plate is drills feeding furnaces directly.

"""Direct plate starters (planners/bootstrap_smelting.generate_direct_smelter).

Iron: 2 drills into 2 furnaces.  Copper: 1 drill into 1 furnace.  Stone:
2 drills into 1 furnace (two stone per brick).  Drills output straight into
the furnace; one inserter publishes plates to a provider chest.  Only the
first build of each plate is a starter; later capacity comes from six-furnace
modules.  Sites are enumerated on the planned (non-small) patches; the policy
chooses, and power comes from training/laya_power.
"""

from __future__ import annotations

from collections.abc import Mapping

from orchestrator.laya_operations import sha256_hash
from planners.bootstrap_smelting import generate_direct_smelter
from planners.plan_validation import validate_build_plan

STARTER_ORE = {"iron-plate": "iron-ore", "copper-plate": "copper-ore", "stone-brick": "stone"}
DIRECTIONS = ("north", "east", "south", "west")
SAMPLE_HALF = 2.5  # survey samples are 4 tiles apart; a drill within this of a sample sits on ore


def starter_bill(plate: str) -> dict[str, int]:
    """Entities one direct starter of ``plate`` places (its power comes on top)."""
    actions = generate_direct_smelter(plate, STARTER_ORE[plate], (0.5, 0.5), "north")["phases"][0]["actions"]
    bill: dict[str, int] = {}
    for action in actions:
        bill[action["entity"]] = bill.get(action["entity"], 0) + 1
    return bill


def small_line_candidates(facts: Mapping, plan: Mapping | None, plate: str, ore: str,
                          furnaces: int, per_plate: int = 2, obstacles: list = ()) -> list[dict]:
    """A stone-brick line sized to the electric furnaces on hand.

    The approved six-furnace module needs six electric furnaces; when the kit has
    fewer left, this offers the same direct-starter geometry repeated on nearby
    ore samples, so the line is built smaller and runs partly starved instead of
    waiting for a full module. One drill each on ore, one furnace each: the plan
    is legal at every unit count, and the option says how many furnaces it takes.
    """
    if plan is None or furnaces < 2 or plate != "stone-brick" or ore != "stone":
        return []
    ports = [p for p in facts["ports"] if p["energy"] > 0]
    zones = [z["rect"] for z in plan["zones"] if z["purpose"] == f"ore:{ore}"]
    samples = [(s["position"]["x"], s["position"]["y"]) for s in facts["resources"] if s["item"] == ore
               and any(r[0] <= s["position"]["x"] <= r[2] and r[1] <= s["position"]["y"] <= r[3] for r in zones)]
    if not samples or not ports:
        return []
    from training.laya_lines import clear_of
    candidates: list[dict] = []
    for x, y in sorted(samples)[:6]:
        port = next((p for p in ports if max(abs(x - p["position"]["x"]), abs(y - p["position"]["y"]))
                     <= min(p["construction_radius"], p["logistic_radius"]) - 6), None)
        if port is None:
            continue
        anchor = min((p["position"] for p in ports if p["network"] == port["network"]), key=lambda q: (q["x"], q["y"]))
        unit_actions: list[dict] = []
        used_drills: set[tuple[float, float]] = set()
        units = 0
        for sx, sy in sorted(samples, key=lambda s: (abs(s[0] - x) + abs(s[1] - y), s)):
            if units >= furnaces:
                break
            placed = False
            for direction in DIRECTIONS:
                for side in (1, -1):
                    actions = generate_direct_smelter(plate, ore, (sx, sy), direction, pole_side=side)["phases"][0]["actions"]
                    drills = [(a["position"]["x"], a["position"]["y"]) for a in actions if a["entity"] == "electric-mining-drill"]
                    if any(d in used_drills for d in drills) or not all(_on_ore(d, samples) for d in drills):
                        continue
                    combined = unit_actions + actions
                    if not clear_of(combined, obstacles):
                        continue
                    plan_dict = {"surface": "nauvis", "force": "player", "atomic": True,
                                 "phases": [{"name": f"saved-world-small-line-{plate}", "actions": combined}]}
                    try:
                        validate_build_plan(plan_dict)
                    except ValueError:
                        continue
                    unit_actions = combined
                    used_drills.update(drills)
                    units += 1
                    placed = True
                    break
                if placed:
                    break
        if units < 2:
            continue
        digest = sha256_hash({"plan": {"phases": [{"name": "small-line", "actions": unit_actions}]},
                              "network_anchor": anchor, "furnaces": units})
        candidates.append({
            "option_id": f"starter-line.{digest[7:19]}", "family": "starter", "product": plate,
            "summary": f"Small {plate} line: {units} direct starters ({units} electric furnaces) near ({x},{y})",
            "plan": plan_dict, "network": port["network"], "network_anchor": anchor,
            "features": {"small_line": True, "furnaces": units},
        })
        if len(candidates) >= per_plate:
            break
    return candidates


def _on_ore(position: tuple[float, float], samples: list[tuple[float, float]]) -> bool:
    return any(max(abs(position[0] - x), abs(position[1] - y)) <= SAMPLE_HALF for x, y in samples)


def starter_candidates(facts: Mapping, plan: Mapping | None, plates: set[str], built: set[str],
                       per_plate: int = 80, obstacles: list = ()) -> list[dict]:
    """Starter builds for plates that still need one, sited on planned ore zones in robot reach."""
    if plan is None:
        return []
    ports = [p for p in facts["ports"] if p["energy"] > 0]
    candidates = []
    for plate, ore in STARTER_ORE.items():
        if plate not in plates or plate in built:
            continue
        zones = [z["rect"] for z in plan["zones"] if z["purpose"] == f"ore:{ore}"]
        samples = [(s["position"]["x"], s["position"]["y"]) for s in facts["resources"] if s["item"] == ore
                   and any(r[0] <= s["position"]["x"] <= r[2] and r[1] <= s["position"]["y"] <= r[3] for r in zones)]
        found = 0
        for x, y in samples:
            port = next((p for p in ports if max(abs(x - p["position"]["x"]), abs(y - p["position"]["y"]))
                         <= min(p["construction_radius"], p["logistic_radius"]) - 6), None)
            if port is None:
                continue
            anchor = min((p["position"] for p in ports if p["network"] == port["network"]), key=lambda q: (q["x"], q["y"]))
            for direction in DIRECTIONS:
                for side in (1, -1):
                    built_plan = generate_direct_smelter(plate, ore, (x, y), direction, pole_side=side)
                    actions = built_plan["phases"][0]["actions"]
                    drills = [(a["position"]["x"], a["position"]["y"]) for a in actions if a["entity"] == "electric-mining-drill"]
                    if not all(_on_ore(d, samples) for d in drills):
                        continue
                    from training.laya_lines import clear_of
                    if not clear_of(actions, obstacles):
                        continue
                    plan_dict = {"surface": "nauvis", "force": "player", "atomic": True,
                                 "phases": [{"name": f"saved-world-starter-{plate}", "actions": actions}]}
                    try:
                        validate_build_plan(plan_dict)
                    except ValueError:
                        continue
                    digest = sha256_hash({"plan": plan_dict, "network_anchor": anchor})
                    candidates.append({"option_id": f"starter.{digest[7:19]}", "family": "starter", "product": plate,
                                       "summary": f"Direct {plate} starter at ({x},{y}) facing {direction}",
                                       "plan": plan_dict, "network": port["network"], "network_anchor": anchor})
                    found += 1
            if found >= per_plate:
                break
    return candidates
