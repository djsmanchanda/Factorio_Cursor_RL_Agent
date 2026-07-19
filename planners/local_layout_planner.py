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
# loader replaces this table when multi-ingredient chains are planned.
LINE_RECIPES: Dict[str, dict] = {
    "iron-gear-wheel": {"machine": "assembling-machine-2", "input_item": "iron-plate"},
    "copper-cable": {"machine": "assembling-machine-2", "input_item": "copper-plate"},
    "iron-stick": {"machine": "assembling-machine-2", "input_item": "iron-plate"},
}

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

        spec = LINE_RECIPES[recipe]
        machine = spec["machine"]
        input_item = spec["input_item"]
        length = machine_count * MACHINE_WIDTH
        ox, oy = origin_x, origin_y

        def at(x: float, y: float) -> dict:
            return {"x": ox + x, "y": oy + y}

        ghosts: List[dict] = []
        # Belts: input lane must cover the feeder inserter's drop tile (-2).
        for x in range(-2, length):
            ghosts.append({"action_type": "place_ghost", "entity": "transport-belt",
                           "position": at(x + 0.5, 0.5), "direction": "east"})
        for x in range(0, length):
            ghosts.append({"action_type": "place_ghost", "entity": "transport-belt",
                           "position": at(x + 0.5, 6.5), "direction": "east"})

        for i in range(machine_count):
            base = i * MACHINE_WIDTH
            center = base + 1.5
            ghosts.append({"action_type": "place_ghost", "entity": machine,
                           "position": at(center, 3.5), "recipe": recipe})
            ghosts.append({"action_type": "place_ghost", "entity": "fast-inserter",
                           "position": at(center, 1.5), "direction": "north"})
            ghosts.append({"action_type": "place_ghost", "entity": "fast-inserter",
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
        scaffolding: List[dict] = [
            {"action_type": "place_entity", "entity": "electric-energy-interface", "position": at(-7.5, 3.5)},
            {"action_type": "place_entity", "entity": "substation", "position": at(-4.0, 2.0)},
            {"action_type": "place_entity", "entity": "infinity-chest",
             "position": at(-3.5, 0.5), "infinity_filter": input_item},
            {"action_type": "place_entity", "entity": "fast-inserter",
             "position": at(-2.5, 0.5), "direction": "west"},
            {"action_type": "place_entity", "entity": "steel-chest", "position": at(length + 1.5, 6.5)},
            {"action_type": "place_entity", "entity": "fast-inserter",
             "position": at(length + 0.5, 6.5), "direction": "west"},
        ]

        plan = {
            "phases": [
                {"name": "line_scaffolding", "actions": scaffolding},
                {"name": f"line_{recipe}", "actions": ghosts},
            ]
        }

        repo_root = Path(__file__).resolve().parents[1]
        schema_path = repo_root / "schemas" / "build_plan.schema.json"
        with schema_path.open("r", encoding="utf-8") as handle:
            schema = json.load(handle)
        errors = list(Draft7Validator(schema).iter_errors(plan))
        if errors:
            messages = [f"- {self._format_error_path(e)}: {e.message}" for e in errors]
            raise ValueError("BuildPlan validation FAILED:\n" + "\n".join(messages))
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
