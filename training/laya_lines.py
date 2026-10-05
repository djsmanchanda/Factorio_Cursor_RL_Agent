# Path: training/laya_lines.py
# Purpose: Smelting line options for Laya: a paired-drill mine unit, a haul belt, and one column of the approved Start/Middle/End refinery units.

"""Long smelting lines (docs/rl/Laya_Strategy_Guidelines.md, Opening).

A *line* is one column of the approved refinery units from
``planners/smelter_block`` (regular-belt "basic" variant): Start + End is a
6-furnace line, Start - Middle - Middle - End an 18-furnace line.  Lines grow
in parallel: each new line is an independent column with its own mine unit
and haul, placed beside the previous one inside the plate's smelting strip.

Each option bundles:
* a paired mine unit (``stage_extraction.paired_mining_drill_positions``):
  drills above and below one east-flowing collector belt, with a substation at
  the row end (substations are allowed in mine areas);
* a haul: east along the collector row, one turn through the corridor, into
  the line's ore input from the west (the template's interface);
* the line's units, and an output belt with provider taps sized to its rate.

The line keeps its own template poles; training/laya_power links them to the
grid.  Everything is ghost placements; nothing is removed or rebuilt.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

from orchestrator.laya_operations import sha256_hash
from orchestrator.stage_extraction import paired_mining_drill_positions
from planners.plan_validation import validate_build_plan
from planners.smelter_block import COLUMN_PITCH, END_DEPTH, MIDDLE_PITCH, START_DEPTH, _repeated_actions
from training.laya_saved_world import ghost
from training.laya_starters import SAMPLE_HALF, STARTER_ORE

FURNACES_PER_ROW = 6
LINE_ROWS = (1, 3)  # Start+End (6 furnaces); Start-Middle-Middle-End (18 furnaces)
LINE_PITCH = 16  # nominal parallel pitch, also used by the lab's fixed array layout
SITE_STEP = 4  # Shifted column origins remain inside the strip and pass collision checks.
PLATE_RATE = 0.625  # electric furnace plates/s for iron, copper (stone brick likewise)
CHEST_SPARE = 4  # provider chests kept for later lines before a regular (3-chest) tap row is offered
TAP_DEPTH = 4  # output taps sit below the line: inserter, chest, pole rows
TAP_RATE = 0.83  # regular inserter, chest to belt, items/s (planners/recipe_data feeder ceilings are higher)
# Belt-to-chest items/s per tap. Before plastic, provider chests cannot be crafted (they need an
# advanced circuit), so fast taps that empty a line into one or two chests save the kit's chests.
TAP_RATES = {"inserter": TAP_RATE, "fast-inserter": 2.31}
MAX_HAUL = 90  # belt tiles from mine to line


def line_units(recipe: str, rows: int, ox: float, oy: float) -> list[dict]:
    """One column of Start, ``rows - 1`` Middles, and End (basic variant)."""
    actions = list(_repeated_actions("start", 1, origin_x=ox, origin_y=oy, recipe=recipe, variant="basic"))
    for row in range(rows - 1):
        actions += _repeated_actions("middle", 1, origin_x=ox, origin_y=oy + START_DEPTH + row * MIDDLE_PITCH,
                                     recipe=recipe, variant="basic")
    end_y = oy + START_DEPTH + (rows - 1) * MIDDLE_PITCH
    actions += _repeated_actions("end", 1, origin_x=ox, origin_y=end_y, recipe=recipe, variant="basic")
    return actions


def line_depth(rows: int) -> float:
    return START_DEPTH + END_DEPTH + MIDDLE_PITCH * (rows - 1)


def line_output(rows: int, ox: float, oy: float) -> tuple[float, float]:
    """Plate output belt tile of a single-column basic line (smelter_block.refinery_interfaces)."""
    return ox + COLUMN_PITCH + 2.5, oy + START_DEPTH + (rows - 1) * MIDDLE_PITCH + 9.5


def full_taps(rows: int, inserter: str = "inserter") -> int:
    return math.ceil(FURNACES_PER_ROW * rows * PLATE_RATE / TAP_RATES[inserter])


def output_taps(rows: int, ox: float, oy: float, *, taps: int | None = None, inserter: str = "inserter") -> list[dict]:
    """East-running output belt with inserter -> provider taps on its south side (deterministic tap pattern)."""
    x, y = line_output(rows, ox, oy)
    full = full_taps(rows, inserter)
    if taps is None:
        taps = full
    if type(taps) is not int or not 1 <= taps <= full:
        raise ValueError(f"output tap count must be an integer in 1..{full}")
    actions = [ghost("transport-belt", x, y, "east")]
    for i in range(1, taps + 1):
        actions += [ghost("transport-belt", x + i, y, "east"), ghost(inserter, x + i, y + 1, "north"),
                    ghost("passive-provider-chest", x + i, y + 2)]
    # Medium poles under the chests power the tap inserters (supply reaches 3.5 tiles each way).
    actions += [ghost("medium-electric-pole", x + i, y + 3) for i in range(3, taps + 3, 6)]  # each covers taps i-2..i+3
    return actions


def mine_unit(ox: float, oy: float, drills_per_row: int, flow: str = "east") -> tuple[list[dict], tuple[float, float]]:
    """Paired drills on one collector flowing ``flow``; returns actions and the tile just past the collector's head.

    Substations sit above the upper drills, leaving both collector ends free.
    """
    positions = paired_mining_drill_positions((ox, oy), drills_per_row)
    belt_y = oy + 0.5
    actions = [ghost("electric-mining-drill", x, y, "south" if y < belt_y else "north") for x, y in positions]
    first, last = math.floor(ox), math.floor(max(x for x, _ in positions) + 2)  # collector tiles [first, last)
    actions += [ghost("transport-belt", x + 0.5, belt_y, flow) for x in range(first, last)]
    # Substations above the upper drills, one per 18 tiles of row (each supplies 18x18).
    actions += [ghost("substation", x, oy - 4) for x in range(first + 9, last + 9, 18)]
    return actions, ((last + 0.5, belt_y) if flow == "east" else (first - 0.5, belt_y))


def haul(start: tuple[float, float], flow: str, line_x: float, line_y: float) -> list[dict] | None:
    """Along the collector row, one turn, then east into the line input (line_x - 0.5, line_y + 1.5).

    ``start`` is the first tile past the collector head; an east flow must reach the
    turn column from the west, a west flow from the east.
    """
    turn_x, in_y = line_x - 1.5, line_y + 1.5
    sx, sy = start
    if (flow == "east" and turn_x < sx) or (flow == "west" and turn_x > sx):
        return None
    if flow == "east":
        belts = [ghost("transport-belt", x + 0.5, sy, "east") for x in range(math.floor(sx), math.floor(turn_x))]
    else:
        belts = [ghost("transport-belt", x + 0.5, sy, "west") for x in range(math.floor(sx), math.floor(turn_x), -1)]
    if abs(in_y - sy) < 1e-9 and flow == "west":
        return None  # A west flow cannot turn back east on its own row.
    step = 1 if in_y > sy else -1
    vertical = "south" if step > 0 else "north"
    if abs(in_y - sy) > 1e-9:
        belts.append(ghost("transport-belt", turn_x, sy, vertical))
        y = sy + step
        while abs(y - in_y) > 1e-9:
            belts.append(ghost("transport-belt", turn_x, y, vertical))
            y += step
    belts.append(ghost("transport-belt", turn_x, in_y, "east"))
    return belts if len(belts) <= MAX_HAUL else None


def tap_sites(plan: Mapping | None, plate: str) -> list[tuple[float, float]]:
    """Where a 6-furnace line's provider taps would sit in the plate's strip (they need logistic coverage)."""
    # Every smelting zone of the plate: grid land grows a plate into more parcels.
    strips = [z["rect"] for z in (plan or {}).get("zones", []) if z["purpose"] == f"smelt:{plate}"]
    sites = []
    for strip, lx in ((s, lx) for s in strips
                      for lx in range(math.ceil(s[0] + 1), math.floor(s[2] - COLUMN_PITCH - 3) + 1, LINE_PITCH)):
        for ly in range(math.ceil(strip[1]), math.floor(strip[3] - line_depth(1) - TAP_DEPTH) + 1, 8):
            x, y = line_output(1, lx, ly)
            sites.append((x + 3, y + 2))  # Middle of the tap row.
    return sites


def clear_of(actions: list[dict], obstacles: list[Mapping]) -> bool:
    """No action's footprint overlaps an already placed entity (bounding-box prefilter first)."""
    from planners.infrastructure_geometry import boxes_overlap
    from training.laya_power import _size
    xs = [a["position"]["x"] for a in actions]
    ys = [a["position"]["y"] for a in actions]
    near = [o for o in obstacles if min(xs) - 3 <= o["position"]["x"] <= max(xs) + 3
            and min(ys) - 3 <= o["position"]["y"] <= max(ys) + 3]
    return not any(boxes_overlap((a["position"]["x"], a["position"]["y"]), _size(a["entity"]),
                                 (o["position"]["x"], o["position"]["y"]), _size(o["entity"]))
                   for a in actions for o in near)


def off_corridors(actions: list[dict], plan: Mapping | None) -> bool:
    """On grid land, only corridor entities (belts, pipes, poles) may cross the corridor lattice."""
    grid = (plan or {}).get("grid")
    if not grid:
        return True
    from training.laya_land_grid import in_corridor
    from training.laya_power import _size
    from training.laya_site_plan import CORRIDOR_ENTITIES
    for a in actions:
        if a["entity"] in CORRIDOR_ENTITIES:
            continue
        half = _size(a["entity"]) / 2
        x, y = a["position"]["x"], a["position"]["y"]
        if in_corridor(grid, (x - half, y - half, x + half, y + half)):
            return False
    return True


def _reachable(actions: list[dict], ports: list[Mapping], network: str) -> bool:
    """Every entity inside construction range, provider chests inside logistic range, of one network."""
    own = [p for p in ports if p["network"] == network]

    def within(position: Mapping, radius_key: str) -> bool:
        return any(max(abs(position["x"] - p["position"]["x"]), abs(position["y"] - p["position"]["y"]))
                   <= p[radius_key] - 1 for p in own)

    return all(within(a["position"], "construction_radius") for a in actions) and all(
        within(a["position"], "logistic_radius") for a in actions if a["entity"] == "passive-provider-chest")


def _samples(facts: Mapping, ore: str, rect: list[float]) -> list[tuple[float, float]]:
    return [(s["position"]["x"], s["position"]["y"]) for s in facts["resources"] if s["item"] == ore
            and rect[0] <= s["position"]["x"] <= rect[2] and rect[1] <= s["position"]["y"] <= rect[3]]


def line_candidates(facts: Mapping, plan: Mapping | None, plates: set[str], per_plate: int = 40,
                    obstacles: list[Mapping] = (), diagnostics: dict | None = None) -> list[dict]:
    """Mine + haul + line options for each needed plate's smelting strip; the policy chooses which and where."""
    if plan is None:
        return []
    ports = [p for p in facts["ports"] if p["energy"] > 0]
    chests = sum(int((n.get("items") or {}).get("passive-provider-chest", 0)) for n in (facts.get("networks") or {}).values())
    candidates = []
    for plate, ore in STARTER_ORE.items():
        if plate not in plates:
            continue
        counts={}
        if diagnostics is not None:diagnostics[plate]=counts
        def count(stage):counts[stage]=counts.get(stage,0)+1
        strips = [z["rect"] for z in plan["zones"] if z["purpose"] == f"smelt:{plate}"]
        ores = [z["rect"] for z in plan["zones"] if z["purpose"] == f"ore:{ore}"]
        if not strips or not ores:
            count('missing_zone')
            continue
        samples = [p for rect in ores for p in _samples(facts, ore, rect)]
        found = 0
        for rows in LINE_ROWS:
            if found >= per_plate:
                break
            depth = line_depth(rows)
            per_row = FURNACES_PER_ROW * rows // 2  # Drills on each side of the collector, matched to furnaces.
            mines = {}
            for sx, sy in samples:
                ox, oy = math.floor(sx) - 1, math.floor(sy) + 1
                for flow in ("east", "west"):
                    mine, end = mine_unit(ox, oy, per_row, flow)
                    covered = all(any(max(abs(a["position"]["x"] - x), abs(a["position"]["y"] - y)) <= SAMPLE_HALF
                                      for x, y in samples)
                                  for a in mine if a["entity"] == "electric-mining-drill")
                    mines[(sx, sy, flow)] = (ox, oy, mine, end, covered,
                                            covered and clear_of(mine, obstacles) and off_corridors(mine, plan))
            for strip, lx in ((s, lx) for s in strips
                              for lx in range(math.ceil(s[0] + 1), math.floor(s[2] - COLUMN_PITCH - 3) + 1, SITE_STEP)):
                for ly in range(math.ceil(strip[1]), math.floor(strip[3] - depth - TAP_DEPTH) + 1, SITE_STEP):
                    count('line_sites_considered')
                    port = next((p for p in ports if max(abs(lx + 7 - p["position"]["x"]), abs(ly + depth / 2 - p["position"]["y"]))
                                 <= p["construction_radius"] - 6), None)
                    if port is None:
                        count('outside_construction')
                        continue
                    sites = sorted(samples, key=lambda s: abs(s[1] - (ly + 1.5)) + abs(s[0] - lx))
                    furnaces = line_units(plate, rows, lx, ly)
                    if not clear_of(furnaces, obstacles):
                        count('line_body_collision')
                        continue
                    for (sx, sy), flow in ((site, flow) for site in sites for flow in ("east", "west")):
                        ox, oy, mine, end, covered, mine_clear = mines[(sx, sy, flow)]
                        if not covered:
                            count('mine_samples_uncovered')
                            continue
                        if not mine_clear:
                            count('mine_collision')
                            continue
                        route = haul(end, flow, lx, ly)
                        if route is None:
                            count('haul_unavailable')
                            continue
                        # Output capacity is an explicit resource/throughput choice;
                        # fewer taps reduce the bill without changing furnace geometry.
                        # Fast taps (one or two chests) first; the cheapest regular row is the fallback.
                        valid_site = False
                        tap_options = [("fast-inserter", n) for n in sorted({1, full_taps(rows, "fast-inserter")})]
                        regular = math.ceil(full_taps(rows) / 2)
                        if chests >= regular + CHEST_SPARE:  # Chests are finite before plastic; keep some for later lines.
                            tap_options.append(("inserter", regular))
                        for inserter, taps in tap_options:
                            actions = mine + route + furnaces + output_taps(rows, lx, ly, taps=taps, inserter=inserter)
                            if not _reachable(actions, ports, port["network"]):
                                count('outside_network_reach')
                                continue
                            if not clear_of(actions, obstacles):
                                count('placed_collision')
                                continue
                            if not off_corridors(actions, plan):
                                count('grid_corridor')
                                continue
                            built = {"surface": "nauvis", "force": "player", "atomic": True,
                                     "phases": [{"name": f"saved-world-line-{plate}", "actions": actions}]}
                            try:
                                validate_build_plan(built)
                            except ValueError:
                                count('internal_plan_invalid')
                                continue
                            anchor = min((p["position"] for p in ports if p["network"] == port["network"]),
                                         key=lambda q: (q["x"], q["y"]))
                            digest = sha256_hash({"plan": built, "network_anchor": anchor})
                            rate = round(min(FURNACES_PER_ROW * rows * PLATE_RATE, taps * TAP_RATES[inserter]), 3)
                            candidates.append({
                                "option_id": f"line.{digest[7:19]}", "family": "line", "product": plate,
                                "summary": f"{FURNACES_PER_ROW * rows}-furnace {plate} line at ({lx},{ly}) fed by "
                                           f"{2 * per_row} drills at ({ox},{oy}); {taps} {inserter} output taps, estimated {rate:g}/s",
                                "plan": built, "network": port["network"], "network_anchor": anchor,
                                "internal_power": True, "furnaces": FURNACES_PER_ROW * rows,
                                "output_taps": taps, "tap_inserter": inserter, "estimated_output_per_s": rate})
                            found += 1
                            count('candidates')
                            valid_site = True
                            if found >= per_plate:
                                break
                        if valid_site:
                            break  # One mine per line site; both funded output topologies remain choices.
                    if found >= per_plate:
                        break
                if found >= per_plate:
                    break
    return candidates
