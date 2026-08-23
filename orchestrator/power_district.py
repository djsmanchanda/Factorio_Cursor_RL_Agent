# Path: orchestrator/power_district.py
# Purpose: Deterministic rectangular solar districts with atomic footprint validation.

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence

from planners.infrastructure_geometry import boxes_overlap, footprint_tile_indices
from planners.plan_validation import ENTITY_FOOTPRINTS
from orchestrator.live_base import TelemetryError
from tools.rcon_client import RconClient

Point = tuple[float, float]
Emit = Callable[[str], None]

SOLAR_KW = 60.0
ACCUMULATOR_MJ = 5.0
DAY_SECONDS = 250.0
NIGHT_SECONDS = 41.67
SAFETY_MARGIN = 1.25
CLEARANCE_TILES = 2
MAX_CANDIDATES_PER_PASS = 64
@dataclass(frozen=True)
class UnitTemplate:
    name: str
    width: int
    height: int
    stride_x: int
    stride_y: int
    placements: tuple[tuple[str, float, float], ...]
    connection_points: tuple[Point, ...]

    @property
    def panels(self) -> int:
        return self.count("solar-panel")

    @property
    def accumulators(self) -> int:
        return self.count("accumulator")

    @property
    def generation_kw(self) -> float:
        return self.panels * SOLAR_KW

    @property
    def storage_mj(self) -> float:
        return self.accumulators * ACCUMULATOR_MJ

    @property
    def materials(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for entity, _x, _y in self.placements:
            result[entity] = result.get(entity, 0) + 1
        return result

    def count(self, entity: str) -> int:
        return sum(1 for name, _x, _y in self.placements if name == entity)


EARLY_MEDIUM_UNIT = UnitTemplate(
    name="medium-pole-8-panel",
    width=14,
    height=10,
    stride_x=18,
    stride_y=12,
    placements=(
        ("medium-electric-pole", 4.0, 4.0),
        ("medium-electric-pole", 9.0, 4.0),
        ("medium-electric-pole", 13.0, 4.0),
        ("medium-electric-pole", 4.0, 9.0),
        ("medium-electric-pole", 13.0, 9.0),
        ("solar-panel", 2.5, 1.5),
        ("solar-panel", 5.5, 1.5),
        ("solar-panel", 8.5, 1.5),
        ("solar-panel", 11.5, 1.5),
        ("solar-panel", 2.5, 4.5),
        ("solar-panel", 5.5, 4.5),
        ("solar-panel", 8.5, 4.5),
        ("solar-panel", 11.5, 4.5),
        ("accumulator", 2.5, 7.5),
        ("accumulator", 5.5, 7.5),
        ("accumulator", 8.5, 7.5),
    ),
    connection_points=((4.0, 4.0), (9.0, 4.0)),
)


LARGER_SUBSTATION_UNIT = UnitTemplate(
    name="substation-12x4",
    width=18,
    height=20,
    stride_x=25,
    stride_y=24,
    placements=(
        ("substation", 8.0, 2.0),
        ("substation", 8.0, 15.0),
        ("medium-electric-pole", 1.0, 3.0),
        ("medium-electric-pole", 17.0, 3.0),
        ("medium-electric-pole", 1.0, 14.0),
        ("medium-electric-pole", 17.0, 14.0),
        *[
            (entity, x, y)
            for y in (5.5, 8.5, 11.5)
            for x in (2.5, 5.5, 11.5, 14.5)
            for entity in ("solar-panel",)
        ],
        ("accumulator", 2.5, 14.5),
        ("accumulator", 5.5, 14.5),
        ("accumulator", 11.5, 14.5),
        ("accumulator", 14.5, 14.5),
    ),
    connection_points=((8.0, 2.0), (8.0, 15.0)),
)


TEMPLATES = {
    "early": EARLY_MEDIUM_UNIT,
    "substation": LARGER_SUBSTATION_UNIT,
}


def template_for(substation_available: bool) -> UnitTemplate:
    return LARGER_SUBSTATION_UNIT if substation_available else EARLY_MEDIUM_UNIT


def district_origin(reference: Point, substation_available: bool) -> Point:
    """Keep the first cells close enough for service, but outside starter rows."""
    offset = (-72.0, -56.0) if substation_available else (-48.0, -36.0)
    return (math.floor(reference[0] + offset[0]), math.floor(reference[1] + offset[1]))


def cell_origin(
    index: int, origin: Point, template: UnitTemplate,
) -> tuple[float, float]:
    columns = max(1, 8)
    row, column = divmod(index, columns)
    return (
        origin[0] + column * template.stride_x,
        origin[1] + row * template.stride_y,
    )


def absolute_placements(
    index: int, origin: Point, template: UnitTemplate,
) -> list[tuple[str, float, float]]:
    cell_x, cell_y = cell_origin(index, origin, template)
    return [(name, cell_x + x, cell_y + y) for name, x, y in template.placements]


def unit_bounds(index: int, origin: Point, template: UnitTemplate) -> tuple[Point, Point]:
    left, top = cell_origin(index, origin, template)
    return (left, top), (left + template.width, top + template.height)


def clearance_bounds(
    index: int, origin: Point, template: UnitTemplate,
    padding: int = CLEARANCE_TILES,
) -> tuple[Point, Point]:
    (left, top), (right, bottom) = unit_bounds(index, origin, template)
    return (left - padding, top - padding), (right + padding, bottom + padding)


def unit_tiles(index: int, origin: Point, template: UnitTemplate) -> set[tuple[int, int]]:
    tiles: set[tuple[int, int]] = set()
    for entity, x, y in absolute_placements(index, origin, template):
        tiles |= footprint_tile_indices((x, y), ENTITY_FOOTPRINTS.get(entity, 1))
    return tiles


def build_unit_plan(
    *, surface: str, force: str, index: int, origin: Point,
    template: UnitTemplate,
) -> dict:
    actions = [
        {
            "action_type": "place_entity",
            "entity": entity,
            "position": {"x": round(x, 3), "y": round(y, 3)},
            "power_unit_index": index,
        }
        for entity, x, y in absolute_placements(index, origin, template)
    ]
    (min_x, min_y), (max_x, max_y) = unit_bounds(index, origin, template)
    return {
        "phases": [{
            "name": f"power_unit_{template.name}_{index:04d}",
            "actions": actions,
        }],
        "surface": surface,
        "force": force,
        "atomic": True,
        "power_unit": {
            "district_version": 1,
            "index": index,
            "origin": list(cell_origin(index, origin, template)),
            "bounds": [list(unit_bounds(index, origin, template)[0]),
                       list(unit_bounds(index, origin, template)[1])],
            "connection_point": list(template.connection_points[0]),
        },
    }


def _entity_tiles(record: Mapping[str, object]) -> set[tuple[int, int]]:
    position = record.get("position")
    if not isinstance(position, tuple) or len(position) != 2:
        return set()
    name = str(record.get("build_name") or record.get("name") or "")
    size = ENTITY_FOOTPRINTS.get(name, 1)
    return footprint_tile_indices(position, size)


def _overlaps_unit(
    record: Mapping[str, object], index: int, origin: Point,
    template: UnitTemplate,
) -> bool:
    record_tiles = _entity_tiles(record)
    return bool(record_tiles & unit_tiles(index, origin, template))


def classify_unit(
    records: Sequence[Mapping[str, object]], *, index: int, origin: Point,
    template: UnitTemplate,
) -> tuple[str, list[Mapping[str, object]]]:
    expected: dict[tuple[float, float], str] = {
        (x, y): name for name, x, y in absolute_placements(index, origin, template)
    }
    built: set[tuple[float, float]] = set()
    ghosts: set[tuple[float, float]] = set()
    foreign: list[Mapping[str, object]] = []
    for record in records:
        position = record.get("position")
        if not isinstance(position, tuple):
            continue
        exact = next(
            (
                (x, y) for x, y in expected
                if _entity_tiles(record) & footprint_tile_indices((x, y), ENTITY_FOOTPRINTS.get(expected[(x, y)], 1))
            ),
            None,
        )
        build_name = str(record.get("ghost_name") or record.get("tile_name") or record.get("name") or "")
        if exact is not None and build_name == expected[exact]:
            if record.get("is_ghost"):
                ghosts.add(exact)
            else:
                built.add(exact)
        elif _overlaps_unit(record, index, origin, template):
            foreign.append(record)
    if foreign:
        return "obstructed_unrelated", foreign
    if built == set(expected):
        return "complete_owned", []
    if built or ghosts:
        return "pending_owned" if ghosts else "incomplete_owned", []
    return "empty_available", []


def validate_candidate(
    *,
    index: int,
    origin: Point,
    template: UnitTemplate,
    occupied_tiles: set[tuple[int, int]],
    deconstruction_tiles: set[tuple[int, int]],
    reserved_tiles: set[tuple[int, int]],
    pending_plan_tiles: set[tuple[int, int]],
) -> str | None:
    claimed = unit_tiles(index, origin, template)
    for label, blocked in (
        ("live_entity_or_terrain", occupied_tiles),
        ("deconstruction_order", deconstruction_tiles),
        ("reserved_corridor", reserved_tiles),
        ("pending_plan", pending_plan_tiles),
    ):
        overlap = claimed & blocked
        if overlap:
            first = min(overlap)
            return f"{label}:({first[0]},{first[1]})"
    return None


def coverage_faults(index: int, origin: Point, template: UnitTemplate) -> list[str]:
    supply_radius = 3.5 if template is EARLY_MEDIUM_UNIT else 9.0
    poles = [
        (x, y) for name, x, y in absolute_placements(index, origin, template)
        if name in {"medium-electric-pole", "substation"}
    ]
    faults = []
    for entity, x, y in absolute_placements(index, origin, template):
        if entity not in {"solar-panel", "accumulator"}:
            continue
        half = ENTITY_FOOTPRINTS.get(entity, 1) / 2
        covered = any(
            max(abs(x-pole_x), abs(y-pole_y)) - half <= supply_radius + 0.001
            for pole_x, pole_y in poles
        )
        if not covered:
            faults.append(f"{entity}@({x},{y})")
    return faults


def usable_metrics(
    firm_generation_kw: float, solar_generation_kw: float,
    storage_mj: float, peak_load_kw: float,
) -> dict[str, float]:
    effective_solar = solar_generation_kw * 0.7
    usable_generation = firm_generation_kw + effective_solar
    target_load = peak_load_kw * SAFETY_MARGIN
    night_load_kw = max(0.0, target_load - firm_generation_kw)
    night_energy_mj = night_load_kw * NIGHT_SECONDS / 1000.0
    recharge_surplus_kw = night_energy_mj / DAY_SECONDS * 1000.0
    return {
        "usable_generation_kw": usable_generation,
        "generation_target_kw": target_load,
        "storage_mj": storage_mj,
        "storage_target_mj": night_energy_mj,
        "recharge_surplus_kw": effective_solar - night_load_kw,
        "recharge_target_kw": (
            recharge_surplus_kw + SAFETY_MARGIN * 10.0
            if night_load_kw > 0.0 else 0.0
        ),
    }


def required_units(
    template: UnitTemplate, *, firm_generation_kw: float,
    solar_generation_kw: float, storage_mj: float,
    peak_load_kw: float,
) -> int:
    arguments = {
        "firm_generation_kw": firm_generation_kw,
        "solar_generation_kw": solar_generation_kw,
        "storage_mj": storage_mj,
        "peak_load_kw": peak_load_kw,
    }
    for name, value in arguments.items():
        if not math.isfinite(value):
            raise TelemetryError(f"power-sizing {name} must be finite, got {value}")
        if value < 0:
            raise ValueError(f"power-sizing {name} must be non-negative, got {value}")
    metrics = usable_metrics(
        firm_generation_kw, solar_generation_kw, storage_mj, peak_load_kw,
    )
    generation_gap = max(0.0, metrics["generation_target_kw"] - metrics["usable_generation_kw"])
    storage_gap = max(0.0, metrics["storage_target_mj"] - metrics["storage_mj"])
    recharge_gap = max(0.0, metrics["recharge_target_kw"] - metrics["recharge_surplus_kw"])
    gaps = []
    if generation_gap:
        divisor = template.generation_kw
        if not math.isfinite(divisor) or divisor <= 0:
            raise ValueError(
                "power template generation capacity must be finite and positive "
                f"for a generation gap, got {divisor}"
            )
        gaps.append(generation_gap / divisor)
    if storage_gap:
        divisor = template.storage_mj
        if not math.isfinite(divisor) or divisor <= 0:
            raise ValueError(
                "power template storage capacity must be finite and positive "
                f"for a storage gap, got {divisor}"
            )
        gaps.append(storage_gap / divisor)
    if recharge_gap:
        divisor = template.generation_kw * 0.7
        if not math.isfinite(divisor) or divisor <= 0:
            raise ValueError(
                "power template recharge capacity must be finite and positive "
                f"for a recharge gap, got {divisor}"
            )
        gaps.append(recharge_gap / divisor)
    return math.ceil(max(gaps, default=0.0) - 1e-9)


def state_path(script_output: Path | str) -> Path:
    return Path(script_output) / "logs" / "deterministic-power-state.json"


def reservations_path(script_output: Path | str) -> Path:
    return Path(script_output) / "logs" / "deterministic-plan-reservations.json"


def reservation_log_path(script_output: Path | str) -> Path:
    return Path(script_output) / "logs" / "deterministic-plan-reservations.jsonl"


def _read_json(path: Path, default: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def load_state(script_output: Path | str) -> dict:
    value = _read_json(state_path(script_output), {})
    return value if isinstance(value, dict) else {}


def save_state(script_output: Path | str, state: Mapping[str, object]) -> None:
    path = state_path(script_output)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_reserved_tiles(script_output: Path | str) -> set[tuple[int, int]]:
    tiles: set[tuple[int, int]] = set()
    log_path = reservation_log_path(script_output)
    if log_path.exists():
        try:
            lines = log_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            for tile in entry.get("tiles", []):
                if isinstance(tile, list) and len(tile) == 2:
                    tiles.add((int(tile[0]), int(tile[1])))
    payload = _read_json(reservations_path(script_output), [])
    if isinstance(payload, list):
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            for tile in entry.get("tiles", []):
                if isinstance(tile, list) and len(tile) == 2:
                    tiles.add((int(tile[0]), int(tile[1])))
    return tiles


def plan_footprint_tiles(plan: Mapping[str, object]) -> set[tuple[int, int]]:
    tiles: set[tuple[int, int]] = set()
    for phase in plan.get("phases", []):
        for action in phase.get("actions", []):
            position = action.get("position")
            if not isinstance(position, dict) or "x" not in position or "y" not in position:
                continue
            point = (float(position["x"]), float(position["y"]))
            entity = action.get("entity", action.get("tile", ""))
            tiles |= footprint_tile_indices(
                point, ENTITY_FOOTPRINTS.get(str(entity), 1),
            )
    return tiles


def append_plan_reservation(
    script_output: Path | str, name: str, plan: Mapping[str, object],
) -> None:
    """Append exact claimed tiles so later candidates cannot reuse pending work."""
    path = reservation_log_path(script_output)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "name": name,
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "tiles": sorted(plan_footprint_tiles(plan)),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, separators=(",", ":")) + "\n")


def network_peak_consumption_kw(
    client: RconClient, surface: str, force: str, near: Point,
    *, emit: Emit | None = None,
) -> float | None:
    """Conservative connected prototype demand on the roboport's network."""
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];"
        "local nx,ny=" + str(near[0]) + "," + str(near[1]) + ";"
        "local best,bd=nil,1e18;"
        "for _,e in pairs(s.find_entities_filtered{name='roboport',force=f}) do "
        "local d=(e.position.x-nx)^2+(e.position.y-ny)^2;if d<bd then bd=d;best=e end end;"
        "if not best then rcon.print('NONE') return end;"
        "local ok,net=pcall(function() return best.electric_network_id end);"
        "if not ok or net==nil then rcon.print('NONE') return end;"
        "local total=0;"
        "for _,e in pairs(s.find_entities_filtered{force=f}) do "
        "local okid,id=pcall(function() return e.electric_network_id end);"
        "if okid and id==net then local interface_valid=false "
        "if e.type=='electric-energy-interface' then "
        "local oki,i=pcall(function() return e.power_usage end);"
        "if oki and type(i)=='number' and i==i and i~=math.huge and i~=-math.huge "
        "then total=total+i/1000; local interface_valid=true "
        "elseif oki and type(i)=='number' then "
        "rcon.print('INVALID|'..e.type..'|'..e.name..'|'..e.position.x..','"
        "..e.position.y..'|power_usage|'..tostring(i)) return;"
        "else interface_valid=false end;"
        "else local passive=false;"
        "for _,kind in ipairs({'accumulator','burner-generator',"
        "'electric-pole','fusion-generator','generator','solar-panel'}) do "
        "if e.type==kind then passive=true end end;"
        "if not passive then "
        "local kw=nil;"
        "local oku,u=pcall(function() return e.prototype.get_max_energy_usage() end);"
        "if oku and type(u)=='number' and u==u and u~=math.huge and u~=-math.huge "
        "then kw=u elseif oku and type(u)=='number' then "
        "rcon.print('INVALID|'..e.type..'|'..e.name..'|'..e.position.x..','"
        "..e.position.y..'|get_max_energy_usage|'..tostring(u)) return end;"
        "if kw==nil then "
        "local okp,p=pcall(function() return e.prototype.energy_usage end);"
        "if okp and type(p)=='number' and p==p and p~=math.huge and p~=-math.huge "
        "then kw=p elseif okp and type(p)=='number' then "
        "rcon.print('INVALID|'..e.type..'|'..e.name..'|'..e.position.x..','"
        "..e.position.y..'|energy_usage|'..tostring(p)) return end end;"
        "if kw==nil then "
        "local oki,i=pcall(function() return e.power_usage end);"
        "if oki and type(i)=='number' and i==i and i~=math.huge and i~=-math.huge "
        "then kw=i/1000 elseif oki and type(i)=='number' then "
        "rcon.print('INVALID|'..e.type..'|'..e.name..'|'..e.position.x..','"
        "..e.position.y..'|power_usage|'..tostring(i)) return end end;"
        "if kw==nil then rcon.print('INVALID|'..e.type..'|'..e.name..'|'"
        "..e.position.x..','..e.position.y..'|consumer_demand|unavailable') return end;"
        "total=total+kw end end end end;"
        "rcon.print('OK|'..tostring(total))"
    )
    raw = client.command("/sc " + lua).strip()
    if raw == "NONE":
        if emit:
            emit("  POWER DISTRICT skipped: no roboport electric network was available")
        return None
    if raw.startswith("INVALID|"):
        detail = raw.removeprefix("INVALID|")
        if emit:
            emit(f"  POWER DISTRICT skipped: invalid consumer telemetry: {detail}")
        return None
    try:
        label, encoded = raw.split("|", 1)
        if label != "OK":
            raise ValueError(raw)
        value = float(encoded)
    except (ValueError, TypeError):
        if emit:
            emit(f"  POWER DISTRICT skipped: malformed consumer telemetry: {raw!r}")
        return None
    if not math.isfinite(value) or value < 0:
        if emit:
            emit(f"  POWER DISTRICT skipped: invalid consumer demand: {value}")
        return None
    return value


def ensure_power_capacity(
    *, client: RconClient, bridge: object, surface: str, force: str,
    near: Point, script_output: Path | str, emit: Emit,
    submit: Callable[..., dict], max_units: int = 128,
) -> bool:
    """Build at most one validated template unit per call; stop when sizing converges."""
    from orchestrator import live_base

    try:
        firm = live_base.network_firm_generation_kw(client, surface, force, near)
        total_generation = live_base.network_generation_kw(client, surface, force, near)
        peak = network_peak_consumption_kw(
            client, surface, force, near, emit=emit,
        )
        storage_mj = live_base.network_accumulator_storage_mj(
            client, surface, force, near,
        )
    except TelemetryError as exc:
        emit(f"  POWER DISTRICT skipped: invalid telemetry: {exc}")
        return False
    if firm is None or total_generation is None or peak is None or storage_mj is None:
        emit("  POWER DISTRICT skipped: electric network survey was unavailable")
        return False
    solar = max(0.0, total_generation - firm)
    stock = live_base.available_items(client, surface, force)
    substation_available = (
        int(stock.get("substation", 0)) > 0
        or _has_built(client, surface, force, "substation")
    )
    template = template_for(substation_available)
    origin = district_origin(near, substation_available)
    state = load_state(script_output)
    if state.get("version") != 1 or state.get("origin") != list(origin) or state.get("template") != template.name:
        state = {"version": 1, "origin": list(origin), "template": template.name,
                 "next_index": 0, "blocked_indices": {}, "active_index": None}
    next_index = int(state.get("next_index", 0))
    try:
        measured_required = required_units(
            template,
            firm_generation_kw=firm,
            solar_generation_kw=solar,
            storage_mj=storage_mj,
            peak_load_kw=max(peak, 100.0),
        )
    except (TelemetryError, ValueError) as exc:
        emit(f"  POWER DISTRICT skipped: {exc}")
        return False
    desired = min(measured_required, max_units)
    if measured_required > max_units:
        emit(
            f"POWER DISTRICT policy cap: measured={measured_required}, "
            f"max_units={max_units}; expanding one unit at a time"
        )
    if desired <= 0:
        metrics = usable_metrics(firm, solar, storage_mj, peak)
        emit(
            "POWER DISTRICT converged: "
            f"usable={metrics['usable_generation_kw']:.0f}kW/"
            f"{metrics['generation_target_kw']:.0f}kW, "
            f"storage={metrics['storage_mj']:.1f}MJ/{metrics['storage_target_mj']:.1f}MJ"
        )
        if state.get("active_index") is not None:
            state["active_index"] = None
            save_state(script_output, state)
        return False
    active_index = state.get("active_index")
    if active_index is not None:
        emit(f"POWER UNIT {active_index} is still marked active; waiting for reconciliation")
        return False
    emit(
        f"POWER DISTRICT: peak={peak:.0f}kW firm={firm:.0f}kW solar={solar:.0f}kW; "
        f"{desired} {template.name} unit(s) required"
    )
    reserved = load_reserved_tiles(script_output)
    scanned = 0
    while next_index < max_units and scanned < MAX_CANDIDATES_PER_PASS:
        index = next_index
        scanned += 1
        records = live_base.area_entity_records(
            client, surface, force, *clearance_bounds(index, origin, template),
        )
        classification, foreign = classify_unit(
            records, index=index, origin=origin, template=template,
        )
        if classification == "complete_owned":
            next_index += 1
            continue
        if classification == "pending_owned":
            emit(f"POWER UNIT {index} pending; waiting without another submission")
            break
        if classification == "incomplete_owned":
            plan = build_unit_plan(surface=surface, force=force, index=index,
                                   origin=origin, template=template)
            submit(client, bridge, surface, plan,
                   f"repair_power_unit_{index}", emit)
            emit(f"POWER UNIT {index} repaired as one complete idempotent unit")
            break
        if classification == "obstructed_unrelated":
            detail = foreign[0].get("name", "unknown") if foreign else "unknown"
            state.setdefault("blocked_indices", {})[str(index)] = str(detail)
            next_index += 1
            continue
        occupied = live_base.occupied_tiles(
            client, surface, *clearance_bounds(index, origin, template),
            include_clutter=True, include_resources=True,
        )
        deconstruction = live_base.deconstruction_tiles(
            client, surface, *clearance_bounds(index, origin, template),
        )
        reason = validate_candidate(
            index=index, origin=origin, template=template,
            occupied_tiles=occupied,
            deconstruction_tiles=deconstruction,
            reserved_tiles=reserved,
            pending_plan_tiles=set(),
        )
        if reason is not None:
            state.setdefault("blocked_indices", {})[str(index)] = reason
            next_index += 1
            continue
        faults = coverage_faults(index, origin, template)
        if faults:
            raise RuntimeError("power template coverage fault: " + ", ".join(faults))
        missing = {
            item: count - int(stock.get(item, 0))
            for item, count in template.materials.items()
            if int(stock.get(item, 0)) < count
        }
        if missing:
            emit(
                "POWER UNIT deferred; full unit materials unavailable: " +
                ", ".join(f"{item} short {count}" for item, count in sorted(missing.items()))
            )
            break
        plan = build_unit_plan(surface=surface, force=force, index=index,
                               origin=origin, template=template)
        state["active_index"] = index
        save_state(script_output, state)
        try:
            submit(client, bridge, surface, plan, f"power_unit_{index}", emit)
        except Exception:
            state["active_index"] = None
            state["last_failure"] = {"index": index, "stage": "submission"}
            save_state(script_output, state)
            raise
        after = live_base.area_entity_records(
            client, surface, force, *unit_bounds(index, origin, template),
        )
        final_classification, final_foreign = classify_unit(
            after, index=index, origin=origin, template=template,
        )
        if final_classification != "complete_owned":
            state["last_failure"] = {
                "index": index, "classification": final_classification,
                "foreign": [dict(item) for item in final_foreign[:3]],
            }
            save_state(script_output, state)
            raise RuntimeError(
                f"power unit {index} did not become complete ({final_classification})"
            )
        state["next_index"] = index + 1
        state["completed_count"] = int(state.get("completed_count", 0)) + 1
        state["active_index"] = None
        save_state(script_output, state)
        emit(f"POWER UNIT {index} complete at {cell_origin(index, origin, template)}")
        return True
    state["next_index"] = next_index
    save_state(script_output, state)
    return False


def _has_built(
    client: RconClient, surface: str, force: str, entity: str,
) -> bool:
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "local n=s.count_entities_filtered{name='" + entity + "',force='"
        + force + "'};rcon.print(tostring(n>0))"
    )
    return client.command("/sc " + lua).strip().lower() == "true"
