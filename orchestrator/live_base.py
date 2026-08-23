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


def find_idle_machine_row(
    client: RconClient, surface: str, force: str, recipe: str, machine: str,
    near: Point, *, radius: float = 24.0,
) -> LineState | None:
    """Find unset machines near a known refinery origin for recovery.

    Furnaces infer their recipe from the inserted ore, so an unpowered or
    starved plate row has no recipe and is invisible to `find_line`. This
    narrow structural survey is only used by plate-recovery code; callers must
    validate the returned row's belts, output, and contiguity before reusing it.
    """
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];"
        "local out={};local area={{" + str(near[0] - radius) + "," + str(near[1] - radius) + "},{"
        + str(near[0] + radius) + "," + str(near[1] + radius) + "}};"
        "local machines=s.find_entities_filtered{name='" + machine + "',force=f,area=area};"
        "for _,g in pairs(s.find_entities_filtered{type='entity-ghost',ghost_name='" + machine + "',force=f,area=area}) do "
        "table.insert(machines,g) end;"
        "for _,e in pairs(machines) do "
        "local ok,r=pcall(function() return e.get_recipe() end);"
        "if ok and not r then out[#out+1]=e.position.x..':'..e.position.y end end;"
        "rcon.print(table.concat(out,','))"
    )
    raw = _sc(client, lua)
    positions = tuple(
        sorted(
            (float(pair.split(":")[0]), float(pair.split(":")[1]))
            for pair in raw.split(",") if pair
        )
    )
    if not positions:
        return None
    return LineState(
        recipe=recipe, machine_count=len(positions), working_count=0,
        output_position=positions[-1], machine_positions=positions,
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
        "local ghost=(e.type=='entity-ghost' and e.ghost_name or '-');"
        "rcon.print(e.name..' '..e.type..' '..(e.force and e.force.name or 'neutral')..' '..ghost)"
    )
    raw = _sc(client, lua)
    if raw == "NONE":
        return None
    parts = raw.split(" ", 3)
    name, entity_type, force = parts[:3]
    result = {"name": name, "type": entity_type, "force": force}
    if len(parts) == 4 and parts[3] != "-":
        result["ghost_name"] = parts[3]
    return result


def chest_stored_items(
    client: RconClient, surface: str, position: Point,
) -> int:
    """Total items held by the requester-chest at `position`.

    Returns -1 when the position does not hold a requester-chest. Live run 33
    (2026-08-23): an engine-unit cell's middle feed chest had lost its whole
    request group -- zero items delivered forever while its machines starved
    behind a 'healthy, just supply-starved' verdict. A labelled-but-empty
    feed is degradation, not patience; the 2.1.14 request-slot API does not
    exist on this build, so held content is the observable."""
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "local e=s.find_entities_filtered{position={" + str(position[0]) + "," + str(position[1]) + "},"
        "radius=0.4,limit=1}[1];"
        "if not e or e.name~='requester-chest' then rcon.print('-1') return end;"
        "local inv=e.get_inventory(defines.inventory.chest);"
        "local total=0;"
        "for _,st in pairs(inv.get_contents()) do total=total+(st.count or 0) end;"
        "rcon.print(total)"
    )
    raw = _sc(client, lua)
    try:
        return int(raw)
    except ValueError:
        return -1



def entity_signatures_at(
    client: RconClient, surface: str, force: str, positions: Sequence[Point],
) -> dict[Point, dict]:
    """Read exact named occupants and belt settings in one bounded RCON call."""
    unique = tuple(dict.fromkeys(positions))
    if not unique:
        return {}
    points = "{" + ",".join(f"{{{x},{y}}}" for x, y in unique) + "}"
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];"
        "local out={};for _,p in ipairs(" + points + ") do local e=nil;"
        "for _,candidate in pairs(s.find_entities_filtered{position=p,radius=0.4,force=f}) do "
        "if math.abs(candidate.position.x-p[1])<0.01 and "
        "math.abs(candidate.position.y-p[2])<0.01 then e=candidate break end end;"
        "if not e then out[#out+1]=p[1]..'|'..p[2]..'|NONE|-|-|-';else "
        "local name=(e.type=='entity-ghost' and e.ghost_name or e.name);"
        "local ip='-';local op='-';"
        "local ok,v=pcall(function() return e.splitter_input_priority end);"
        "if ok and v then ip=v end;"
        "ok,v=pcall(function() return e.splitter_output_priority end);"
        "if ok and v then op=v end;"
        "out[#out+1]=p[1]..'|'..p[2]..'|'..name..'|'..e.direction..'|'..ip..'|'..op end end;"
        "rcon.print(table.concat(out,';'))"
    )
    result: dict[Point, dict] = {}
    for record in _sc(client, lua).split(";"):
        x, y, name, direction, input_priority, output_priority = record.split("|")
        result[(float(x), float(y))] = {
            "name": name,
            "direction": int(direction) if direction != "-" else None,
            "input_priority": None if input_priority == "-" else input_priority,
            "output_priority": None if output_priority == "-" else output_priority,
        }
    return result


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
    include_clutter: bool = False, include_water: bool = True,
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
    built on it and no caller ever wants to route through it. Fluid routing is
    the sole exception: it requests ``include_water=False`` and handles the
    separately surveyed terrain only through pipe-to-ground/landfill actions.
    """
    ignored = "{" + ",".join("['" + name + "']=true" for name in ignore_names) + "}"
    water_scan = (
        "for _,t in pairs(s.find_tiles_filtered{area=area,collision_mask='water_tile'}) do "
        "out[#out+1]=t.position.x..','..t.position.y end;"
        if include_water else ""
    )
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
        + water_scan + "for _,e in pairs(s.find_entities_filtered{area=area}) do "
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



def water_tiles(
    client: RconClient, surface: str, min_point: Point, max_point: Point,
) -> set[tuple[int, int]]:
    """Return water terrain in a bounded area for underground route planning."""
    lua = (
        "local s=game.surfaces['" + surface + "'];local out={};"
        "local area={{" + str(min_point[0]) + "," + str(min_point[1]) + "},"
        "{" + str(max_point[0]) + "," + str(max_point[1]) + "}};"
        "for _,t in pairs(s.find_tiles_filtered{area=area,collision_mask='water_tile'}) do "
        "out[#out+1]=t.position.x..','..t.position.y end;"
        "rcon.print(table.concat(out,';'))"
    )
    raw = _sc(client, lua)
    result: set[tuple[int, int]] = set()
    for pair in raw.split(';'):
        if not pair:
            continue
        x, _, y = pair.partition(',')
        result.add((int(x), int(y)))
    return result


def ghost_blockages(
    client: RconClient, surface: str, force: str,
    area: tuple[Point, Point] | None = None,
) -> list[dict[str, object]]:
    """Explain why the remaining construction ghosts are not reviving.

    This is deliberately read-only.  It mirrors the mod's live ghost probe:
    coverage, construction-bot availability, and the first required item are
    checked in the same logistic network that would build each ghost.
    """
    area_clause = ""
    if area is not None:
        (min_x, min_y), (max_x, max_y) = area
        area_clause = (
            ",area={{" + str(min_x) + "," + str(min_y) + "},{"
            + str(max_x) + "," + str(max_y) + "}}"
        )
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];"
        "local out={};"
        "for _,g in pairs(s.find_entities_filtered{type='entity-ghost',force=f"
        + area_clause + "}) do "
        "local reason='pending';"
        "local ok,network=pcall(function() return s.find_logistic_network_by_position(g.position,f) end);"
        "if not ok or not network then reason='out_of_construction_range' else "
        "local bots_ok,bots=pcall(function() return network.all_construction_robots end);"
        "if not bots_ok or bots==0 then reason='no_construction_robots' else "
        "local proto_ok,proto=pcall(function() return g.ghost_prototype end);"
        "if proto_ok and proto and proto.items_to_place_this and proto.items_to_place_this[1] then "
        "local item=proto.items_to_place_this[1];"
        "local have_ok,have=pcall(function() return network.get_item_count(item.name) end);"
        "if have_ok and have < (item.count or 1) then "
        "reason='missing_material:'..item.name..':'..tostring(item.count or 1)..':'..tostring(have) end end end end;"
        "out[#out+1]=string.format('%.1f|%.1f|%s|%s',g.position.x,g.position.y,g.ghost_name,reason) end;"
        "rcon.print(table.concat(out,';'))"
    )
    raw = _sc(client, lua)
    records: list[dict[str, object]] = []
    for record in raw.split(";"):
        fields = record.split("|", 3)
        if len(fields) != 4:
            continue
        try:
            position = (float(fields[0]), float(fields[1]))
        except ValueError:
            continue
        reason = fields[3]
        detail: dict[str, object] = {
            "position": position,
            "entity": fields[2],
            "reason": reason,
        }
        if reason.startswith("missing_material:"):
            _, item, required, available = reason.split(":", 3)
            detail.update({
                "item": item,
                "required": int(required),
                "available": int(available),
            })
        records.append(detail)
    return records


def area_entity_records(
    client: RconClient, surface: str, force: str,
    min_point: Point, max_point: Point,
) -> list[dict[str, object]]:
    """Return bounded placement evidence, including ghosts and tile ghosts."""
    lua = (
        "local s=game.surfaces['" + surface + "'];local out={};"
        "local area={{" + str(min_point[0]) + "," + str(min_point[1]) + "},"
        "{" + str(max_point[0]) + "," + str(max_point[1]) + "}};"
        "for _,e in pairs(s.find_entities_filtered{area=area}) do "
        "if e.type~='character' and e.type~='resource' then "
        "local ghost='';local tile='';"
        "if e.type=='entity-ghost' then ghost=e.ghost_name or '' end;"
        "if e.type=='tile-ghost' then tile=e.ghost_name or '' end;"
        "local decon=false;local okd,d=pcall(function() return e.to_be_deconstructed() end);"
        "if okd then decon=d and true or false end;"
        "out[#out+1]=table.concat({e.name,e.type,(e.force and e.force.name or 'neutral'),"
        "string.format('%.3f',e.position.x),string.format('%.3f',e.position.y),"
        "ghost,tile,tostring(decon)},'|') end end;"
        "rcon.print(table.concat(out,';'))"
    )
    records: list[dict[str, object]] = []
    for raw in _sc(client, lua).split(";"):
        if not raw:
            continue
        fields = raw.split("|", 7)
        if len(fields) != 8:
            continue
        records.append({
            "name": fields[0],
            "type": fields[1],
            "force": fields[2],
            "position": (float(fields[3]), float(fields[4])),
            "ghost_name": fields[5] or None,
            "tile_name": fields[6] or None,
            "deconstructed": fields[7].lower() == "true",
            "is_ghost": fields[1] in {"entity-ghost", "tile-ghost"},
        })
    return records


def deconstruction_tiles(
    client: RconClient, surface: str, min_point: Point, max_point: Point,
) -> set[tuple[int, int]]:
    """Tiles covered by an entity with a pending deconstruction order."""
    lua = (
        "local s=game.surfaces['" + surface + "'];local out={};"
        "local area={{" + str(min_point[0]) + "," + str(min_point[1]) + "},"
        "{" + str(max_point[0]) + "," + str(max_point[1]) + "}};"
        "for _,e in pairs(s.find_entities_filtered{area=area}) do "
        "local ok,d=pcall(function() return e.to_be_deconstructed() end);"
        "if ok and d then local b=e.bounding_box;"
        "for x=math.floor(b.left_top.x),math.ceil(b.right_bottom.x)-1 do "
        "for y=math.floor(b.left_top.y),math.ceil(b.right_bottom.y)-1 do "
        "out[#out+1]=x..','..y end end end end;"
        "rcon.print(table.concat(out,';'))"
    )
    tiles: set[tuple[int, int]] = set()
    for pair in _sc(client, lua).split(";"):
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

def roboport_positions(client: RconClient, surface: str, force: str) -> list[Point]:
    """Every built roboport position for the force, in one round trip."""
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];"
        "local out={};for _,e in pairs(s.find_entities_filtered{name='roboport',force=f}) do "
        "out[#out+1]=string.format('%.2f %.2f',e.position.x,e.position.y) end;"
        "rcon.print(table.concat(out,';'))"
    )
    raw = _sc(client, lua)
    if not raw:
        return []
    return [tuple(float(v) for v in record.split()) for record in raw.split(";")]


_GENERATOR_TYPES = (
    "'generator','electric-energy-interface','fusion-generator','burner-generator'"
)


class TelemetryError(RuntimeError):
    """A live survey returned an unusable numeric value."""


def _network_generation_kw_impl(
    client: RconClient, surface: str, force: str, near: Point,
    *, include_solar: bool,
) -> float | None:
    """Combined generation capacity (kW) of the electric network nearest
    `near`, or None when no roboport defines that network.

    Generators only: accumulators store rather than generate, so they are
    excluded -- a charged battery bank must not license a placement burst the
    grid cannot sustain. Prototype maxima are the nameplate quantity a charging
    burst competes against (verified live on 2.1.14:
    LuaEntityPrototype.get_max_energy_production() returns kilowatts), EXCEPT
    for script-configured sources: an electric-energy-interface's real output
    lives on the entity (power_production, watts). Live evidence 2026-08-22:
    one EEI reported prototype 8_333_333_333 kW vs entity 166.7 kW, which
    silently gated off every solar top-up and browned out the whole base.
    """
    types = _GENERATOR_TYPES + (",'solar-panel'" if include_solar else "")
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];"
        "local nx,ny=" + str(near[0]) + "," + str(near[1]) + ";"
        "local best=nil;local bd=1e18;"
        "for _,e in pairs(s.find_entities_filtered{name='roboport',force=f}) do "
        "local d=(e.position.x-nx)^2+(e.position.y-ny)^2;if d<bd then bd=d;best=e end end;"
        "if not best then rcon.print('NONE') return end;"
        "local ok,net=pcall(function() return best.electric_network_id end);"
        "if not ok or net==nil then rcon.print('NONE') return end;"
        "local total=0;"
        "for _,g in pairs(s.find_entities_filtered{force=f,type={" + types + "}}) do "
        "local okid,id=pcall(function() return g.electric_network_id end);"
        "if okid and id==net then "
        "local kw=nil;"
        "if g.type=='electric-energy-interface' then "
        "local okw,w=pcall(function() return g.power_production end);"
        "if okw and type(w)=='number' and w==w and w~=math.huge and w~=-math.huge "
        "then kw=w/1000 elseif okw and type(w)=='number' then "
        "rcon.print('INVALID|'..g.type..'|'..g.name..'|'..g.position.x..','"
        "..g.position.y..'|power_production|'..tostring(w)) return end end;"
        "if kw==nil then "
        "local okp,p=pcall(function() return g.prototype.get_max_energy_production() end);"
        "if okp and type(p)=='number' and p==p and p~=math.huge and p~=-math.huge "
        "then kw=p elseif okp and type(p)=='number' then "
        "rcon.print('INVALID|'..g.type..'|'..g.name..'|'..g.position.x..','"
        "..g.position.y..'|get_max_energy_production|'..tostring(p)) return end end;"
        "if kw==nil then rcon.print('INVALID|'..g.type..'|'..g.name..'|'"
        "..g.position.x..','..g.position.y..'|generation|unavailable') return end;"
        "total=total+kw end end;"
        "rcon.print(tostring(math.floor(total*10+0.5)/10))"
    )
    raw = _sc(client, lua)
    if raw == "NONE":
        return None
    if raw.startswith("INVALID|"):
        raise TelemetryError(f"non-finite or unavailable generation survey: {raw}")
    try:
        value = float(raw)
    except ValueError as exc:
        raise TelemetryError(f"malformed generation survey: {raw!r}") from exc
    if not math.isfinite(value) or value < 0:
        raise TelemetryError(f"invalid generation survey value: {value}")
    return value


def network_generation_kw(
    client: RconClient, surface: str, force: str, near: Point,
) -> float | None:
    """Nameplate generation including solar. See network_firm_generation_kw
    before using this to license night-time-sensitive decisions."""
    return _network_generation_kw_impl(
        client, surface, force, near, include_solar=True,
    )


def network_firm_generation_kw(
    client: RconClient, surface: str, force: str, near: Point,
) -> float | None:
    """Generation capacity (kW) EXCLUDING solar panels on the nearest network.

    Live evidence 2026-08-23: one modded solar panel reports a 1000 kW
    nameplate while producing nothing after dusk -- with zero accumulators
    the whole base still collapsed to its 166.7 kW interface every night.
    Night-survivable capacity is FIRM capacity; licensing placement bursts
    against daylight nameplate browns out after sundown."""
    return _network_generation_kw_impl(
        client, surface, force, near, include_solar=False,
    )


def network_accumulator_storage_mj(
    client: RconClient, surface: str, force: str, near: Point,
) -> float | None:
    """Maximum accumulator energy on the nearest roboport's electric network."""
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];"
        "local nx,ny=" + str(near[0]) + "," + str(near[1]) + ";"
        "local best,bd=nil,1e18;"
        "for _,e in pairs(s.find_entities_filtered{name='roboport',force=f}) do "
        "local d=(e.position.x-nx)^2+(e.position.y-ny)^2;if d<bd then bd=d;best=e end end;"
        "if not best then rcon.print('NONE') return end;"
        "local ok,net=pcall(function() return best.electric_network_id end);"
        "if not ok or net==nil then rcon.print('NONE') return end;"
        "local mj=0;"
        "for _,a in pairs(s.find_entities_filtered{name='accumulator',force=f}) do "
        "local okid,id=pcall(function() return a.electric_network_id end);"
        "if okid and id==net then "
        "local joules=nil;"
        "local okb,b=pcall(function() return a.prototype.buffer_capacity end);"
        "if okb and type(b)=='number' and b==b and b~=math.huge and b~=-math.huge "
        "then joules=b elseif okb and type(b)=='number' then "
        "rcon.print('INVALID|'..a.type..'|'..a.name..'|'..a.position.x..','"
        "..a.position.y..'|buffer_capacity|'..tostring(b)) return end;"
        "if joules==nil then "
        "local oke,e=pcall(function() return a.electric_buffer_size end);"
        "if oke and type(e)=='number' and e==e and e~=math.huge and e~=-math.huge "
        "then joules=e elseif oke and type(e)=='number' then "
        "rcon.print('INVALID|'..a.type..'|'..a.name..'|'..a.position.x..','"
        "..a.position.y..'|electric_buffer_size|'..tostring(e)) return end end;"
        "if joules==nil then rcon.print('INVALID|'..a.type..'|'..a.name..'|'"
        "..a.position.x..','..a.position.y..'|storage|unavailable') return end;"
        "mj=mj+joules/1000000 end end;"
        "rcon.print(tostring(math.floor(mj*1000+0.5)/1000))"
    )
    raw = _sc(client, lua)
    if raw == "NONE":
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise TelemetryError(f"malformed accumulator-storage survey: {raw!r}") from exc
    if not math.isfinite(value) or value < 0:
        raise TelemetryError(f"invalid accumulator-storage value: {value}")
    return value


def drill_drop_belt_tile(
    client: RconClient, surface: str, ore_output: Point,
) -> Point:
    """The belt tile beside a drill drop column nearest `ore_output`.

    The output tile can be the UPSTREAM tail of the mine belt -- on eastbound
    rows nothing ever passes it, and an intake there waits for source items
    forever (live run 8: 86 frozen ore east of a starving intake). A tile
    beside a drill's drop column receives ore by construction. Raises
    ValueError when no drill/belt pairing exists near the row."""
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "local ox,oy=" + str(ore_output[0]) + "," + str(ore_output[1]) + ";"
        "local best=nil;local bd=1e18;"
        "for _,d in pairs(s.find_entities_filtered{type='mining-drill'}) do "
        "if math.abs(d.position.y-oy)<=1.5 then "
        "for _,b in pairs(s.find_entities_filtered{type='transport-belt',"
        "area={{d.position.x-1,oy-0.5},{d.position.x+1,oy+0.5}}}) do "
        "local dist=math.abs(b.position.x-ox);"
        "if dist<bd then bd=dist;best=b.position end end end end;"
        "if not best then rcon.print('NONE') return end;"
        "rcon.print(best.x..' '..best.y)"
    )
    raw = _sc(client, lua)
    parts = raw.split()
    if len(parts) != 2:
        raise ValueError(f"no drill drop column near the {surface} belt row")
    return (float(parts[0]), float(parts[1]))


def chained_clear_spots(
    client: RconClient, surface: str,
    placements: Sequence[tuple[str, int]], start: Point,
    *, step_radius: float = 6.0,
) -> list[tuple[str, float, float]]:
    """Non-colliding centres for a compact cluster of prototypes.

    Each unit is placed within `step_radius` of the previous one, so the
    cluster stays contiguous -- solar arrays must sit inside their own supply
    pole's area, not scattered around it. Returns (name, x, y) triples; fewer
    than requested when the terrain runs out.

    Spots must also be DISTINCT: live run 2026-08-22 22:27 showed
    find_non_colliding_position returning the cursor tile itself whenever that
    tile is still clear, so an eight-panel array collapsed onto one tile (mod
    report attempted=9 placed=2 already_present=7 ok=true) and the base kept
    running on its starved grid. Probed duplicates now walk the cursor off the
    taken tile before the next search.
    """
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "local cur={" + str(start[0]) + "," + str(start[1]) + "};"
        "local out={};"
        "local used={};"
        "local function spot_key(x,y) return string.format('%.2f|%.2f',x,y) end;"
        "local wanted={"
        + ",".join(
            "{'" + name + "'," + str(count) + "}"
            for name, count in placements
        )
        + "};"
        "for _,w in ipairs(wanted) do "
        "for i=1,w[2] do "
        "local p=s.find_non_colliding_position(w[1],cur," + str(step_radius) + ",0.5);"
        "local probes=0;"
        "while p and used[spot_key(p.x,p.y)] and probes<8 do "
        "probes=probes+1;"
        "cur={cur[1]+2.0*math.cos(probes),cur[2]+2.0*math.sin(probes)};"
        "p=s.find_non_colliding_position(w[1],cur," + str(step_radius) + ",0.5);"
        "end;"
        "if p then used[spot_key(p.x,p.y)]=true;out[#out+1]=w[1]..' '..p.x..' '..p.y;cur={p.x,p.y} end "
        "end end;"
        "rcon.print(table.concat(out,';'))"
    )
    raw = _sc(client, lua)
    spots: list[tuple[str, float, float]] = []
    for entry in raw.split(";"):
        parts = entry.split()
        if len(parts) == 3:
            spots.append((parts[0], float(parts[1]), float(parts[2])))
    return spots


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


def roboport_energy(
    client: RconClient, surface: str, force: str, near: Point,
) -> float | None:
    """Internal buffer energy (J) of the roboport at `near`, or None if absent.

    A fresh roboport lands at roughly half its 100 MJ buffer (live-verified)
    and draws megawatts while charging its internal batteries -- the transient
    that stacks into a production-killing spike when whole chains land at once.

    An unparseable reply also reads None: live run 29 lost the whole mission
    when one poll arrived truncated at the server and float() raised. The
    reading is advisory charge telemetry; a skipped sample just delays the
    next poll.
    """
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];"
        "for _,e in pairs(s.find_entities_filtered{name='roboport',force=f}) do "
        "if math.abs(e.position.x-" + str(near[0]) + ")<0.5 and "
        "math.abs(e.position.y-" + str(near[1]) + ")<0.5 then "
        "local ok,v=pcall(function() return e.energy end);"
        "if ok and type(v)=='number' then rcon.print(string.format('%.1f',v)) return end end end;"
        "rcon.print('NONE')"
    )
    raw = _sc(client, lua)
    if raw == "NONE":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def occupied_tile_owners(
    client: RconClient, surface: str, min_point: Point, max_point: Point,
    *, include_resources: bool = False,
) -> dict[tuple[int, int], tuple[str, float, float]]:
    """Same survey as occupied_tiles (clutter/water excluded) but recording
    WHICH entity occupies each tile: name plus exact centre position.

    Tile arithmetic against declared footprint constants cannot classify a
    collision reliably -- a live bounding box can cover tiles the declared
    footprint never claims -- but an entity we PLANNED is identifiable by its
    exact centre no matter how wide its box surveys as."""
    lua = (
        "local s=game.surfaces['" + surface + "'];local out={};"
        "local area={{" + str(min_point[0]) + "," + str(min_point[1]) + "},"
        "{" + str(max_point[0]) + "," + str(max_point[1]) + "}};"
        "for _,e in pairs(s.find_entities_filtered{area=area}) do "
        "if " + ("" if include_resources else "e.type~='resource' and ")
        + "e.type~='character' and "
        "not (e.force and e.force.name=='neutral' and "
        "(e.type=='tree' or e.type=='simple-entity')) then "
        "local b=e.bounding_box;"
        "for x=math.floor(b.left_top.x),math.ceil(b.right_bottom.x)-1 do "
        "for y=math.floor(b.left_top.y),math.ceil(b.right_bottom.y)-1 do "
        "out[#out+1]=x..','..y..' '..e.name..' '..string.format('%.3f %.3f',"
        "e.position.x,e.position.y) end end end end;"
        "rcon.print(table.concat(out,';'))"
    )
    raw = _sc(client, lua)
    owners: dict[tuple[int, int], tuple[str, float, float]] = {}
    for record in raw.split(";"):
        if not record:
            continue
        tile, _, rest = record.partition(" ")
        name, _, centre = rest.partition(" ")
        cx, cy = centre.split()
        owners[(int(tile.split(",")[0]), int(tile.split(",")[1]))] = (
            name, float(cx), float(cy),
        )
    return owners


def pending_ghost_count(
    client: RconClient, surface: str, force: str,
) -> int:
    """Entity + tile ghosts outstanding for the force, whole surface.

    One cheap query used as a construction-progress signal: a falling count
    means bots are visibly building, so a repeated decision signature is
    patience rather than a livelock."""
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];"
        "local n=s.count_entities_filtered{type='entity-ghost',force=f}"
        "+s.count_entities_filtered{type='tile-ghost',force=f};"
        "rcon.print(n)"
    )
    return int(_sc(client, lua))


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
    """Every non-generator entity inside `area` reading no_power, nearest-first.

    A stage's health cannot be judged from its machines alone. An inserter that
    moves the product is not a "machine", so a mining row whose pole supply area
    reaches the drills but stops one tile short of the output row reports every
    drill as fine while the output inserter sits dead and the belt backs up.

    Generation sources are excluded: a solar panel after dusk also reads
    no_power, and live runs 26-27 (2026-08-22) then classified it as an
    unpowered support entity and waited out every round for a generator to
    "charge". An idle source is physics, not a wiring defect; a genuinely
    starved grid is the generation-capacity observation's business.
    """
    (min_x, min_y), (max_x, max_y) = area
    lua = (
        "local s=game.surfaces['" + surface + "'];local out={};"
        "local gens={['solar-panel']=true,['generator']=true,['accumulator']=true,"
        "['reactor']=true,['electric-energy-interface']=true,"
        "['fusion-generator']=true,['burner-generator']=true};"
        "for _,e in pairs(s.find_entities_filtered{area={{" + str(min_x) + "," + str(min_y) + "},"
        "{" + str(max_x) + "," + str(max_y) + "}}}) do "
        "if e.status==defines.entity_status.no_power and not gens[e.type] then "
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


def pole_context(
    client: RconClient, surface: str, position: Point,
) -> dict | None:
    """The pole at `position` with what it powers and what it is wired to.

    One round trip, because deciding whether a pole may step aside needs both
    halves at once: the consumers inside its supply area, and the poles its
    copper wire actually reaches. Reading them separately let the two answers
    describe different moments.

    `supplied` deliberately excludes other poles -- a pole standing inside
    another's supply area is not powered BY it, and counting one would pin a
    nudge to a constraint that does not exist.
    """
    x, y = position[0], position[1]
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "local p=s.find_entities_filtered{position={" + str(x) + "," + str(y) + "},"
        "radius=0.4,limit=1}[1];"
        "if not p or p.type~='electric-pole' then rcon.print('NONE') return end;"
        "local r=p.prototype.supply_area_distance;"
        "local sup={};"
        "for _,e in pairs(s.find_entities_filtered{area={{p.position.x-r,p.position.y-r},"
        "{p.position.x+r,p.position.y+r}}}) do "
        "if e.type~='electric-pole' and e.valid and e.prototype.electric_energy_source_prototype then "
        "sup[#sup+1]=string.format('%.1f,%.1f',e.position.x,e.position.y) end end;"
        "local nb={};"
        "for _,n in pairs(p.neighbours and p.neighbours.copper or {}) do "
        "nb[#nb+1]=string.format('%.1f,%.1f',n.position.x,n.position.y) end;"
        "rcon.print(p.name..'|'..table.concat(sup,';')..'|'..table.concat(nb,';'))"
    )
    raw = _sc(client, lua)
    if raw == "NONE" or "|" not in raw:
        return None
    name, supplied_raw, neighbours_raw = raw.split("|", 2)

    def _points(blob: str) -> list[Point]:
        return [
            (float(part.split(",")[0]), float(part.split(",")[1]))
            for part in blob.split(";") if part
        ]

    return {
        "name": name,
        "supplied": _points(supplied_raw),
        "neighbours": _points(neighbours_raw),
    }


def poles_in_area(
    client: RconClient, surface: str, area: tuple[Point, Point],
) -> list[tuple[str, Point]]:
    """Every electric pole inside `area`, as (name, position)."""
    (min_x, min_y), (max_x, max_y) = area
    lua = (
        "local s=game.surfaces['" + surface + "'];local out={};"
        "for _,e in pairs(s.find_entities_filtered{type='electric-pole',"
        "area={{" + str(min_x) + "," + str(min_y) + "},"
        "{" + str(max_x) + "," + str(max_y) + "}}}) do "
        "out[#out+1]=string.format('%s %.1f %.1f',e.name,e.position.x,e.position.y) end;"
        "rcon.print(table.concat(out,';'))"
    )
    found: list[tuple[str, Point]] = []
    for record in _sc(client, lua).split(";"):
        if not record:
            continue
        name, x, y = record.rsplit(" ", 2)
        found.append((name, (float(x), float(y))))
    return found


def bootstrap_cell_origins(
    client: RconClient, surface: str, force: str, ore: str,
) -> list[Point]:
    """Origins of standing compact bootstrap cells requesting exactly `ore`.

    A cell's furnaces carry no recipe until first craft, so machine surveys
    cannot see them -- its REQUESTER is the identity: first logistic-section
    slot requesting exactly `ore` x50 is the cell's signature."""
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "local out={};"
        "for _,c in pairs(s.find_entities_filtered{name='requester-chest'}) do "
        "local ok=false;"
        "pcall(function() local sec=c.get_logistic_sections().sections[1];"
        "local sl=sec and sec.get_slot(1);"
        "if sl and sl.value and sl.value.name=='" + ore + "' "
        "and sl.min==50 then ok=true end end);"
        "if ok then out[#out+1]=c.position.x..' '..c.position.y end end;"
        "rcon.print(table.concat(out,';'))"
    )
    raw = _sc(client, lua)
    origins: list[Point] = []
    for entry in filter(None, raw.split(";")):
        parts = entry.split()
        if len(parts) == 2:
            origins.append((float(parts[0]), float(parts[1])))
    return origins


def intake_candidate_tiles(
    client: RconClient, surface: str, ore_output: Point,
) -> list[Point]:
    """Belt tiles PROVEN to hold or receive ore, best first, ends as fallback.

    Ground truth over geometry: a tile qualifies when a drill drops onto it,
    or its lanes hold items right now. Everything else -- upstream tails,
    west-of-drop edges on eastbound rows -- waited forever with 'no source
    items' beside frozen ore (live runs 8-10). Both entity kinds are
    snapshotted to plain numbers before any second query."""
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "local ox,oy=" + str(ore_output[0]) + "," + str(ore_output[1]) + ";"
        "local offs={[0]={0,-2},[4]={2,0},[8]={0,2},[12]={-2,0}};"
        "local tiles={};local drops={};local drills={};"
        "local minx,maxx=nil,nil;"
        "for _,d in pairs(s.find_entities_filtered{type='mining-drill'}) do "
        "if math.abs(d.position.y-oy)<=3 then "
        "local dd={x=d.position.x,y=d.position.y,dir=d.direction};"
        "drills[#drills+1]=dd;"
        "local off=offs[dd.dir] or {0,2};"
        "drops[math.floor(dd.x+off[1])..'_'..math.floor(dd.y+off[2])]=true end end;"
        "for _,b in pairs(s.find_entities_filtered{type='transport-belt'}) do "
        "if math.abs(b.position.y-oy)<0.6 and math.abs(b.position.x-ox)<=40 then "
        "local n=0;"
        "for _,li in ipairs({b.get_transport_line(1),b.get_transport_line(2)}) do "
        "for _,c in pairs(li.get_contents()) do n=n+c.count end end;"
        "local key=math.floor(b.position.x)..'_'..math.floor(b.position.y);"
        "tiles[#tiles+1]={x=b.position.x,y=b.position.y,n=n,"
        "drop=not not drops[key],key=key};"
        "if not minx or b.position.x<minx then minx=b.position.x end "
        "if not maxx or b.position.x>maxx then maxx=b.position.x end end end;"
        "local out={};local seen={};"
        "for _,t in ipairs(tiles) do "
        "if (t.drop or t.n>0) and not seen[t.key] then seen[t.key]=true;"
        "out[#out+1]=t.x..' '..t.y end end;"
        "for _,t in ipairs(tiles) do "
        "if (t.x==minx or t.x==maxx) and not seen[t.key] then seen[t.key]=true;"
        "out[#out+1]=t.x..' '..t.y end end;"
        "rcon.print(table.concat(out,';'))"
    )
    raw = _sc(client, lua)
    tiles_out: list[Point] = []
    seen: set[tuple[float, float]] = set()
    for entry in filter(None, raw.split(";")):
        parts = entry.split()
        if len(parts) == 2:
            key = (float(parts[0]), float(parts[1]))
            if key not in seen:
                seen.add(key)
                tiles_out.append(key)
    return tiles_out


def blocked_drill_drop_tile(
    client: RconClient, surface: str, near: Point, *, radius: float = 40.0,
) -> Point | None:
    """An empty drop tile of an ore-blocked drill near `near`, if any.

    A drill reporting waiting-for-space stares at a drop tile where this
    blueprint laid no belt -- exactly the tile a logistic intake needs. A
    provider chest placed there turns the blocked drill into the mine's ore
    interface with zero demolition."""
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "local f=game.forces['player'];"
        "local nx,ny=" + str(near[0]) + "," + str(near[1]) + ";"
        "local offs={[0]={0,-2},[4]={2,0},[8]={0,2},[12]={-2,0}};"
        "for _,d in pairs(s.find_entities_filtered{type='mining-drill',force=f}) do "
        "if math.abs(d.position.x-nx)<=radius and math.abs(d.position.y-ny)<=radius then "
        "local ok,st=pcall(function() return d.status end);"
        "if ok and st==defines.entity_status.waiting_for_space_in_destination then "
        "local off=offs[d.direction] or {0,2};"
        "local tx,ty=d.position.x+off[1],d.position.y+off[2];"
        "local e=s.find_entities_filtered{position={tx,ty}}[1];"
        "local b=s.find_entities_filtered{position={tx,ty},type='transport-belt'}[1];"
        "if not e and not b then rcon.print(tx..' '..ty) return end "
        "end end end;"
        "rcon.print('NONE')"
    )
    raw = _sc(client, lua)
    parts = raw.split()
    if len(parts) == 2:
        return (float(parts[0]), float(parts[1]))
    return None


def chest_has_items(
    client: RconClient, surface: str, position: Point,
) -> bool:
    """Whether the chest at `position` holds anything at all."""
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "local e=s.find_entities_filtered{position={"
        + str(position[0]) + "," + str(position[1]) + "},radius=0.4,limit=1}[1];"
        "if not e then rcon.print('EMPTY') return end;"
        "local ok=false;"
        "pcall(function() local i=e.get_inventory(defines.inventory.chest);"
        "for k=1,#i do local st=i[k] if st and st.valid_for_read then ok=true end end end);"
        "rcon.print(ok and 'FULL' or 'EMPTY')"
    )
    raw = _sc(client, lua)
    return raw.strip() == "FULL"


def network_item_count(
    client: RconClient, surface: str, force: str, near: Point, item: str,
) -> int | None:
    """Units of `item` inside the logistic network covering `near` (None: none)."""
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];"
        "local n=s.find_logistic_network_by_position({"
        + str(near[0]) + "," + str(near[1]) + "},f);"
        "if not n then rcon.print('NONE') return end;"
        "rcon.print(tostring(n.get_item_count('" + item + "')))"
    )
    raw = _sc(client, lua)
    if raw == "NONE":
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def transfer_stock(
    client: RconClient, surface: str, item: str, count: int, to_position: Point,
) -> int:
    """Relocate up to `count` of `item` from our containers INTO the chest at
    `to_position`. Conservation-honest: units are removed at the source in
    the same Lua transaction that inserts them at the destination. Returns
    the number actually moved."""
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "local f=game.forces['player'];"
        "local dst=s.find_entities_filtered{position={"
        + str(to_position[0]) + "," + str(to_position[1]) + "},radius=0.4,"
        "type='logistic-container',limit=1}[1];"
        "if not dst then rcon.print('NODST') return end;"
        "local remaining=" + str(count) + ";"
        "for _,c in pairs(s.find_entities_filtered{type='container',force=f}) do "
        "if remaining<=0 then break end;"
        "if c.unit_number~=dst.unit_number then "
        "pcall(function() local i=c.get_inventory(defines.inventory.chest);"
        "local n=i.get_item_count('" + item + "');"
        "if n>0 then local take=math.min(n,remaining);"
        "local got=i.remove({name='" + item + "',count=take});"
        "if got>0 then dst.insert({name='" + item + "',count=got});"
        "remaining=remaining-got end end end) end end;"
        "rcon.print(tostring(" + str(count) + "-remaining))"
    )
    raw = _sc(client, lua)
    try:
        return int(raw)
    except ValueError:
        return 0


def requester_requesting(
    client: RconClient, surface: str, item: str, near: Point,
    *, radius: float = 30.0,
) -> Point | None:
    """A requester chest whose first section slot asks for `item`, nearest
    `near` -- the identity of the mall cell producing from it."""
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "local f=game.forces['player'];"
        "local nx,ny=" + str(near[0]) + "," + str(near[1]) + ";"
        "local best=nil;local bd=1e18;"
        "for _,c in pairs(s.find_entities_filtered{name='requester-chest',force=f}) do "
        "local d=(c.position.x-nx)^2+(c.position.y-ny)^2;"
        "if d<bd then "
        "local ok,matched=pcall(function() "
        "local secs=c.get_logistic_sections();"
        "for _,sec in pairs(secs.sections) do "
        "local sl=sec.get_slot(1);"
        "if sl and sl.value and sl.value.name=='" + item + "' then return true end "
        "end return false end);"
        "if ok and matched then best=c.position;bd=d end end end;"
        "if best then rcon.print(best.x..' '..best.y) else rcon.print('NONE') end"
    )
    raw = _sc(client, lua)
    parts = raw.split()
    if len(parts) == 2:
        return (float(parts[0]), float(parts[1]))
    return None
