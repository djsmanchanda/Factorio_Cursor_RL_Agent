# Path: tests/test_training_rewards.py
# Purpose: Verify bounded mining rewards and the non-negotiable safety gate.

from copy import deepcopy

import pytest

from training.rewards import reward_components, safety_violation
from training.scenarios.mining_delivery import generate_mining_delivery_scenario


def _report(*, rate: float = 0.02, delivered: int = 60, drills: int = 3) -> dict:
    capacity_ticks = drills * 1_800
    return {
        "status": "completed",
        "elapsed_ticks": 1_800,
        "metrics": {
            "rate_per_tick": rate,
            "delivered_items": delivered,
            "electric_pole_count": 5,
            "actual_delivery_route_tiles": 70,
            "shortest_delivery_route_tiles": 70,
            "occupied_footprint_tiles": 80,
            "placed_mining_drills": drills,
            "productive_mining_drills": drills,
            "productive_mining_drill_ratio": 1.0,
            "mining_drill_capacity_ticks": capacity_ticks,
            "mining_drill_working_ticks": capacity_ticks,
        },
    }


def _candidate(*, material_cost: int = 60) -> dict:
    return {
        "features": {
            "material_cost": material_cost,
            "pole_count": 5,
            "collection_belt_tiles": 10,
            "actual_delivery_route_tiles": 70,
            "shortest_delivery_route_tiles": 70,
            "route_excess_tiles": 0,
            "route_efficiency": 1.0,
            "route_tiles": 80,
            "occupied_footprint_tiles": 80,
            "drill_count": 3,
        }
    }


def _scenario(seed: int) -> dict:
    scenario = deepcopy(generate_mining_delivery_scenario(seed))
    scenario["reward_weights"].update({
        "throughput": 8.0,
        "pole": -1.0,
        "route_excess": -2.0,
        "land": -0.5,
        "unproductive_drill_capacity": -3.0,
    })
    return scenario


def _reward(scenario: dict, report: dict, candidate: dict | None = None) -> dict[str, float]:
    return reward_components(scenario, report, candidate or _candidate(), 0)


def test_reward_total_is_exact_sum_of_components() -> None:
    reward = _reward(_scenario(1), _report())

    assert reward["total"] == sum(value for key, value in reward.items() if key != "total")


def test_reward_unproductive_drills_score_worse_at_equal_output() -> None:
    scenario = _scenario(2)
    efficient = _report(drills=3)
    wasteful = _report(drills=7)
    wasteful["metrics"].update({
        "productive_mining_drills": 3,
        "productive_mining_drill_ratio": 3 / 7,
        "mining_drill_working_ticks": 3 * 1_800,
    })

    efficient_reward = _reward(scenario, efficient)
    wasteful_reward = _reward(scenario, wasteful)

    assert efficient_reward["unproductive_drill_capacity"] == 0
    assert wasteful_reward["unproductive_drill_capacity"] < 0
    assert wasteful_reward["total"] < efficient_reward["total"]


def test_reward_excess_route_and_poles_reduce_score() -> None:
    scenario = _scenario(3)
    efficient = _report()
    wasteful = deepcopy(efficient)
    wasteful["metrics"].update({
        "electric_pole_count": 12,
        "actual_delivery_route_tiles": 110,
        "shortest_delivery_route_tiles": 70,
        "route_excess_tiles": 40,
    })

    efficient_reward = _reward(scenario, efficient)
    wasteful_reward = _reward(scenario, wasteful)

    assert wasteful_reward["poles"] < efficient_reward["poles"]
    assert wasteful_reward["route_excess"] < efficient_reward["route_excess"]
    assert wasteful_reward["total"] < efficient_reward["total"]


def test_reward_faster_output_can_justify_higher_material_cost() -> None:
    scenario = _scenario(4)
    slower = _report(rate=0.005, delivered=15)
    faster = _report(rate=0.04, delivered=120)

    slower_reward = _reward(scenario, slower, _candidate(material_cost=30))
    faster_reward = _reward(scenario, faster, _candidate(material_cost=120))

    assert faster_reward["materials"] < slower_reward["materials"]
    assert faster_reward["total"] > slower_reward["total"]


def test_leaky_throughput_reward_is_steep_to_target_and_ten_times_flatter_afterward() -> None:
    scenario = deepcopy(generate_mining_delivery_scenario(4))
    target = scenario["objective"]["target_rate_per_tick"]

    below = _reward(scenario, _report(rate=target * 0.5))
    at_target = _reward(scenario, _report(rate=target))
    above = _reward(scenario, _report(rate=target * 1.5))

    weight = scenario["reward_weights"]["throughput"]
    assert below["throughput"] == pytest.approx(weight * 0.5)
    assert at_target["throughput"] == pytest.approx(weight)
    assert above["throughput"] == pytest.approx(weight * 1.05)
    assert above["throughput"] - at_target["throughput"] == pytest.approx(
        (at_target["throughput"] - below["throughput"]) / 10,
    )


def test_terminal_stage_target_controls_staged_reward() -> None:
    scenario = deepcopy(generate_mining_delivery_scenario(5))
    scenario["objective"]["target_rate_per_tick"] = 5 / 60
    report = _report(rate=35 / 60)
    report["objective"] = {"target_rate_per_tick": 30 / 60}

    reward = _reward(scenario, report)

    assert reward["throughput"] == pytest.approx(
        scenario["reward_weights"]["throughput"] * (1 + 0.1 * (35 / 30 - 1)),
    )

def test_report_metrics_override_predicted_candidate_efficiency() -> None:
    scenario = _scenario(5)
    candidate = _candidate()
    candidate["features"].update({"pole_count": 1, "actual_delivery_route_tiles": 20})
    report = _report()
    report["metrics"].update({
        "electric_pole_count": 9,
        "actual_delivery_route_tiles": 90,
        "route_excess_tiles": 20,
    })

    reward = _reward(scenario, report, candidate)

    assert reward["poles"] < 0
    assert reward["route_excess"] < 0


def test_reward_cost_terms_are_bounded_by_scenario_limits() -> None:
    scenario = _scenario(6)
    report = _report(rate=1_000_000)
    report["metrics"].update({
        "electric_pole_count": 1_000_000,
        "actual_delivery_route_tiles": 1_000_070,
        "route_excess_tiles": 1_000_000,
        "occupied_footprint_tiles": 1_000_000,
    })

    reward = _reward(scenario, report, _candidate(material_cost=1_000_000))

    assert reward["throughput"] > 2 * scenario["reward_weights"]["throughput"]
    pole_floor = scenario["reward_weights"]["pole"] * scenario["construction_budget"]["medium-electric-pole"]
    route_limit = sum(scenario["construction_budget"].get(name, 0) for name in (
        "transport-belt", "underground-belt", "splitter",
    ))
    route_floor = scenario["reward_weights"]["route_excess"] * route_limit
    bounds = scenario["constraints"]["allowed_build_area"]
    land_tiles = (bounds["x_max_exclusive"] - bounds["x_min"]) * (bounds["y_max_exclusive"] - bounds["y_min"])
    land_floor = scenario["reward_weights"]["land"] * land_tiles
    assert pole_floor <= reward["poles"] <= 0
    assert route_floor <= reward["route_excess"] <= 0
    assert land_floor <= reward["land_usage"] <= 0


def test_safety_is_not_a_reward_tradeoff() -> None:
    assert safety_violation({"failure": {"kind": "fixture"}})
    assert safety_violation({"failure_kind": "safety"})
    assert not safety_violation({"failure_kind": "strategy"})


@pytest.mark.parametrize(
    "report,candidate,failed",
    [
        (_report(rate=float("nan")), _candidate(), 0),
        (_report(), {"features": {"material_cost": float("inf")}}, 0),
        (_report(), _candidate(), float("-inf")),
    ],
    ids=["report", "candidate", "failed-placement-count"],
)
def test_non_finite_reward_input_is_rejected(
    report: dict, candidate: dict, failed: float
) -> None:
    with pytest.raises(ValueError, match="must be finite"):
        reward_components(_scenario(7), report, candidate, failed)
