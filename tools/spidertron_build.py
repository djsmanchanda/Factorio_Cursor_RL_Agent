# Path: tools/spidertron_build.py
# Purpose: Build a ghost field CENTER-OUT, one concentric ring at a time, so the
#          construction network grows outward from a single hub and never starts in
#          disconnected parts (docs/25_execution_rework_brief.md + the ring redirect:
#          "start with one roboport in the center ... gradually increase the build
#          radius. If it starts construction in multiple parts, it gets out of sync").
#
#          A self-powered construction spidertron (fusion reactor + personal roboports
#          + battery) is the mobile hub: it carries its own power and construction area
#          so each ring builds without depending on the previous ring's power reaching
#          it. Ghosts for ring k+1 are only placed AFTER ring k is fully built, so the
#          build visibly expands from the center.
#
#          Ring/tour geometry is pure and unit-tested (tests/test_spidertron_tour.py);
#          the drive loop runs over RCON against a live server.

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.rcon_client import RconClient, RconError

Point = tuple[float, float]


# ---------------------------------------------------------------------------
# Pure geometry (unit-tested in tests/test_spidertron_tour.py)
# ---------------------------------------------------------------------------

def _axis_coords(lo: float, hi: float, step: float) -> list[float]:
    """Evenly spaced coordinates covering [lo, hi] with spacing <= step. A span that
    already fits within one step collapses to its midpoint, so a small area is served
    from a single stop instead of being needlessly toured (every extra teleport
    interrupts in-flight construction bots)."""
    span = hi - lo
    if span <= step:
        return [(lo + hi) / 2.0]
    intervals = math.ceil(span / step)
    return [lo + i * span / intervals for i in range(intervals + 1)]


def lawnmower_waypoints(
    min_x: float, min_y: float, max_x: float, max_y: float, step: float
) -> list[Point]:
    """Deterministic serpentine tour covering a box; spacing <= step on both axes so
    every tile is within step of a waypoint and consecutive hops stay <= step."""
    if step <= 0:
        raise ValueError("step must be positive")
    if max_x < min_x or max_y < min_y:
        raise ValueError("bounding box max must be >= min on both axes")
    xs = _axis_coords(min_x, max_x, step)
    ys = _axis_coords(min_y, max_y, step)
    waypoints: list[Point] = []
    for row, y in enumerate(ys):
        row_xs = xs if row % 2 == 0 else list(reversed(xs))
        for x in row_xs:
            waypoints.append((float(x), float(y)))
    return waypoints


def concentric_rings(
    positions: Sequence[Point], center: Point, ring_width: float
) -> list[list[Point]]:
    """Partition positions into concentric rings by Chebyshev (square) distance from
    the center, innermost first. Chebyshev distance makes each ring a square shell,
    so a ring of width ~ the roboport construction reach lands entirely within the
    coverage already established by the ring inside it. Positions within each ring are
    sorted deterministically. Empty rings are dropped, but relative ordering (index by
    distance) is preserved so the build always expands outward."""
    if ring_width <= 0:
        raise ValueError("ring_width must be positive")
    cx, cy = center
    buckets: dict[int, list[Point]] = {}
    for x, y in positions:
        chebyshev = max(abs(x - cx), abs(y - cy))
        index = int(chebyshev // ring_width)
        buckets.setdefault(index, []).append((float(x), float(y)))
    rings: list[list[Point]] = []
    for index in sorted(buckets):
        rings.append(sorted(buckets[index]))
    return rings


def square_grid(count: int, spacing: float, center: Point) -> list[Point]:
    """A deterministic square grid of ~count points centered on `center`, used by the
    self-test to synthesize a build field larger than one construction radius."""
    if count <= 0:
        raise ValueError("count must be positive")
    side = math.ceil(math.sqrt(count))
    cx, cy = center
    half = (side - 1) / 2.0
    points: list[Point] = []
    for row in range(side):
        for col in range(side):
            if len(points) >= count:
                break
            points.append((cx + (col - half) * spacing, cy + (row - half) * spacing))
    return points


def ring_bbox(ring: Sequence[Point]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return (min(xs), min(ys), max(xs), max(ys))


# ---------------------------------------------------------------------------
# RCON helpers
# ---------------------------------------------------------------------------

class SpidertronBuildError(RuntimeError):
    pass


def _sc(client: RconClient, lua: str) -> str:
    response = client.command("/sc " + lua)
    text = response.strip()
    if text.startswith("Error") or "Cannot execute command" in text:
        raise SpidertronBuildError(f"RCON /sc failed: {text}")
    return text


def _num(text: str, what: str) -> int:
    try:
        return int(float(text))
    except ValueError as exc:
        raise SpidertronBuildError(f"expected a number for {what}, got {text!r}") from exc


def count_ghosts(client: RconClient, surface: str) -> int:
    lua = (
        f"local s=game.surfaces['{surface}'];"
        "rcon.print(s.count_entities_filtered{name='entity-ghost',force=game.forces.planner})"
    )
    return _num(_sc(client, lua), "ghost count")


def count_entities(client: RconClient, surface: str, name: str) -> int:
    lua = (
        f"local s=game.surfaces['{surface}'];"
        f"rcon.print(s.count_entities_filtered{{name='{name}',force=game.forces.planner}})"
    )
    return _num(_sc(client, lua), f"{name} count")


def electric_network_count(client: RconClient, surface: str, name: str) -> tuple[int, int]:
    """Return (built entity count, distinct electric_network_id count) for `name`."""
    lua = (
        f"local s=game.surfaces['{surface}'];local f=game.forces.planner;local nets={{}};"
        f"local es=s.find_entities_filtered{{name='{name}',force=f}};"
        "for _,e in pairs(es) do local ok,id=pcall(function() return e.electric_network_id end);"
        "if ok and id then nets[id]=true end end;"
        "local n=0;for _ in pairs(nets) do n=n+1 end;rcon.print(#es..' '..n)"
    )
    parts = _sc(client, lua).split()
    if len(parts) != 2:
        raise SpidertronBuildError("malformed electric network response")
    return int(parts[0]), int(parts[1])


def place_ghosts(
    client: RconClient, surface: str, inner_name: str, positions: Sequence[Point], batch: int = 40
) -> int:
    """Place entity ghosts for `inner_name` at the given positions, in batches so no
    single RCON chunk grows unbounded."""
    placed = 0
    for start in range(0, len(positions), batch):
        chunk = positions[start:start + batch]
        pts = ",".join(f"{{{x},{y}}}" for x, y in chunk)
        lua = (
            f"local s=game.surfaces['{surface}'];local f=game.forces.planner;local n=0;"
            f"local pts={{{pts}}};"
            "for _,p in ipairs(pts) do "
            f"if s.create_entity{{name='entity-ghost',inner_name='{inner_name}',position=p,force=f}} "
            "then n=n+1 end end;rcon.print(n)"
        )
        placed += _num(_sc(client, lua), "placed ghost count")
    return placed


def spawn_spidertron(
    client: RconClient, surface: str, position: Point, *,
    roboports: int, batteries: int, bots: int,
) -> int:
    x, y = position
    lua = (
        f"local s=game.surfaces['{surface}'];local f=game.forces.planner;"
        f"local sp=s.create_entity{{name='spidertron',position={{{x},{y}}},force=f}};"
        "if not sp then rcon.print('E:spawn') return end;"
        "local g=sp.grid;"
        "if not g.put{name='fusion-reactor-equipment'} then rcon.print('E:reactor') return end;"
        f"for i=1,{roboports} do g.put{{name='personal-roboport-mk2-equipment'}} end;"
        f"for i=1,{batteries} do g.put{{name='battery-mk2-equipment'}} end;"
        "local t=sp.get_inventory(defines.inventory.spider_trunk);"
        f"t.insert{{name='construction-robot',count={bots}}};"
        "storage._spbuild=sp;rcon.print(sp.unit_number)"
    )
    text = _sc(client, lua)
    if text.startswith("E:"):
        raise SpidertronBuildError(f"spidertron spawn failed: {text}")
    return _num(text, "spidertron unit_number")


def read_construction_radius(client: RconClient, retries: int = 10) -> float:
    for _ in range(retries):
        lua = (
            "local sp=storage._spbuild;"
            "if not (sp and sp.valid) then rcon.print('E:invalid') return end;"
            "local c=sp.logistic_cell;rcon.print(c and c.construction_radius or 'nil')"
        )
        text = _sc(client, lua)
        if text not in ("nil", "E:invalid"):
            return float(text)
        time.sleep(0.3)
    raise SpidertronBuildError("spidertron logistic cell never reported a construction radius")


def _materials_lua(materials: dict[str, int]) -> str:
    if not materials:
        return "{}"
    body = ",".join(f"['{item}']={int(count)}" for item, count in sorted(materials.items()))
    return "{" + body + "}"


def move_and_restock(
    client: RconClient, position: Point, *, bots: int, materials: dict[str, int]
) -> None:
    x, y = position
    lua = (
        "local sp=storage._spbuild;"
        "if not (sp and sp.valid) then rcon.print('E:invalid') return end;"
        f"sp.teleport({{{x},{y}}});"
        "local t=sp.get_inventory(defines.inventory.spider_trunk);"
        f"local have=t.get_item_count('construction-robot');"
        f"if have<{bots} then t.insert{{name='construction-robot',count={bots}-have}} end;"
        f"local mats={_materials_lua(materials)};"
        "for item,target in pairs(mats) do local h=t.get_item_count(item);"
        "if h<target then t.insert{name=item,count=target-h} end end;rcon.print('ok')"
    )
    text = _sc(client, lua)
    if text != "ok":
        raise SpidertronBuildError(f"move/restock failed: {text}")


def count_actionable_in_range(client: RconClient, surface: str) -> int:
    """Ghosts within the spidertron's current construction radius that are genuinely
    buildable right now (can_revive). If this is >0 while nothing is being built, the
    bots are simply slow to dispatch (base tech) -- not a real stall. If it is 0, this
    stop has nothing more to give and the vehicle should move on."""
    lua = (
        f"local s=game.surfaces['{surface}'];local f=game.forces.planner;"
        "local sp=storage._spbuild;if not (sp and sp.valid) then rcon.print('-1') return end;"
        "local cell=sp.logistic_cell;if not cell then rcon.print('0') return end;"
        "local r=cell.construction_radius;local px=sp.position.x;local py=sp.position.y;local n=0;"
        "for _,g in pairs(s.find_entities_filtered{name='entity-ghost',force=f}) do "
        "local dx=g.position.x-px;local dy=g.position.y-py;"
        "if dx*dx+dy*dy<=r*r then "
        "local ok,can=pcall(function() return s.can_place_entity{name=g.ghost_name,"
        "position=g.position,direction=g.direction,force=f,"
        "build_check_type=defines.build_check_type.ghost_revive} end);"
        "if ok and can then n=n+1 end end end;rcon.print(n)"
    )
    return _num(_sc(client, lua), "actionable count")


def cleanup_spidertron(client: RconClient) -> None:
    lua = (
        "local sp=storage._spbuild;if sp and sp.valid then sp.destroy() end;"
        "storage._spbuild=nil;rcon.print('cleaned')"
    )
    _sc(client, lua)


def cleanup_entities(client: RconClient, surface: str, name: str) -> None:
    lua = (
        f"local s=game.surfaces['{surface}'];local f=game.forces.planner;"
        f"for _,e in pairs(s.find_entities_filtered{{name='{name}',force=f}}) do e.destroy() end;"
        f"for _,e in pairs(s.find_entities_filtered{{name='entity-ghost',force=f}}) do e.destroy() end;"
        "rcon.print('ok')"
    )
    _sc(client, lua)


# ---------------------------------------------------------------------------
# Center-out ring build loop
# ---------------------------------------------------------------------------

def _wait_until_stable(
    client: RconClient, surface: str, *, settle_seconds: float, patience: int, max_wait: float
) -> int:
    """Stay at a stop until its in-range ghosts are built. Bot dispatch at base tech is
    slow and variable (seconds to tens of seconds), so we do NOT treat a quiet poll as
    a stall: as long as buildable ghosts remain within range, we keep waiting. We move
    on only when no in-range ghost is buildable (`patience` confirming polls) or after
    `max_wait` seconds as a safety bound. Returns the total remaining ghost count."""
    deadline = time.monotonic() + max_wait
    remaining = count_ghosts(client, surface)
    quiet = 0
    while time.monotonic() < deadline:
        time.sleep(settle_seconds)
        now = count_ghosts(client, surface)
        if now == 0:
            return 0
        if now < remaining:
            remaining = now
            quiet = 0
            continue
        # No progress this poll: is anything here still buildable?
        if count_actionable_in_range(client, surface) > 0:
            quiet = 0  # bots are just slow; keep waiting
            continue
        quiet += 1
        if quiet >= patience:
            return remaining
    return count_ghosts(client, surface)


def build_ring(
    client: RconClient,
    surface: str,
    ring: Sequence[Point],
    *,
    step: float,
    bots: int,
    materials: dict[str, int],
    settle_seconds: float,
    stop_patience: int,
    stop_max_wait: float,
    park_offset: float,
    max_passes: int,
    emit,
) -> bool:
    """Drive the spidertron over one ring, stopping at each waypoint long enough for
    its bots to build every in-range ghost before moving on (moving early strands the
    bots). Repeat the tour until a full pass builds nothing (a real stall) or ghosts
    hit 0. Returns True on completion.

    Each stop is nudged by `park_offset` so the spidertron's collision box (+/-1 tile)
    never sits directly on a ghost -- a ghost under the parked vehicle reports
    can_revive=false and can never be built. Consecutive passes alternate the nudge
    sign so anything shadowed on one pass is clear on the next."""
    min_x, min_y, max_x, max_y = ring_bbox(ring)
    waypoints = lawnmower_waypoints(min_x, min_y, max_x, max_y, step)
    for attempt in range(max_passes):
        pass_start = count_ghosts(client, surface)
        if pass_start == 0:
            return True
        nudge = park_offset if attempt % 2 == 0 else -park_offset
        for wx, wy in waypoints:
            move_and_restock(client, (wx + nudge, wy + nudge), bots=bots, materials=materials)
            if _wait_until_stable(
                client, surface, settle_seconds=settle_seconds,
                patience=stop_patience, max_wait=stop_max_wait,
            ) == 0:
                return True
        pass_end = count_ghosts(client, surface)
        if pass_end >= pass_start:
            emit(f"    stall: a full ring pass built nothing ({pass_end} left)")
            return False
    return count_ghosts(client, surface) == 0


def build_center_out(
    client: RconClient,
    surface: str,
    rings: Sequence[Sequence[Point]],
    inner_name: str,
    center: Point,
    *,
    roboports: int,
    batteries: int,
    bots: int,
    materials: dict[str, int],
    step_factor: float,
    settle_seconds: float,
    stop_patience: int,
    stop_max_wait: float,
    park_offset: float,
    max_passes: int,
    emit=print,
) -> dict:
    """Spawn the mobile hub at the center, then place+build one ring at a time,
    outward. Ring k+1's ghosts are placed only after ring k is fully built."""
    unit = spawn_spidertron(
        client, surface, center, roboports=roboports, batteries=batteries, bots=bots
    )
    radius = read_construction_radius(client)
    step = radius * step_factor
    emit(
        f"mobile hub: spidertron unit={unit} construction_radius={radius:.1f} "
        f"step={step:.1f} rings={len(rings)}"
    )
    per_ring: list[dict] = []
    start_time = time.monotonic()
    stalled = False
    for index, ring in enumerate(rings):
        before = count_ghosts(client, surface)
        placed = place_ghosts(client, surface, inner_name, ring)
        after_place = count_ghosts(client, surface)
        completed = build_ring(
            client, surface, ring, step=step, bots=bots, materials=materials,
            settle_seconds=settle_seconds, stop_patience=stop_patience,
            stop_max_wait=stop_max_wait, park_offset=park_offset,
            max_passes=max_passes, emit=emit,
        )
        after = count_ghosts(client, surface)
        min_x, min_y, max_x, max_y = ring_bbox(ring)
        emit(
            f"  ring {index}: cells={len(ring)} bbox=({min_x:.0f},{min_y:.0f}).."
            f"({max_x:.0f},{max_y:.0f}) placed={placed} "
            f"ghosts {before}->{after_place}(placed)->{after}(built) "
            f"{'ok' if completed else 'STALLED'}"
        )
        per_ring.append({
            "ring": index, "cells": len(ring), "placed": placed,
            "ghosts_before": before, "ghosts_after_place": after_place,
            "ghosts_after_build": after, "completed": completed,
        })
        if not completed:
            stalled = True
            break
    elapsed = time.monotonic() - start_time
    built, networks = electric_network_count(client, surface, inner_name)
    return {
        "rings": per_ring,
        "stalled": stalled,
        "construction_radius": radius,
        "step": step,
        "elapsed_seconds": elapsed,
        "built_entities": built,
        "electric_networks": networks,
        "remaining_ghosts": count_ghosts(client, surface),
    }


def _parse_materials(spec: str | None) -> dict[str, int]:
    if not spec:
        return {}
    materials: dict[str, int] = {}
    for pair in spec.split(","):
        pair = pair.strip()
        if not pair:
            continue
        name, _, count = pair.partition("=")
        materials[name.strip()] = int(count)
    return materials


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a ghost field center-out, one concentric ring at a time, "
        "with a self-powered construction spidertron as the mobile hub."
    )
    parser.add_argument("--rcon-host", default="127.0.0.1")
    parser.add_argument("--rcon-port", type=int, default=27015)
    parser.add_argument("--rcon-password", required=True)
    parser.add_argument("--surface", default="planner-sandbox")
    parser.add_argument("--center", nargs=2, type=float, default=[0.0, 0.0], metavar=("X", "Y"))
    parser.add_argument("--roboports", type=int, default=4)
    parser.add_argument("--batteries", type=int, default=2)
    parser.add_argument("--bots", type=int, default=50)
    parser.add_argument("--ring-width", type=float, default=14.0,
                        help="Ring shell width in tiles (~ one roboport construction reach)")
    parser.add_argument("--step-factor", type=float, default=0.9,
                        help="Waypoint spacing as a fraction of the spidertron radius")
    parser.add_argument("--settle-seconds", type=float, default=0.6,
                        help="Poll interval while bots build at a stop")
    parser.add_argument("--stop-patience", type=int, default=3,
                        help="Confirming polls with no in-range buildable ghost before moving on")
    parser.add_argument("--stop-max-wait", type=float, default=90.0,
                        help="Safety bound (seconds) on how long to wait at one stop")
    parser.add_argument("--max-passes", type=int, default=3,
                        help="Max tours of a single ring before declaring a stall")
    parser.add_argument("--park-offset", type=float, default=None,
                        help="Tiles to nudge each stop off the ghost grid so the "
                        "spidertron's footprint never shadows a ghost "
                        "(default: half the self-test spacing)")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--self-test", type=int, metavar="N",
                        help="Synthesize an N-cell small-electric-pole field and build it center-out")
    parser.add_argument("--self-test-spacing", type=float, default=7.0,
                        help="Pole grid spacing (<= 7.5 so poles join one network)")
    args = parser.parse_args(argv)

    try:
        client = RconClient(args.rcon_host, args.rcon_port, args.rcon_password)
    except (RconError, OSError) as exc:
        print(f"Could not connect to RCON at {args.rcon_host}:{args.rcon_port}: {exc}")
        return 3

    center = (args.center[0], args.center[1])
    result: dict = {}
    try:
        if not args.self_test:
            print("This tool currently drives the center-out self-test; pass --self-test N.")
            return 2

        inner_name = "small-electric-pole"
        materials = {inner_name: max(args.self_test, 100)}
        positions = square_grid(args.self_test, args.self_test_spacing, center)
        rings = concentric_rings(positions, center, args.ring_width)
        park_offset = args.park_offset if args.park_offset is not None else args.self_test_spacing / 2.0
        print(
            f"self-test field: {len(positions)} {inner_name} cells, spacing "
            f"{args.self_test_spacing}, center {center}, {len(rings)} rings "
            f"(width {args.ring_width})"
        )
        result = build_center_out(
            client, args.surface, rings, inner_name, center,
            roboports=args.roboports, batteries=args.batteries, bots=args.bots,
            materials=materials, step_factor=args.step_factor,
            settle_seconds=args.settle_seconds, stop_patience=args.stop_patience,
            stop_max_wait=args.stop_max_wait, park_offset=park_offset, max_passes=args.max_passes,
        )
        print(
            f"RESULT built={result['built_entities']} "
            f"electric_networks={result['electric_networks']} "
            f"remaining_ghosts={result['remaining_ghosts']} stalled={result['stalled']} "
            f"elapsed={result['elapsed_seconds']:.1f}s"
        )
    except SpidertronBuildError as exc:
        print(f"ERROR: {exc}")
        return 2
    finally:
        try:
            if not args.keep:
                cleanup_spidertron(client)
                if args.self_test:
                    cleanup_entities(client, args.surface, "small-electric-pole")
        except SpidertronBuildError as exc:
            print(f"cleanup warning: {exc}")
        client.close()

    ok = bool(result) and result.get("remaining_ghosts") == 0 and not result.get("stalled")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
