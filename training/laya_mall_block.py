# Path: training/laya_mall_block.py
# Purpose: Mall cells anywhere in Laya's one large mall block (quad cells on a grid), growing from the edge nearest a roboport.

"""Mall block cells.

The shared generator only offers mall cells at fixed quad sites around each
roboport.  The mall block (training/laya_site_plan, one large zone beside the
starter area) instead gets quad cells on a grid across the whole block, built
with the same geometry (planners/laya_quad_cell.generate_quad_slot).  Cells
need logistic coverage for their requester and provider, so the block fills
from the covered edge outward as coverage grows; family ``assemble`` keeps mall
slots, gates and retargets working unchanged.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

from orchestrator.laya_operations import sha256_hash
from planners.laya_quad_cell import generate_quad_slot
from planners.plan_validation import validate_build_plan
from training.laya_lines import clear_of

MODULE_PITCH = 12  # quad centres on a 12-tile grid inside the block
EDGE = 6  # a quad module reaches about 5 tiles from its centre
FURNACE_PRODUCTS = {"iron-plate", "copper-plate", "stone-brick", "steel-plate"}
SIDES = ("west", "east", "north", "south")


def block_centres(zone: list[float]) -> list[tuple[float, float]]:
    x0, y0, x1, y1 = zone
    return [(x + .5, y + .5) for x in range(math.ceil(x0 + EDGE), math.floor(x1 - EDGE) + 1, MODULE_PITCH)
            for y in range(math.ceil(y0 + EDGE), math.floor(y1 - EDGE) + 1, MODULE_PITCH)]


def mall_cell_candidates(facts: Mapping, plan: Mapping | None, products: set[str], *, per_product: int = 6,
                         obstacles: list = ()) -> list[dict]:
    """Quad cells for ``products`` in the mall block, nearest a powered roboport first."""
    if not plan:
        return []
    zones = [z["rect"] for z in plan["zones"] if z["purpose"] == "mall"]
    ports = [p for p in facts["ports"] if p["energy"] > 0]
    recipes = facts.get("recipes") or {}
    if not zones or not ports:
        return []
    centres = sorted((c for zone in zones for c in block_centres(zone)),
                     key=lambda c: min(math.dist(c, (p["position"]["x"], p["position"]["y"])) for p in ports))
    taken = {(m["position"]["x"], m["position"]["y"]) for m in facts.get("machines", [])}
    candidates = []
    for product in sorted(p for p in products if p in recipes and p not in FURNACE_PRODUCTS):
        found = 0
        for centre in centres:
            for side in SIDES:
                placements = generate_quad_slot(product, recipes[product], centre, side)
                machine = next(a["position"] for a in placements if a["entity"].startswith("assembling-machine"))
                if (machine["x"], machine["y"]) in taken:
                    continue
                port = next((p for p in ports if all(
                    max(abs(a["position"]["x"] - p["position"]["x"]), abs(a["position"]["y"] - p["position"]["y"]))
                    <= p["logistic_radius"] - 1 for a in placements)), None)
                if port is None or not clear_of(placements, obstacles):
                    continue
                plan_ = {"surface": "nauvis", "force": "player", "atomic": True,
                         "phases": [{"name": f"saved-world-assemble-{product}", "actions": placements}]}
                try:
                    validate_build_plan(plan_)
                except ValueError:
                    continue
                anchor = min((p["position"] for p in ports if p["network"] == port["network"]),
                             key=lambda q: (q["x"], q["y"]))
                digest = sha256_hash({"plan": plan_, "network_anchor": anchor})
                candidates.append({"option_id": f"assemble.{digest[7:19]}", "family": "assemble", "product": product,
                                   "summary": f"{product} {side} ({centre[0]},{centre[1]}) in the mall block",
                                   "plan": plan_, "network": port["network"], "network_anchor": anchor})
                found += 1
                if found >= per_product:
                    break
            if found >= per_product:
                break
    return candidates
