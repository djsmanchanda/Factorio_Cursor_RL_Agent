# Path: core/metrics.py
# Purpose: Compute deterministic, read-only metrics over a FactoryGraph.

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple

from core.factory_graph import FactoryGraph


@dataclass(frozen=True)
class BotMetrics:
    active_logistic_bots: int
    active_construction_bots: int
    total_bots: int
    bot_density_per_tile: Optional[float]
    bot_utilization_ratio: Optional[float]
    estimated_bot_trip_span: Optional[float]


@dataclass(frozen=True)
class ProductionMetrics:
    assemblers_per_recipe: Dict[str, int]
    miners_by_name: Dict[str, int]
    furnaces_by_name: Dict[str, int]
    labs_count: int
    module_slots_total: Optional[int]
    module_slots_used: Optional[int]


@dataclass(frozen=True)
class PowerMetrics:
    generators_by_name: Dict[str, int]
    consumers_by_name: Dict[str, int]
    estimated_peak_draw: Optional[float]


def _count_by_name(entities: Iterable[dict]) -> Dict[str, int]:
    counter = Counter()
    for entity in entities:
        name = entity.get("name")
        if name:
            counter[name] += 1
    return dict(counter)


def _count_by_name_filter(entities: Iterable[dict], predicate) -> Dict[str, int]:
    counter = Counter()
    for entity in entities:
        if predicate(entity):
            name = entity.get("name")
            if name:
                counter[name] += 1
    return dict(counter)


def _count_entities(entities: Iterable[dict], predicate) -> int:
    return sum(1 for entity in entities if predicate(entity))


def _bounds_area(graph: FactoryGraph) -> Optional[float]:
    if not graph.bounds:
        return None
    width = (graph.bounds.max_x - graph.bounds.min_x) + 1.0
    height = (graph.bounds.max_y - graph.bounds.min_y) + 1.0
    if width <= 0 or height <= 0:
        return None
    return width * height


def _bounds_span(graph: FactoryGraph) -> Optional[float]:
    if not graph.bounds:
        return None
    width = graph.bounds.max_x - graph.bounds.min_x
    height = graph.bounds.max_y - graph.bounds.min_y
    return math.hypot(width, height)


def compute_bot_metrics(graph: FactoryGraph) -> BotMetrics:
    entities = graph.entities

    def is_logistic_bot(entity: dict) -> bool:
        return entity.get("type") == "logistic-robot" or entity.get("name") == "logistic-robot"

    def is_construction_bot(entity: dict) -> bool:
        return entity.get("type") == "construction-robot" or entity.get("name") == "construction-robot"

    active_logistic_bots = _count_entities(entities, is_logistic_bot)
    active_construction_bots = _count_entities(entities, is_construction_bot)
    total_bots = active_logistic_bots + active_construction_bots

    area = _bounds_area(graph)
    bot_density_per_tile = (total_bots / area) if area else None
    estimated_bot_trip_span = _bounds_span(graph)

    return BotMetrics(
        active_logistic_bots=active_logistic_bots,
        active_construction_bots=active_construction_bots,
        total_bots=total_bots,
        bot_density_per_tile=bot_density_per_tile,
        bot_utilization_ratio=None,
        estimated_bot_trip_span=estimated_bot_trip_span,
    )


def compute_production_metrics(graph: FactoryGraph) -> ProductionMetrics:
    entities = graph.entities

    def is_assembler(entity: dict) -> bool:
        return entity.get("type") == "assembling-machine" or str(entity.get("name", "")).startswith(
            "assembling-machine"
        )

    def is_miner(entity: dict) -> bool:
        return entity.get("type") == "mining-drill" or "mining-drill" in str(entity.get("name", ""))

    def is_furnace(entity: dict) -> bool:
        return entity.get("type") == "furnace" or "furnace" in str(entity.get("name", ""))

    def is_lab(entity: dict) -> bool:
        return entity.get("type") == "lab" or entity.get("name") == "lab"

    assemblers = [entity for entity in entities if is_assembler(entity)]
    assemblers_per_recipe = Counter()
    for entity in assemblers:
        recipe = entity.get("recipe")
        if recipe:
            assemblers_per_recipe[recipe] += 1

    miners_by_name = _count_by_name_filter(entities, is_miner)
    furnaces_by_name = _count_by_name_filter(entities, is_furnace)
    labs_count = _count_entities(entities, is_lab)

    return ProductionMetrics(
        assemblers_per_recipe=dict(assemblers_per_recipe),
        miners_by_name=miners_by_name,
        furnaces_by_name=furnaces_by_name,
        labs_count=labs_count,
        module_slots_total=None,
        module_slots_used=None,
    )


def compute_power_metrics(graph: FactoryGraph) -> PowerMetrics:
    entities = graph.entities

    generator_names = {
        "steam-engine",
        "steam-turbine",
        "solar-panel",
        "nuclear-reactor",
        "boiler",
    }

    consumer_names = {
        "electric-mining-drill",
        "assembling-machine-1",
        "assembling-machine-2",
        "assembling-machine-3",
        "electric-furnace",
        "lab",
    }

    generators_by_name = _count_by_name_filter(entities, lambda e: e.get("name") in generator_names)
    consumers_by_name = _count_by_name_filter(entities, lambda e: e.get("name") in consumer_names)

    return PowerMetrics(
        generators_by_name=generators_by_name,
        consumers_by_name=consumers_by_name,
        estimated_peak_draw=None,
    )


def compute_all_metrics(graph: FactoryGraph) -> Tuple[BotMetrics, ProductionMetrics, PowerMetrics]:
    return compute_bot_metrics(graph), compute_production_metrics(graph), compute_power_metrics(graph)
