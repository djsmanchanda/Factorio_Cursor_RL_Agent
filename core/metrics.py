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


def build_phase_metrics(graph: FactoryGraph) -> Dict[str, object]:
    """Return a minimal, explicit metrics dictionary for phase planners."""
    production = compute_production_metrics(graph)
    circuits_recipes = {
        "electronic-circuit",
        "advanced-circuit",
        "processing-unit",
    }

    circuits_present = any(recipe in circuits_recipes for recipe in production.assemblers_per_recipe)
    smelters_present = bool(production.furnaces_by_name)

    return {
        "labs_count": production.labs_count,
        "smelters_present": smelters_present,
        "circuits_present": circuits_present,
    }


def derive_rl_observation_health(progress_state: dict, metrics_summary: dict) -> Dict[str, object]:
    """
    Derive deterministic RL observation health indicators from existing metrics/progress.
    Fails loudly when required source fields are unavailable.
    """
    required_progress = {"current_capacity", "committed_capacity", "active_phase_capacity"}
    missing_progress = sorted(required_progress.difference(progress_state.keys()))
    if missing_progress:
        raise ValueError(f"ProgressState missing required fields for RL enrichment: {missing_progress}")

    current_capacity = int(progress_state["current_capacity"])
    committed_capacity = int(progress_state["committed_capacity"])
    active_phase_capacity = int(progress_state["active_phase_capacity"])

    # Bot utilization: prefer explicit active/total counts; otherwise fail loudly.
    if "total_bots" not in metrics_summary:
        raise ValueError("latest metrics summary missing total_bots for bot_utilization_ratio")
    total_bots = int(metrics_summary["total_bots"])
    active_logistic = int(metrics_summary.get("active_logistic_bots", 0))
    active_construction = int(metrics_summary.get("active_construction_bots", 0))
    active_bots = active_logistic + active_construction
    bot_utilization_ratio = 0.0 if total_bots <= 0 else min(1.0, float(active_bots) / float(total_bots))

    # Power stress: consumers/production when present; else deterministic conservative proxy.
    if "power_consumers_total" in metrics_summary and "power_producers_total" in metrics_summary:
        consumers = float(metrics_summary["power_consumers_total"])
        producers = float(metrics_summary["power_producers_total"])
        if producers <= 0.0:
            power_stress_ratio = 1.0 if consumers <= 0.0 else float(consumers)
        else:
            power_stress_ratio = consumers / producers
    elif "estimated_peak_draw" in metrics_summary and "estimated_power_supply" in metrics_summary:
        draw = float(metrics_summary["estimated_peak_draw"])
        supply = float(metrics_summary["estimated_power_supply"])
        if supply <= 0.0:
            power_stress_ratio = 1.0 if draw <= 0.0 else float(draw)
        else:
            power_stress_ratio = draw / supply
    else:
        # Conservative proxy if direct power accounting is unavailable.
        labs_count = int(metrics_summary.get("labs_count", 0))
        entity_count = int(metrics_summary.get("entity_count", 0))
        if entity_count <= 0 and labs_count <= 0:
            raise ValueError(
                "latest metrics summary missing power fields and proxy fields for power_stress_ratio"
            )
        power_stress_ratio = float(labs_count + max(1, entity_count // 50)) / float(max(1, entity_count // 40))

    # Construction backlog estimate: deterministic proxy from progress deltas.
    construction_backlog_estimate = max(0, committed_capacity - current_capacity)

    # Phase completion ratio.
    phase_completion_ratio = 0.0 if active_phase_capacity <= 0 else float(current_capacity) / float(active_phase_capacity)

    # Factory density score: entity_count / area_tiles when available.
    if "entity_count" not in metrics_summary or "factory_area_tiles" not in metrics_summary:
        raise ValueError("latest metrics summary must include entity_count and factory_area_tiles for factory_density_score")
    entity_count = int(metrics_summary["entity_count"])
    area_tiles = float(metrics_summary["factory_area_tiles"])
    if area_tiles <= 0.0:
        raise ValueError("factory_area_tiles must be > 0 for factory_density_score")
    factory_density_score = float(entity_count) / area_tiles

    return {
        "bot_utilization_ratio": float(round(bot_utilization_ratio, 6)),
        "power_stress_ratio": float(round(power_stress_ratio, 6)),
        "construction_backlog_estimate": int(construction_backlog_estimate),
        "phase_completion_ratio": float(round(phase_completion_ratio, 6)),
        "factory_density_score": float(round(factory_density_score, 6)),
    }
