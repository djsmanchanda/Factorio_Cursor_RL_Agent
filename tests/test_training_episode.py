# Path: tests/test_training_episode.py
# Purpose: Verify one-plan episodes produce bound transitions and always recycle.

import pytest

from training.candidates import mining_delivery_candidates
from training.episode import run_episode
from training.policies import DeterministicBaseline
from training.scenarios.mining_delivery import generate_mining_delivery_scenario


class FakeBridge:
    def __init__(self, fail_execution: bool = False):
        self.fail_execution = fail_execution
        self.recycled = []
        self.observations = 0

    def provision(self, episode_id, scenario):
        return {"started_tick": 100, "scenario_hash": scenario["scenario_hash"]}

    def observe(self, episode_id):
        self.observations += 1
        terminal = self.observations > 1
        return {
            "tick": 700 if terminal else 100, "status": "completed" if terminal else "ready",
            "elapsed_ticks": 600 if terminal else 0,
            "objective": {"target_rate_per_tick": 1 / 60},
            "metrics": {
                "rate_per_tick": 1 / 60 if terminal else 0,
                "sustained_ticks": 600 if terminal else 0,
                "resource_remaining": 100_000, "delivered_items": 10 if terminal else 0,
            },
            "failure": {"kind": "none", "reason": ""},
        }

    def execute(self, authorization, plan):
        if self.fail_execution:
            raise RuntimeError("placement failed")
        assert authorization["approved_actions"]
        assert plan["surface"].startswith("training/")
        return {"succeeded_placements": 10, "failed_placements": 0}

    def recycle(self, episode_id):
        self.recycled.append(episode_id)
        return {"status": "completed"}


def test_episode_emits_one_valid_transition_and_recycles():
    scenario = generate_mining_delivery_scenario(3)
    bridge = FakeBridge()
    transition = run_episode(
        bridge, scenario, mining_delivery_candidates(scenario),
        DeterministicBaseline(), 9, episode_id="episode-test", poll_seconds=0,
    )
    assert transition["result"]["status"] == "completed"
    assert transition["scenario_hash"] == scenario["scenario_hash"]
    assert transition["chosen_action_id"] in {
        candidate["action_id"] for candidate in mining_delivery_candidates(scenario)
    }
    assert bridge.recycled == ["episode-test"]


def test_episode_recycles_after_execution_error():
    scenario = generate_mining_delivery_scenario(4)
    bridge = FakeBridge(fail_execution=True)
    with pytest.raises(RuntimeError, match="placement failed"):
        run_episode(
            bridge, scenario, mining_delivery_candidates(scenario),
            DeterministicBaseline(), 1, episode_id="episode-error", poll_seconds=0,
        )
    assert bridge.recycled == ["episode-error"]


def test_episode_reports_live_phases_without_changing_the_transition():
    scenario = generate_mining_delivery_scenario(5)
    phases = []
    transition = run_episode(
        FakeBridge(), scenario, mining_delivery_candidates(scenario),
        DeterministicBaseline(), 2, episode_id="episode-progress", poll_seconds=0,
        on_progress=lambda event: phases.append(event["phase"]),
    )
    assert phases == [
        "provisioned", "observed", "selected", "executed", "measuring", "finished",
    ]
    assert transition["result"]["status"] == "completed"
