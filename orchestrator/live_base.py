# Path: orchestrator/live_base.py
# Purpose: Observe a real, arbitrary Factorio base (surface/force) over RCON -- what's already built and working, what raw resources are nearby, what's blocking a spot, and where there's clear space to build.

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from orchestrator import resource_patches
from planners.recipe_data import DRILL_MINING_AREAS, drill_mining_reach
from tools.rcon_client import RconClient

Point = tuple[float, float]

# Entities safe to auto-clear when they block a planned placement: incidental
# map clutter a player would just chop/mine through without a second thought.
# Never includes anything a force actually built -- those get routed around,
# not bulldozed (see autonomous_builder.py's obstruction handling).
_SAFE_TO_CLEAR_TYPES = {"tree", "simple-entity"}


def _sc(client: RconClient, lua: str) -> str:
    return client.command("/sc " + lua).strip()


def game_tick(client: RconClient) -> int:
    """Current simulation time for deterministic scheduling and task aging."""
    return int(_sc(client, "rcon.print(game.tick)"))


@dataclass(frozen=True)
class LineState:
    recipe: str
    machine_count: int
    working_count: int
    output_position: Point | None
    machine_positions: tuple[Point, ...] = ()


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
        "local n=0;local w=0;local pos={};"
        "local machines=s.find_entities_filtered{name='" + machine + "',force=f};"
        "for _,g in pairs(s.find_entities_filtered{type='entity-ghost',force=f}) do "
        "if g.ghost_name=='" + machine + "' then table.insert(machines,g) end end;"
        "for _,e in pairs(machines) do "
        "local ok,r=pcall(function() return e.get_recipe() end);"
        "if ok and r and r.name=='" + recipe + "' then n=n+1;"
        "if e.type~='entity-ghost' and e.status==defines.entity_status.working then w=w+1 end;"
        "pos[#pos+1]=e.position.x..':'..e.position.y end end;"
        "rcon.print(n..' '..w..' '..table.concat(pos,','))"
    )
    parts = _sc(client, lua).split(" ", 2)
    count = int(parts[0])
    if count == 0:
        return None
    working = int(parts[1])
    machines = tuple(
        (float(pair.split(":")[0]), float(pair.split(":")[1]))
        for pair in (parts[2] if len(parts) > 2 else "").split(",") if pair
    )
    return LineState(
        recipe=recipe, machine_count=count, working_count=working,
        output_position=machines[-1] if machines else None,
        machine_positions=machines,
    )


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



def drill_siting_conflicts(
    client: RconClient, surface: str, resource: str, centres: list[Point],
    *, drill: str = "electric-mining-drill",
) -> list[tuple[Point, str]]:
    """Every planned centre that cannot cleanly mine `resource`, and why.

    Two separate faults, both fatal to a mine:

    * nothing to mine -- resource entities do not block construction, so a clear
      staging box alone cannot prove a drill has ore under it.
    * MIXED ore -- a drill reaches beyond its own footprint (5x5 for the
      electric drill, 13x13 for the big one). Sited near where two patches
      touch, a drill standing entirely on iron still reaches the copper next to
      it, mines both, and jams its output with a second item that nothing
      downstream accepts.

    One query for every centre; an empty list means the whole row is safe.
    """
    if not centres:
        raise ValueError("at least one drill centre is required")
    reach = drill_mining_reach(drill)
    checks = ";".join(
        "local t=#s.find_entities_filtered{name='" + resource + "',type='resource',"
        "area={{" + str(x - 1.5) + "," + str(y - 1.5) + "},{"
        + str(x + 1.5) + "," + str(y + 1.5) + "}}};"
        "local f='';for _,e in pairs(s.find_entities_filtered{type='resource',"
        "area={{" + str(x - reach) + "," + str(y - reach) + "},{"
        + str(x + reach) + "," + str(y + reach) + "}}}) do "
        "if e.name~='" + resource + "' then f=e.name end end;"
        "out[#out+1]=t..','..f"
        for x, y in centres
    )
    lua = (
        "local s=game.surfaces['" + surface + "'];local out={};" + checks
        + ";rcon.print(table.concat(out,';'))"
    )
    replies = _sc(client, lua).split(";")
    conflicts: list[tuple[Point, str]] = []
    for centre, reply in zip(centres, replies):
        target, _, foreign = reply.partition(",")
        if int(target or 0) <= 0:
            conflicts.append((centre, f"no {resource} under the drill"))
        elif foreign:
            conflicts.append((centre, (
                f"its {DRILL_MINING_AREAS[drill]}x{DRILL_MINING_AREAS[drill]} mining "
                f"area also reaches {foreign}, which would jam the output"
            )))
    return conflicts


def drill_footprints_have_resource(
    client: RconClient, surface: str, resource: str, centres: list[Point],
    *, drill: str = "electric-mining-drill",
) -> bool:
    """True only when every drill mines `resource` AND nothing else.

    Kept as the boolean gate every siting search already calls, so the
    mixed-ore rule cannot be forgotten by a future caller: see
    drill_siting_conflicts for the faults it rejects.
    """
    return not drill_siting_conflicts(
        client, surface, resource, centres, drill=drill,
    )


def area_clear(
    client: RconClient, surface: str, min_point: Point, max_point: Point, *,
    avoid_resources: bool = False, resource_clearance: float = 0.0,
) -> bool:
    """Whether a box is buildable, optionally with an ore-free apron."""
    if resource_clearance < 0:
        raise ValueError("resource_clearance cannot be negative")
    resource_min = (
        min_point[0] - resource_clearance, min_point[1] - resource_clearance,
    )
    resource_max = (
        max_point[0] + resource_clearance, max_point[1] + resource_clearance,
    )
    resource_scan = (
        "local resources=#s.find_entities_filtered{type='resource',area={{" +
        str(resource_min[0]) + "," + str(resource_min[1]) + "},{" +
        str(resource_max[0]) + "," + str(resource_max[1]) + "}}};"
        if avoid_resources else "local resources=0;"
    )
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "local n=0;for _,e in pairs(s.find_entities_filtered{area={{" +
        str(min_point[0]) + "," + str(min_point[1]) + "},{" +
        str(max_point[0]) + "," + str(max_point[1]) +
        "}}}) do if e.type~='resource' then n=n+1 end end;" + resource_scan +
        "local bad_tiles=0;"
        "for x=" + str(math.floor(min_point[0])) + "," + str(math.ceil(max_point[0])) + " do "
        "for y=" + str(math.floor(min_point[1])) + "," + str(math.ceil(max_point[1])) + " do "
        "local t=s.get_tile(x,y);if not t.valid or t.name:find('water') then bad_tiles=bad_tiles+1 end end end;"
        "rcon.print(n..' '..bad_tiles..' '..resources)"
    )
    counts = (int(part) for part in _sc(client, lua).split())
    return all(count == 0 for count in counts)


def find_clear_area(
    client: RconClient, surface: str, near: Point, width: float, height: float, *,
    max_radius: float = 200.0, step: float = 10.0,
    avoid_resources: bool = False,
    resource_clearance: float = 0.0,
) -> Point | None:
    """Find the nearest deterministic clear box satisfying land reservations.

    Every terrain question is answered from two region-wide queries made once
    up front -- occupancy (entities, water, and clutter) and, when reserving
    mining land, where resources are at all -- so candidate evaluation is pure
    set arithmetic. Probing each candidate over its own RCON round-trip instead
    made siting cost one blocking game round-trip per rejected position, and
    every one of those answers was already contained in the region scan.

    ``box_has_reserved_patch`` is the one probe that still runs per candidate,
    because whether a patch is worth reserving depends on the whole patch's
    remaining amount rather than on anything visible in the box. It is asked
    only of candidates that actually overlap a resource tile; an ore-free box
    cannot touch a patch, and siting with ``avoid_resources`` is looking for
    ore-free land, so in practice it is asked rarely or never.
    """
    candidates: list[tuple[float, Point]] = []
    radius = 0.0
    while radius <= max_radius:
        for dx in _ring_offsets(radius):
            for dy in _ring_offsets(radius):
                if radius > 0 and max(abs(dx), abs(dy)) != radius:
                    continue
                candidates.append((math.hypot(dx, dy), (near[0] + dx, near[1] + dy)))
        radius += step
        if len(candidates) > 40:
            break
    if not candidates:
        return None
    ordered = [candidate for _distance, candidate in sorted(candidates, key=lambda item: item[0])]
    region_min = (min(c[0] for c in ordered), min(c[1] for c in ordered))
    region_max = (max(c[0] for c in ordered) + width, max(c[1] for c in ordered) + height)
    occupied = occupied_tiles(
        client, surface, region_min, region_max, include_clutter=True,
    )
    # One tile wider than any apron a candidate can ask about, so the per-box
    # margin below can never look outside what this scan actually covered.
    reserved_region = (
        resource_patches.resource_tiles(
            client, surface,
            (region_min[0] - resource_clearance - 1, region_min[1] - resource_clearance - 1),
            (region_max[0] + resource_clearance + 1, region_max[1] + resource_clearance + 1),
        )
        if avoid_resources else set()
    )
    for candidate in ordered:
        candidate_max = (candidate[0] + width, candidate[1] + height)
        box = _box_tiles(candidate, candidate_max)
        if box & occupied:
            continue
        if avoid_resources:
            reserved_box = (
                candidate[0] - resource_clearance, candidate[1] - resource_clearance,
            )
            reserved_box_max = (
                candidate_max[0] + resource_clearance, candidate_max[1] + resource_clearance,
            )
            if _box_tiles(reserved_box, reserved_box_max, margin=1) & reserved_region:
                if resource_patches.box_has_reserved_patch(
                    client, surface, reserved_box, reserved_box_max,
                ):
                    continue
        return candidate
    return None


def _box_tiles(
    min_point: Point, max_point: Point, *, margin: int = 0,
) -> set[tuple[int, int]]:
    """Tile indices a box covers, matching occupied_tiles' floor-based indexing.

    ``margin`` widens the result by whole tiles. Callers using this to decide
    whether an exact check is WORTH MAKING pass margin=1: a resource entity
    centred just outside a box still overlaps it, and floor-indexed tiles would
    otherwise miss that. Over-including only costs the exact check it guards.
    """
    return {
        (x, y)
        for x in range(math.floor(min_point[0]) - margin, math.ceil(max_point[0]) + margin)
        for y in range(math.floor(min_point[1]) - margin, math.ceil(max_point[1]) + margin)
    }


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



def logistic_request_total(
    client: RconClient, surface: str, force: str, item: str,
) -> int:
    """Total requested count for one item across requester and buffer chests."""
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];"
        "local total=0;for _,e in pairs(s.find_entities_filtered{"
        "name={'requester-chest','buffer-chest'},force=f}) do "
        "local ok,sections=pcall(function() return e.get_logistic_sections() end);"
        "if ok and sections then for _,section in pairs(sections.sections) do "
        "for i=1,section.filters_count do local slot=section.get_slot(i);"
        "if slot and slot.value then local name=slot.value.name or slot.value;"
        "if name=='" + item + "' then total=total+(tonumber(slot.min) or 0) end end end end end end;"
        "rcon.print(math.ceil(total))"
    )
    return int(_sc(client, lua))


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


# A find_entities_filtered position+radius query matches an entity's CENTRE, not
# its footprint, so a lookup by planned position misses a pole the game centred
# elsewhere. A 2x2 substation planned on a .5 coordinate lands 0.707 tiles away;
# poles are >=2 tiles apart in every layout here, so 1.5 resolves the snap
# without ever reaching a neighbouring pole -- and the nearest match is taken
# rather than an arbitrary one.
_POLE_LOOKUP_RADIUS = 1.5


def pole_network_id(client: RconClient, surface: str, position: Point) -> int | None:
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "local px,py=" + str(position[0]) + "," + str(position[1]) + ";"
        "local best,bd=nil,1e18;"
        "for _,e in pairs(s.find_entities_filtered{position={px,py},"
        "type='electric-pole',radius=" + str(_POLE_LOOKUP_RADIUS) + "}) do "
        "local d=(e.position.x-px)^2+(e.position.y-py)^2;"
        "if d<bd then bd=d;best=e end end;"
        "if not best then rcon.print('NONE') return end;"
        "local ok,id=pcall(function() return best.electric_network_id end);"
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


# Entity types that actually FEED an electric network. An accumulator is
# deliberately absent: it only stores what a source already produced, so a
# network holding nothing else generates nothing.
_GENERATOR_TYPES = (
    "'electric-energy-interface','generator','solar-panel',"
    "'burner-generator','fusion-generator'"
)


def nearest_powered_pole(
    client: RconClient, surface: str, force: str, near: Point,
    exclude_network_id: int | None = None,
) -> tuple[Point, str] | None:
    """Nearest pole belonging to a network that has real generation on it.

    Bridging to merely a DIFFERENT network is not enough and silently wastes a
    remediation round: the nearest other network is often a stage's own local
    substation island, which has no source on it either, so the consumer stays
    unpowered after a bridge that looked successful.
    """
    exclusion = (
        "" if exclude_network_id is None
        else "if id==" + str(exclude_network_id) + " then goto continue end;"
    )
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];"
        "local nx,ny=" + str(near[0]) + "," + str(near[1]) + ";"
        "local powered={};"
        "for _,e in pairs(s.find_entities_filtered{type={" + _GENERATOR_TYPES + "}}) do "
        "local ok,id=pcall(function() return e.electric_network_id end);"
        "if ok and id then powered[id]=true end end;"
        "local best,bd,bname=nil,1e18,nil;"
        "for _,e in pairs(s.find_entities_filtered{type='electric-pole',force=f}) do "
        "local ok,id=pcall(function() return e.electric_network_id end);"
        "if ok and id and powered[id] then "
        + exclusion +
        "local d=(e.position.x-nx)^2+(e.position.y-ny)^2;"
        "if d<bd then bd=d;best=e.position;bname=e.name end end;"
        "::continue:: end;"
        "if not best then rcon.print('NONE') return end;"
        "rcon.print(best.x..' '..best.y..' '..bname)"
    )
    raw = _sc(client, lua)
    if raw == "NONE":
        return None
    x, y, name = raw.split(" ", 2)
    return (float(x), float(y)), name


def occupied_tiles(
    client: RconClient, surface: str, min_point: Point, max_point: Point,
    *, ignore_names: Sequence[str] = (), include_resources: bool = False,
    include_clutter: bool = False,
) -> set[tuple[int, int]]:
    """Every tile index inside the box that a route may not occupy.

    Resources stay traversable for belts/pipes by default; callers may opt in
    when a placement policy reserves mining land.

    Trees and rocks are likewise traversable by default -- a route may cross
    them because construction clears them (see _SAFE_TO_CLEAR_TYPES). A caller
    SITING a new area rather than routing through one wants them counted, since
    an area is chosen once and clutter there is work; `include_clutter` says so.

    This is what turns a naive L-route into one that goes around real
    infrastructure instead of demanding it be bulldozed.

    TERRAIN counts as occupancy too. A route is unbuildable on water whether or
    not anything stands there, and a ghost on water is worse than a rejected
    route: bots accept it, never build it, and the stage reports "bots still
    working" until its rounds run out. Observed live: one fast-transport-belt
    ghost at (-38.5, 44.5) on a water tile stalled a whole science stage.
    Water is therefore blocked unconditionally -- unlike ore, nothing can be
    built on it and no caller ever wants to route through it.
    """
    ignored = "{" + ",".join("['" + name + "']=true" for name in ignore_names) + "}"
    resource_check = "" if include_resources else "e.type~='resource' and "
    clutter_check = (
        "" if include_clutter else
        "not (e.force and e.force.name=='neutral' and "
        "(e.type=='tree' or e.type=='simple-entity')) and "
    )
    lua = (
        "local s=game.surfaces['" + surface + "'];local out={};"
        "local ignored=" + ignored + ";"
        "local area={{" + str(min_point[0]) + "," + str(min_point[1]) + "},"
        "{" + str(max_point[0]) + "," + str(max_point[1]) + "}};"
        "for _,t in pairs(s.find_tiles_filtered{area=area,collision_mask='water_tile'}) do "
        "out[#out+1]=t.position.x..','..t.position.y end;"
        "for _,e in pairs(s.find_entities_filtered{area=area}) do "
        "if " + resource_check + "e.type~='character' and "
        + clutter_check + "not ignored[e.name] then "
        "local b=e.bounding_box;"
        "for x=math.floor(b.left_top.x),math.ceil(b.right_bottom.x)-1 do "
        "for y=math.floor(b.left_top.y),math.ceil(b.right_bottom.y)-1 do "
        "out[#out+1]=x..','..y end end end end;"
        "rcon.print(table.concat(out,';'))"
    )
    raw = _sc(client, lua)
    tiles: set[tuple[int, int]] = set()
    for pair in raw.split(";"):
        if not pair:
            continue
        x, _, y = pair.partition(",")
        tiles.add((int(x), int(y)))
    return tiles


def available_items(client: RconClient, surface: str, force: str) -> dict[str, int]:
    """Everything the force is holding in containers on this surface.

    This is the real build budget: construction bots can only revive a ghost
    from material that exists somewhere they can reach. Planning against an
    item the base does not have produces ghosts that sit forever -- observed
    live when fast-transport-belt ran to zero mid-build.
    """
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];local t={};"
        "for _,c in pairs(s.find_entities_filtered{force=f,type={'container','logistic-container'}}) do "
        "local inv=c.get_inventory(defines.inventory.chest);"
        "if inv then for _,it in pairs(inv.get_contents()) do "
        "t[it.name]=(t[it.name] or 0)+it.count end end end;"
        "local o={};for n,c in pairs(t) do o[#o+1]=n..'='..c end;rcon.print(table.concat(o,','))"
    )
    raw = _sc(client, lua)
    if not raw:
        return {}
    counts: dict[str, int] = {}
    for pair in raw.split(","):
        if not pair:
            continue
        name, _, count = pair.partition("=")
        counts[name] = int(count)
    return counts


def roboports_needing_power(
    client: RconClient, surface: str, force: str,
) -> list[tuple[Point, str]]:
    """Built roboports that cannot yet provide reliable construction/logistics."""
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];"
        "local names={};for k,v in pairs(defines.entity_status) do names[v]=k end;"
        "local powered={};for _,g in pairs(s.find_entities_filtered{type={"
        "'electric-energy-interface','generator','solar-panel','burner-generator',"
        "'fusion-generator'}}) do local ok,id=pcall(function() return g.electric_network_id end);"
        "if ok and id then powered[id]=true end end;"
        "local out={};for _,e in pairs(s.find_entities_filtered{name='roboport',force=f}) do "
        "local status=names[e.status];local ok,id=pcall(function() return e.electric_network_id end);"
        "if status=='no_power' or (status=='low_power' and not (ok and id and powered[id])) then "
        "out[#out+1]=e.position.x..','..e.position.y..','..status end end;"
        "rcon.print(table.concat(out,';'))"
    )
    raw = _sc(client, lua)
    result = []
    for record in raw.split(";"):
        if record:
            x, y, status = record.split(",")
            result.append(((float(x), float(y)), status))
    return result

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


def nearest_container(
    client: RconClient, surface: str, force: str, near: Point,
    names: Sequence[str] = ("passive-provider-chest", "steel-chest"),
) -> Point | None:
    """Nearest output chest to a line's machines.

    A pre-existing line is only usable as an ingredient source if its
    collection chest can be found; find_line reports machines, not chests.
    """
    literal = ",".join("'" + n + "'" for n in names)
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];"
        "local nx,ny=" + str(near[0]) + "," + str(near[1]) + ";local best;local bd=1e18;"
        "for _,e in pairs(s.find_entities_filtered{name={" + literal + "},force=f}) do "
        "local d=(e.position.x-nx)^2+(e.position.y-ny)^2;"
        "if d<bd then bd=d;best=e.position end end;"
        "if not best then rcon.print('NONE') return end;rcon.print(best.x..' '..best.y)"
    )
    raw = _sc(client, lua)
    if raw == "NONE":
        return None
    x, y = raw.split()
    return (float(x), float(y))


def entity_statuses(
    client: RconClient, surface: str, positions: Sequence[Point],
) -> dict[Point, str]:
    """Decoded `.status` for many entities in ONE round trip.

    Diagnosis polls every machine in a stage repeatedly; one query per machine
    per poll was ~50ms each and scaled with stage size for no reason.
    """
    if not positions:
        return {}
    literal = ",".join("{" + str(p[0]) + "," + str(p[1]) + "}" for p in positions)
    lua = (
        "local s=game.surfaces['" + surface + "'];local out={};"
        "local names={};for k,v in pairs(defines.entity_status) do names[v]=k end;"
        "for i,p in ipairs({" + literal + "}) do "
        "local e=s.find_entities_filtered{position=p,radius=0.4,limit=1}[1];"
        "out[#out+1]=i..'='..(e and (names[e.status] or 'unknown') or 'missing') end;"
        "rcon.print(table.concat(out,','))"
    )
    raw = _sc(client, lua)
    statuses: dict[Point, str] = {}
    for pair in raw.split(","):
        if not pair:
            continue
        index, _, status = pair.partition("=")
        statuses[tuple(positions[int(index) - 1])] = status
    return statuses


def progress_counters(
    client: RconClient, surface: str, positions: Sequence[Point],
) -> dict[Point, float]:
    """A per-machine number that MOVES while the machine is doing its job.

    Status alone cannot separate "correctly built and waiting for its first
    item" from "starved because the feed is broken" -- both read
    no_ingredients. Progress can: a machine that has produced anything, or is
    part-way through a craft, is demonstrably fed. Preferring an observation to
    a timer is also what keeps the health check independent of belt tier, route
    length and craft time, none of which a fixed grace window can track.

    `products_finished` (crafting machines) is monotonic; `mining_progress` and
    `crafting_progress` are cyclic, so callers must treat any CHANGE as
    progress rather than only an increase. Entities exposing none of these are
    omitted and stay judged on status alone.
    """
    if not positions:
        return {}
    literal = ",".join("{" + str(p[0]) + "," + str(p[1]) + "}" for p in positions)
    lua = (
        "local s=game.surfaces['" + surface + "'];local out={};"
        "for i,p in ipairs({" + literal + "}) do "
        "local e=s.find_entities_filtered{position=p,radius=0.4,limit=1}[1];"
        "if e then "
        "local v=nil;"
        "local ok,n=pcall(function() return e.products_finished end);"
        "if ok and n then v=n*1000 end;"
        "local ok2,m=pcall(function() return e.mining_progress end);"
        "if ok2 and m then v=(v or 0)+m end;"
        "local ok3,c=pcall(function() return e.crafting_progress end);"
        "if ok3 and c then v=(v or 0)+c end;"
        "if v then out[#out+1]=i..'='..string.format('%.4f',v) end "
        "end end;"
        "rcon.print(table.concat(out,','))"
    )
    raw = _sc(client, lua)
    counters: dict[Point, float] = {}
    for pair in raw.split(","):
        if not pair:
            continue
        index, _, value = pair.partition("=")
        counters[tuple(positions[int(index) - 1])] = float(value)
    return counters


def machine_health(
    client: RconClient, surface: str, positions: Sequence[Point],
) -> tuple[dict[Point, str], dict[Point, float]]:
    """Read machine status and progress for a whole stage in one RCON call."""
    if not positions:
        return {}, {}
    literal = ",".join("{" + str(p[0]) + "," + str(p[1]) + "}" for p in positions)
    lua = (
        "local s=game.surfaces['" + surface + "'];local out={};"
        "local names={};for k,v in pairs(defines.entity_status) do names[v]=k end;"
        "for i,p in ipairs({" + literal + "}) do "
        "local e=s.find_entities_filtered{position=p,radius=0.4,limit=1}[1];"
        "local status=e and (names[e.status] or 'unknown') or 'missing';local v='';"
        "if e then local n=nil;local ok,x=pcall(function() return e.products_finished end);"
        "if ok and x then n=x*1000 end;"
        "local ok2,y=pcall(function() return e.mining_progress end);"
        "if ok2 and y then n=(n or 0)+y end;"
        "local ok3,z=pcall(function() return e.crafting_progress end);"
        "if ok3 and z then n=(n or 0)+z end;"
        "if n then v=string.format('%.4f',n) end end;"
        "out[#out+1]=i..'='..status..'@'..v end;"
        "rcon.print(table.concat(out,','))"
    )
    statuses: dict[Point, str] = {}
    counters: dict[Point, float] = {}
    for pair in _sc(client, lua).split(","):
        if not pair:
            continue
        index, _, payload = pair.partition("=")
        status, _, value = payload.partition("@")
        position = tuple(positions[int(index) - 1])
        statuses[position] = status
        if value:
            counters[position] = float(value)
    return statuses, counters

def logistic_robot_speed(client: RconClient, force: str) -> float:
    """Tiles per second a logistic robot actually flies, research included.

    Prototype speed is per TICK and worker-robot-speed research multiplies it,
    so a base 0.05 becomes 14.1 tiles/s at a +3.7 modifier. Reading both live is
    the only way a delivery estimate stays right as research lands.
    """
    lua = (
        "local p=prototypes.entity['logistic-robot'];"
        "local f=game.forces['" + force + "'];"
        "rcon.print(p.speed*60*(1+f.worker_robots_speed_modifier))"
    )
    return float(_sc(client, lua))


def unpowered_entities(
    client: RconClient, surface: str, area: tuple[Point, Point],
) -> list[Point]:
    """Every entity inside `area` that reads no_power, nearest-first by name.

    A stage's health cannot be judged from its machines alone. An inserter that
    moves the product is not a "machine", so a mining row whose pole supply area
    reaches the drills but stops one tile short of the output row reports every
    drill as fine while the output inserter sits dead and the belt backs up.
    """
    (min_x, min_y), (max_x, max_y) = area
    lua = (
        "local s=game.surfaces['" + surface + "'];local out={};"
        "for _,e in pairs(s.find_entities_filtered{area={{" + str(min_x) + "," + str(min_y) + "},"
        "{" + str(max_x) + "," + str(max_y) + "}}}) do "
        "if e.status==defines.entity_status.no_power then "
        "out[#out+1]=string.format('%s %.1f %.1f',e.name,e.position.x,e.position.y) end end;"
        "rcon.print(table.concat(out,';'))"
    )
    raw = _sc(client, lua)
    found: list[Point] = []
    for record in raw.split(";"):
        if not record:
            continue
        _name, x, y = record.rsplit(" ", 2)
        found.append((float(x), float(y)))
    return found


def logistic_network_ids(
    client: RconClient, surface: str, positions: Sequence[Point],
) -> dict[Point, int | None]:
    """Which logistic network serves each position, in ONE round trip.

    A coloured logistic chest (passive/active provider, requester, buffer,
    storage) only takes part in the logistic system while it stands inside a
    roboport's LOGISTIC supply area -- a radius of 25, less than half the
    construction radius of 55 that let bots build it there in the first place.
    Outside it `entity.logistic_network` is nil, so a provider supplies nothing
    and a requester is never filled, with no error anywhere. Live-verified on
    the running base: two passive-provider chests ~28 tiles from the nearest
    roboport built fine and then reported no network at all, starving every
    stage downstream of them.

    A position with NOTHING built on it yet is left OUT of the result rather
    than mapped to None. The distinction matters: an unbuilt chest is still a
    ghost the bots are working on, while a built one reporting no network is a
    real, permanent fault. Collapsing them would make every stage look broken
    for as long as it was under construction.

    Reading `.logistic_network` is safe on any entity (verified live against a
    substation, drill, belt and steel chest -- all return nil, none error).
    """
    if not positions:
        return {}
    literal = ",".join("{" + str(p[0]) + "," + str(p[1]) + "}" for p in positions)
    lua = (
        "local s=game.surfaces['" + surface + "'];local out={};"
        "for i,p in ipairs({" + literal + "}) do "
        "local e=s.find_entities_filtered{position=p,radius=0.4,limit=1}[1];"
        "local n=e and e.logistic_network;"
        "out[#out+1]=i..'='..(e and (n and tostring(n.network_id) or '-') or 'x') end;"
        "rcon.print(table.concat(out,','))"
    )
    served: dict[Point, int | None] = {}
    for pair in _sc(client, lua).split(","):
        if not pair:
            continue
        index, _, network = pair.partition("=")
        if network == "x":
            continue
        served[tuple(positions[int(index) - 1])] = None if network == "-" else int(network)
    return served


def chest_contents(client: RconClient, surface: str, position: Point) -> dict[str, int]:
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "local c=s.find_entities_filtered{position={" + str(position[0]) + "," + str(position[1]) + "},"
        "radius=0.5,limit=1}[1];"
        "if not c then rcon.print('NONE') return end;"
        "if c.type~='container' and c.type~='logistic-container' then "
        "rcon.print('NONE') return end;"
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
