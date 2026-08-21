# Path: orchestrator/extraction_state.py
# Purpose: Read-only live observations needed to reconcile and size local resource extraction.

from __future__ import annotations

import math
from dataclasses import dataclass

from tools.rcon_client import RconClient

Point = tuple[float, float]
RESERVED_PAIR_COLUMNS = 10
# A smelter site may drift this far from its mine's output
# (find_clear_area max_radius 60 plus footprint), so a pending system within
# this reach of the observed output belongs to THAT mine. Beyond it a ghost is
# some other system's business.
_PENDING_SMELTER_RADIUS = 96

def _sc(client: RconClient, lua: str) -> str:
    return client.command("/sc " + lua).strip()


@dataclass(frozen=True)
class ResourceMine:
    output: Point
    drill_count: int
    pending: bool = False
    expansion_step: int = -1
    row_capacity: int = 0
    belt_y: float | None = None

    @property
    def shared_belt_y(self) -> float:
        return self.output[1] if self.belt_y is None else self.belt_y


@dataclass(frozen=True)
class ExtractionEntity:
    kind: str
    position: Point
    built: bool


def mining_productivity_bonus(
    client: RconClient, force: str,
) -> float:
    """Read the force bonus used by newly placed electric mining drills."""
    raw = _sc(
        client,
        "local f=game.forces['" + force + "'];"
        "if not f then rcon.print('NONE') else "
        "rcon.print(f.mining_drill_productivity_bonus) end",
    )
    if raw == "NONE":
        raise ValueError(f"Unknown force {force!r}")
    bonus = float(raw)
    if not math.isfinite(bonus) or bonus < 0:
        raise ValueError(f"Invalid mining productivity bonus: {raw!r}")
    return bonus



def pending_plate_smelter(
    client: RconClient, surface: str, force: str, ore: str, near: Point,
) -> bool:
    """Whether an UNBUILT managed furnace system is still constructing near
    `near`.

    The test is deliberately coarse: any electric-furnace GHOST within
    _PENDING_SMELTER_RADIUS of the mine's output means the system serving that
    mine is mid-construction, so more demand must wait rather than open a
    duplicate system. The previous detector walked HORIZONTAL furnace rows and
    their chest positions, but the modular Start/Repeat/End templates lay
    furnaces out in vertical columns -- observed live at the landfill refinery
    (54.5/-101.5..-107.5) -- so it never recognized a pending modular system,
    the runner opened a second one, and its survey collided with the first
    system's own ore bridge.

    A furnace is deliberately NOT judged on its recipe: unlike an assembling
    machine it has no settable recipe and auto-selects one from the first ore
    inserted, so `get_recipe()` is nil for every furnace that has not been fed
    yet. A fully built, powered row that had never received ore must stay
    NOT-pending (that starvation has its own remediation path); only ghosts
    mean construction is still in flight.

    `ore` is kept for call-site stability; ghost proximity alone answers the
    duplicate question.
    """
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];"
        "local area={{" + str(near[0] - _PENDING_SMELTER_RADIUS) + ","
        + str(near[1] - _PENDING_SMELTER_RADIUS) + "},{"
        + str(near[0] + _PENDING_SMELTER_RADIUS) + ","
        + str(near[1] + _PENDING_SMELTER_RADIUS) + "}};"
        "for _,g in pairs(s.find_entities_filtered{type='entity-ghost',force=f,area=area}) do "
        "if g.ghost_name=='electric-furnace' then rcon.print('1') return end end;"
        "rcon.print('0')"
    )
    return _sc(client, lua) == "1"
def _parse_entities(raw: str) -> list[ExtractionEntity]:
    if not raw:
        return []
    entities = []
    for record in raw.split(";"):
        kind, x, y, built = record.split(",")
        entities.append(ExtractionEntity(kind, (float(x), float(y)), built == "1"))
    return entities


def _classify_direct_mines(
    entities: list[ExtractionEntity], near: Point,
) -> list[ResourceMine]:
    """Classify legacy terminal chests and continuous-belt side taps."""
    by_kind: dict[str, dict[Point, bool]] = {}
    for entity in entities:
        states = by_kind.setdefault(entity.kind, {})
        states[entity.position] = states.get(entity.position, False) or entity.built

    drills = by_kind.get("drill", {})
    belts = by_kind.get("belt", {})
    inserters = by_kind.get("inserter", {})
    candidates: list[tuple[float, ResourceMine]] = []
    for chest, chest_built in by_kind.get("chest", {}).items():
        for expansion_step in (-1, 1):
            layouts = [
                (chest[1], (chest[0] + expansion_step, chest[1]),
                 (chest[0] + 2 * expansion_step, chest[1]), False),
                (chest[1] + 2, (chest[0], chest[1] + 1),
                 (chest[0], chest[1] + 2), True),
            ]
            for belt_y, inserter, endpoint, side_tap in layouts:
                if inserter not in inserters or endpoint not in belts:
                    continue
                row: dict[Point, bool] = {}
                for index in range(34):
                    position = (
                        chest[0] + expansion_step * (4 + 3 * index), belt_y - 2,
                    )
                    if position not in drills:
                        break
                    row[position] = drills[position]
                if not row:
                    continue
                expected_count = len(row)
                farthest_x = chest[0] + expansion_step * (4 + 3 * (expected_count - 1))
                lo, hi = sorted((endpoint[0], farthest_x))
                belt_positions = {
                    (lo + offset, belt_y)
                    for offset in range(round(hi - lo) + 1)
                }
                complete = (
                    chest_built
                    and inserters[inserter]
                    and all(row.values())
                    and all(belts.get(position, False) for position in belt_positions)
                )
                row_capacity = (
                    expected_count + RESERVED_PAIR_COLUMNS
                    if expansion_step > 0 else 0
                )
                mine = ResourceMine(
                    chest, expected_count, pending=not complete,
                    expansion_step=expansion_step, row_capacity=row_capacity,
                    belt_y=belt_y if side_tap else None,
                )
                distance = (chest[0] - near[0]) ** 2 + (chest[1] - near[1]) ** 2
                candidates.append((distance, mine))
    if candidates:
        return [mine for _distance, mine in sorted(candidates, key=lambda item: item[0])]

    direct_candidates: list[tuple[float, ResourceMine]] = []
    belt_rows = sorted({position[1] for position in belts})
    for belt_y in belt_rows:
        row_drills = {
            position: built for position, built in drills.items()
            if (math.isclose(position[1] + 2, belt_y)
                or math.isclose(position[1] - 2, belt_y))
        }
        if not row_drills:
            continue
        drill_xs = sorted({position[0] for position in row_drills})
        output_x = min(position[0] for position in belts if position[1] == belt_y)
        required_end = max(drill_xs) + 2
        required_belts = [
            (output_x + offset, belt_y)
            for offset in range(round(required_end - output_x) + 1)
        ]
        complete = (
            all(row_drills.values())
            and all(belts.get(position, False) for position in required_belts)
        )
        mine = ResourceMine(
            (output_x, belt_y), len(drill_xs), pending=not complete,
            expansion_step=1, row_capacity=len(drill_xs) + RESERVED_PAIR_COLUMNS,
            belt_y=belt_y,
        )
        distance = (output_x - near[0]) ** 2 + (belt_y - near[1]) ** 2
        direct_candidates.append((distance, mine))
    if direct_candidates:
        return [mine for _distance, mine in sorted(direct_candidates, key=lambda item: item[0])]
    pending_drills = list(drills)
    if pending_drills:
        drill = min(
            pending_drills,
            key=lambda p: (p[0] - near[0]) ** 2 + (p[1] - near[1]) ** 2,
        )
        return [ResourceMine((drill[0] + 4, drill[1] + 2), 1, pending=True)]
    return []

def _classify_direct_mine(
    entities: list[ExtractionEntity], near: Point,
) -> ResourceMine | None:
    mines = _classify_direct_mines(entities, near)
    return mines[0] if mines else None


def _resource_mine_entities(
    client: RconClient,
    surface: str,
    force: str,
    resource: str,
    near: Point,
) -> list[ExtractionEntity]:
    """Read entity records needed to reconcile every managed resource mine."""
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "local f=game.forces['" + force + "'];local out={};"
        "local function add(k,e,b) table.insert(out,k..','..e.position.x..','.."
        "e.position.y..','..(b and '1' or '0')) end;"
        "local function ghosts(kind,p) local found={};for _,g in pairs("
        "s.find_entities_filtered{type='entity-ghost',force=f,position=p,radius=0.2}"
        ") do if g.ghost_type==kind then table.insert(found,g) end end return found end;"
        "local drills=s.find_entities_filtered{name='electric-mining-drill',force=f};"
        "for _,d in pairs(drills) do local t=d.mining_target;if t and "
        "t.name=='" + resource + "' then add('drill',d,true) end end;"
        "for _,g in pairs(s.find_entities_filtered{type='entity-ghost',force=f}) do "
        "if g.ghost_name=='electric-mining-drill' and "
        "#s.find_entities_filtered{type='resource',name='" + resource + "',"
        "area={{g.position.x-1.5,g.position.y-1.5},"
        "{g.position.x+1.5,g.position.y+1.5}},limit=1}>0 then "
        "add('drill',g,false) end end;"
        "local belt_area={{" + str(near[0] - 320) + "," + str(near[1] - 320) + "},{"
        + str(near[0] + 320) + "," + str(near[1] + 320) + "}};"
        "for _,e in pairs(s.find_entities_filtered{type='transport-belt',force=f,area=belt_area}) do add('belt',e,true) end;"
        "for _,g in pairs(s.find_entities_filtered{type='entity-ghost',force=f,area=belt_area}) do "
        "if g.ghost_name and g.ghost_name:find('transport-belt',1,true) then add('belt',g,false) end end;"
        "local chests=s.find_entities_filtered{name={'passive-provider-chest','steel-chest'},force=f};"
        "for _,g in pairs(s.find_entities_filtered{type='entity-ghost',force=f}) do "
        "if g.ghost_name=='passive-provider-chest' or g.ghost_name=='steel-chest' "
        "then table.insert(chests,g) end end;"
        "local function record(c,ins,endbelt,belt_y) if #ins>0 and #endbelt>0 then "
        "add('chest',c,c.type~='entity-ghost');"
        "for _,e in pairs(ins) do add('inserter',e,e.type~='entity-ghost') end;"
        "for x=c.position.x-160,c.position.x+160,1 do for _,e in pairs("
        "s.find_entities_filtered{type='transport-belt',force=f,"
        "position={x,belt_y},radius=0.2}) do add('belt',e,true) end;"
        "for _,g in pairs(ghosts('transport-belt',{x,belt_y})) do "
        "add('belt',g,false) end end end end;"
        "for _,c in pairs(chests) do local cp=c.position;local ins={};local endbelt={};"
        "for _,side in pairs({-1,1}) do for _,e in pairs("
        "s.find_entities_filtered{type='inserter',force=f,"
        "position={cp.x+side,cp.y},radius=0.2}) do table.insert(ins,e) end;"
        "for _,g in pairs(ghosts('inserter',{cp.x+side,cp.y})) do table.insert(ins,g) end;"
        "for _,e in pairs(s.find_entities_filtered{type='transport-belt',force=f,"
        "position={cp.x+2*side,cp.y},radius=0.2}) do table.insert(endbelt,e) end;"
        "for _,g in pairs(ghosts('transport-belt',{cp.x+2*side,cp.y})) do "
        "table.insert(endbelt,g) end end;record(c,ins,endbelt,cp.y);"
        "local tapins=s.find_entities_filtered{type='inserter',force=f,"
        "position={cp.x,cp.y+1},radius=0.2};"
        "for _,g in pairs(ghosts('inserter',{cp.x,cp.y+1})) do table.insert(tapins,g) end;"
        "local tapbelt=s.find_entities_filtered{type='transport-belt',force=f,"
        "position={cp.x,cp.y+2},radius=0.2};"
        "for _,g in pairs(ghosts('transport-belt',{cp.x,cp.y+2})) do "
        "table.insert(tapbelt,g) end;record(c,tapins,tapbelt,cp.y+2) end;"
        "rcon.print(table.concat(out,';'))"
    )
    return _parse_entities(_sc(client, lua))


def find_resource_mines(
    client: RconClient, surface: str, force: str, resource: str, near: Point,
) -> list[ResourceMine]:
    """Find every planner-managed mine for one resource, nearest-first."""
    return _classify_direct_mines(
        _resource_mine_entities(client, surface, force, resource, near), near,
    )


def find_resource_mine(
    client: RconClient, surface: str, force: str, resource: str, near: Point,
) -> ResourceMine | None:
    """Compatibility wrapper returning only the nearest managed mine."""
    mines = find_resource_mines(client, surface, force, resource, near)
    return mines[0] if mines else None


def resource_drill_count(
    client: RconClient, surface: str, force: str, resource: str,
) -> int:
    """Count all built drills currently targeting this resource."""
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];"
        "local n=0;for _,d in pairs(s.find_entities_filtered{"
        "name='electric-mining-drill',force=f}) do local t=d.mining_target;"
        "if t and t.name=='" + resource + "' then n=n+1 end end;rcon.print(n)"
    )
    return int(_sc(client, lua))
