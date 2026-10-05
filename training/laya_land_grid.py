# Path: training/laya_land_grid.py
# Purpose: Grid land model for Laya: 64-tile parcels with a 6-tile corridor lattice, parcels assigned per purpose (docs/rl/Laya_Land_Grid.md).

"""Land grid.

Parcels are 64 x 64 tiles anchored so the starter roboport sits mid-parcel.
The first ``CORRIDOR`` tiles of each parcel's west and north edges form a
map-wide corridor lattice for transport and power; corridors are tested
arithmetically (``in_corridor``), never stored, so a 5k x 5k map costs nothing
extra.  Assigned parcels are ordinary plan zones (``purpose`` + ``rect`` of the
interior), so lines, starters, coverage and reservations read them unchanged.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping

from training.laya_site_plan import (MALL_EXTENT, MALL_SITE_OFFSETS, SMELTED, VERSION, _base, survey_patches,
                                     usable_patches)

PITCH = 64
CORRIDOR = 6
ORE_MIN_SAMPLES = 4  # survey samples (4x4 tiles each) a parcel needs to be an ore parcel
SMELT_SEARCH = 4  # parcels from an ore parcel searched for its smelting parcel
SCOPE_MARGIN = 2  # the saved-world validator rejects positions within 2 tiles of its bounds


def actionable(rect: list[float]) -> bool:
    """Inside the area where builds are validated today (training/laya_saved_world.BOUNDS).

    The survey sees much further; parcels outside stay planned but unbuilt until the action
    area grows (docs/rl/Laya_Land_Grid.md).
    """
    from training.laya_saved_world import BOUNDS
    return (rect[0] >= BOUNDS["x_min"] + SCOPE_MARGIN and rect[1] >= BOUNDS["y_min"] + SCOPE_MARGIN
            and rect[2] <= BOUNDS["x_max_exclusive"] - SCOPE_MARGIN and rect[3] <= BOUNDS["y_max_exclusive"] - SCOPE_MARGIN)


def grid_origin(base: tuple[float, float]) -> tuple[int, int]:
    return round(base[0]) - PITCH // 2, round(base[1]) - PITCH // 2


def parcel_of(origin: tuple[int, int], x: float, y: float) -> tuple[int, int]:
    return math.floor((x - origin[0]) / PITCH), math.floor((y - origin[1]) / PITCH)


def interior(origin: tuple[int, int], parcel: tuple[int, int]) -> list[float]:
    x0, y0 = origin[0] + parcel[0] * PITCH, origin[1] + parcel[1] * PITCH
    return [x0 + CORRIDOR, y0 + CORRIDOR, x0 + PITCH, y0 + PITCH]


def _hits_stripes(low: float, high: float, start: int) -> bool:
    """Whether the open interval (low, high) overlaps any stripe [start + kP, start + kP + C)."""
    for k in range(math.floor((low - start) / PITCH), math.floor((high - start) / PITCH) + 1):
        stripe = start + k * PITCH
        if low < stripe + CORRIDOR and high > stripe:
            return True
    return False


def in_corridor(grid: Mapping, box: tuple[float, float, float, float]) -> bool:
    """A footprint (x1, y1, x2, y2) overlaps the corridor lattice."""
    ox, oy = grid["origin"]
    return _hits_stripes(box[0], box[2], ox) or _hits_stripes(box[1], box[3], oy)


def _ore_parcels(facts: Mapping, origin: tuple[int, int]) -> dict[tuple[int, int], str]:
    """Parcels whose interior holds enough samples of a usable patch, by dominant resource.

    The base parcel is never an ore parcel: it hosts the starter area and the mall.
    """
    base_parcel = parcel_of(origin, *_base(facts))
    patches = usable_patches(survey_patches(facts), _base(facts))
    counts: dict[tuple[int, int], Counter] = {}
    for site in facts["resources"]:
        x, y = site["position"]["x"], site["position"]["y"]
        if not any(p["resource"] == site["item"] and p["rect"][0] <= x <= p["rect"][2]
                   and p["rect"][1] <= y <= p["rect"][3] for p in patches):
            continue  # Ignored small patches give no parcel.
        parcel = parcel_of(origin, x, y)
        if parcel == base_parcel:
            continue
        box = interior(origin, parcel)
        if box[0] <= x <= box[2] and box[1] <= y <= box[3]:
            counts.setdefault(parcel, Counter())[site["item"]] += 1
    return {parcel: c.most_common(1)[0][0] for parcel, c in counts.items()
            if c.most_common(1)[0][1] >= ORE_MIN_SAMPLES}


def _base_ore(facts: Mapping, origin: tuple[int, int]) -> list[dict]:
    """Ore under the base parcel: tight zones for the starters only (the parcel itself stays the base)."""
    base_parcel = parcel_of(origin, *_base(facts))
    box = interior(origin, base_parcel)
    by_item: dict[str, list[tuple[float, float]]] = {}
    for site in facts["resources"]:
        x, y = site["position"]["x"], site["position"]["y"]
        if box[0] <= x <= box[2] and box[1] <= y <= box[3]:
            by_item.setdefault(site["item"], []).append((x, y))
    half = 2.0  # half a survey sample
    return [{"zone_id": f"base-ore:{item}", "purpose": f"ore:{item}",
             "rect": [min(x for x, _ in pts) - half, min(y for _, y in pts) - half,
                      max(x for x, _ in pts) + half, max(y for _, y in pts) + half]}
            for item, pts in sorted(by_item.items()) if len(pts) >= ORE_MIN_SAMPLES]


def _zone(origin, parcel, purpose: str) -> dict:
    return {"zone_id": f"parcel:{parcel[0]}:{parcel[1]}", "purpose": purpose, "rect": interior(origin, parcel),
            "parcel": list(parcel)}


def allocate(plan: dict, purpose: str, near: Iterable[tuple[int, int]], taken: set | None = None) -> dict | None:
    """Assign the free parcel nearest ``near`` (adjacent to the same purpose first) and return its zone."""
    origin = tuple(plan["grid"]["origin"])
    used = {tuple(z["parcel"]) for z in plan["zones"] if "parcel" in z} | {tuple(b) for b in plan["grid"].get("blocked", [])}
    used |= taken or set()
    near = list(near)
    if not near:
        return None
    own = [tuple(z["parcel"]) for z in plan["zones"] if z.get("purpose") == purpose and "parcel" in z]
    best, best_key = None, None
    for ni, nj in near:
        for di in range(-SMELT_SEARCH, SMELT_SEARCH + 1):
            for dj in range(-SMELT_SEARCH, SMELT_SEARCH + 1):
                parcel = (ni + di, nj + dj)
                if parcel in used or not actionable(interior(origin, parcel)):
                    continue
                # Manhattan: an edge neighbour is reached through one corridor; a diagonal one would
                # route its belts across a third parcel.
                to_inputs = min(abs(parcel[0] - a) + abs(parcel[1] - b) for a, b in near)
                to_own = min((abs(parcel[0] - a) + abs(parcel[1] - b) for a, b in own), default=0)
                key = (to_own > 1, to_inputs, abs(parcel[0]) + abs(parcel[1]), parcel)
                if best_key is None or key < best_key:
                    best, best_key = parcel, key
    if best is None:
        return None
    zone = _zone(origin, best, purpose)
    plan["zones"].append(zone)
    return zone


def grid_plan(facts: Mapping, mall: list[float] | None = None, surveyed: Mapping | None = None) -> dict:
    """Ore parcels for usable patches, one smelting parcel per plate beside its ore, the mall zone.

    With a wide ``surveyed`` map (``survey``), ore and oil parcels come from it within PLAN_RADIUS;
    the local sample scan still decides parcels it covers.
    """
    origin = grid_origin(_base(facts))
    base_parcel = parcel_of(origin, *_base(facts))
    ore = {**(survey_ore_parcels(surveyed, base_parcel) if surveyed else {}), **_ore_parcels(facts, origin)}
    plan = {"version": VERSION, "land_model": "grid", "mall_site": None,
            "grid": {"origin": list(origin), "pitch": PITCH, "corridor": CORRIDOR, "base_parcel": list(base_parcel),
                     "blocked": [list(base_parcel)]},
            "zones": [_zone(origin, parcel, f"{'oil' if resource == 'crude-oil' else 'ore'}:{resource}")
                      for parcel, resource in sorted(ore.items())]
                     + _base_ore(facts, origin)}
    for resource, plate in SMELTED.items():
        # Smelt beside ore Laya can mine today: parcels inside the action area first.
        sources = [p for p, r in ore.items() if r == resource and actionable(interior(origin, p))] or \
                  [p for p, r in ore.items() if r == resource]
        allocate(plan, f"smelt:{plate}", sorted(sources, key=lambda p: abs(p[0] - base_parcel[0]) + abs(p[1] - base_parcel[1]))[:1])
    if mall:
        plan["zones"].append({"zone_id": "mall", "purpose": "mall", "rect": mall})
    return plan


def grid_plan_options(facts: Mapping, surveyed: Mapping | None = None) -> list[dict]:
    """Mall-site alternatives in the base parcel; each carries the same grid plan."""
    bx, by = _base(facts)
    origin = grid_origin((bx, by))
    options = []
    for dx, dy in MALL_SITE_OFFSETS:
        sx, sy = math.floor(bx + dx) + .5, math.floor(by + dy) + .5
        mall = [sx + MALL_EXTENT[0], sy + MALL_EXTENT[1], sx + MALL_EXTENT[2], sy + MALL_EXTENT[3]]
        if in_corridor({"origin": origin}, tuple(mall)) or any(
                mall[0] < z["rect"][2] and mall[2] > z["rect"][0] and mall[1] < z["rect"][3] and mall[3] > z["rect"][1]
                for z in _base_ore(facts, origin)):
            continue  # The mall keeps clear of the corridors and of the ore under the base.
        plan = grid_plan(facts, mall, surveyed)
        plan["mall_site"] = [sx, sy]
        kinds = Counter(z["purpose"].split(":")[0] for z in plan["zones"])
        options.append({"option_id": f"plan.grid.mall.{int(sx)}.{int(sy)}", "family": "plan", "need_id": "plan:site",
                        "summary": f"Grid plan: mall at ({sx},{sy}); {kinds['ore']} ore parcels, {kinds['smelt']} smelting "
                                   f"parcels, 64-tile parcels on a 6-tile corridor lattice",
                        "site_plan": plan})
    return options


# --- Wide survey: resources summarised per parcel, once, so planning scales past the local scan. ---

SURVEY_BOX = 256  # tiles per Lua call (one find_entities_filtered over 256 x 256)
PLAN_RADIUS = 6  # parcels from the base that get planned zones; farther survey parcels wait for expansion
SURVEY_MIN_TILES = 64  # resource tiles a parcel needs to be an ore parcel (a crude well counts as one)


def survey_lua(origin: tuple[int, int], box: tuple[int, int, int, int]) -> str:
    """Lua that sums resource tiles and amounts per parcel inside ``box`` and prints JSON."""
    ox, oy = origin
    x1, y1, x2, y2 = box
    return ("/sc local s=game.surfaces.nauvis; local out={}; "
            f"for _,e in pairs(s.find_entities_filtered{{type='resource', area={{{{{x1},{y1}}},{{{x2},{y2}}}}}}}) do "
            f"local p=math.floor((e.position.x-({ox}))/{PITCH})..':'..math.floor((e.position.y-({oy}))/{PITCH}); "
            "local r=out[p] or {}; out[p]=r; local t=r[e.name] or {tiles=0, amount=0}; r[e.name]=t; "
            "t.tiles=t.tiles+1; t.amount=t.amount+e.amount end; rcon.print(helpers.table_to_json(out))")


def survey(command, origin: tuple[int, int], radius: int) -> dict[str, dict[str, dict]]:
    """Parcel -> resource -> {tiles, amount} for the square of half-size ``radius`` tiles around the origin.

    ``command`` runs one Lua command and returns its printed output (an RCON bridge).
    """
    import json
    merged: dict[str, dict[str, dict]] = {}
    cx, cy = origin[0] + PITCH // 2, origin[1] + PITCH // 2
    for x in range(cx - radius, cx + radius, SURVEY_BOX):
        for y in range(cy - radius, cy + radius, SURVEY_BOX):
            part = json.loads(command(survey_lua(origin, (x, y, x + SURVEY_BOX, y + SURVEY_BOX))) or "{}")
            for parcel, resources in (part if isinstance(part, dict) else {}).items():
                for name, totals in resources.items():
                    into = merged.setdefault(parcel, {}).setdefault(name, {"tiles": 0, "amount": 0})
                    into["tiles"] += totals["tiles"]
                    into["amount"] += totals["amount"]
    return merged


def survey_ore_parcels(found: Mapping, base_parcel: tuple[int, int]) -> dict[tuple[int, int], str]:
    """Planned ore parcels from a survey: dominant resource, enough tiles, within PLAN_RADIUS of the base."""
    parcels = {}
    for key, resources in found.items():
        parcel = tuple(int(v) for v in key.split(":"))
        if parcel == base_parcel or max(abs(parcel[0] - base_parcel[0]), abs(parcel[1] - base_parcel[1])) > PLAN_RADIUS:
            continue
        name, totals = max(resources.items(), key=lambda kv: kv[1]["amount"])
        if totals["tiles"] >= SURVEY_MIN_TILES or name == "crude-oil":
            parcels[parcel] = name
    return parcels
