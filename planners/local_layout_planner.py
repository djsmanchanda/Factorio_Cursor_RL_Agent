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

# Vertical pitch between stacked lines: 8 rows of layout plus 8 reserved for
# expansion, so lines can grow east (more machines) and south (more lines).
LINE_PITCH_Y = 16

# Logistics tiers (docs/21): higher tiers raise line throughput. Turbo belts
# and stack inserters have off-planet sourcing constraints in real supply
# chains; on the sandbox they arrive via scaffolding.
BELT_TIERS = {"transport-belt": 15, "fast-transport-belt": 30, "express-transport-belt": 45, "turbo-transport-belt": 60}
INSERTER_TIERS = {"fast-inserter", "bulk-inserter", "stack-inserter"}

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
    ) -> dict:
        """Deterministic single-recipe production line.

        Row layout (y offsets from origin, y grows south):
          0: input belt flowing east          4: (machine bottom row)
          1: input inserters + power poles    5: output inserters
          2: machine top row                  6: output belt flowing east
        Feed (infinity chest) at the west end, collection chest at the east end.
        Inserters face their pickup side; drop is the opposite tile.
        """
        if recipe not in LINE_RECIPES:
            raise ValueError(f"No line recipe knowledge for: {recipe}")
        if machine_count <= 0:
            raise ValueError("machine_count must be positive")

        if belt_type not in BELT_TIERS:
            raise ValueError(f"Unknown belt tier: {belt_type}")
        if inserter_type not in INSERTER_TIERS:
            raise ValueError(f"Unknown inserter tier: {inserter_type}")

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
        amounts = spec["amounts"]
        crafts_per_second = machine_count * MACHINE_SPEEDS[machine] / spec["craft_time"]
        feeder_rate = FEEDER_RATES[inserter_type]
        feeders_needed = [
            max(1, -(-int(amount * crafts_per_second * 10) // int(feeder_rate * 10)))
            for amount in amounts
        ]
        feed_slots = max(feeders_needed) if not mining_feed else 0
        belt_west = -(1 + max(1, feed_slots))

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
        scaffolding: List[dict] = [
            {"action_type": "place_entity", "entity": "electric-energy-interface", "position": at(-7.5, 3.5)},
            {"action_type": "place_entity", "entity": "substation", "position": at(-4.0, 2.0)},
            {"action_type": "place_entity", "entity": "steel-chest", "position": at(length + 1.5, 6.5)},
            {"action_type": "place_entity", "entity": inserter_type,
             "position": at(length + 0.5, 6.5), "direction": "west"},
        ]
        if not mining_feed:
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
