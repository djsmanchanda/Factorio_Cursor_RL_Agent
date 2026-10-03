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
from orchestrator.resource_patches import MINIMUM_NEW_PATCH_RESOURCE
from planners.zoning_geometry import Rect
from training.laya_power import POLES
from training.laya_saved_world import ghost

VERSION = "laya-site-plan-v1"
SAMPLE_SPACING = 4.0  # saved-world resource survey grid
SMELTED = {"iron-ore": "iron-plate", "copper-ore": "copper-plate", "stone": "stone-brick"}
# A basic six-furnace module (planners/smelter_block, variant "basic") is 15 tiles wide and an
# 18-furnace line (Start-Middle-Middle-End) is 32 deep: 34 fits two parallel lines across an
# east/west strip or one long line down a north/south strip; 15 is the one-module fallback.
STRIP_WIDTHS = (34.0, 15.0)
CORRIDOR_WIDTH = 5.0  # reserved for future belts on both sides of a strip
SMALL_PATCH_RADIUS = 250.0
BASE_CORE = 12.0  # half-size of the starter area strips must avoid
SMALL_PATCH_SAMPLES = 16  # survey cells (~256 tiles); smaller patches cannot host module-fed mine lines  # small patches are used only if no larger one lies within this of the base
CORRIDOR_ENTITIES = frozenset({"transport-belt", "underground-belt", "splitter", "fast-transport-belt",
                               "medium-electric-pole", "big-electric-pole"})
MALL_SITE_OFFSETS = ((10, 10), (10, -10), (-10, 10), (-10, -10))  # the generator's module sites per port
MALL_EXTENT = (-7.0, -6.0, 10.0, 16.0)  # three quad modules around a site


def survey_patches(facts: Mapping) -> list[dict]:
    """Cluster the survey samples of each resource into patches (8-neighbour on the sample grid).

    ``amount`` estimates the patch total: each sample stands for a 4x4 area.
    """
    by_resource: dict[str, list[tuple[float, float, float]]] = {}
    for site in facts["resources"]:
        by_resource.setdefault(site["item"], []).append(
            (site["position"]["x"], site["position"]["y"], float(site.get("remaining", 0))))
    patches = []
    for resource in sorted(by_resource):
        amounts = {(x, y): a for x, y, a in by_resource[resource]}
        remaining = set(amounts)
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
                            "amount": round(sum(amounts[m] for m in members) * SAMPLE_SPACING ** 2),
                            "rect": [min(xs) - half, min(ys) - half, max(xs) + half, max(ys) + half]})
    return patches


def _distance_to(rect: Rect, point: tuple[float, float]) -> float:
    dx = max(rect.min_x - point[0], 0, point[0] - rect.max_x)
    dy = max(rect.min_y - point[1], 0, point[1] - rect.max_y)
    return math.hypot(dx, dy)


def _large(patch: Mapping) -> bool:
    """The deterministic new-site amount floor and room for mine lines."""
    return patch["amount"] >= MINIMUM_NEW_PATCH_RESOURCE and patch["samples"] >= SMALL_PATCH_SAMPLES


def usable_patches(patches: list[dict], base: tuple[float, float]) -> list[dict]:
    """Patches worth planning: at least the deterministic new-site minimum, unless a resource
    has no such patch within SMALL_PATCH_RADIUS of the base (then its small patches stay)."""
    kept = []
    for patch in patches:
        if _large(patch):
            kept.append(patch)
            continue
        larger_near = any(p["resource"] == patch["resource"] and _large(p)
                          and _distance_to(_rect(p["rect"]), base) <= SMALL_PATCH_RADIUS for p in patches)
        if not larger_near:
            kept.append(patch)
    return kept


def _rect(values: Sequence[float]) -> Rect:
    return Rect(*values)


def _strip_and_corridors(patch: Rect, side: str, width: float) -> tuple[Rect, list[Rect]]:
    """A smelting strip on one patch side, with belt corridors between it and the patch and beyond it."""
    c = CORRIDOR_WIDTH
    if side == "west":
        return (Rect(patch.min_x - c - width, patch.min_y, patch.min_x - c, patch.max_y),
                [Rect(patch.min_x - c, patch.min_y, patch.min_x, patch.max_y),
                 Rect(patch.min_x - 2 * c - width, patch.min_y, patch.min_x - c - width, patch.max_y)])
    if side == "east":
        return (Rect(patch.max_x + c, patch.min_y, patch.max_x + c + width, patch.max_y),
                [Rect(patch.max_x, patch.min_y, patch.max_x + c, patch.max_y),
                 Rect(patch.max_x + c + width, patch.min_y, patch.max_x + 2 * c + width, patch.max_y)])
    if side == "north":
        return (Rect(patch.min_x, patch.min_y - c - width, patch.max_x, patch.min_y - c),
                [Rect(patch.min_x, patch.min_y - c, patch.max_x, patch.min_y),
                 Rect(patch.min_x, patch.min_y - 2 * c - width, patch.max_x, patch.min_y - c - width)])
    return (Rect(patch.min_x, patch.max_y + c, patch.max_x, patch.max_y + c + width),
            [Rect(patch.min_x, patch.max_y, patch.max_x, patch.max_y + c),
             Rect(patch.min_x, patch.max_y + c + width, patch.max_x, patch.max_y + 2 * c + width)])


def _base(facts: Mapping) -> tuple[float, float]:
    """The starter roboport: the powered port nearest the centre of the generated power grid.

    Port order from the game is arbitrary, and later coverage adds ports far from the base.
    """
    ports = [p for p in facts["ports"] if p["energy"] > 0]
    grid = [p["position"] for p in facts.get("poles", []) if p.get("generated")]
    if not grid:
        return ports[0]["position"]["x"], ports[0]["position"]["y"]
    cx, cy = sum(p["x"] for p in grid) / len(grid), sum(p["y"] for p in grid) / len(grid)
    port = min(ports, key=lambda p: math.dist((p["position"]["x"], p["position"]["y"]), (cx, cy)))
    return port["position"]["x"], port["position"]["y"]


def _base_core(facts: Mapping) -> Rect:
    """Starter area kept free of smelting strips (roboport, kit chest, starter power)."""
    bx, by = _base(facts)
    return Rect(bx - BASE_CORE, by - BASE_CORE, bx + BASE_CORE, by + BASE_CORE)


def base_zones(facts: Mapping, avoid: Sequence[Rect] = ()) -> list[dict]:
    """Ore zones for usable patches; a module-wide smelting strip with belt corridors beside the
    nearest iron, copper and stone patch, on the side facing the base where it fits."""
    base = _base(facts)
    zones = []
    patches = usable_patches(survey_patches(facts), base)
    for index, patch in enumerate(patches):
        zones.append({"zone_id": f"ore-{index}", "purpose": f"ore:{patch['resource']}", "rect": patch["rect"],
                      "amount": patch["amount"]})
    for resource, plate in SMELTED.items():
        own = [p for p in patches if p["resource"] == resource]
        if not own:
            continue
        nearest = _rect(min(own, key=lambda p: _distance_to(_rect(p["rect"]), base))["rect"])
        taken = [_rect(z["rect"]) for z in zones] + list(avoid) + [_base_core(facts)]
        sides = sorted(("west", "east", "north", "south"),
                       key=lambda side: math.dist(_strip_and_corridors(nearest, side, STRIP_WIDTHS[0])[0].centre, base))
        choice = next(((strip, corridors) for width in STRIP_WIDTHS for side in sides
                       for strip, corridors in [_strip_and_corridors(nearest, side, width)]
                       if not any(r.overlaps(t) for r in (strip, *corridors) for t in taken)), None)
        if choice is None:
            continue  # No side fits a module column; that line must be planned elsewhere.
        strip, corridors = choice
        zones.append({"zone_id": f"smelt-{plate}", "purpose": f"smelt:{plate}",
                      "rect": [strip.min_x, strip.min_y, strip.max_x, strip.max_y]})
        for number, corridor in enumerate(corridors):
            zones.append({"zone_id": f"corridor-{plate}-{number}", "purpose": "corridor",
                          "rect": [corridor.min_x, corridor.min_y, corridor.max_x, corridor.max_y]})
    return zones


def plan_options(facts: Mapping) -> list[dict]:
    """Mall-site alternatives around the starter roboport; each plan's strips avoid its mall."""
    ore_rects = [_rect(z["rect"]) for z in base_zones(facts) if z["purpose"].startswith("ore:")]
    bx, by = _base(facts)
    options = []
    for dx, dy in MALL_SITE_OFFSETS:
        sx, sy = math.floor(bx + dx) + .5, math.floor(by + dy) + .5
        mall = Rect(sx + MALL_EXTENT[0], sy + MALL_EXTENT[1], sx + MALL_EXTENT[2], sy + MALL_EXTENT[3])
        if any(mall.overlaps(r) for r in ore_rects):
            continue
        zones = base_zones(facts, avoid=[mall])
        plan = {"version": VERSION, "mall_site": [sx, sy], "zones": zones + [
            {"zone_id": "mall", "purpose": "mall", "rect": [mall.min_x, mall.min_y, mall.max_x, mall.max_y]}]}
        strips = sum(z["purpose"].startswith("smelt:") for z in zones)
        options.append({"option_id": f"plan.mall.{int(sx)}.{int(sy)}", "family": "plan",
                        "summary": f"Reserve the mall at ({sx},{sy}); protect {sum(z['purpose'].startswith('ore:') for z in zones)} "
                                   f"ore patches, {strips} smelting strips and their belt corridors",
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
            if kind == "corridor":
                if action["entity"] in CORRIDOR_ENTITIES:
                    continue
                return reject("reserved:corridor", action)
            starter_ore = family in ("starter", "line") and SMELTED.get(subject) == product  # Its own patch.
            if kind == "ore" and not ((family == "mine" and (product == subject or own_ore)) or starter_ore):
                return reject(f"reserved:{zone['purpose']}", action)
            # Roboports may stand in a smelting strip: its lines' provider taps need logistic coverage.
            if kind == "smelt" and not ((family in ("smelt", "starter", "line") and product == subject) or family == "expand"
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
    colours = {"ore": "{r=0.9,g=0.6,b=0.1}", "smelt": "{r=0.9,g=0.2,b=0.2}", "mall": "{r=0.2,g=0.6,b=1}",
               "corridor": "{r=0.6,g=0.6,b=0.6}"}
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
