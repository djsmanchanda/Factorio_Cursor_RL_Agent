# Path: planners/fluid_layouts.py
# Purpose: Deterministic layout primitives for FLUID-using production rows
# (chemical plants, oil refineries, fluid-recipe assemblers) plus the sandbox
# fluid sources that feed them.
# Connection offsets and fluid-purity rules are documented in docs/reference/factorio_mechanics.md.
# All geometry remains deterministic planner data; cross-stage links live in fluid_routing.py.
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from jsonschema import Draft7Validator

from core.fluid_systems import (
    validate_network_purity,
    validate_pipeline_span,
    validate_underground_span,
)
from planners.local_layout_planner import (
    BELT_TIERS,
    FEEDER_RATES,
    feeder_rate,
    FEED_HEADROOM,
    INSERTER_TIERS,
    MACHINE_SPEEDS,
    MEDIUM_POLE_WIRE_REACH,
    _reject_fuel_entities,
)

# Crafting speeds, live-verified. MACHINE_SPEEDS already carries the assembler.
FLUID_MACHINE_SPEEDS: Dict[str, float] = dict(MACHINE_SPEEDS)
FLUID_MACHINE_SPEEDS.update({"chemical-plant": 1.0, "oil-refinery": 1.0})

# Square footprints in tiles.
MACHINE_FOOTPRINTS: Dict[str, int] = {
    "assembling-machine-2": 3, "chemical-plant": 3, "oil-refinery": 5,
}

# Recipe data transcribed from the live game. Fluid amounts are per craft.
FLUID_RECIPES: Dict[str, dict] = {
    "basic-oil-processing": {
        "machine": "oil-refinery", "craft_time": 5.0,
        "item_ingredients": [], "item_amounts": [],
        "fluid_ingredients": {"crude-oil": 100},
        "item_products": [], "item_product_amounts": {}, "fluid_products": {"petroleum-gas": 45},
    },
    "sulfur": {
        "machine": "chemical-plant", "craft_time": 1.0,
        "item_ingredients": [], "item_amounts": [],
        "fluid_ingredients": {"water": 30, "petroleum-gas": 30},
        "item_products": ["sulfur"], "item_product_amounts": {"sulfur": 2}, "fluid_products": {},
    },
    "sulfuric-acid": {
        "machine": "chemical-plant", "craft_time": 1.0,
        "item_ingredients": ["iron-plate", "sulfur"], "item_amounts": [1, 5],
        "fluid_ingredients": {"water": 100},
        "item_products": [], "item_product_amounts": {}, "fluid_products": {"sulfuric-acid": 50},
    },
    "plastic-bar": {
        "machine": "chemical-plant", "craft_time": 1.0,
        "item_ingredients": ["coal"], "item_amounts": [1],
        "fluid_ingredients": {"petroleum-gas": 20},
        "item_products": ["plastic-bar"], "item_product_amounts": {"plastic-bar": 2}, "fluid_products": {},
    },
    "processing-unit": {
        "machine": "assembling-machine-2", "craft_time": 10.0,
        "item_ingredients": ["electronic-circuit", "advanced-circuit"],
        "item_amounts": [20, 2],
        "fluid_ingredients": {"sulfuric-acid": 5},
        "item_products": ["processing-unit"], "item_product_amounts": {"processing-unit": 1}, "fluid_products": {},
    },
}

# PIPE TILE offsets (= target_position - entity.position), live-verified above.
# Where a fluidbox exposed two interchangeable connections we take exactly ONE
# (the lowest x, then lowest y) -- a single connection carries ~4200 fluid/s,
# far past any of these recipes, and one tile fewer means one adjacency fewer.
VERIFIED_PIPE_TILES: Dict[str, dict] = {
    "basic-oil-processing": {"inputs": {"crude-oil": (1, 3)},
                             "outputs": {"petroleum-gas": (2, -3)}},
    "sulfur": {"inputs": {"water": (-1, -2), "petroleum-gas": (1, -2)}, "outputs": {}},
    "sulfuric-acid": {"inputs": {"water": (-1, -2)},
                      "outputs": {"sulfuric-acid": (-1, 2)}},
    "plastic-bar": {"inputs": {"petroleum-gas": (-1, -2)}, "outputs": {}},
    "processing-unit": {"inputs": {"sulfuric-acid": (0, -2)}, "outputs": {}},
}

HEADER_WEST = -1  # westmost header column: one tile clear of the machines
SOURCE_PERCENTAGE = 1.0  # infinity-pipe fill target, mode "at-least"
FLUID_SOURCES = {"water": "water", "crude-oil": "crude-oil"}
_MAX_EXTRA_PITCH = 3  # how far generate_* may spread machines to stay pure
_REPO_ROOT = Path(__file__).resolve().parents[1]

def _validate(plan: dict) -> None:
    """Schema-validate a BuildPlan, then enforce the electric-only invariant."""
    schema_path = _REPO_ROOT / "schemas" / "build_plan.schema.json"
    with schema_path.open("r", encoding="utf-8") as handle:
        schema = json.load(handle)
    errors = list(Draft7Validator(schema).iter_errors(plan))
    if errors:
        joined = "\n".join(f"- {'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
                           for e in errors)
        raise ValueError("BuildPlan validation FAILED:\n" + joined)
    _reject_fuel_entities(plan)
def _stub_row(width: int, dy: int) -> int:
    """Tile row of a pipe tile whose centre offset is dy, machines on rows 2..
    Machine centre sits at y = 2 + width/2, so the north stub row is always 1
    and the south stub row is always width + 2, for both 3x3 and 5x5 bodies.
    """
    return int(2 + width / 2 + dy - 0.5)
def header_row(origin_y: int, footprint: int, side: str, slot: int = 0) -> int:
    """World y of header row `slot` on `side` of a row at `origin_y`.

    THE header row rule, and the only place it is written down: _networks()
    below calls this, so a caller that wants to attach to a header can never
    disagree with the pipes the generator actually places. Slot n sits 3 rows
    further out than slot n-1 (header, riser, one clear row), which is what
    keeps two fluids on the same face apart.
    """
    if slot < 0:
        raise ValueError("header slot must be non-negative")
    if side == "north":
        return origin_y - 2 - 3 * slot
    if side == "south":
        return origin_y + footprint + 5 + 3 * slot
    raise ValueError(f"Unknown header side: {side!r} (expected 'north' or 'south')")

def _outward(side: str) -> int:
    """Step that leads AWAY from the machines: -1 north of the row, +1 south."""
    return -1 if side == "north" else 1

def _networks(recipe: str, machine_count: int, pitch: int) -> List[dict]:
    """Per-fluid geometry in TILE INDICES, deterministic and origin-relative.

    Each entry: {fluid, role, side, slot, stubs, risers, header, span}. Every
    fluid gets its own header row on the side its machine connection faces, one
    row of pipe-to-ground risers between header and stubs, and one underground
    hop per machine that dives beneath whatever rows lie between (item belt,
    other headers). Slot n on a side is pushed 3 rows further out than slot n-1
    so no two headers, and no header and foreign riser, ever touch.

      north slot n: risers row -1 - 3n, header row -2 - 3n
      south slot n: risers row width+4 + 3n, header row width+5 + 3n
    """
    spec = FLUID_RECIPES[recipe]
    width = MACHINE_FOOTPRINTS[spec["machine"]]
    tiles = VERIFIED_PIPE_TILES[recipe]

    entries: List[dict] = []
    for role in ("inputs", "outputs"):
        for fluid, (dx, dy) in tiles[role].items():
            entries.append({"fluid": fluid, "role": role[:-1], "dx": dx,
                            "row": _stub_row(width, dy), "side": "north" if dy < 0 else "south"})

    networks: List[dict] = []
    for side in ("north", "south"):
        on_side = sorted((e for e in entries if e["side"] == side),
                         key=lambda e: (e["dx"], e["fluid"]))
        for slot, entry in enumerate(on_side):
            head = header_row(0, width, side, slot)
            riser_row = head - _outward(side)  # one row back towards the machines
            cols = [i * pitch + (width - 1) // 2 + entry["dx"] for i in range(machine_count)]
            networks.append({
                "fluid": entry["fluid"], "role": entry["role"], "side": side, "slot": slot,
                "stub_row": entry["row"], "riser_row": riser_row, "header_row": head,
                "cols": cols,
                "stubs": [(c, entry["row"]) for c in cols],
                "risers": [(c, riser_row) for c in cols],
                "header": [(c, head) for c in range(HEADER_WEST, max(cols) + 1)],
            })
    return networks

def fluid_network_segments(recipe: str, machine_count: int, origin_x: int = 0,
                           origin_y: int = 0, pitch: int | None = None) -> List[dict]:
    """validate_network_purity() segments for a fluid machine row, in world tiles.
    One segment per fluid: its stubs, risers and header. Public so callers (and
    tests) can re-run the anti-mixing guard, or doctor a segment and prove the
    guard still bites."""
    _check_recipe(recipe, machine_count)
    pitch = _pitch(recipe) if pitch is None else pitch
    segments = []
    for net in _networks(recipe, machine_count, pitch):
        tiles = net["stubs"] + net["risers"] + net["header"]
        segments.append({
            "fluid": net["fluid"], "separated_by_pump": False,
            "tiles": [(origin_x + c, origin_y + r) for c, r in tiles],
        })
    return segments

def _pitch(recipe: str) -> int:
    """Smallest machine pitch >= the footprint that keeps every fluid pure.
    A chemical plant taking TWO fluids exposes them on opposite corners of its
    north face, so packed at the 3-tile footprint pitch machine i's east input
    lands orthogonally adjacent to machine i+1's west input -- different fluids,
    touching, which is the one failure docs/23 calls unrecoverable. Spreading
    the row by one tile restores a clear tile between them; single-fluid rows
    keep the tight footprint pitch. Decided by running the real purity guard on
    a 3-machine probe rather than by a hand-written special case."""
    width = MACHINE_FOOTPRINTS[FLUID_RECIPES[recipe]["machine"]]
    for pitch in range(width, width + _MAX_EXTRA_PITCH + 1):
        try:
            validate_network_purity(fluid_network_segments(recipe, 3, pitch=pitch))
        except ValueError:
            continue
        return pitch
    raise ValueError(f"No pitch <= {width + _MAX_EXTRA_PITCH} keeps '{recipe}' fluid-pure")

def _check_recipe(recipe: str, machine_count: int) -> None:
    if recipe not in FLUID_RECIPES:
        raise ValueError(f"No fluid recipe knowledge for: {recipe}")
    if recipe not in VERIFIED_PIPE_TILES:
        raise ValueError(f"Pipe connection tiles for '{recipe}' are unverified; refusing to place")
    if machine_count <= 0:
        raise ValueError("machine_count must be positive")

def _free_columns(recipe: str, machine_count: int, pitch: int, row: int) -> List[int]:
    """Machine-relative column offsets on `row` not taken by a fluid pipe tile."""
    width = MACHINE_FOOTPRINTS[FLUID_RECIPES[recipe]["machine"]]
    taken = {n["cols"][0] for n in _networks(recipe, machine_count, pitch) if n["stub_row"] == row}
    base = (width - 1) // 2
    return [d for d in range(-base, pitch - base) if base + d not in taken]

def _item_feeders(recipe: str, machine_count: int, inserter_type: str) -> List[int]:
    """Feed points per item ingredient = ceil(demand * headroom / inserter rate).
    Same integer-scaled arithmetic and same FEED_HEADROOM standard as
    LocalLayoutPlanner._feeders_needed, so both layers size feeds identically."""
    spec = FLUID_RECIPES[recipe]
    crafts = machine_count * FLUID_MACHINE_SPEEDS[spec["machine"]] / spec["craft_time"]
    rate = feeder_rate(inserter_type)
    return [max(1, -(-int(a * crafts * FEED_HEADROOM * 100) // int(rate * 100)))
            for a in spec["item_amounts"]]

def generate_fluid_machine_row(
    recipe: str,
    machine_count: int,
    origin_x: int = 0,
    origin_y: int = 0,
    belt_type: str = "transport-belt",
    inserter_type: str = "fast-inserter",
    chained_items: set[str] | None = None,
    terminal_collector: bool = True,
) -> dict:
    """Deterministic row of fluid-using machines with piped headers.

    Rows (y offsets from origin, y grows south; W = machine footprint, so the
    machine body is rows 2..W+1 and its fluid tiles land on rows 1 and W+2):

      -5  fluid header, north slot 1   ====== (2nd north fluid, e.g. sulfur gas)
      -4  risers, north slot 1              U     pipe-to-ground facing north
      -3  (clear)                                 keeps slot 1 off slot 0
      -2  fluid header, north slot 0   ======
      -1  risers, north slot 0              u
       0  item input belt              >>>>>>>>>  undergrounds pass BENEATH it
       1  stubs + item input inserter + pole   u i p
       2  machine top row              [ M M M ]
      ..  machine body
     W+1  machine bottom row
     W+2  stubs + item output inserter + pole
     W+3  item output belt             >>>>>>>>>  terminal collector at its east
     W+4  risers, south slot 0
     W+5  fluid header, south slot 0   ======     (e.g. sulfuric acid out)
     W+7  risers, south slot 1
     W+8  fluid header, south slot 1

    Each machine's verified connection tile holds a pipe-to-ground whose normal
    end faces the machine and whose underground end runs out to a riser on the
    header row's near side; the header itself is a straight pipe run from
    HEADER_WEST east to the last riser column, so an upstream source or a
    downstream consumer attaches at its west end. Undergrounds are what let the
    item belt on row 0 stay unbroken and what let a second fluid cross the first
    fluid's header (both live-verified; see the module header).

    Machines sit at `pitch` tiles apart, where pitch is the tightest spacing the
    anti-mixing guard accepts: the footprint for single-fluid rows, footprint+1
    when two different fluids enter the same face. Item ingredients (up to two,
    one per belt lane) and an item product ride the existing belt+inserter
    convention; the item inserters take the first machine column the fluid tiles
    left free, which is why a processing-unit assembler -- whose acid tile sits
    on its centre column -- feeds from the column west of centre instead.
    Every emitted plan is schema-validated, fuel-guarded, purity-checked, and
    its underground spans are checked against MAX_UNDERGROUND_SPAN.
    """
    _check_recipe(recipe, machine_count)
    if belt_type not in BELT_TIERS:
        raise ValueError(f"Unknown belt tier: {belt_type}")
    if inserter_type not in INSERTER_TIERS:
        raise ValueError(f"Unknown inserter tier: {inserter_type}")

    spec = FLUID_RECIPES[recipe]
    chained_items = set(chained_items or ())
    unknown_chained = chained_items - set(spec["item_ingredients"])
    if unknown_chained:
        raise ValueError(f"Unknown chained item ingredients for {recipe}: {sorted(unknown_chained)}")
    machine = spec["machine"]
    width = MACHINE_FOOTPRINTS[machine]
    if len(spec["item_ingredients"]) > 2:
        raise ValueError("Fluid rows support at most two item ingredients (two belt lanes)")

    pitch = _pitch(recipe)
    networks = _networks(recipe, machine_count, pitch)
    length = machine_count * pitch
    base = (width - 1) // 2
    north_row, south_row = 1, width + 2
    ox, oy = origin_x, origin_y

    def at(col: float, row: float) -> dict:
        return {"x": ox + col + 0.5, "y": oy + row + 0.5}

    ghosts: List[dict] = []
    scaffolding: List[dict] = []

    # --- fluid: stubs dive under everything, risers surface onto the header ---
    for net in networks:
        stub_dir = "south" if net["side"] == "north" else "north"
        riser_dir = "north" if net["side"] == "north" else "south"
        for (col, row), (_, riser_row) in zip(net["stubs"], net["risers"]):
            validate_underground_span((col, row), (col, riser_row))
            ghosts.append({"action_type": "place_ghost", "entity": "pipe-to-ground",
                           "position": at(col, row), "direction": stub_dir})
            ghosts.append({"action_type": "place_ghost", "entity": "pipe-to-ground",
                           "position": at(col, riser_row), "direction": riser_dir})
        for col, row in net["header"]:
            ghosts.append({"action_type": "place_ghost", "entity": "pipe",
                           "position": at(col, row)})

    # --- machines, item inserters and poles, one machine at a time ------------
    in_free = _free_columns(recipe, machine_count, pitch, north_row)
    out_free = _free_columns(recipe, machine_count, pitch, south_row)
    in_col = min(in_free, key=lambda d: (abs(d), d))
    out_col = min(out_free, key=lambda d: (abs(d), d))
    for row, free, used in ((north_row, in_free, in_col), (south_row, out_free, out_col)):
        if not [d for d in free if d != used]:
            raise ValueError(f"No free column on row {row} for a power pole in a '{recipe}' row")

    for i in range(machine_count):
        centre = i * pitch + width / 2
        ghosts.append({"action_type": "place_ghost", "entity": machine,
                       "position": {"x": ox + centre, "y": oy + 2 + width / 2},
                       "recipe": recipe})
        if spec["item_ingredients"]:
            ghosts.append({"action_type": "place_ghost", "entity": inserter_type,
                           "position": at(i * pitch + base + in_col, north_row),
                           "direction": "north"})
        if spec["item_products"]:
            ghosts.append({"action_type": "place_ghost", "entity": inserter_type,
                           "position": at(i * pitch + base + out_col, south_row),
                           "direction": "north"})
        # One pole per machine per row: pitch (3-4 tiles) is far inside
        # MEDIUM_POLE_WIRE_REACH, and the two rows are width+1 apart, so the
        # whole row forms one connected grid without a spacing special case.
        north_pole = max(d for d in in_free if d != in_col or not spec["item_ingredients"])
        south_pole = max(d for d in out_free if d != out_col or not spec["item_products"])
        ghosts.append({"action_type": "place_ghost", "entity": "medium-electric-pole",
                       "position": at(i * pitch + base + north_pole, north_row)})
        ghosts.append({"action_type": "place_ghost", "entity": "medium-electric-pole",
                       "position": at(i * pitch + base + south_pole, south_row)})

    # --- item belts, chest feeders, terminal collector ------------------------
    feeders = _item_feeders(recipe, machine_count, inserter_type)
    belt_west = -3 if chained_items else (-(1 + max(feeders)) if feeders else 0)
    if spec["item_ingredients"]:
        for col in range(belt_west, length):
            ghosts.append({"action_type": "place_ghost", "entity": belt_type,
                           "position": at(col, 0), "direction": "east"})
        # Ingredient 0 loads from the north side of the belt, ingredient 1 from
        # the south, so each lands on its own lane (an inserter drops far-lane).
        for index, ingredient in enumerate(spec["item_ingredients"]):
            if ingredient in chained_items:
                continue
            chest_row, ins_row = (-2, -1) if index == 0 else (2, 1)
            facing = "north" if index == 0 else "south"
            for slot in range(feeders[index]):
                col = -2 - slot
                scaffolding.append({"action_type": "place_entity", "entity": "infinity-chest",
                                    "position": at(col, chest_row), "infinity_filter": ingredient})
                scaffolding.append({"action_type": "place_entity", "entity": inserter_type,
                                    "position": at(col, ins_row), "direction": facing})
    if spec["item_products"]:
        for col in range(0, length):
            ghosts.append({"action_type": "place_ghost", "entity": belt_type,
                           "position": at(col, width + 3), "direction": "east"})
        if terminal_collector:
            scaffolding.append({"action_type": "place_entity", "entity": inserter_type,
                                "position": at(length, width + 3), "direction": "west"})
            scaffolding.append({"action_type": "place_entity", "entity": "steel-chest",
                                "position": at(length + 1, width + 3)})

    # --- power scaffolding ----------------------------------------------------
    # Parked south-west: rows width+3 and width+4 are empty west of x=0 whatever
    # the feeder count does to the north-west corner, so this never collides.
    substation = {"x": ox - 2.0, "y": oy + width + 4.0}
    scaffolding.insert(0, {"action_type": "place_entity", "entity": "substation",
                           "position": substation})
    scaffolding.insert(0, {"action_type": "place_entity", "entity": "electric-energy-interface",
                           "position": {"x": ox - 5.0, "y": oy + width + 4.0}})
    first_pole = at(base + max(d for d in out_free), south_row)
    reach = ((first_pole["x"] - substation["x"]) ** 2 + (first_pole["y"] - substation["y"]) ** 2) ** 0.5
    if reach > MEDIUM_POLE_WIRE_REACH:
        raise ValueError(
            f"Substation is {reach:.2f} tiles from the first pole, past "
            f"MEDIUM_POLE_WIRE_REACH ({MEDIUM_POLE_WIRE_REACH}) -- the row would have no power"
        )

    plan = {"phases": [
        {"name": "fluid_row_scaffolding", "actions": scaffolding},
        {"name": f"fluid_row_{recipe}", "actions": ghosts},
    ]}
    _validate(plan)
    validate_network_purity(fluid_network_segments(recipe, machine_count, ox, oy, pitch))
    return plan

def generate_fluid_source(kind: str, origin_x: int = 0, origin_y: int = 0,
                          run_length: int = 0) -> dict:
    """Raw fluid input for a header: an infinity-pipe plus an optional pipe run.

      col: 0        1        2      ..  run_length
           [inf-pipe][pipe  ][pipe  ] .. >  attaches to a header's west end

    SANDBOX STAND-IN, deliberate: 'water' would normally come from an
    offshore-pump and 'crude-oil' from a pumpjack, but the planner sandbox is a
    lab-tile surface with neither a water body nor an oil patch. An infinity-
    pipe filtered to the fluid reproduces the same boundary condition (an
    unbounded source at a fixed tile) without terraforming the surface, and it
    is the ONLY part of a fluid chain that a real planet replaces with a real
    source entity. Live-verified: 'infinity-pipe' exists, is 1x1, and
    set_infinity_pipe_filter{name=..., percentage=1, mode='at-least'} succeeds
    and reads back as water/1/at-least.

    ESCALATION (not fixed here, out of scope): the plan carries the fluid in the
    schema's `infinity_filter` field, but factorio_mod/control.lua applies that
    field with set_infinity_container_filter, the infinity-CHEST api, which will
    error on an infinity-pipe. control.lua needs a branch on entity type
    (set_infinity_pipe_filter for pipes), and the schema has no field for the
    fill percentage -- SOURCE_PERCENTAGE is documented here instead of emitted.
    """
    if kind not in FLUID_SOURCES:
        raise ValueError(f"Unknown fluid source kind: {kind!r} (expected one of {sorted(FLUID_SOURCES)})")
    if run_length < 0:
        raise ValueError("run_length must be non-negative")

    fluid = FLUID_SOURCES[kind]
    actions = [{"action_type": "place_entity", "entity": "infinity-pipe",
                "position": {"x": origin_x + 0.5, "y": origin_y + 0.5},
                "infinity_filter": fluid}]
    for col in range(1, run_length + 1):
        actions.append({"action_type": "place_ghost", "entity": "pipe",
                        "position": {"x": origin_x + col + 0.5, "y": origin_y + 0.5}})

    plan = {"phases": [{"name": f"fluid_source_{fluid}", "actions": actions}]}
    _validate(plan)
    validate_network_purity([fluid_source_segment(kind, origin_x, origin_y, run_length)])
    return plan

def fluid_source_segment(kind: str, origin_x: int = 0, origin_y: int = 0,
                         run_length: int = 0) -> dict:
    """The purity segment of a generate_fluid_source() run, in world tiles."""
    return {"fluid": FLUID_SOURCES[kind], "separated_by_pump": False,
            "tiles": [(origin_x + c, origin_y) for c in range(run_length + 1)]}

def source_attachment(origin_x: int, origin_y: int) -> tuple:
    """Where a chain link touches a fluid source: the tile west of its
    infinity-pipe. The run itself grows EAST, so its west end is always free."""
    return (origin_x - 1, origin_y)

def header_attachment(recipe: str, fluid: str, machine_count: int = 1,
                      origin_x: int = 0, origin_y: int = 0) -> dict:
    """Where a chain link must touch to feed or drain `fluid` on a machine row.

    Side and slot come straight out of _networks(), i.e. from the generator's
    OWN ordering (fluids on a face sorted by connection column then name, slot n
    pushed 3 rows further out than slot n-1) -- nothing here is guessed.

    Returns {fluid, role, side, slot, row, west, attach}. `west` is the header's
    west end; `attach` is one row FURTHER OUT than the header. Attaching from
    outside rather than along the header row itself is deliberate: the row 3
    tiles beyond a header is empty by construction (that is the clear row the
    slot pitch reserves), whereas the header row can be occupied further west --
    live-verified, plastic-bar's coal chest sits on the gas header row one tile
    west of its west end, and sulfuric-acid's iron-plate chest does the same on
    its water header row.
    """
    _check_recipe(recipe, machine_count)
    for net in _networks(recipe, machine_count, _pitch(recipe)):
        if net["fluid"] != fluid:
            continue
        row = origin_y + net["header_row"]
        west = origin_x + HEADER_WEST
        return {"fluid": fluid, "role": net["role"], "side": net["side"],
                "slot": net["slot"], "row": row, "west": (west, row),
                "attach": (west, row + _outward(net["side"]))}
    raise ValueError(f"Recipe '{recipe}' has no '{fluid}' header to attach to")
# Backward-compatible exports; routing lives in its own file to keep row layout
# generation separate from cross-stage network composition.
from planners.fluid_routing import (  # noqa: E402
    LINK_TUNNEL_CLEARANCE,
    fluid_chain_link_segments,
    fluid_chain_link_trunk,
    generate_fluid_chain_link,
)
