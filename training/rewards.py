# Path: training/rewards.py
# Purpose: Compute bounded, auditable training rewards with a separate safety gate.

from __future__ import annotations

import math
from collections.abc import Mapping

from training.material_costs import construction_budget_material_cost

_LEAKY_THROUGHPUT_PROFILES = frozenset({"mining-throughput-leaky-v1"})
_BALANCED_REWARD_PROFILES = frozenset({"mining-throughput-cost-v1"})
_EXCESS_THROUGHPUT_SLOPE = 0.1


def _finite(value: object, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _nonnegative(value: object, label: str) -> float:
    number = _finite(value, label)
    if number < 0:
        raise ValueError(f"{label} must be non-negative")
    return number


def _features(selected_candidate: Mapping) -> Mapping:
    value = selected_candidate.get("features")
    return value if isinstance(value, Mapping) else selected_candidate


def _metric(report: Mapping, features: Mapping, name: str, default: object = 0) -> object:
    metrics = report.get("metrics")
    if isinstance(metrics, Mapping) and name in metrics:
        return metrics[name]
    if name in report:
        return report[name]
    return features.get(name, default)


def _weight(weights: Mapping, name: str) -> float:
    return _finite(weights[name], f"{name} weight")


def _terminal_target_rate(scenario: Mapping, report: Mapping) -> float:
    """Use the measured terminal stage target when staged demand advanced."""
    objective = report.get("objective")
    if not isinstance(objective, Mapping):
        objective = scenario["objective"]
    target = _nonnegative(objective["target_rate_per_tick"], "target_rate_per_tick")
    if target == 0:
        raise ValueError("target_rate_per_tick must be greater than zero")
    return target


def _throughput_component(profile: object, rate: float, target_rate: float, weight: float) -> float:
    """Reward target progress steeply and excess throughput at a tenth slope."""
    ratio = rate / target_rate
    if profile in _LEAKY_THROUGHPUT_PROFILES | _BALANCED_REWARD_PROFILES:
        return weight * (
            ratio if ratio <= 1.0
            else 1.0 + _EXCESS_THROUGHPUT_SLOPE * (ratio - 1.0)
        )
    return min(ratio, 2.0) * weight


def _material_component(
    scenario: Mapping, material_cost: float, rate: float, target_rate: float, weight: float,
) -> float:
    """Make cost a bounded co-objective that only applies to useful output."""
    if scenario.get("reward_profile") not in _BALANCED_REWARD_PROFILES:
        return material_cost * weight
    budget_cost = construction_budget_material_cost(scenario.get("construction_budget") or {})
    cost_ratio = min(material_cost / budget_cost, 1.0)
    useful_output_ratio = min(rate / target_rate, 1.0)
    return cost_ratio * useful_output_ratio * weight


def _elapsed_component(
    profile: object, elapsed: float, max_ticks: float, weight: float,
) -> float:
    """Score ramp-up time on a bounded scenario-relative scale."""
    if profile in _BALANCED_REWARD_PROFILES:
        if max_ticks <= 0:
            raise ValueError("max_episode_ticks must be greater than zero")
        return min(elapsed / max_ticks, 1.0) * weight
    return min(elapsed, max_ticks) * weight


def reward_components(
    scenario: Mapping,
    terminal_report: Mapping,
    selected_candidate: Mapping,
    failed_placements: int | float,
    *,
    material_cost: int | float | None = None,
) -> dict[str, float]:
    """Score output, ramp-up time, and bounded construction efficiency.

    Safety remains a separate promotion gate. The balanced mining profile
    normalizes material and elapsed costs so their raw units cannot overwhelm
    useful production, while legacy profiles retain their original semantics.
    """
    weights = scenario["reward_weights"]
    features = _features(selected_candidate)
    constraints = scenario["constraints"]
    budget = scenario.get("construction_budget") or {}

    target_rate = _terminal_target_rate(scenario, terminal_report)
    rate = _nonnegative(_metric(terminal_report, features, "rate_per_tick"), "rate_per_tick")
    elapsed = _nonnegative(terminal_report.get("elapsed_ticks", 0), "elapsed_ticks")
    max_ticks = _nonnegative(constraints["max_episode_ticks"], "max_episode_ticks")
    observed_material_cost = _nonnegative(
        features.get("material_cost", 0) if material_cost is None else material_cost,
        "material_cost",
    )
    failed = _nonnegative(failed_placements, "failed_placements")

    pole_count = _nonnegative(
        _metric(terminal_report, features, "electric_pole_count", features.get("pole_count", 0)),
        "electric_pole_count",
    )
    pole_limit = _nonnegative(budget.get("medium-electric-pole", 0), "pole budget")
    route_excess = _nonnegative(
        _metric(terminal_report, features, "route_excess_tiles"), "route_excess_tiles"
    )
    route_limit = sum(
        _nonnegative(budget.get(name, 0), f"{name} budget")
        for name in ("transport-belt", "underground-belt", "splitter")
    )
    occupied_land = _nonnegative(
        _metric(terminal_report, features, "occupied_footprint_tiles"),
        "occupied_footprint_tiles",
    )
    bounds = constraints["allowed_build_area"]
    land_limit = _nonnegative(
        (bounds["x_max_exclusive"] - bounds["x_min"])
        * (bounds["y_max_exclusive"] - bounds["y_min"]),
        "available land",
    )

    placed_drills = _nonnegative(
        _metric(terminal_report, features, "placed_mining_drills", features.get("drill_count", 0)),
        "placed_mining_drills",
    )
    productive_drills = _nonnegative(
        _metric(terminal_report, features, "productive_mining_drills"),
        "productive_mining_drills",
    )
    if productive_drills > placed_drills:
        raise ValueError("productive_mining_drills cannot exceed placed_mining_drills")
    productive_ratio = _metric(
        terminal_report, features, "productive_mining_drill_ratio", None
    )
    if productive_ratio is None:
        productive_ratio = productive_drills / placed_drills if placed_drills else 0.0
    productive_ratio = _nonnegative(productive_ratio, "productive_mining_drill_ratio")
    if productive_ratio > 1:
        raise ValueError("productive_mining_drill_ratio cannot exceed one")

    capacity_ticks = _nonnegative(
        _metric(terminal_report, features, "mining_drill_capacity_ticks"),
        "mining_drill_capacity_ticks",
    )
    working_ticks = _nonnegative(
        _metric(terminal_report, features, "mining_drill_working_ticks"),
        "mining_drill_working_ticks",
    )
    if working_ticks > capacity_ticks and capacity_ticks > 0:
        raise ValueError("mining_drill_working_ticks cannot exceed capacity ticks")
    unproductive_ratio = (
        1.0 - working_ticks / capacity_ticks
        if capacity_ticks > 0 else 1.0 - productive_ratio
    )

    components = {
        "completion": _weight(weights, "completion")
        if terminal_report.get("status") == "completed" else 0.0,
        "throughput": _throughput_component(
            scenario.get("reward_profile"), rate, target_rate, _weight(weights, "throughput"),
        ),
        "elapsed_ticks": _elapsed_component(
            scenario.get("reward_profile"), elapsed, max_ticks,
            _weight(weights, "elapsed_tick"),
        ),
        "materials": _material_component(
            scenario, observed_material_cost, rate, target_rate,
            _weight(weights, "material_item"),
        ),
        "poles": min(pole_count, pole_limit) * _weight(weights, "pole"),
        "route_excess": min(route_excess, route_limit) * _weight(weights, "route_excess"),
        "land_usage": min(occupied_land, land_limit) * _weight(weights, "land"),
        "unproductive_drill_capacity": unproductive_ratio
        * _weight(weights, "unproductive_drill_capacity"),
        "failed_placements": failed * _weight(weights, "failed_placement"),
    }
    components["total"] = sum(components.values())
    return components


def safety_violation(report: Mapping) -> bool:
    """Identify failures that can never be outweighed by ordinary reward."""
    failure = report.get("failure")
    failure_kind = failure.get("kind") if isinstance(failure, Mapping) else None
    return (failure_kind or report.get("failure_kind")) in {
        "safety", "identity", "budget", "fixture",
    }
