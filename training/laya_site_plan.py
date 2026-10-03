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

from orchestrator.laya_operations import sha256_hash
from planners.plan_validation import ENTITY_FOOTPRINTS, validate_build_plan
from planners.zoning_geometry import Rect
from training.laya_power import POLES
from training.laya_saved_world import ghost

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


# Same furnace cell as the saved-world generator: requester -> inserter -> furnace -> inserter -> provider.
SMELT_INPUTS = {"iron-plate": ("iron-ore", 20), "copper-plate": ("copper-ore", 20), "stone-brick": ("stone", 40)}


def zone_smelt_candidates(facts: Mapping, plan: Mapping, plates: set[str]) -> list[dict]:
    """Furnace cells placed inside each needed plate's smelting strip.

    The plan decides where smelting goes; this enumerates every site in the
    strip that a powered roboport can build.  Power is added by laya_power.
    """
    ports = [p for p in facts["ports"] if p["energy"] > 0]
    candidates = []
    for zone in plan["zones"]:
        kind, _, plate = zone["purpose"].partition(":")
        if kind != "smelt" or plate not in plates or plate not in SMELT_INPUTS:
            continue
        x1, y1, x2, y2 = zone["rect"]
        ore, count = SMELT_INPUTS[plate]
        for cx in range(math.ceil(x1 + 3.5), math.floor(x2 - 3.5) + 1, 8):
            for cy in range(math.ceil(y1 + 1.5), math.floor(y2 - 1.5) + 1, 4):
                x, y = cx + .5, cy + .5
                port = next((p for p in ports if max(abs(x - p["position"]["x"]), abs(y - p["position"]["y"]))
                             <= p["construction_radius"] - 6), None)
                if port is None:
                    continue
                chest = ghost("requester-chest", x - 3, y)
                chest["logistic_requests"] = [{"name": ore, "count": count}]
                actions = [chest, ghost("inserter", x - 2, y, "west"), ghost("electric-furnace", x, y),
                           ghost("inserter", x + 2, y, "west"), ghost("passive-provider-chest", x + 3, y)]
                build = {"surface": "nauvis", "force": "player", "atomic": True,
                         "phases": [{"name": "saved-world-smelt", "actions": actions}]}
                try:
                    validate_build_plan(build)
                except ValueError:
                    continue
                anchor = min((p["position"] for p in ports if p["network"] == port["network"]), key=lambda q: (q["x"], q["y"]))
                digest = sha256_hash({"plan": build, "network_anchor": anchor})
                candidates.append({"option_id": f"smelt.z{digest[7:19]}", "family": "smelt", "product": plate,
                                   "summary": f"Make {plate} ({x},{y}) in {zone['zone_id']}", "plan": build,
                                   "network": port["network"], "network_anchor": anchor})
    return candidates


RING_ANGLES = range(0, 360, 45)
RING_RADII = (24, 32, 40)


def ring_expand_candidates(facts: Mapping, plan: Mapping | None) -> list[dict]:
    """Roboport hops in eight directions and three distances around each powered port.

    The generator only offers four fixed hops, which can all land on reserved
    land; this enumerates the ring so coverage can grow toward any patch.
    Power is added by laya_power; reservations still apply.
    """
    ports = [p for p in facts["ports"] if p["energy"] > 0]
    existing = [(p["position"]["x"], p["position"]["y"]) for p in facts["ports"]]
    seen, candidates = set(), []
    for port in ports:
        px, py = port["position"]["x"], port["position"]["y"]
        anchor = min((p["position"] for p in ports if p["network"] == port["network"]), key=lambda q: (q["x"], q["y"]))
        for angle in RING_ANGLES:
            for radius in RING_RADII:
                x = round(px + radius * math.cos(math.radians(angle)))
                y = round(py + radius * math.sin(math.radians(angle)))
                if (x, y) in seen or any(max(abs(x - ex), abs(y - ey)) < 12 for ex, ey in existing):
                    continue
                seen.add((x, y))
                build = {"surface": "nauvis", "force": "player", "atomic": True,
                         "phases": [{"name": "saved-world-expand", "actions": [ghost("roboport", x, y)]}]}
                candidate = {"family": "expand", "product": None, "summary": f"Extend bot coverage to ({x},{y})",
                             "plan": build, "network": port["network"], "network_anchor": anchor}
                if plan and reservation_reason(plan, candidate):
                    continue
                digest = sha256_hash({"plan": build, "network_anchor": anchor})
                candidates.append({**candidate, "option_id": f"expand.r{digest[7:19]}"})
    return candidates


def _size(entity: str) -> float:
    return POLES[entity][2] if entity in POLES else ENTITY_FOOTPRINTS.get(entity, 1)


# Wire relays are cheap and relocatable; they may cross ore and smelting land (not the mall).
RELAY_POLES = frozenset({"medium-electric-pole", "big-electric-pole"})


def reservation_reason(plan: Mapping, candidate: Mapping, detail: list | None = None) -> str | None:
    """Why a build would take land reserved for another purpose; ``None`` if it may build.

    ``detail`` (optional) receives ``{reason, entity, position}`` for the
    offending entity so journals can show exactly what was rejected.
    """
    return _reservation(plan, candidate, detail)


def _reservation(plan: Mapping, candidate: Mapping, detail: list | None) -> str | None:
    family, product = candidate["family"], candidate.get("product")
    zones = [(z, _rect(z["rect"])) for z in plan["zones"]]

    def reject(reason: str, action: Mapping) -> str:
        if detail is not None:
            detail.append({"reason": reason, "entity": action["entity"], "position": action["position"]})
        return reason

    has_mall = any(z["purpose"] == "mall" for z in plan["zones"])
    in_mall = False
    for action in candidate["plan"]["phases"][0]["actions"]:
        x, y = action["position"]["x"], action["position"]["y"]
        half = _size(action["entity"]) / 2
        box = Rect(x - half, y - half, x + half, y + half)
        overlapping = [(zone, rect) for zone, rect in zones if box.overlaps(rect)]
        # Patch boxes are survey approximations and can overlap; a mine standing in its own
        # resource's zone is mining that resource even where another patch's box reaches.
        own_ore = family == "mine" and any(z["purpose"] == f"ore:{product}" for z, _ in overlapping)
        for zone, rect in overlapping:
            kind, _, subject = zone["purpose"].partition(":")
            if action["entity"] in RELAY_POLES and kind in ("ore", "smelt"):
                continue
            if kind == "ore" and not (family == "mine" and (product == subject or own_ore)):
                return reject(f"reserved:{zone['purpose']}", action)
            if kind == "smelt" and not ((family == "smelt" and product == subject)
                                        or (family == "mine" and SMELTED.get(product) == subject)):
                return reject(f"reserved:{zone['purpose']}", action)
            if kind == "mall":
                if family != "assemble":
                    return reject("reserved:mall", action)
                in_mall = True
    if family == "assemble" and has_mall and not in_mall:
        return reject("outside_mall_zone", candidate["plan"]["phases"][0]["actions"][0])
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
