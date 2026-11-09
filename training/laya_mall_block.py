# Path: training/laya_mall_block.py
# Purpose: Mall cells in Laya's one large mall block, on one module lattice, W/E slots first, growing from the edge nearest a roboport.

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
from planners.quad_mall_layout import QUAD_MALL_MACHINE_OFFSETS
from planners.plan_validation import validate_build_plan
from training.laya_lines import clear_of

# One lattice per mall (user reference layout, 2026-11-08, data/laya-saved-world/cohort-kit-20261108/
# user-reference-mall-*): module centres at anchor + i*(6,5) + j*(-3,10), the continuation of
# DENSE_MALL_MODULE_OFFSETS. Every 3x5 cell is a hub or one machine of exactly one hub (a plus-pentomino
# tiling): no gaps, no shared machines. The old 19x24 bank grid left modules off this lattice.
LATTICE = ((6.0, 5.0), (-3.0, 10.0))
MODULE_EXTENT = (-4.5, -6.5, 4.5, 6.5)  # Machines at (+-3, 0) and (0, +-5), 3x3 each.
ANCHOR_INSET = (7.0, 6.0)  # The first module sits where the old first bank site did (existing worlds keep it).
FURNACE_PRODUCTS = {"iron-plate", "copper-plate", "stone-brick", "steel-plate"}
HORIZONTAL, VERTICAL = ("west", "east"), ("north", "south")  # vertical slots need long-handed inserters


def lattice_anchor(zone: list[float]) -> tuple[float, float]:
    return (math.floor(zone[0] + ANCHOR_INSET[0]) + .5, math.floor(zone[1] + ANCHOR_INSET[1]) + .5)


def block_centres(zone: list[float]) -> list[tuple[float, float]]:
    """Every lattice module centre whose four machines fit inside the mall block."""
    x0, y0, x1, y1 = zone
    ax, ay = lattice_anchor(zone)
    (ux, uy), (vx, vy) = LATTICE
    span = int(max(x1 - x0, y1 - y0)) // 3 + 2
    centres = []
    for i in range(-span, span + 1):
        for j in range(-span, span + 1):
            x, y = ax + i * ux + j * vx, ay + i * uy + j * vy
            if x + MODULE_EXTENT[0] >= x0 and y + MODULE_EXTENT[1] >= y0 and x + MODULE_EXTENT[2] <= x1 \
                    and y + MODULE_EXTENT[3] <= y1:
                centres.append((x, y))
    return sorted(centres, key=lambda c: (c[1], c[0]))


def fill_order(zone: list[float], ports: list[Mapping]) -> list[tuple[tuple[float, float], str]]:
    """(module centre, side) in build priority (user, 2026-11-08): W/E slots of modules nearest a roboport
    first (short inserters, fast-upgradable), then N/S slots; a N/S slot is used only beside a built W/E slot."""
    def near(centre):
        return (min(math.dist(centre, (p["position"]["x"], p["position"]["y"])) for p in ports), centre[1], centre[0])
    centres = sorted(block_centres(zone), key=near)
    return [(c, side) for sides in (HORIZONTAL, VERTICAL) for c in centres for side in sides]


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

    def built(centre, side):
        dx, dy = QUAD_MALL_MACHINE_OFFSETS[side]
        return (centre[0] + dx, centre[1] + dy) in taken

    def reach(placements):
        return next((p for p in ports if all(
            max(abs(a["position"]["x"] - p["position"]["x"]), abs(a["position"]["y"] - p["position"]["y"]))
            <= p["logistic_radius"] - 1 for a in placements)), None)

    def clear(placements):
        """Clear of everything but machines and the module's own shared hub: a slot repeats its module's
        requester/provider exactly (each slot owns a request section), which is not a collision."""
        own = {(a["entity"], a["position"]["x"], a["position"]["y"]) for a in placements}
        return clear_of([a for a in placements if not a["entity"].startswith("assembling")],
                        [o for o in obstacles if not o["entity"].startswith("assembling")
                         and (o["entity"], o["position"]["x"], o["position"]["y"]) not in own])

    def placeable(placements):
        return reach(placements) is not None and clear(placements)

    # One slot: the first in priority order whose placement is in reach and clear - Laya chooses what to make,
    # not where (user, 2026-11-08). Slot geometry is the same for every product, so one reference product checks it
    # (eval-fixes-20261108: the first slot was blocked and, checked by coverage only, froze mall growth).
    makeable = sorted(p for p in products if p in recipes and p not in FURNACE_PRODUCTS)
    free = []
    for centre, side in (slot for zone in zones for slot in fill_order(zone, ports)) if makeable else ():
        if built(centre, side):
            continue
        if side in VERTICAL and not any(built(centre, s) for s in HORIZONTAL):
            continue  # Top/bottom only on a module whose left/right is started.
        if placeable(generate_quad_slot(makeable[0], recipes[makeable[0]], centre, side)):
            free = [(centre, side)]
            break
    candidates = []
    for product in sorted(p for p in products if p in recipes and p not in FURNACE_PRODUCTS):
        for centre, side in free:
            placements = generate_quad_slot(product, recipes[product], centre, side)
            port = next((p for p in ports if all(
                max(abs(a["position"]["x"] - p["position"]["x"]), abs(a["position"]["y"] - p["position"]["y"]))
                <= p["logistic_radius"] - 1 for a in placements)), None)
            if port is None or not clear(placements):
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
