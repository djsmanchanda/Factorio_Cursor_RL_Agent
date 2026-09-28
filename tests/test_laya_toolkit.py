# Path: tests/test_laya_toolkit.py
# Purpose: Verify exact binding and fresh validation of Laya toolkit choices.

from __future__ import annotations

import copy

import pytest

from training.laya_toolkit import LayaToolkit, LayaToolkitError, StaleLayaOption
from training.scenarios.mining_delivery import generate_mining_delivery_scenario
from training.scenarios.furnace_refining import generate_furnace_refining_scenario


class Bridge:
    def __init__(self, scenario):
        self.report = {
            "status": "ready",
            "scenario_id": scenario["scenario_id"],
            "surface": scenario["environment"]["surface_name"],
            "force": scenario["environment"]["force_name"],
            "tick": 1,
            "objective": {
                "target_rate_per_tick": scenario["objective"]["target_rate_per_tick"],
                "stage_index": 1,
            },
            "metrics": {"rate_per_tick": 0.0, "sustained_ticks": 0},
        }
        self.executions = []

    def observe(self, _episode_id):
        return copy.deepcopy(self.report)

    def execute(self, episode_id, authorization, plan):
        self.executions.append((episode_id, authorization, plan))
        return {"succeeded_placements": 1, "failed_placements": 0}


def test_candidate_handler_revalidates_exact_plan_and_scope_before_execution() -> None:
    scenario = generate_mining_delivery_scenario(7)
    bridge = Bridge(scenario)
    toolkit = LayaToolkit(bridge, scenario, sleeper=lambda _seconds: None)
    toolkit.bind_episode("episode-test")
    candidates = toolkit.candidate_options(bridge.report)
    selected = candidates[1]

    result = selected.handler()

    assert result.kind == "execute"
    assert bridge.executions[0][0] == "episode-test"
    assert selected.option.plan_hash == selected.candidate["plan_hash"]
    assert bridge.executions[0][2]["surface"] == scenario["environment"]["surface_name"]


def test_candidate_handler_rejects_objective_drift_as_stale() -> None:
    scenario = generate_mining_delivery_scenario(8)
    bridge = Bridge(scenario)
    toolkit = LayaToolkit(bridge, scenario, sleeper=lambda _seconds: None)
    toolkit.bind_episode("episode-test")
    selected = toolkit.candidate_options(bridge.report)[0]
    bridge.report["objective"]["target_rate_per_tick"] *= 2

    with pytest.raises(StaleLayaOption, match="changed"):
        selected.handler()
    assert bridge.executions == []


def test_selection_cannot_change_bound_signature_or_scope() -> None:
    scenario = generate_mining_delivery_scenario(9)
    bridge = Bridge(scenario)
    toolkit = LayaToolkit(bridge, scenario, sleeper=lambda _seconds: None)
    toolkit.bind_episode("episode-test")
    options = toolkit.candidate_options(bridge.report)
    selected = options[0].state_option()
    selected["scope"] = {**selected["scope"], "force": "player"}

    with pytest.raises(LayaToolkitError, match="scope"):
        toolkit.resolve_selection({"selected_option": selected}, options)


def test_wait_duration_is_model_selected_from_safe_bounds() -> None:
    scenario = generate_mining_delivery_scenario(10)
    for speed in (1.0, 4.0):
        bridge = Bridge(scenario)
        bridge.report["tick"] = 100
        waits = []

        def sleep(seconds):
            waits.append(seconds)
            bridge.report["tick"] += int(seconds * 60 * speed)

        toolkit = LayaToolkit(bridge, scenario, sleeper=sleep, game_speed=speed)
        toolkit.bind_episode("episode-test")
        options = toolkit.operation_options(bridge.report)
        selected = next(option for option in options if option.option.option_id == "wait.15s")

        result = selected.handler()

        # Waits are measured in game ticks; wall time scales with game speed.
        assert result.kind == "wait"
        assert 900 <= result.report["tick"] - 100 <= 900 + int(0.05 * 60 * speed) * len(waits)
        assert sum(waits) < 15.0 / speed + 1.0


def test_stepped_wait_advances_ticks_directly_without_sleeping() -> None:
    """Stepped mode must jump exactly the requested ticks with no wall-clock noise."""
    scenario = generate_mining_delivery_scenario(12)
    bridge = Bridge(scenario)
    bridge.report["tick"] = 100
    bridge.advance_calls: list[tuple[str, int]] = []

    def advance_ticks(episode_id, ticks):
        bridge.advance_calls.append((episode_id, ticks))
        bridge.report["tick"] += ticks

    bridge.advance_ticks = advance_ticks

    def sleep(_seconds):
        raise AssertionError("stepped mode must never sleep wall time")

    toolkit = LayaToolkit(bridge, scenario, sleeper=sleep, stepped=True)
    toolkit.bind_episode("episode-test")
    options = toolkit.operation_options(bridge.report)
    selected = next(option for option in options if option.option.option_id == "wait.15s")

    result = selected.handler()

    assert result.kind == "wait"
    assert bridge.advance_calls == [("episode-test", 900)]
    assert result.report["tick"] == 1000  # exact: no polling jitter is possible


def test_furnace_mechanics_are_exposed_as_choices_without_ranking() -> None:
    scenario = generate_furnace_refining_scenario(11)
    bridge = Bridge(scenario)
    toolkit = LayaToolkit(bridge, scenario, sleeper=lambda _seconds: None)
    toolkit.bind_episode("episode-test")

    options = toolkit.candidate_options(bridge.report)

    assert len(options) == 2
    assert {option.option.tool_id for option in options} == {"training.furnace_refining"}
    assert len({option.option.plan_hash for option in options}) == 2
