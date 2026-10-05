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
from planners.quad_mall_layout import DENSE_MALL_MODULE_OFFSETS, QUAD_MALL_MACHINE_OFFSETS
from planners.plan_validation import validate_build_plan
from training.laya_lines import clear_of

# A bank is the shared dense layout: three interlocking quad modules at DENSE_MALL_MODULE_OFFSETS
# around a site (12 machines), the footprint MALL_EXTENT (-7,-6)-(10,16). Banks tile the block.
BANK_EXTENT = (-7.0, -6.0, 10.0, 16.0)
BANK_PITCH = (19, 24)  # bank footprint plus a pole/gap tile row
FURNACE_PRODUCTS = {"iron-plate", "copper-plate", "stone-brick", "steel-plate"}
HORIZONTAL, VERTICAL = ("west", "east"), ("north", "south")  # vertical slots need long-handed inserters


def bank_sites(zone: list[float]) -> list[tuple[float, float]]:
    """Bank sites tiling the block, aligned like the shared generator's sites (x.5, y.5)."""
    x0, y0, x1, y1 = zone
    sites = []
    y = y0 - BANK_EXTENT[1]
    while y + BANK_EXTENT[3] <= y1:
        x = x0 - BANK_EXTENT[0]
        while x + BANK_EXTENT[2] <= x1:
            sites.append((math.floor(x) + .5, math.floor(y) + .5))
            x += BANK_PITCH[0]
        y += BANK_PITCH[1]
    return sites


def block_centres(zone: list[float]) -> list[tuple[float, float]]:
    """Every quad module centre of every bank in the block."""
    return [(sx + dx, sy + dy) for sx, sy in bank_sites(zone) for dx, dy in DENSE_MALL_MODULE_OFFSETS]


def fill_order(zone: list[float], ports: list[Mapping]) -> list[tuple[tuple[float, float], str]]:
    """(module centre, side) slots in build order: nearest bank first, module by module, sides in a fixed order."""
    def near(site):
        return min(math.dist(site, (p["position"]["x"], p["position"]["y"])) for p in ports)
    return [((sx + dx, sy + dy), side) for sx, sy in sorted(bank_sites(zone), key=near)
            for dx, dy in DENSE_MALL_MODULE_OFFSETS for side in (*HORIZONTAL, *VERTICAL)]


def mall_cell_candidates(facts: Mapping, plan: Mapping | None, products: set[str], *, per_product: int = 6,
                         obstacles: list = ()) -> list[dict]:
    """Quad cells for ``products`` at the mall block's next free slots (one horizontal, one vertical),
    so the block fills bank by bank in the shared dense pattern instead of scattering."""
    if not plan:
        return []
    zones = [z["rect"] for z in plan["zones"] if z["purpose"] == "mall"]
    ports = [p for p in facts["ports"] if p["energy"] > 0]
    recipes = facts.get("recipes") or {}
    if not zones or not ports:
        return []
    taken = {(m["position"]["x"], m["position"]["y"]) for m in facts.get("machines", [])}
    taken |= {(o["position"]["x"], o["position"]["y"]) for o in obstacles if o["entity"].startswith("assembling-machine")}
    free = []  # the next free slot of each kind
    for centre, side in (slot for zone in zones for slot in fill_order(zone, ports)):
        dx, dy = QUAD_MALL_MACHINE_OFFSETS[side]
        if (centre[0] + dx, centre[1] + dy) in taken:
            continue
        if not any(s in HORIZONTAL for _, s in free) and side in HORIZONTAL or \
                not any(s in VERTICAL for _, s in free) and side in VERTICAL:
            free.append((centre, side))
        if len(free) == 2:
            break
    candidates = []
    for product in sorted(p for p in products if p in recipes and p not in FURNACE_PRODUCTS):
        for centre, side in free:
            placements = generate_quad_slot(product, recipes[product], centre, side)
            port = next((p for p in ports if all(
                max(abs(a["position"]["x"] - p["position"]["x"]), abs(a["position"]["y"] - p["position"]["y"]))
                <= p["logistic_radius"] - 1 for a in placements)), None)
            if port is None or not clear_of([a for a in placements if not a["entity"].startswith("assembling")],
                                            [o for o in obstacles if not o["entity"].startswith("assembling")]):
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
    return candidates
