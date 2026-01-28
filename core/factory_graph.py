# Path: core/factory_graph.py
# Purpose: Build a deterministic, read-only graph view of a factory snapshot.

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

Position = Tuple[float, float]
Edge = Tuple[int, int]


@dataclass(frozen=True)
class Bounds:
    min_x: float
    max_x: float
    min_y: float
    max_y: float


class FactoryGraph:
    """Read-only graph representation of a snapshot."""

    def __init__(self, snapshot: dict):
        self.snapshot = snapshot
        self.entities: List[dict] = snapshot.get("entities", [])

        self.index_by_name: Dict[str, List[int]] = defaultdict(list)
        self.index_by_type: Dict[str, List[int]] = defaultdict(list)
        self.index_by_recipe: Dict[str, List[int]] = defaultdict(list)
        self.index_by_position: Dict[Position, List[int]] = defaultdict(list)

        self._build_indexes()
        self.edges: List[Edge] = self._build_edges()
        self.bounds = self._compute_bounds()

    def _build_indexes(self) -> None:
        for idx, entity in enumerate(self.entities):
            name = entity.get("name")
            entity_type = entity.get("type")
            recipe = entity.get("recipe")
            position = entity.get("position") or {}
            pos = (float(position.get("x", 0.0)), float(position.get("y", 0.0)))

            if name is not None:
                self.index_by_name[name].append(idx)
            if entity_type is not None:
                self.index_by_type[entity_type].append(idx)
            if recipe is not None:
                self.index_by_recipe[recipe].append(idx)

            self.index_by_position[pos].append(idx)

    def _build_edges(self) -> List[Edge]:
        edges: List[Edge] = []
        pos_index = self.index_by_position

        for idx, entity in enumerate(self.entities):
            position = entity.get("position") or {}
            x = float(position.get("x", 0.0))
            y = float(position.get("y", 0.0))

            neighbors = [
                (x + 1.0, y),
                (x - 1.0, y),
                (x, y + 1.0),
                (x, y - 1.0),
            ]

            for neighbor in neighbors:
                for neighbor_idx in pos_index.get(neighbor, []):
                    if neighbor_idx > idx:
                        edges.append((idx, neighbor_idx))

        edges.sort()
        return edges

    def _compute_bounds(self) -> Bounds | None:
        if not self.entities:
            return None

        xs = [float(entity.get("position", {}).get("x", 0.0)) for entity in self.entities]
        ys = [float(entity.get("position", {}).get("y", 0.0)) for entity in self.entities]

        return Bounds(min(xs), max(xs), min(ys), max(ys))

    def iter_entities(self) -> Iterable[dict]:
        return iter(self.entities)
