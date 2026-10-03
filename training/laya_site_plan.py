# Path: training/laya_site_plan.py
# Purpose: Laya land planning: survey ore patches, offer mall-site plan alternatives, reserve zones, and refuse builds that take another purpose's land.

"""Site plan and land reservation for the clean Laya runner.

Zones:
* ``ore:<resource>``  every surveyed patch; only mines of that resource build there.
* ``smelt:<plate>``   a strip beside the nearest iron/copper/stone patch, on the
                      side facing the base, kept for that plate's mine+furnace line.
* ``mall``            the chosen mall site; mall (assemble) builds go only here.

The mall site is a planning decision: ``plan_options`` enumerates legal sites
and the policy chooses, like any other decision.  Reservation checks are hard
land rules applied to every option; they reject, never choose.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path

from planners.plan_validation import ENTITY_FOOTPRINTS
from planners.zoning_geometry import Rect
from training.laya_power import POLES

VERSION = "laya-site-plan-v1"
SAMPLE_SPACING = 4.0  # saved-world resource survey grid
SMELTED = {"iron-ore": "iron-plate", "copper-ore": "copper-plate", "stone": "stone-brick"}
STRIP_WIDTH = 12.0
MALL_SITE_OFFSETS = ((10, 10), (10, -10), (-10, 10), (-10, -10))  # the generator's module sites per port
MALL_EXTENT = (-7.0, -6.0, 10.0, 16.0)  # three quad modules around a site


def survey_patches(facts: Mapping) -> list[dict]:
    """Cluster the survey samples of each resource into patches (8-neighbour on the sample grid)."""
    by_resource: dict[str, list[tuple[float, float]]] = {}
    for site in facts["resources"]:
        by_resource.setdefault(site["item"], []).append((site["position"]["x"], site["position"]["y"]))
    patches = []
    for resource in sorted(by_resource):
        remaining = set(by_resource[resource])
        while remaining:
            seed = min(remaining, key=lambda p: (p[1], p[0]))
            remaining.remove(seed)
            pending, members = [seed], []
            while pending:
                point = pending.pop()
                members.append(point)
                near = {p for p in remaining if max(abs(p[0] - point[0]), abs(p[1] - point[1])) <= SAMPLE_SPACING}
                remaining -= near
                pending.extend(near)
            xs, ys = [p[0] for p in members], [p[1] for p in members]
            half = SAMPLE_SPACING / 2
            patches.append({"resource": resource, "samples": len(members),
                            "rect": [min(xs) - half, min(ys) - half, max(xs) + half, max(ys) + half]})
    return patches


def _rect(values: Sequence[float]) -> Rect:
    return Rect(*values)


def _strips(patch_rect: Rect, base: tuple[float, float]) -> list[Rect]:
    """Strips along each patch side, the side facing the base first."""
    r, w = patch_rect, STRIP_WIDTH
    sides = {"west": Rect(r.min_x - 1 - w, r.min_y, r.min_x - 1, r.max_y),
             "east": Rect(r.max_x + 1, r.min_y, r.max_x + 1 + w, r.max_y),
             "north": Rect(r.min_x, r.min_y - 1 - w, r.max_x, r.min_y - 1),
             "south": Rect(r.min_x, r.max_y + 1, r.max_x, r.max_y + 1 + w)}
    return sorted(sides.values(), key=lambda strip: math.dist(strip.centre, base))


def _base(facts: Mapping) -> tuple[float, float]:
    port = next(p for p in facts["ports"] if p["energy"] > 0)
    return port["position"]["x"], port["position"]["y"]


def base_zones(facts: Mapping) -> list[dict]:
    """Ore zones for every patch, smelting strips beside the nearest iron/copper/stone patch."""
    base = _base(facts)
    zones = []
    patches = survey_patches(facts)
    for index, patch in enumerate(patches):
        zones.append({"zone_id": f"ore-{index}", "purpose": f"ore:{patch['resource']}", "rect": patch["rect"]})
    ore_rects = [_rect(z["rect"]) for z in zones]
    for resource, plate in SMELTED.items():
        own = [p for p in patches if p["resource"] == resource]
        if not own:
            continue
        nearest = min(own, key=lambda p: math.dist(_rect(p["rect"]).centre, base))
        taken = ore_rects + [_rect(z["rect"]) for z in zones if z["purpose"].startswith("smelt:")]
        strip = next((s for s in _strips(_rect(nearest["rect"]), base) if not any(s.overlaps(r) for r in taken)), None)
        if strip is None:
            continue  # Every side is taken; that line must be planned elsewhere.
        zones.append({"zone_id": f"smelt-{plate}", "purpose": f"smelt:{plate}",
                      "rect": [strip.min_x, strip.min_y, strip.max_x, strip.max_y]})
    return zones


def plan_options(facts: Mapping) -> list[dict]:
    """Mall-site alternatives around the starter roboport that clear every other zone."""
    zones = base_zones(facts)
    rects = [_rect(z["rect"]) for z in zones]
    bx, by = _base(facts)
    options = []
    for dx, dy in MALL_SITE_OFFSETS:
        sx, sy = math.floor(bx + dx) + .5, math.floor(by + dy) + .5
        mall = Rect(sx + MALL_EXTENT[0], sy + MALL_EXTENT[1], sx + MALL_EXTENT[2], sy + MALL_EXTENT[3])
        if any(mall.overlaps(r) for r in rects):
            continue
        plan = {"version": VERSION, "mall_site": [sx, sy], "zones": zones + [
            {"zone_id": "mall", "purpose": "mall", "rect": [mall.min_x, mall.min_y, mall.max_x, mall.max_y]}]}
        options.append({"option_id": f"plan.mall.{int(sx)}.{int(sy)}", "family": "plan",
                        "summary": f"Reserve the mall at ({sx},{sy}); protect {sum(z['purpose'].startswith('ore:') for z in zones)} "
                                   f"ore patches and {sum(z['purpose'].startswith('smelt:') for z in zones)} smelting strips",
                        "site_plan": plan, "need_id": "plan:site"})
    return options


def _size(entity: str) -> float:
    return POLES[entity][2] if entity in POLES else ENTITY_FOOTPRINTS.get(entity, 1)


def reservation_reason(plan: Mapping, candidate: Mapping) -> str | None:
    """Why a build would take land reserved for another purpose; ``None`` if it may build."""
    family, product = candidate["family"], candidate.get("product")
    zones = [(z, _rect(z["rect"])) for z in plan["zones"]]
    has_mall = any(z["purpose"] == "mall" for z in plan["zones"])
    in_mall = False
    for action in candidate["plan"]["phases"][0]["actions"]:
        x, y = action["position"]["x"], action["position"]["y"]
        half = _size(action["entity"]) / 2
        box = Rect(x - half, y - half, x + half, y + half)
        for zone, rect in zones:
            if not box.overlaps(rect):
                continue
            kind, _, subject = zone["purpose"].partition(":")
            if kind == "ore" and not (family == "mine" and product == subject):
                return f"reserved:{zone['purpose']}"
            if kind == "smelt" and not ((family == "smelt" and product == subject)
                                        or (family == "mine" and SMELTED.get(product) == subject)):
                return f"reserved:{zone['purpose']}"
            if kind == "mall":
                if family != "assemble":
                    return "reserved:mall"
                in_mall = True
    if family == "assemble" and has_mall and not in_mall:
        return "outside_mall_zone"
    return None


def load_plan(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def save_plan(path: Path, plan: Mapping) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(plan, indent=2) + "\n")
    tmp.replace(path)


def draw_command(plan: Mapping) -> str:
    """Lua that outlines every zone on the ground for spectators (rendering only, no game state)."""
    colours = {"ore": "{r=0.9,g=0.6,b=0.1}", "smelt": "{r=0.9,g=0.2,b=0.2}", "mall": "{r=0.2,g=0.6,b=1}"}
    parts = ["storage.laya_zone_ids=storage.laya_zone_ids or {}",
             "for _,id in pairs(storage.laya_zone_ids) do local o=rendering.get_object_by_id(id) if o then o.destroy() end end",
             "storage.laya_zone_ids={}"]
    for zone in plan["zones"]:
        x1, y1, x2, y2 = zone["rect"]
        colour = colours[zone["purpose"].split(":")[0]]
        parts.append(f'table.insert(storage.laya_zone_ids, rendering.draw_rectangle{{color={colour},width=3,filled=false,'
                     f'left_top={{{x1},{y1}}},right_bottom={{{x2},{y2}}},surface="nauvis",draw_on_ground=true}}.id)')
        parts.append(f'table.insert(storage.laya_zone_ids, rendering.draw_text{{text="{zone["purpose"]}",color={colour},'
                     f'target={{{x1 + 0.5},{y1 + 0.5}}},surface="nauvis",scale=2}}.id)')
    return "/sc " + "; ".join(parts) + '; rcon.print("zones-drawn")'
