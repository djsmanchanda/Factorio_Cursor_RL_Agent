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


def derive_spatial_pressure(metrics_summary: dict, factory_density_score: float) -> float:
    """
    Derive deterministic normalized spatial pressure [0, 1] from summary metrics.
    Requires bounds-derived dimensions and entity/bbox counts.
    """
    required_metrics = {"entity_count", "factory_area_tiles", "surface_bounds_width", "surface_bounds_height"}
    missing = sorted(required_metrics.difference(metrics_summary.keys()))
    if missing:
        raise ValueError(f"latest metrics summary missing required fields for spatial pressure: {missing}")

    entity_count = int(metrics_summary["entity_count"])
    bbox_area = float(metrics_summary["factory_area_tiles"])
    surface_bounds_width = float(metrics_summary["surface_bounds_width"])
    surface_bounds_height = float(metrics_summary["surface_bounds_height"])

    if entity_count < 0:
        raise ValueError("entity_count must be >= 0 for spatial pressure")
    if bbox_area <= 0.0:
        raise ValueError("factory_area_tiles must be > 0 for spatial pressure")
    if surface_bounds_width <= 0.0 or surface_bounds_height <= 0.0:
        raise ValueError("surface bounds must be > 0 for spatial pressure")

    surface_area = surface_bounds_width * surface_bounds_height
    if surface_area <= 0.0:
        raise ValueError("surface bounds area must be > 0 for spatial pressure")

    # Normalized deterministic components:
    # - density_component: local crowding inside occupied bbox.
    # - fill_component: how much of available bounded surface is already occupied.
    # - count_component: saturation proxy from absolute scale.
    density_component = min(1.0, max(0.0, float(factory_density_score) / 0.06))
    fill_component = min(1.0, max(0.0, bbox_area / surface_area))
    count_component = min(1.0, max(0.0, float(entity_count) / 5000.0))

    pressure = (0.50 * density_component) + (0.30 * fill_component) + (0.20 * count_component)
    return float(round(min(1.0, max(0.0, pressure)), 6))


def derive_throughput_stress(
    metrics_summary: dict,
    phase_completion_ratio: float,
    factory_density_score: float,
) -> float:
    """
    Derive deterministic normalized throughput stress [0, 1] from existing metrics outputs.
    No simulation; uses only summary aggregates.
    """
    required = {"assemblers_per_recipe", "entity_count", "labs_count"}
    missing = sorted(required.difference(metrics_summary.keys()))
    if missing:
        raise ValueError(f"latest metrics summary missing required fields for throughput stress: {missing}")

    assemblers_per_recipe = metrics_summary["assemblers_per_recipe"]
    if type(assemblers_per_recipe) is not dict:
        raise ValueError("assemblers_per_recipe must be an object for throughput stress")

    recipe_counts = []
    for _, value in assemblers_per_recipe.items():
        if not isinstance(value, int) or value < 0:
            raise ValueError("assemblers_per_recipe values must be non-negative integers")
        recipe_counts.append(value)

    assemblers_total = int(metrics_summary.get("assemblers_total", sum(recipe_counts)))
    labs_count = int(metrics_summary["labs_count"])
    entity_count = int(metrics_summary["entity_count"])

    if assemblers_total < 0 or labs_count < 0 or entity_count < 0:
        raise ValueError("throughput stress inputs must be non-negative")
    if entity_count == 0:
        return 0.0

    # Distribution skew: concentration of assembler capacity into a narrow recipe set.
    if assemblers_total <= 0 or len(recipe_counts) == 0:
        skew_component = 1.0
    else:
        top_recipe = max(recipe_counts)
        skew_component = min(1.0, max(0.0, float(top_recipe) / float(assemblers_total)))

    # Lab-to-assembler pressure: high lab demand relative to assembler base.
    lab_pressure_component = min(1.0, float(labs_count) / float(max(1, assemblers_total)))

    # Concentration pressure from global density and overall completion pressure.
    entity_concentration_component = min(1.0, max(0.0, float(factory_density_score) / 0.05))
    phase_component = min(1.0, max(0.0, float(phase_completion_ratio)))

    stress = (
        0.40 * skew_component
        + 0.20 * lab_pressure_component
        + 0.20 * entity_concentration_component
        + 0.20 * phase_component
    )
    return float(round(min(1.0, max(0.0, stress)), 6))


def derive_block_pressure_attribution(
    progress_state: dict,
    metrics_summary: dict,
    throughput_stress_index: float,
) -> Dict[str, float]:
    """
    Derive deterministic block-level pressure attribution from existing summary signals.
    Returns a map block_id -> normalized pressure in [0, 1], sorted by block_id when emitted.
    """
    required_progress = {"current_capacity", "committed_capacity"}
    missing_progress = sorted(required_progress.difference(progress_state.keys()))
    if missing_progress:
        raise ValueError(f"ProgressState missing required fields for block attribution: {missing_progress}")

    required_metrics = {"priority_blocks", "assemblers_per_recipe", "entity_count", "factory_area_tiles"}
    missing_metrics = sorted(required_metrics.difference(metrics_summary.keys()))
    if missing_metrics:
        raise ValueError(f"latest metrics summary missing required fields for block attribution: {missing_metrics}")

    if not isinstance(throughput_stress_index, (int, float)):
        raise ValueError("throughput_stress_index must be numeric for block attribution")
    if throughput_stress_index < 0.0 or throughput_stress_index > 1.0:
        raise ValueError("throughput_stress_index must be in [0,1] for block attribution")

    priority_blocks = metrics_summary["priority_blocks"]
    if type(priority_blocks) is not list or len(priority_blocks) == 0:
        raise ValueError("priority_blocks must be a non-empty array for block attribution")

    blocks = []
    for entry in priority_blocks:
        if type(entry) is not dict:
            raise ValueError("priority_blocks entries must be objects")
        block = entry.get("block")
        score = entry.get("score")
        if not isinstance(block, str) or block == "":
            raise ValueError("priority_blocks.block must be a non-empty string")
        if not isinstance(score, (int, float)):
            raise ValueError("priority_blocks.score must be numeric")
        blocks.append((block, float(score)))

    assemblers_per_recipe = metrics_summary["assemblers_per_recipe"]
    if type(assemblers_per_recipe) is not dict:
        raise ValueError("assemblers_per_recipe must be an object for block attribution")

    recipe_total = 0
    recipe_peak = 0
    for _, value in assemblers_per_recipe.items():
        if not isinstance(value, int) or value < 0:
            raise ValueError("assemblers_per_recipe values must be non-negative integers")
        recipe_total += int(value)
        if int(value) > recipe_peak:
            recipe_peak = int(value)

    entity_count = int(metrics_summary["entity_count"])
    area_tiles = float(metrics_summary["factory_area_tiles"])
    if entity_count < 0:
        raise ValueError("entity_count must be >= 0 for block attribution")
    if area_tiles <= 0.0:
        raise ValueError("factory_area_tiles must be > 0 for block attribution")

    current_capacity = int(progress_state["current_capacity"])
    committed_capacity = int(progress_state["committed_capacity"])
    if current_capacity < 0 or committed_capacity < 0:
        raise ValueError("capacity fields must be non-negative for block attribution")

    committed_ratio = float(committed_capacity) / float(max(1, committed_capacity + current_capacity))
    factory_density_score = float(entity_count) / area_tiles
    density_component = min(1.0, max(0.0, factory_density_score / 0.05))
    recipe_skew = 0.0 if recipe_total <= 0 else min(1.0, float(recipe_peak) / float(recipe_total))

    global_pressure = (
        0.45 * float(throughput_stress_index)
        + 0.25 * committed_ratio
        + 0.15 * density_component
        + 0.15 * recipe_skew
    )
    global_pressure = min(1.0, max(0.0, global_pressure))

    # Deterministic rank by declared priority score desc, then block_id asc.
    ranked_blocks = sorted(blocks, key=lambda item: (-item[1], item[0]))

    score_total = sum(max(0.0, score) for _, score in ranked_blocks)
    base_weights = {}
    for idx, (block_id, score) in enumerate(ranked_blocks, start=1):
        priority_weight = (max(0.0, score) / score_total) if score_total > 0.0 else (1.0 / len(ranked_blocks))
        rank_weight = 1.0 / float(idx)
        base_weights[block_id] = (0.70 * priority_weight) + (0.30 * rank_weight)

    max_weight = max(base_weights.values()) if base_weights else 1.0
    raw = {}
    for block_id, weight in base_weights.items():
        raw[block_id] = global_pressure * (weight / max_weight)

    normalized = {}
    for block_id in sorted(raw.keys()):
        normalized[block_id] = float(round(min(1.0, max(0.0, raw[block_id])), 6))
    return normalized


def derive_production_gap_estimate(
    progress_state: dict,
    metrics_summary: dict,
    throughput_stress_index: float,
    pressure_attribution_map: dict,
) -> Dict[str, int]:
    """
    Derive conservative deterministic per-recipe production shortfall estimates.
    Returns a map recipe_name -> integer gap, sorted by recipe_name.
    """
    required_progress = {"current_capacity", "committed_capacity", "active_phase_capacity"}
    missing_progress = sorted(required_progress.difference(progress_state.keys()))
    if missing_progress:
        raise ValueError(f"ProgressState missing required fields for production gap estimate: {missing_progress}")

    if "assemblers_per_recipe" not in metrics_summary:
        raise ValueError("latest metrics summary missing assemblers_per_recipe for production gap estimate")
    assemblers_per_recipe = metrics_summary["assemblers_per_recipe"]
    if type(assemblers_per_recipe) is not dict or len(assemblers_per_recipe) == 0:
        raise ValueError("assemblers_per_recipe must be a non-empty object for production gap estimate")

    if not isinstance(throughput_stress_index, (int, float)):
        raise ValueError("throughput_stress_index must be numeric for production gap estimate")
    throughput_stress = float(throughput_stress_index)
    if throughput_stress < 0.0 or throughput_stress > 1.0:
        raise ValueError("throughput_stress_index must be in [0,1] for production gap estimate")

    if type(pressure_attribution_map) is not dict:
        raise ValueError("pressure_attribution_map must be an object for production gap estimate")
    pressure_peak = 0.0
    for block_id, value in pressure_attribution_map.items():
        if not isinstance(block_id, str) or block_id == "":
            raise ValueError("pressure_attribution_map keys must be non-empty strings")
        if not isinstance(value, (int, float)):
            raise ValueError("pressure_attribution_map values must be numeric")
        numeric = float(value)
        if numeric < 0.0 or numeric > 1.0:
            raise ValueError("pressure_attribution_map values must be in [0,1]")
        if numeric > pressure_peak:
            pressure_peak = numeric

    current_capacity = int(progress_state["current_capacity"])
    committed_capacity = int(progress_state["committed_capacity"])
    active_phase_capacity = int(progress_state["active_phase_capacity"])
    if current_capacity < 0 or committed_capacity < 0 or active_phase_capacity < 0:
        raise ValueError("capacity fields must be non-negative for production gap estimate")

    phase_completion_ratio = 0.0 if active_phase_capacity == 0 else float(current_capacity) / float(active_phase_capacity)
    phase_completion_ratio = min(1.0, max(0.0, phase_completion_ratio))
    backlog_ratio = float(max(0, committed_capacity - current_capacity)) / float(max(1, committed_capacity))

    total_assemblers = 0
    for recipe, value in assemblers_per_recipe.items():
        if not isinstance(recipe, str) or recipe == "":
            raise ValueError("assemblers_per_recipe keys must be non-empty strings")
        if not isinstance(value, int) or value < 0:
            raise ValueError("assemblers_per_recipe values must be non-negative integers")
        total_assemblers += int(value)

    stress_scale = min(
        1.0,
        max(
            0.0,
            (0.55 * throughput_stress) + (0.30 * phase_completion_ratio * throughput_stress) + (0.15 * backlog_ratio),
        ),
    )
    block_weight = 1.0 + (0.25 * pressure_peak)
    global_gap_budget = int(round(12.0 * stress_scale * block_weight))

    recipe_names = sorted(assemblers_per_recipe.keys())
    gaps = {}
    for recipe in recipe_names:
        count = int(assemblers_per_recipe[recipe])
        scarcity = 1.0 if total_assemblers <= 0 else 1.0 - (float(count) / float(total_assemblers))
        raw_gap = float(global_gap_budget) * scarcity
        gap = int(max(0, round(raw_gap)))
        gaps[recipe] = gap

    return gaps


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
    spatial_pressure_index = derive_spatial_pressure(metrics_summary, factory_density_score)
    throughput_stress_index = derive_throughput_stress(metrics_summary, phase_completion_ratio, factory_density_score)
    pressure_attribution_map = derive_block_pressure_attribution(
        progress_state, metrics_summary, throughput_stress_index
    )
    production_gap_estimate = derive_production_gap_estimate(
        progress_state, metrics_summary, throughput_stress_index, pressure_attribution_map
    )

    return {
        "bot_utilization_ratio": float(round(bot_utilization_ratio, 6)),
        "power_stress_ratio": float(round(power_stress_ratio, 6)),
        "construction_backlog_estimate": int(construction_backlog_estimate),
        "phase_completion_ratio": float(round(phase_completion_ratio, 6)),
        "factory_density_score": float(round(factory_density_score, 6)),
        "spatial_pressure_index": float(spatial_pressure_index),
        "throughput_stress_index": float(throughput_stress_index),
        "pressure_attribution_map": pressure_attribution_map,
        "production_gap_estimate": production_gap_estimate,
    }
