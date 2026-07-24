# Path: orchestrator/live_base.py
# Purpose: Observe a real, arbitrary Factorio base (surface/force) over RCON -- what's already built and working, what raw resources are nearby, what's blocking a spot, and where there's clear space to build.

from __future__ import annotations

import math
from dataclasses import dataclass

from tools.rcon_client import RconClient

Point = tuple[float, float]

# Entities safe to auto-clear when they block a planned placement: incidental
# map clutter a player would just chop/mine through without a second thought.
# Never includes anything a force actually built -- those get routed around,
# not bulldozed (see autonomous_builder.py's obstruction handling).
_SAFE_TO_CLEAR_TYPES = {"tree", "simple-entity"}


def _sc(client: RconClient, lua: str) -> str:
    return client.command("/sc " + lua).strip()


@dataclass(frozen=True)
class LineState:
    recipe: str
    machine_count: int
    working_count: int
    output_position: Point | None


def find_line(client: RconClient, surface: str, force: str, recipe: str, machine: str) -> LineState | None:
    """Every machine of `machine` type with `recipe` set, anywhere on the base.

    A recipe counts as "already produced" only when at least one such machine
    exists AND is not sitting idle for a structural reason (no_power,
    no_ingredients, item_ingredient_shortage, etc. all count as "exists but
    not yet verified working" -- callers decide what to do with a struggling
    line; this just reports what's there).
    """
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];"
        "local n=0;local w=0;local ex,ey;"
        "for _,e in pairs(s.find_entities_filtered{name='" + machine + "',force=f}) do "
        "local ok,r=pcall(function() return e.get_recipe() end);"
        "if ok and r and r.name=='" + recipe + "' then n=n+1;"
        "if e.status==defines.entity_status.working then w=w+1 end;"
        "ex=e.position.x;ey=e.position.y end end;"
        "rcon.print(n..' '..w..' '..tostring(ex)..' '..tostring(ey))"
    )
    parts = _sc(client, lua).split()
    count = int(parts[0])
    if count == 0:
        return None
    working = int(parts[1])
    position = (float(parts[2]), float(parts[3])) if parts[2] != "nil" else None
    return LineState(recipe=recipe, machine_count=count, working_count=working, output_position=position)


def nearest_resource(
    client: RconClient, surface: str, resource: str, near: Point, *, search_radius: float = 400.0,
) -> tuple[Point, Point, Point] | None:
    """Nearest tile of `resource` to `near`, plus the bounding box of the
    contiguous patch it belongs to. Returns (nearest_tile, bbox_min, bbox_max),
    or None if nothing of that resource exists within `search_radius`."""
    min_x, min_y = near[0] - search_radius, near[1] - search_radius
    max_x, max_y = near[0] + search_radius, near[1] + search_radius
    lua = (
        "local s=game.surfaces['" + surface + "'];local nx,ny=" + str(near[0]) + "," + str(near[1]) + ";"
        "local best=nil;local bd=1e18;"
        "for _,e in pairs(s.find_entities_filtered{name='" + resource + "',"
        "area={{" + str(min_x) + "," + str(min_y) + "},{" + str(max_x) + "," + str(max_y) + "}}}) do "
        "local d=(e.position.x-nx)^2+(e.position.y-ny)^2;if d<bd then bd=d;best=e.position end end;"
        "if not best then rcon.print('NONE') return end;"
        "local patch=s.find_entities_filtered{name='" + resource + "',"
        "area={{best.x-40,best.y-40},{best.x+40,best.y+40}}};"
        "local minx,miny,maxx,maxy=best.x,best.y,best.x,best.y;"
        "for _,e in pairs(patch) do minx=math.min(minx,e.position.x);miny=math.min(miny,e.position.y);"
        "maxx=math.max(maxx,e.position.x);maxy=math.max(maxy,e.position.y) end;"
        "rcon.print(best.x..' '..best.y..' '..minx..' '..miny..' '..maxx..' '..maxy)"
    )
    raw = _sc(client, lua)
    if raw == "NONE":
        return None
    x, y, minx, miny, maxx, maxy = (float(part) for part in raw.split())
    return (x, y), (minx, miny), (maxx, maxy)


def drill_footprints_have_resource(
    client: RconClient, surface: str, resource: str, centres: list[Point],
) -> bool:
    """True only when every 3x3 electric-drill footprint overlaps `resource`.

    Resource entities do not block construction, so a clear staging box alone
    cannot prove a drill can mine. This probes the live target resource for
    each planned drill before a BuildPlan is submitted.
    """
    if not centres:
        raise ValueError("at least one drill centre is required")
    checks = ";".join(
        "local n=#s.find_entities_filtered{name='" + resource + "',type='resource',area={{" +
        str(x - 1.5) + "," + str(y - 1.5) + "},{" + str(x + 1.5) + "," + str(y + 1.5) + "}}};" +
        "out[#out+1]=(n>0 and '1' or '0')"
        for x, y in centres
    )
    lua = "local s=game.surfaces['" + surface + "'];local out={};" + checks + ";rcon.print(table.concat(out,''))"
    return _sc(client, lua) == "1" * len(centres)

def area_clear(client: RconClient, surface: str, min_point: Point, max_point: Point) -> bool:
    """True iff no BUILDABLE-conflicting entities (any force, excluding ore/
    resource patches -- a mining stage needs to stand ON ore, not avoid it;
    Factorio itself only blocks placement on water, not on resource tiles)
    and no water tiles occupy the box."""
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "local n=0;for _,e in pairs(s.find_entities_filtered{area={{" + str(min_point[0]) + "," + str(min_point[1]) + "},"
        "{" + str(max_point[0]) + "," + str(max_point[1]) + "}}}) do if e.type~='resource' then n=n+1 end end;"
        "local bad_tiles=0;"
        "for x=" + str(math.floor(min_point[0])) + "," + str(math.ceil(max_point[0])) + " do "
        "for y=" + str(math.floor(min_point[1])) + "," + str(math.ceil(max_point[1])) + " do "
        "local t=s.get_tile(x,y);if not t.valid or t.name:find('water') then bad_tiles=bad_tiles+1 end end end;"
        "rcon.print(n..' '..bad_tiles)"
    )
    entity_count, bad_tiles = (int(part) for part in _sc(client, lua).split())
    return entity_count == 0 and bad_tiles == 0


def find_clear_area(
    client: RconClient, surface: str, near: Point, width: float, height: float, *,
    max_radius: float = 200.0, step: float = 10.0,
) -> Point | None:
    """Nearest clear box (as its min-corner) big enough for `width` x `height`,
    searching outward from `near` in a deterministic expanding ring."""
    candidates: list[tuple[float, Point]] = []
    radius = 0.0
    while radius <= max_radius:
        for dx in _ring_offsets(radius):
            for dy in _ring_offsets(radius):
                if radius > 0 and max(abs(dx), abs(dy)) != radius:
                    continue
                candidate = (near[0] + dx, near[1] + dy)
                candidates.append((math.hypot(dx, dy), candidate))
        radius += step
        if len(candidates) > 40:
            break
    for _, candidate in sorted(candidates, key=lambda item: item[0]):
        max_point = (candidate[0] + width, candidate[1] + height)
        if area_clear(client, surface, candidate, max_point):
            return candidate
    return None


def _ring_offsets(radius: float) -> list[float]:
    return [0.0] if radius == 0 else [-radius, radius]


def entity_at(client: RconClient, surface: str, position: Point) -> dict | None:
    """The single entity (if any) occupying `position`, with enough detail to
    decide whether it's safe to auto-clear (see is_safe_to_clear)."""
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "local e=s.find_entities_filtered{position={" + str(position[0]) + "," + str(position[1]) + "},"
        "radius=0.4,limit=1}[1];"
        "if not e then rcon.print('NONE') return end;"
        "rcon.print(e.name..' '..e.type..' '..(e.force and e.force.name or 'neutral'))"
    )
    raw = _sc(client, lua)
    if raw == "NONE":
        return None
    name, entity_type, force = raw.split(" ", 2)
    return {"name": name, "type": entity_type, "force": force}


def is_safe_to_clear(entity: dict) -> bool:
    """Map clutter (trees, rocks) -- never anything a force actually built."""
    return entity["type"] in _SAFE_TO_CLEAR_TYPES and entity["force"] == "neutral"


def remove_entity_at(client: RconClient, surface: str, position: Point) -> bool:
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "local e=s.find_entities_filtered{position={" + str(position[0]) + "," + str(position[1]) + "},"
        "radius=0.4,limit=1}[1];"
        "if e then e.destroy();rcon.print('OK') else rcon.print('NONE') end"
    )
    return _sc(client, lua) == "OK"


def entity_status_name(client: RconClient, surface: str, position: Point) -> str | None:
    """Decode an entity's live `.status` (e.g. "working", "no_power",
    "no_ingredients") for stuck-diagnosis -- never guess from symptoms alone."""
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "local e=s.find_entities_filtered{position={" + str(position[0]) + "," + str(position[1]) + "},"
        "radius=0.4,limit=1}[1];"
        "if not e then rcon.print('NONE') return end;"
        "local st=e.status;for k,v in pairs(defines.entity_status) do if v==st then rcon.print(k) return end end;"
        "rcon.print('unknown_status_'..tostring(st))"
    )
    raw = _sc(client, lua)
    return None if raw == "NONE" else raw


def pole_network_id(client: RconClient, surface: str, position: Point) -> int | None:
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "local e=s.find_entities_filtered{position={" + str(position[0]) + "," + str(position[1]) + "},"
        "type='electric-pole',radius=0.5,limit=1}[1];"
        "if not e then rcon.print('NONE') return end;"
        "local ok,id=pcall(function() return e.electric_network_id end);"
        "rcon.print(ok and tostring(id) or 'NONE')"
    )
    raw = _sc(client, lua)
    return None if raw in ("NONE", "nil") else int(raw)


def nearest_pole_on_other_network(
    client: RconClient, surface: str, force: str, near: Point, exclude_network_id: int,
) -> tuple[Point, str] | None:
    """Nearest existing pole/substation NOT on `exclude_network_id` -- i.e. a
    real bridge point into a different (presumably the main) power network."""
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];"
        "local nx,ny=" + str(near[0]) + "," + str(near[1]) + ";"
        "local best=nil;local bd=1e18;local bname=nil;"
        "for _,e in pairs(s.find_entities_filtered{type='electric-pole',force=f}) do "
        "local ok,id=pcall(function() return e.electric_network_id end);"
        "if ok and id and id~=" + str(exclude_network_id) + " then "
        "local d=(e.position.x-nx)^2+(e.position.y-ny)^2;"
        "if d<bd then bd=d;best=e.position;bname=e.name end end end;"
        "if not best then rcon.print('NONE') return end;"
        "rcon.print(best.x..' '..best.y..' '..bname)"
    )
    raw = _sc(client, lua)
    if raw == "NONE":
        return None
    x, y, name = raw.split(" ", 2)
    return (float(x), float(y)), name


def nearest_roboport(client: RconClient, surface: str, force: str, near: Point) -> Point | None:
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];"
        "local nx,ny=" + str(near[0]) + "," + str(near[1]) + ";"
        "local best=nil;local bd=1e18;"
        "for _,e in pairs(s.find_entities_filtered{name='roboport',force=f}) do "
        "local d=(e.position.x-nx)^2+(e.position.y-ny)^2;if d<bd then bd=d;best=e.position end end;"
        "if not best then rcon.print('NONE') return end;"
        "rcon.print(best.x..' '..best.y)"
    )
    raw = _sc(client, lua)
    if raw == "NONE":
        return None
    x, y = raw.split()
    return (float(x), float(y))


def chest_contents(client: RconClient, surface: str, position: Point) -> dict[str, int]:
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "local c=s.find_entities_filtered{position={" + str(position[0]) + "," + str(position[1]) + "},"
        "radius=0.5,limit=1}[1];"
        "if not c then rcon.print('NONE') return end;"
        "local inv=c.get_inventory(defines.inventory.chest);local out={};"
        "for _,it in pairs(inv.get_contents()) do out[#out+1]=it.name..'='..it.count end;"
        "rcon.print(table.concat(out,','))"
    )
    raw = _sc(client, lua)
    if raw in ("NONE", ""):
        return {}
    return {pair.split("=")[0]: int(pair.split("=")[1]) for pair in raw.split(",") if pair}


def bot_and_power_summary(client: RconClient, surface: str, force: str) -> dict:
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];"
        "local rp=s.find_entities_filtered{name='roboport',force=f}[1];"
        "local net=rp and rp.logistic_network;"
        "local nets={};for _,e in pairs(s.find_entities_filtered{type='electric-pole',force=f}) do "
        "local ok,id=pcall(function() return e.electric_network_id end);if ok and id then nets[id]=true end end;"
        "local nc=0;for _ in pairs(nets) do nc=nc+1 end;"
        "rcon.print((net and net.all_logistic_robots or 0)..' '..(net and net.all_construction_robots or 0)..' '..nc)"
    )
    logistic, construction, networks = (int(part) for part in _sc(client, lua).split())
    return {"logistic_bots": logistic, "construction_bots": construction, "electric_networks": networks}
