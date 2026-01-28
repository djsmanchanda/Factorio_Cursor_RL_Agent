# Path: planners/local_layout_planner.py
# Purpose: Read-only planner skeleton that inspects snapshot facts.

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Dict, List

from core.factory_graph import FactoryGraph
from tools.validate_snapshot import validate_snapshot_file


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
