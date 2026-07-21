# Path: orchestrator/chain_telemetry.py
# Purpose: Live per-line and per-chain measurement over RCON, shaped for core.bottleneck_diagnosis.diagnose_line.

from __future__ import annotations

from typing import List, Optional

# Layering mirrors core/bottleneck_diagnosis.py: this module only talks to the
# live game (via a GameBridge-shaped object exposing .command(text) -> str)
# and produces plain dicts. It does not import core.bottleneck_diagnosis and
# does not decide anything; it only measures.

SURFACE = "planner-sandbox"
FORCE = "planner"
DEFAULT_BELT_CAPACITY_PER_TILE = 8

# Geometry (per orchestrator brief, 2026-07-21): a line at origin (ox, oy)
# with N machines occupies input belt row oy, input inserters row oy+1,
# machines rows oy+2..oy+4 centered at (ox+3i+1.5, oy+3.5), output inserters
# row oy+5, output belt row oy+6. Lab rows are 3x3 at (ox+3i+1.5, oy+3.5)
# with input belt row oy (no output belt/collectors - labs consume, not emit).
#
# ASSUMPTION (escalate if wrong): the brief does not give an explicit tile for
# the input belt's "start" and "end" sample points. This module samples the
# input belt directly under the first machine center (ox, oy) as START and
# directly under the last machine center (ox + 3*(n-1), oy) as END. That is
# the natural read of "start"/"end" of a line whose machines march east in
# steps of 3, but if the real belt geometry places start/end elsewhere
# (e.g. west of x=0 for chained feeds - see planners/local_layout_planner.py
# CHAINED_BELT_WEST), the sampled tile could land on empty space beside the
# belt rather than on it. The Lua is defensive either way: a missing belt
# entity at the sampled tile returns 0, it never errors.


def _lua_str(value: str) -> str:
    """Escape a value for embedding as a single-quoted Lua string literal."""
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


# Reused across every per-line query: looks up whatever transport-belt entity
# occupies a small box around (x, y) and sums both lanes' item counts. Missing
# entity (belt not built yet, or the sampled tile is off by construction)
# returns 0 rather than erroring - required by the orchestrator brief.
_FILL_HELPER = (
    "local function fill(x,y) "
    "local es=s.find_entities_filtered{type='transport-belt',area={{x-0.4,y-0.4},{x+0.4,y+0.4}}} "
    "if #es==0 then return 0 end local b=es[1] "
    "return b.get_transport_line(1).get_item_count()+b.get_transport_line(2).get_item_count() end "
)


def _build_line_query(line: dict) -> str:
    """Compose the single /sc round trip for one line. Kept defensive: every
    entity lookup degrades to 0/absent instead of raising, since machines,
    belts or collectors may not exist yet mid-construction."""
    ox, oy = line["origin"]
    ox = float(ox)
    oy = float(oy)
    is_lab_row = "labs" in line and "machines" not in line
    count = int(line.get("machines", line.get("labs", 0)) or 0)
    machine_name = line.get("machine_name") or ("lab" if is_lab_row else "assembling-machine-2")
    output_item = line.get("output_item", "")
    surface = line.get("surface", SURFACE)

    n_span = max(count, 1)
    area_x1 = ox - 2
    area_y1 = oy - 1
    area_x2 = ox + 3 * n_span + 2
    area_y2 = oy + 8

    start_x = ox
    end_x = ox + 3 * max(count - 1, 0)

    parts: List[str] = []
    parts.append("/sc local s=game.surfaces['" + _lua_str(surface) + "'] ")
    parts.append("local f=game.forces['" + _lua_str(line.get("force", FORCE)) + "'] ")
    parts.append("local ox,oy=" + repr(ox) + "," + repr(oy) + " ")
    parts.append(_FILL_HELPER)
    parts.append("local names={} for k,v in pairs(defines.entity_status) do names[v]=k end ")
    parts.append(
        "local ms=s.find_entities_filtered{name='" + _lua_str(machine_name) + "',area={{"
        + repr(area_x1) + "," + repr(area_y1) + "},{" + repr(area_x2) + "," + repr(area_y2) + "}}} "
    )
    parts.append("local sc={} for _,e in pairs(ms) do local nm=names[e.status] or 'unknown' sc[nm]=(sc[nm] or 0)+1 end ")
    parts.append(
        "local ib_s=fill(" + repr(start_x) + ",oy) local ib_e=fill(" + repr(end_x) + ",oy) "
        "local ob=fill(" + repr(start_x) + ",oy+6) "
    )
    if output_item:
        parts.append(
            "local prod=f.get_item_production_statistics(s).get_input_count('" + _lua_str(output_item) + "') "
        )
    else:
        parts.append("local prod=-1 ")
    parts.append(
        "local full=1 "
        "for _,c in pairs(s.find_entities_filtered{type={'container','logistic-container'},area={{"
        + repr(area_x1) + "," + repr(oy + 5) + "},{" + repr(area_x2) + "," + repr(oy + 8) + "}}}) do "
        "local ok,inv=pcall(function() return c.get_inventory(defines.inventory.chest) end) "
        "if ok and inv and inv.valid and inv.count_empty_stacks()>0 then full=0 end end "
    )
    parts.append(
        "local out='machines='..#ms"
        "..' input_belt_start='..ib_s..' input_belt_end='..ib_e..' output_belt='..ob"
        "..' produced='..prod..' collectors_full='..full "
    )
    parts.append("for nm,c in pairs(sc) do out=out..' status_'..nm..'='..c end ")
    parts.append("rcon.print(out)")
    return "".join(parts)


def _parse_kv(raw: str) -> dict:
    fields: dict = {}
    for token in raw.split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        try:
            fields[key] = int(value)
        except ValueError:
            try:
                fields[key] = float(value)
            except ValueError:
                fields[key] = value
    return fields


def measure_line(bridge, line: dict) -> dict:
    """Measure one production line with a single RCON round trip.

    `line` (input, caller-owned):
      - origin: (ox, oy)
      - machines: int (assembler/furnace line) XOR labs: int (lab row)
      - machine_name: prototype name for the machine/lab entity searched for
        (default: 'lab' for a lab row, 'assembling-machine-2' otherwise -
        smelter lines etc. MUST pass machine_name explicitly, e.g.
        'electric-furnace', since the default only fits assembler lines)
      - output_item (optional): item riding the output belt; when given,
        enables a real cumulative produced-count via force production
        statistics (LuaForce.get_item_production_statistics), not just the
        belt-level proxy
      - belt_capacity_per_tile (optional, default 8): full-tile capacity (both
        lanes) used as the denominator for the fill ratios core.bottleneck_
        diagnosis expects
      - machine_rate (optional): items/s per machine at 100% uptime; combined
        with machines/labs count to report theoretical_capacity_per_s
      - surface / force (optional): default to the sandbox surface/force

    Returns a dict shaped for core.bottleneck_diagnosis.diagnose_line:
      status_counts (dict of entity_status NAME -> count),
      input_belt_start_count/_capacity, input_belt_end_count/_capacity,
      output_belt_count/_capacity (this is the "measured output proxy" -
      items currently sitting on the output belt tile, a level not a rate),
      collectors_full (bool; True unless some terminal chest/logistic-chest
      has free slots - see module docstring on the default-True convention),
      theoretical_capacity_per_s (present only if machine_rate was given),
      produced_count (cumulative production count for output_item, or None
      if no output_item was given - this is what rate_tracker consumes to
      derive an actual items/s rate across two measurements).

      measured_output_per_s is deliberately NOT set here: a single snapshot
      cannot yield a rate. Callers combine two measure_line results with
      rate_tracker() to obtain it, then merge that into this dict before
      calling diagnose_line.
    """
    query = _build_line_query(line)
    assert len(query) < 1500, f"line telemetry query is {len(query)} chars, must stay under 1500"

    raw = bridge.command(query).strip()
    fields = _parse_kv(raw)

    belt_capacity = float(line.get("belt_capacity_per_tile", DEFAULT_BELT_CAPACITY_PER_TILE))

    status_counts = {
        key[len("status_"):]: value
        for key, value in fields.items()
        if key.startswith("status_")
    }

    produced_raw = fields.get("produced")
    produced_count: Optional[float] = None
    if produced_raw is not None and produced_raw != -1:
        produced_count = float(produced_raw)

    measurement: dict = {
        "raw": raw,
        "status_counts": status_counts,
        "input_belt_start_count": fields.get("input_belt_start", 0),
        "input_belt_start_capacity": belt_capacity,
        "input_belt_end_count": fields.get("input_belt_end", 0),
        "input_belt_end_capacity": belt_capacity,
        "output_belt_count": fields.get("output_belt", 0),
        "output_belt_capacity": belt_capacity,
        "collectors_full": bool(fields.get("collectors_full", 0)),
        "produced_count": produced_count,
        "measured_output_per_s": None,
    }

    machine_rate = line.get("machine_rate")
    count = int(line.get("machines", line.get("labs", 0)) or 0)
    if machine_rate is not None and count > 0:
        measurement["theoretical_capacity_per_s"] = float(machine_rate) * count

    return measurement


_GLOBAL_QUERY_TEMPLATE = (
    "/sc local f=game.forces['{force}'] "
    "local r=f.current_research "
    "local name,prog='none',0 "
    "if r then name=r.name prog=f.research_progress end "
    "rcon.print('current_research='..name..' research_progress='..prog)"
)


def _measure_global(bridge, force: str = FORCE) -> dict:
    query = _GLOBAL_QUERY_TEMPLATE.format(force=_lua_str(force))
    raw = bridge.command(query).strip()
    fields = _parse_kv(raw)
    return {
        "raw": raw,
        "current_research": fields.get("current_research"),
        "research_progress": fields.get("research_progress"),
    }


def measure_chain(bridge, chain: list) -> dict:
    """Measure every line in a chain plus force-wide state.

    `chain`: list of line dicts (see measure_line), each with a unique "name".

    Returns {line_name: measurement, ..., "_global": {research_progress,
    current_research, labs_working, labs_total}}. labs_working/labs_total are
    aggregated in Python from any chain entries whose machine_name resolves
    to 'lab' (or that pass labs=N) - no extra RCON round trip needed for
    those, since measure_line already fetched their status_counts.
    """
    result: dict = {}
    labs_working = 0
    labs_total = 0
    for entry in chain:
        name = entry["name"]
        measurement = measure_line(bridge, entry)
        result[name] = measurement
        is_lab_row = "labs" in entry and "machines" not in entry
        resolved_name = entry.get("machine_name") or ("lab" if is_lab_row else None)
        if resolved_name == "lab":
            counts = measurement["status_counts"]
            labs_working += int(counts.get("working", 0))
            labs_total += sum(int(v) for v in counts.values())

    forces = {entry.get("force", FORCE) for entry in chain} or {FORCE}
    force = next(iter(forces))
    global_state = _measure_global(bridge, force=force)
    global_state["labs_working"] = labs_working
    global_state["labs_total"] = labs_total
    result["_global"] = global_state
    return result


def rate_tracker(previous: dict, current: dict, elapsed_s: float) -> dict:
    """Compute per-line output rates (items/s) between two measure_chain (or
    measure_line-keyed) snapshots.

    IMPORTANT: belt contents (input_belt_*_count, output_belt_count) are a
    LEVEL - how many items are sitting on a tile right now - not a cumulative
    counter. Subtracting two levels and dividing by elapsed time does NOT
    yield a meaningful rate (a belt can be full at both t0 and t1 while having
    produced any number of items in between, or a level can even fall while
    production continues, e.g. a downstream consumer draining faster). So
    this function never derives a rate from belt levels.

    The only field trusted for rate computation is "produced_count" - the
    cumulative force production-statistics counter measure_line populates
    when the line dict provided output_item. When produced_count is present
    on both previous and current for a line, and elapsed_s > 0, the rate is
    (current.produced_count - previous.produced_count) / elapsed_s (clamped
    to >= 0 - a negative delta means the underlying counter was reset, e.g. a
    force/game reload, and reporting a negative "rate" would be nonsense).

    When produced_count is missing (no output_item was given for that line)
    or elapsed_s <= 0, this reports None for that line rather than
    inventing/estimating a number from level data.

    Returns {line_name: {"output_rate_per_s": float_or_None}} for every line
    name present in `current` (excluding "_global").
    """
    result: dict = {}
    for name, curr_measurement in current.items():
        if name == "_global":
            continue
        prev_measurement = previous.get(name) if previous else None
        rate: Optional[float] = None
        if (
            elapsed_s
            and elapsed_s > 0
            and prev_measurement is not None
            and curr_measurement.get("produced_count") is not None
            and prev_measurement.get("produced_count") is not None
        ):
            delta = float(curr_measurement["produced_count"]) - float(prev_measurement["produced_count"])
            if delta >= 0:
                rate = delta / elapsed_s
            else:
                rate = None  # counter went backwards - reload/reset, not a real rate
        result[name] = {"output_rate_per_s": rate}
    return result
