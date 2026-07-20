# Path: planners/local_layout_planner.py
# Purpose: Entity-level line layouts by deterministic math, plus snapshot inspection.

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Dict, List

from jsonschema import Draft7Validator

from core.factory_graph import FactoryGraph
from tools.validate_snapshot import validate_snapshot_file

# Minimal deterministic recipe knowledge for line layouts. A full recipe DAG
# loader replaces this table when arbitrary chains are planned. Up to two
# ingredients ride the two lanes of the single input belt (wiki heuristic:
# split items across both lanes; inserters only grab what the target accepts).
LINE_RECIPES: Dict[str, dict] = {
    "iron-gear-wheel": {"machine": "assembling-machine-2", "ingredients": ["iron-plate"], "amounts": [2], "craft_time": 0.5},
    "copper-cable": {"machine": "assembling-machine-2", "ingredients": ["copper-plate"], "amounts": [1], "craft_time": 0.5},
    "iron-stick": {"machine": "assembling-machine-2", "ingredients": ["iron-plate"], "amounts": [1], "craft_time": 0.5},
    "electronic-circuit": {"machine": "assembling-machine-2", "ingredients": ["copper-cable", "iron-plate"], "amounts": [3, 1], "craft_time": 0.5},
    "automation-science-pack": {"machine": "assembling-machine-2", "ingredients": ["copper-plate", "iron-gear-wheel"], "amounts": [1, 1], "craft_time": 5.0},
    # Smelting: furnaces auto-select their recipe from the input, so no
    # recipe is set on the ghost. Electric furnaces avoid a fuel lane.
    "iron-plate": {"machine": "electric-furnace", "ingredients": ["iron-ore"], "amounts": [1], "craft_time": 3.2, "set_recipe": False},
    "copper-plate": {"machine": "electric-furnace", "ingredients": ["copper-ore"], "amounts": [1], "craft_time": 3.2, "set_recipe": False},
}

MACHINE_SPEEDS = {"assembling-machine-2": 0.75, "electric-furnace": 2.0}

# Feeder inserter chest->belt throughput estimates (items/s, research-boosted;
# docs/21). Inserter swings are rotation-bound: 180 degrees to load, 180 to
# unload, so one feeder cannot supply a hungry line - feed points scale with
# per-ingredient demand: feeders = ceil(demand / rate).
FEEDER_RATES = {"fast-inserter": 4.0, "bulk-inserter": 8.0, "stack-inserter": 12.0}

# Capacity headroom (user standard, 2026-07-18): provision feed capacity with
# a 20-25% buffer over raw demand so supply never runs at the ragged edge.
FEED_HEADROOM = 1.25

# Vertical pitch between stacked lines: 8 rows of layout plus 8 reserved for
# expansion, so lines can grow east (more machines) and south (more lines).
LINE_PITCH_Y = 16

# Logistics tiers (docs/21): higher tiers raise line throughput. Turbo belts
# and stack inserters have off-planet sourcing constraints in real supply
# chains; on the sandbox they arrive via scaffolding.
BELT_TIERS = {"transport-belt": 15, "fast-transport-belt": 30, "express-transport-belt": 45, "turbo-transport-belt": 60}
INSERTER_TIERS = {"fast-inserter", "bulk-inserter", "stack-inserter"}

# Sideload feeder geometry (feed_style="sideload"). Instead of chest+inserter
# pairs placed directly on the input belt, each ingredient rides a dedicated
# feeder BELT column that T-junctions into the input belt (belt buffering
# sustains far higher throughput than chest+inserter feeding). Both feeder
# columns sit WEST of x=0 so they never touch machines (x>=0), input inserters
# (x=3i+1.5) or poles (x=6j+0.5). Ingredient 0 approaches from the north side
# (belt runs south into the belt's north edge); ingredient 1 from the south
# side (belt runs north into the south edge). Loading chest+inserter pairs sit
# two/one tiles further west of each feeder belt.
SIDELOAD_NORTH_COL = -2  # tile column of the north feeder belt (ingredient 0)
SIDELOAD_SOUTH_COL = -3  # tile column of the south feeder belt (ingredient 1)
FEED_STYLES = {"chest", "sideload"}

# Invariant (docs/20 §12): all equipment is electric. Plans containing any of
# these fuel-burning entities are rejected at validation time.
FORBIDDEN_FUEL_ENTITIES = {
    "burner-mining-drill",
    "stone-furnace",
    "steel-furnace",
    "burner-inserter",
    "boiler",
    "steam-engine",
}


def _reject_fuel_entities(plan: dict) -> None:
    offenders = sorted({
        action["entity"]
        for phase in plan.get("phases", [])
        for action in phase.get("actions", [])
        if action.get("entity") in FORBIDDEN_FUEL_ENTITIES
    })
    if offenders:
        raise ValueError(f"Electric-only invariant violated by: {', '.join(offenders)}")

MACHINE_WIDTH = 3  # tiles; assembling machines are 3x3


class LocalLayoutPlanner:
    """Read-only snapshot inspector for LocalLayoutPlanner layer."""

    def __init__(self, schema_path: Path | None = None):
        repo_root = Path(__file__).resolve().parents[1]
        self.schema_path = schema_path or (repo_root / "schemas" / "snapshot.schema.json")

    def load_snapshot(self, snapshot_path: Path) -> dict:
        errors = validate_snapshot_file(snapshot_path, self.schema_path)
        if errors:
            messages = [f"- {self._format_error_path(error)}: {error.message}" for error in errors]
            joined = "\n".join(messages)
            raise ValueError(f"Snapshot validation FAILED:\n{joined}")
        with snapshot_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def build_graph(self, snapshot: dict) -> FactoryGraph:
        return FactoryGraph(snapshot)

    def inspect(self, graph: FactoryGraph) -> List[str]:
        lines: List[str] = []
        lines.append("Entities:")

        name_counts = Counter()
        for name, indices in graph.index_by_name.items():
            name_counts[name] = len(indices)

        for name in sorted(name_counts):
            lines.append(f"  {name}: {name_counts[name]}")

        recipe_counts = Counter()
        for recipe, indices in graph.index_by_recipe.items():
            recipe_counts[recipe] = len(indices)

        if recipe_counts:
            lines.append("")
            lines.append("Assemblers by recipe:")
            for recipe in sorted(recipe_counts):
                lines.append(f"  {recipe}: {recipe_counts[recipe]}")

        if graph.bounds:
            lines.append("")
            lines.append("Surface bounds:")
            lines.append(f"  x: {graph.bounds.min_x} → {graph.bounds.max_x}")
            lines.append(f"  y: {graph.bounds.min_y} → {graph.bounds.max_y}")

        return lines

    def inspect_snapshot(self, snapshot_path: Path) -> List[str]:
        snapshot = self.load_snapshot(snapshot_path)
        graph = self.build_graph(snapshot)
        return self.inspect(graph)

    @staticmethod
    def _format_error_path(error) -> str:
        if not error.path:
            return "<root>"
        return "/".join(str(part) for part in error.path)

    def generate_line_layout(
        self,
        recipe: str,
        machine_count: int,
        origin_x: int = 0,
        origin_y: int = 0,
        mining_feed: bool = False,
        belt_type: str = "transport-belt",
        inserter_type: str = "fast-inserter",
        feed_style: str = "chest",
    ) -> dict:
        """Deterministic single-recipe production line.

        Row layout (y offsets from origin, y grows south):
          0: input belt flowing east          4: (machine bottom row)
          1: input inserters + power poles    5: output inserters
          2: machine top row                  6: output belt flowing east
        Feed (infinity chest) at the west end, collection chest at the east end.
        Inserters face their pickup side; drop is the opposite tile.

        feed_style="chest" (default): each ingredient's infinity chest + inserter
        pairs sit directly on the input belt at the west extension - ingredient 0
        on the north side (inserter faces north, drops onto the far lane),
        ingredient 1 on the south side.

        feed_style="sideload": ingredients ride dedicated feeder BELT columns
        that T-junction into the input belt, which sustains higher throughput
        than chest+inserter feeding because the feeder belt buffers. Geometry
        (columns are tile indices, all WEST of x=0; N0/N1 = feeders_needed per
        ingredient, sized by demand exactly like the chest feeders):

          col:  -5    -4    -3      -2      -1  0  1 .. machines ->
                                    [belt  ]  input belt (row 0) flows east >>
          north (rows y<0, ingredient 0):
                            ins-> [Nbelt v]              feeder belt runs SOUTH,
                     chest  ins-> [Nbelt v]              its last tile (row -1)
                            ...   [Nbelt v]              sideloads input row 0.
          south (rows y>0, ingredient 1):
              chest  ins->        [Sbelt ^]              feeder belt runs NORTH,
              chest  ins->        [Sbelt ^]              its last tile (row 1)
              ...                 [Sbelt ^]              sideloads input row 0.

        North feeder belt occupies column -2, its loading inserters column -3
        (facing west: pick from the chest, drop east onto the belt) and chests
        column -4. South feeder belt occupies column -3, loaders column -4,
        chests column -5. The input belt extends west to include both junction
        tiles (-2 and, for two ingredients, -3). Power scaffolding is pushed
        further west of every feeder tile so nothing overlaps.
        """
        if recipe not in LINE_RECIPES:
            raise ValueError(f"No line recipe knowledge for: {recipe}")
        if machine_count <= 0:
            raise ValueError("machine_count must be positive")

        if belt_type not in BELT_TIERS:
            raise ValueError(f"Unknown belt tier: {belt_type}")
        if inserter_type not in INSERTER_TIERS:
            raise ValueError(f"Unknown inserter tier: {inserter_type}")
        if feed_style not in FEED_STYLES:
            raise ValueError(f"Unknown feed style: {feed_style}")
        if feed_style == "sideload" and not mining_feed:
            self._check_sideload_lane_capacity(recipe, machine_count, belt_type)

        spec = LINE_RECIPES[recipe]
        machine = spec["machine"]
        ingredients = spec["ingredients"]
        if not 1 <= len(ingredients) <= 2:
            raise ValueError("Line layouts support one or two ingredients (two belt lanes)")
        length = machine_count * MACHINE_WIDTH
        ox, oy = origin_x, origin_y

        def at(x: float, y: float) -> dict:
            return {"x": ox + x, "y": oy + y}

        # Per-ingredient demand (items/s) sets how many feed points each
        # ingredient needs; the input belt extends west to host them.
        crafts_per_second = machine_count * MACHINE_SPEEDS[machine] / spec["craft_time"]
        feeder_rate = FEEDER_RATES[inserter_type]
        feeders_needed = self._feeders_needed(recipe, machine_count, inserter_type)
        feed_slots = max(feeders_needed) if not mining_feed else 0
        belt_west = self._belt_west(recipe, machine_count, feed_style, inserter_type, mining_feed)

        ghosts: List[dict] = []
        # Belts: input lane must cover every feeder inserter's drop tile.
        for x in range(belt_west, length):
            ghosts.append({"action_type": "place_ghost", "entity": belt_type,
                           "position": at(x + 0.5, 0.5), "direction": "east"})
        for x in range(0, length):
            ghosts.append({"action_type": "place_ghost", "entity": belt_type,
                           "position": at(x + 0.5, 6.5), "direction": "east"})

        set_recipe = spec.get("set_recipe", True)
        for i in range(machine_count):
            base = i * MACHINE_WIDTH
            center = base + 1.5
            machine_action = {"action_type": "place_ghost", "entity": machine, "position": at(center, 3.5)}
            if set_recipe:
                machine_action["recipe"] = recipe
            ghosts.append(machine_action)
            ghosts.append({"action_type": "place_ghost", "entity": inserter_type,
                           "position": at(center, 1.5), "direction": "north"})
            ghosts.append({"action_type": "place_ghost", "entity": inserter_type,
                           "position": at(center, 5.5), "direction": "north"})

        # Two pole rows: medium-pole supply is 7x7, so one row cannot reach
        # both the input inserters (y=1) and the output row (y=5).
        for x in range(0, length + 1, 6):
            ghosts.append({"action_type": "place_ghost", "entity": "medium-electric-pole",
                           "position": at(x + 0.5, 1.5)})
            ghosts.append({"action_type": "place_ghost", "entity": "medium-electric-pole",
                           "position": at(x + 0.5, 5.5)})

        # Feed and collection endpoints plus dedicated power are placed as real
        # entities: they are line scaffolding, not part of the planned build.
        # Feeders sit on opposite sides of the input belt so each ingredient
        # lands on its own lane (an inserter drops onto the far lane).
        if feed_style == "sideload" and not mining_feed:
            # Push power west of every feeder tile so its 2x2 footprint cannot
            # collide with a feeder belt, inserter or chest.
            westmost = (SIDELOAD_SOUTH_COL if len(ingredients) == 2 else SIDELOAD_NORTH_COL) - 2
            interface_pos = at(westmost - 6.0, 3.5)
            substation_pos = at(westmost - 3.0, 2.0)
        else:
            interface_pos = at(-7.5, 3.5)
            substation_pos = at(-4.0, 2.0)
        scaffolding: List[dict] = [
            {"action_type": "place_entity", "entity": "electric-energy-interface", "position": interface_pos},
            {"action_type": "place_entity", "entity": "substation", "position": substation_pos},
            {"action_type": "place_entity", "entity": "steel-chest", "position": at(length + 1.5, 6.5)},
            {"action_type": "place_entity", "entity": inserter_type,
             "position": at(length + 0.5, 6.5), "direction": "west"},
        ]
        # Collectors scale with output demand just like feeders; extras drain
        # from the south side of the output belt into their own chests.
        extra_collectors = max(0, -(-int(crafts_per_second * FEED_HEADROOM * 100) // int(feeder_rate * 100)) - 1)
        for i in range(extra_collectors):
            x = length - 1.5 - 2 * i
            scaffolding.extend([
                {"action_type": "place_entity", "entity": inserter_type,
                 "position": at(x, 7.5), "direction": "north"},
                {"action_type": "place_entity", "entity": "steel-chest", "position": at(x, 8.5)},
            ])
        if not mining_feed and feed_style == "chest":
            # Ingredient 0 feeds from the north side, ingredient 1 from the
            # south; each west-extension tile hosts one feed point per side.
            for slot in range(feeders_needed[0]):
                x = -1.5 - slot
                scaffolding.extend([
                    {"action_type": "place_entity", "entity": "infinity-chest",
                     "position": at(x, -1.5), "infinity_filter": ingredients[0]},
                    {"action_type": "place_entity", "entity": inserter_type,
                     "position": at(x, -0.5), "direction": "north"},
                ])
            if len(ingredients) == 2:
                for slot in range(feeders_needed[1]):
                    x = -1.5 - slot
                    scaffolding.extend([
                        {"action_type": "place_entity", "entity": "infinity-chest",
                         "position": at(x, 2.5), "infinity_filter": ingredients[1]},
                        {"action_type": "place_entity", "entity": inserter_type,
                         "position": at(x, 1.5), "direction": "south"},
                    ])
        elif not mining_feed and feed_style == "sideload":
            # North feeder belt (ingredient 0): a column of belt ghosts running
            # SOUTH, its last tile (row -1) sideloading the input belt. Loaded
            # from the west by infinity-chest + inserter pairs (one per demand
            # feed point) so the buffered belt saturates the input.
            north_needed = feeders_needed[0]
            for row in range(-(north_needed + 1), 0):  # rows -(N+1)..-1
                ghosts.append({"action_type": "place_ghost", "entity": belt_type,
                               "position": at(SIDELOAD_NORTH_COL + 0.5, row + 0.5),
                               "direction": "south"})
            for slot in range(north_needed):
                row = -(2 + slot)  # loading tiles sit above the junction (row -1)
                scaffolding.extend([
                    {"action_type": "place_entity", "entity": "infinity-chest",
                     "position": at(SIDELOAD_NORTH_COL - 1.5, row + 0.5),
                     "infinity_filter": ingredients[0]},
                    {"action_type": "place_entity", "entity": inserter_type,
                     "position": at(SIDELOAD_NORTH_COL - 0.5, row + 0.5), "direction": "west"},
                ])
            if len(ingredients) == 2:
                # South feeder belt (ingredient 1): column running NORTH, its
                # last tile (row 1) sideloading the input belt from the south.
                south_needed = feeders_needed[1]
                for row in range(1, south_needed + 2):  # rows 1..S+1
                    ghosts.append({"action_type": "place_ghost", "entity": belt_type,
                                   "position": at(SIDELOAD_SOUTH_COL + 0.5, row + 0.5),
                                   "direction": "north"})
                for slot in range(south_needed):
                    row = 2 + slot  # loading tiles sit below the junction (row 1)
                    scaffolding.extend([
                        {"action_type": "place_entity", "entity": "infinity-chest",
                         "position": at(SIDELOAD_SOUTH_COL - 1.5, row + 0.5),
                         "infinity_filter": ingredients[1]},
                        {"action_type": "place_entity", "entity": inserter_type,
                         "position": at(SIDELOAD_SOUTH_COL - 0.5, row + 0.5), "direction": "west"},
                    ])

        phases = [
            {"name": "line_scaffolding", "actions": scaffolding},
            {"name": f"line_{recipe}", "actions": ghosts},
        ]
        if mining_feed:
            phases.append(self.generate_mining_feed(machine_count, origin_x, origin_y)["phases"][0])
        plan = {"phases": phases}

        repo_root = Path(__file__).resolve().parents[1]
        schema_path = repo_root / "schemas" / "build_plan.schema.json"
        with schema_path.open("r", encoding="utf-8") as handle:
            schema = json.load(handle)
        errors = list(Draft7Validator(schema).iter_errors(plan))
        if errors:
            messages = [f"- {self._format_error_path(e)}: {e.message}" for e in errors]
            raise ValueError("BuildPlan validation FAILED:\n" + "\n".join(messages))
        _reject_fuel_entities(plan)
        return plan

    def generate_mining_feed(
        self,
        machine_count: int,
        origin_x: int = 0,
        origin_y: int = 0,
    ) -> dict:
        """Miners north of the line's input belt, outputting directly onto it.

        Electric drills are 3x3; a drill at rows -3..-1 facing south drops its
        ore onto the y=0 input belt of the line at the same origin. Combined
        with a smelting line this is: ORE -> BELT -> FURNACE -> PLATE BELT,
        with no scripted item source. Requires an ore patch under the drills
        (seeded by sandbox scaffolding on the test surface).
        """
        if machine_count <= 0:
            raise ValueError("machine_count must be positive")
        ox, oy = origin_x, origin_y
        actions: List[dict] = []
        for i in range(machine_count):
            center = i * MACHINE_WIDTH + 1.5
            # Drill drop tile is 2 tiles south of center (footprint edge + 1),
            # so a center at oy-1.5 lands the ore exactly on the y=0 belt row.
            actions.append({
                "action_type": "place_ghost",
                "entity": "electric-mining-drill",
                "position": {"x": ox + center, "y": oy - 1.5},
                "direction": "south",
            })
        plan = {"phases": [{"name": "mining_feed", "actions": actions}]}

        repo_root = Path(__file__).resolve().parents[1]
        schema_path = repo_root / "schemas" / "build_plan.schema.json"
        with schema_path.open("r", encoding="utf-8") as handle:
            schema = json.load(handle)
        errors = list(Draft7Validator(schema).iter_errors(plan))
        if errors:
            messages = [f"- {self._format_error_path(e)}: {e.message}" for e in errors]
            raise ValueError("BuildPlan validation FAILED:\n" + "\n".join(messages))
        _reject_fuel_entities(plan)
        return plan

    def _feeders_needed(self, recipe: str, machine_count: int, inserter_type: str) -> List[int]:
        """Per-ingredient feed-point count = ceil(demand * headroom / rate).

        Feed capacity is provisioned with FEED_HEADROOM (user standard: 20-25%
        buffer) so lines never run at the ragged edge of supply. Shared by
        generate_line_layout and generate_chain_link so both agree on how far
        west a line's input belt extends. Integer scaling by 100 keeps the
        arithmetic deterministic (no float division rounding drift).
        """
        spec = LINE_RECIPES[recipe]
        crafts_per_second = machine_count * MACHINE_SPEEDS[spec["machine"]] / spec["craft_time"]
        feeder_rate = FEEDER_RATES[inserter_type]
        return [
            max(1, -(-int(amount * crafts_per_second * FEED_HEADROOM * 100) // int(feeder_rate * 100)))
            for amount in spec["amounts"]
        ]

    def _check_sideload_lane_capacity(self, recipe: str, machine_count: int, belt_type: str) -> None:
        """Sideload gives each ingredient ONE lane; demand must fit it.

        Hard failure below raw demand (physically cannot keep up — measured
        live: express lane 22.5/s vs 27/s cable demand ran at 8.27/s of a
        9.6/s cap). The error names the cheapest tier meeting demand with
        FEED_HEADROOM so callers can upgrade or switch feed style.
        """
        spec = LINE_RECIPES[recipe]
        crafts_per_second = machine_count * MACHINE_SPEEDS[spec["machine"]] / spec["craft_time"]
        lane_rate = BELT_TIERS[belt_type] / 2
        for ingredient, amount in zip(spec["ingredients"], spec["amounts"]):
            demand = amount * crafts_per_second
            if lane_rate < demand:
                target = demand * FEED_HEADROOM
                adequate = sorted(
                    (name for name, rate in BELT_TIERS.items() if rate / 2 >= target),
                    key=lambda name: BELT_TIERS[name],
                )
                suggestion = adequate[0] if adequate else "no tier suffices; use chest feeding or a dedicated both-lane belt"
                raise ValueError(
                    f"Sideload lane rate {lane_rate}/s < {ingredient} demand {demand}/s "
                    f"for {machine_count} machines; smallest tier with {FEED_HEADROOM}x headroom: {suggestion}"
                )

    def _belt_west(self, recipe: str, machine_count: int, feed_style: str,
                   inserter_type: str, mining_feed: bool) -> int:
        """Westmost input-belt tile column for a line.

        Chest feeders push the belt one tile west per feed slot; sideload feeders
        only need the belt to reach their junction columns (-2, and -3 when a
        second ingredient feeds from the south).
        """
        if feed_style == "sideload" and not mining_feed:
            ingredients = LINE_RECIPES[recipe]["ingredients"]
            return SIDELOAD_SOUTH_COL if len(ingredients) == 2 else SIDELOAD_NORTH_COL
        feeders = self._feeders_needed(recipe, machine_count, inserter_type)
        feed_slots = max(feeders) if not mining_feed else 0
        return -(1 + max(1, feed_slots))

    def _validate_and_reject_fuel(self, plan: dict) -> None:
        """Schema-validate a BuildPlan then enforce the electric-only invariant."""
        repo_root = Path(__file__).resolve().parents[1]
        schema_path = repo_root / "schemas" / "build_plan.schema.json"
        with schema_path.open("r", encoding="utf-8") as handle:
            schema = json.load(handle)
        errors = list(Draft7Validator(schema).iter_errors(plan))
        if errors:
            messages = [f"- {self._format_error_path(e)}: {e.message}" for e in errors]
            raise ValueError("BuildPlan validation FAILED:\n" + "\n".join(messages))
        _reject_fuel_entities(plan)

    def generate_chain_link(
        self,
        producer_origin: tuple,
        producer_recipe: str,
        producer_machines: int,
        consumer_origin: tuple,
        consumer_recipe: str,
        consumer_machines: int,
        belt_type: str = "transport-belt",
        inserter_type: str = "fast-inserter",
        consumer_feed_style: str = "chest",
    ) -> dict:
        """Route a producer line's OUTPUT belt into a consumer line's INPUT belt.

        A producer built by generate_line_layout dumps its output (row 6, flowing
        east) into a terminal collector (inserter at column length, chest at
        column length+1). This method emits one BuildPlan phase that reclaims the
        collector's tiles and lays a belt connector:

          1. remove the terminal collector inserter + chest (freeing two tiles);
          2. extend the output belt one tile east onto the freed inserter tile;
          3. corner SOUTH in a dedicated "turn column" (producer x + length + 1,
             the freed chest tile) and run straight down;
          4. corner EAST into the consumer's input-belt west extension.

        Geometry (absolute tiles; py/px = producer origin, cy/cx = consumer):
          out_row  = py + 6, turn_col = px + producer_len + 1
          row out_row:      .. [belt >][corner v]              (past the collector)
          rows out_row+1..cy-1:          [belt v]              (dedicated column)
          row cy (consumer input): [corner >]--> consumer input belt west end

        Deterministic x-offset RULE for the consumer: the connector corners east
        exactly one tile WEST of the consumer's input-belt west extension and
        feeds into it, so the consumer origin x is pinned by the producer:

            consumer_x == turn_col + 1 - consumer_belt_west

        where consumer_belt_west comes from the same demand math the consumer
        line uses. The method raises if consumer_origin's x does not match, and
        the error states the required value. Constraint: the consumer must sit at
        cy >= py + LINE_PITCH_Y so the vertical run has room and never overlaps
        the producer's own rows.
        """
        if producer_recipe not in LINE_RECIPES:
            raise ValueError(f"No line recipe knowledge for producer: {producer_recipe}")
        if consumer_recipe not in LINE_RECIPES:
            raise ValueError(f"No line recipe knowledge for consumer: {consumer_recipe}")
        if belt_type not in BELT_TIERS:
            raise ValueError(f"Unknown belt tier: {belt_type}")
        if inserter_type not in INSERTER_TIERS:
            raise ValueError(f"Unknown inserter tier: {inserter_type}")
        if consumer_feed_style not in FEED_STYLES:
            raise ValueError(f"Unknown feed style: {consumer_feed_style}")
        if producer_machines <= 0 or consumer_machines <= 0:
            raise ValueError("machine counts must be positive")

        px, py = producer_origin
        cx, cy = consumer_origin
        if cy < py + LINE_PITCH_Y:
            raise ValueError(
                f"consumer must sit at y >= producer_y + {LINE_PITCH_Y} (got cy={cy}, py={py})"
            )

        producer_len = producer_machines * MACHINE_WIDTH
        out_row = py + 6
        in_row = cy  # consumer input belt is at offset 0 from its origin

        consumer_belt_west = self._belt_west(
            consumer_recipe, consumer_machines, consumer_feed_style, inserter_type, False
        )
        turn_col = px + producer_len + 1
        required_cx = turn_col + 1 - consumer_belt_west
        if cx != required_cx:
            raise ValueError(
                f"consumer_origin x must be {required_cx} so the connector lands on the "
                f"consumer input-belt west extension (got {cx}); "
                f"turn_col={turn_col}, consumer_belt_west={consumer_belt_west}"
            )

        actions: List[dict] = []
        # 1) Reclaim the producer's terminal collector so the belt can continue.
        actions.append({"action_type": "remove_entity", "entity": inserter_type,
                        "position": {"x": px + producer_len + 0.5, "y": out_row + 0.5}})
        actions.append({"action_type": "remove_entity", "entity": "steel-chest",
                        "position": {"x": px + producer_len + 1.5, "y": out_row + 0.5}})
        # 2) Extend the output belt east onto the freed inserter tile.
        actions.append({"action_type": "place_ghost", "entity": belt_type,
                        "position": {"x": px + producer_len + 0.5, "y": out_row + 0.5},
                        "direction": "east"})
        # 3) Corner south into the dedicated turn column, then run straight down.
        actions.append({"action_type": "place_ghost", "entity": belt_type,
                        "position": {"x": turn_col + 0.5, "y": out_row + 0.5},
                        "direction": "south"})
        for y in range(out_row + 1, in_row):
            actions.append({"action_type": "place_ghost", "entity": belt_type,
                            "position": {"x": turn_col + 0.5, "y": y + 0.5},
                            "direction": "south"})
        # 4) Corner east into the consumer input-belt west extension (feeds the
        #    tile at turn_col+1, which is the consumer input belt's westmost tile).
        actions.append({"action_type": "place_ghost", "entity": belt_type,
                        "position": {"x": turn_col + 0.5, "y": in_row + 0.5},
                        "direction": "east"})

        plan = {"phases": [{
            "name": f"chain_{producer_recipe}_to_{consumer_recipe}",
            "actions": actions,
        }]}
        self._validate_and_reject_fuel(plan)
        return plan

    @staticmethod
    def material_requirements(plan: dict) -> Dict[str, int]:
        """Items construction bots need: one per ghost, keyed by entity name."""
        needs: Dict[str, int] = {}
        for phase in plan.get("phases", []):
            for action in phase.get("actions", []):
                if action.get("action_type") == "place_ghost":
                    entity = action["entity"]
                    needs[entity] = needs.get(entity, 0) + 1
        return needs
