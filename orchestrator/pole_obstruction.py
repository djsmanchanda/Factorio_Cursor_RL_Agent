# Path: orchestrator/pole_obstruction.py
# Purpose: Move this base's own power poles out of a plan's footprint, replacement first, then recover the old pole.

"""Own poles in the way are rerouted, not built over.

The executor only rejects a blocker centred on the exact planned position, so
a pole that merely overlaps a planned tile let a ghost be placed that bots
could never build (live 2026-10-15: big pole at (58, 35) under the provider
ghost at (58.5, 34.5)). Before a plan is submitted, each such pole is replaced
at a site that keeps every wire neighbour and every consumer only it powers
connected; then construction bots recover the old pole. A pole that connects
nothing and powers nothing is simply recovered. With no legal replacement the
plan fails loudly instead of leaving an unbuildable ghost.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from orchestrator import live_base
from orchestrator.game_bridge import GameBridge
from orchestrator.placement_clutter import _plan_tiles
from orchestrator.recoverable_retirement import retire_entities_via_bots
from planners.infrastructure import POLE_SPECS
from planners.infrastructure_geometry import footprint_tile_indices
from tools.rcon_client import RconClient

Point = tuple[float, float]
Box = tuple[float, float, float, float]  # left, top, right, bottom
RELOCATION_PREFIX = "relocate_obstructing_pole"


class PoleObstruction(RuntimeError):
    """An own pole blocks the plan and no connected replacement site exists."""

    def __init__(self, message: str, details: dict) -> None:
        super().__init__(message)
        self.details = details


@dataclass(frozen=True)
class Pole:
    name: str
    position: Point
    ghost: bool
    tiles: frozenset[tuple[int, int]]
    neighbours: tuple[tuple[str, Point], ...]


def _spec(name: str) -> dict:
    return POLE_SPECS.get(name, POLE_SPECS["medium-electric-pole"])


def _supplies(name: str, centre: Point, box: Box) -> bool:
    reach = _spec(name)["supply"]
    left, top, right, bottom = box
    return (left < centre[0] + reach and right > centre[0] - reach
            and top < centre[1] + reach and bottom > centre[1] - reach)


def dependents(old: tuple[str, Point], poles: Sequence[tuple[str, Point]],
               consumers: Sequence[Box]) -> list[Box]:
    """Consumers inside ``old``'s supply area that no other pole supplies."""
    others = [pole for pole in poles if pole != old]
    return [box for box in consumers
            if _supplies(*old, box) and not any(_supplies(name, at, box) for name, at in others)]


def replacement_site(name: str, old: Point, neighbours: Sequence[tuple[str, Point]],
                     needs: Sequence[Box], blocked: set[tuple[int, int]]) -> Point | None:
    """Nearest free site within wire reach of every neighbour that supplies every dependent."""
    size = int(_spec(name)["size"])
    wire = _spec(name)["wire"]
    offset = 0.0 if size % 2 == 0 else 0.5
    span = math.ceil(wire)
    sites = [(math.floor(old[0]) + dx + offset, math.floor(old[1]) + dy + offset)
             for dx in range(-span, span + 1) for dy in range(-span, span + 1)]
    for site in sorted(sites, key=lambda point: (math.dist(point, old), point)):
        if site == old or footprint_tile_indices(site, size) & blocked:
            continue
        if all(math.dist(site, at) <= min(wire, _spec(other)["wire"]) for other, at in neighbours) \
                and all(_supplies(name, site, box) for box in needs):
            return site
    return None


def _survey_poles(client: RconClient, surface: str, force: str,
                  minimum: Point, maximum: Point) -> list[Pole]:
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];local out={};"
        "local area={{" + f"{minimum[0]},{minimum[1]}" + "},{" + f"{maximum[0]},{maximum[1]}" + "}};"
        "local function nm(e) return e.type=='entity-ghost' and e.ghost_name or e.name end;"
        "local function emit(e) local b=e.bounding_box;local ns={};"
        "local ok,c=pcall(function() return e.get_wire_connector(defines.wire_connector_id.pole_copper,false) end);"
        "if ok and c then for _,w in pairs(c.connections) do local o=w.target.owner;"
        "ns[#ns+1]=nm(o)..','..o.position.x..','..o.position.y end end;"
        "out[#out+1]=nm(e)..'|'..tostring(e.type=='entity-ghost')..'|'..e.position.x..'|'..e.position.y..'|'"
        "..b.left_top.x..','..b.left_top.y..','..b.right_bottom.x..','..b.right_bottom.y..'|'..table.concat(ns,';') end;"
        "for _,e in pairs(s.find_entities_filtered{area=area,type='electric-pole',force=f}) do emit(e) end;"
        "for _,e in pairs(s.find_entities_filtered{area=area,type='entity-ghost',force=f}) do "
        "if e.ghost_type=='electric-pole' then emit(e) end end;"
        "rcon.print(table.concat(out,'\\n'))"
    )
    poles = []
    for line in client.command("/sc " + lua).strip().splitlines():
        name, ghost, x, y, box, links = line.split("|")
        left, top, right, bottom = (float(value) for value in box.split(","))
        tiles = frozenset((tx, ty) for tx in range(math.floor(left), math.ceil(right))
                          for ty in range(math.floor(top), math.ceil(bottom)))
        neighbours = tuple((n, (float(nx), float(ny)))
                           for n, nx, ny in (link.split(",") for link in links.split(";") if link))
        poles.append(Pole(name, (float(x), float(y)), ghost == "true", tiles, neighbours))
    return poles


def _survey_consumers(client: RconClient, surface: str, force: str, pole: Pole) -> list[Box]:
    """Electric consumers (built or planned) inside the pole's supply area."""
    reach = _spec(pole.name)["supply"]
    x, y = pole.position
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];local out={};"
        "for _,e in pairs(s.find_entities_filtered{area={{" + f"{x - reach},{y - reach}" + "},{"
        + f"{x + reach},{y + reach}" + "}},force=f}) do "
        "local p=e.type=='entity-ghost' and e.ghost_prototype or e.prototype;"
        "if p.type~='electric-pole' and p.electric_energy_source_prototype then local b=e.bounding_box;"
        "out[#out+1]=b.left_top.x..','..b.left_top.y..','..b.right_bottom.x..','..b.right_bottom.y end end;"
        "rcon.print(table.concat(out,';'))"
    )
    raw = client.command("/sc " + lua).strip()
    return [tuple(float(v) for v in record.split(",")) for record in raw.split(";") if record]


def _connect(client: RconClient, surface: str, force: str, site: Point,
             neighbours: Sequence[tuple[str, Point]]) -> None:
    """Wire the replacement to the old pole's neighbours before the old pole goes."""
    targets = ",".join("{" + f"{at[0]},{at[1]}" + "}" for _name, at in neighbours)
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];"
        "local function pole(x,y) for _,e in pairs(s.find_entities_filtered{position={x,y},radius=0.01,force=f}) do "
        "if e.type=='electric-pole' then return e end end end;"
        "local r=pole(" + f"{site[0]},{site[1]}" + ");if not r then rcon.print('replacement_not_built');return end;"
        "local rc=r.get_wire_connector(defines.wire_connector_id.pole_copper,true);"
        "for _,t in pairs({" + targets + "}) do local n=pole(t[1],t[2]);if not n then "
        "rcon.print('wire_neighbor_missing');return end;"
        "local nc=n.get_wire_connector(defines.wire_connector_id.pole_copper,true);"
        "pcall(function() rc.connect_to(nc,false) end);local linked=false;"
        "for _,connection in pairs(rc.connections) do if connection.target==nc then linked=true end end;"
        "if not linked then rcon.print('wire_link_unverified');return end end;rcon.print('ok')"
    )
    result = client.command("/sc " + lua).strip()
    if result != "ok":
        raise PoleObstruction(
            f"replacement pole at {site} has no verified live connection to every old neighbour; keeping the old pole",
            {"position": list(site), "reason": result or "wire_verification_missing"},
        )


def relocate_obstructing_poles(
    client: RconClient, bridge: GameBridge, surface: str, force: str, plan: dict, name: str,
    emit: Callable[[str], None], submit: Callable[..., dict],
) -> int:
    """Clear own poles from ``plan``'s footprint; returns how many were moved or recovered."""
    if name.startswith(RELOCATION_PREFIX) or not hasattr(client, "command"):
        return 0
    tiles = _plan_tiles(plan)
    if not tiles:
        return 0
    planned = {(action.get("entity"), action["position"]["x"], action["position"]["y"])
               for phase in plan["phases"] for action in phase["actions"] if "position" in action}
    margin = max(spec["wire"] for spec in POLE_SPECS.values())
    low = (min(x for x, _ in tiles) - margin, min(y for _, y in tiles) - margin)
    high = (max(x for x, _ in tiles) + 1 + margin, max(y for _, y in tiles) + 1 + margin)
    poles = _survey_poles(client, surface, force, low, high)
    in_way = [pole for pole in poles if pole.tiles & tiles
              and (pole.name, pole.position[0], pole.position[1]) not in planned]
    if not in_way:
        return 0
    blocked = set(tiles).union(*(pole.tiles for pole in in_way))
    blocked |= live_base.occupied_tiles(client, surface, low, high)
    others = [(pole.name, pole.position) for pole in poles]
    for pole in in_way:
        needs = dependents((pole.name, pole.position), others, _survey_consumers(client, surface, force, pole))
        neighbours = [n for n in pole.neighbours if n[1] != pole.position]
        if needs or len(neighbours) > 1:
            site = replacement_site(pole.name, pole.position, neighbours, needs, blocked)
            if site is None:
                raise PoleObstruction(
                    f"{name}: own {pole.name} at {pole.position} blocks the plan and no site keeps "
                    f"its {len(neighbours)} wire link(s) and {len(needs)} dependent(s) powered",
                    {"pole": pole.name, "position": list(pole.position),
                     "neighbours": len(neighbours), "dependents": len(needs)},
                )
            emit(f"  POLE RELOCATION: {pole.name} at {pole.position} blocks {name}; "
                 f"building its replacement at {site} first")
            submit(client, bridge, surface, {"surface": surface, "force": force, "phases": [{
                "name": RELOCATION_PREFIX, "actions": [{"action_type": "place_entity", "entity": pole.name,
                                                         "position": {"x": site[0], "y": site[1]}}]}]},
                   f"{RELOCATION_PREFIX}_{pole.name}", emit)
            _connect(client, surface, force, site, neighbours)
            blocked |= footprint_tile_indices(site, int(_spec(pole.name)["size"]))
        else:
            emit(f"  POLE RELOCATION: {pole.name} at {pole.position} blocks {name} and powers nothing; recovering it")
        if pole.ghost:
            live_base.remove_ghost_at(client, surface, force, pole.name, pole.position)
        else:
            retire_entities_via_bots(client, bridge, surface, force, {"phases": [{
                "name": RELOCATION_PREFIX, "actions": [{"action_type": "remove_entity", "entity": pole.name,
                                                         "position": {"x": pole.position[0], "y": pole.position[1]}}]}]},
                f"obstructing_{pole.name}", emit)
    return len(in_way)
