# Path: tests/test_training_surrogate.py
# Purpose: Verify offline surrogate evidence loading without requiring PyTorch.

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

import tools.train_gpu_surrogate as cli
from training.canonical import policy_hash
from training.scenarios.mining_delivery import generate_mining_delivery_scenario
from training.store import TrainingStore
from training.surrogate import FEATURE_REGISTRY, load_terminal_examples, torch_runtime


def _transition(scenario: dict, *, status: str = "completed") -> dict:
    candidate = {
        "action_id": "candidate-1", "plan_hash": "sha256:" + "a" * 64,
        "features": {
            "predicted_completion": 1.0, "predicted_rate_per_tick": 0.02,
            "drill_count": 4, "collection_belt_tiles": 8,
            "actual_delivery_route_tiles": 30, "shortest_delivery_route_tiles": 25,
            "route_excess_tiles": 5, "route_efficiency": 0.83,
            "occupied_land_tiles": 42, "turn_count": 1, "pole_count": 6,
            "material_cost": 60,
        },
    }
    complete = status == "completed"
    return {
        "version": "1.2.0", "episode_id": "episode-1", "scenario_id": scenario["scenario_id"],
        "scenario_seed": scenario["seed"], "scenario_hash": scenario["scenario_hash"],
        "policy_id": "policy-1", "policy_hash": policy_hash({}),
        "started_tick": 0, "ended_tick": 120, "observation": {
            "delivered_rate_per_tick": 0.0, "target_rate_per_tick": 0.02,
            "sustained_ticks": 0.0, "resource_remaining": 100_000.0,
        }, "candidates": [candidate], "chosen_action_id": "candidate-1",
        "result": {"status": status, "failure_kind": "none" if complete else "timeout", "reason": "test"},
        "metrics": {
            "initial_rate_per_tick": 0.0, "final_rate_per_tick": 0.02,
            "delivered_items": 10, "material_cost": 60, "placements_succeeded": 8,
            "placements_failed": 0, "electric_pole_count": 6, "collection_belt_tiles": 8,
            "actual_delivery_route_tiles": 30, "shortest_delivery_route_tiles": 25,
            "route_excess_tiles": 5, "route_efficiency": 0.83, "occupied_footprint_tiles": 42,
            "placed_mining_drills": 4, "productive_mining_drills": 4,
            "productive_mining_drill_ratio": 1.0, "mining_drill_capacity_ticks": 480,
            "mining_drill_working_ticks": 480, "mining_drill_blocked_ticks": 0,
            "mining_drill_idle_ticks": 0,
        },
        "reward": {
            "completion": 10.0 if complete else 0.0, "throughput": 8.0,
            "elapsed_ticks": -0.012, "materials": -0.6, "poles": -0.6,
            "route_excess": -0.1, "land_usage": -0.042,
            "unproductive_drill_capacity": 0.0, "failed_placements": 0.0,
            "total": 16.646 if complete else 6.646,
        }, "next_observation": {"delivered_rate_per_tick": 0.02},
    }


def test_load_terminal_examples_reads_only_finished_episodes(tmp_path: Path) -> None:
    database = tmp_path / "experience.db"
    scenario = generate_mining_delivery_scenario(21)
    with TrainingStore(database) as store:
        store.save_scenario(scenario, "train", scenario["scenario_hash"])
        store.save_policy("policy-1", "test", 0, {}, {})
        store.start_episode("episode-1", scenario["scenario_id"], "policy-1", "worker", 1)
        store.save_transition(_transition(scenario))
        store.finish_episode("episode-1", "completed", 0, 120, {})
        store.start_episode("episode-running", scenario["scenario_id"], "policy-1", "worker", 2)

    examples = load_terminal_examples(database)

    assert len(examples) == 1
    assert len(examples[0].features) == len(FEATURE_REGISTRY.names)
    assert examples[0].reward == pytest.approx(16.646)
    assert not examples[0].failed


def test_load_terminal_examples_marks_failed_outcomes(tmp_path: Path) -> None:
    database = tmp_path / "experience.db"
    scenario = generate_mining_delivery_scenario(22)
    failed = _transition(scenario, status="timed_out")
    with TrainingStore(database) as store:
        store.save_scenario(scenario, "train", scenario["scenario_hash"])
        store.save_policy("policy-1", "test", 0, {}, {})
        store.start_episode("episode-1", scenario["scenario_id"], "policy-1", "worker", 1)
        store.save_transition(failed)
        store.finish_episode("episode-1", "timed_out", 0, 120, {})

    assert load_terminal_examples(database)[0].failed


def test_cli_rejects_small_data_before_loading_torch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_terminal_examples", lambda *_args: [])
    with pytest.raises(ValueError, match="at least 32"):
        cli.run(cli._parser().parse_args(["--output", str(tmp_path / "model.pt")]))


def test_missing_torch_has_clear_diagnostic(monkeypatch) -> None:
    import training.surrogate as surrogate
    from training.compute import TrainingComputeRuntime

    monkeypatch.setattr(
        surrogate, "resolve_training_compute",
        lambda _preference: TrainingComputeRuntime(
            "auto", "cpu", None, "PyTorch is not installed; using CPU"
        ),
    )
    with pytest.raises(RuntimeError, match="PyTorch is not installed"):
        torch_runtime()
