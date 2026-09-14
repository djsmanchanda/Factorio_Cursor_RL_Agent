# Path: tests/test_training_episode.py
# Purpose: Verify one-plan episodes produce bound transitions and always recycle.

import pytest

from training.candidates import mining_delivery_candidates
from training.episode import EpisodeCapacityInterrupted, _observation, _stage_candidates, run_episode
from training.policies import DeterministicBaseline
from training.scenarios.mining_delivery import (
    generate_mining_delivery_scenario,
    generate_staged_mining_delivery_scenario,
)


def test_staged_catalog_expands_to_thirty_then_two_thirty_per_second_lines():
    scenario = generate_staged_mining_delivery_scenario(321, sustain_ticks=600)
    stage_two = _stage_candidates(scenario, {
        "objective": {
            "target_rate_per_tick": 30 / 60,
            "destination_fixture_ids": ["delivery-sink-a"],
        },
    })
    stage_three = _stage_candidates(scenario, {
        "objective": {
            "target_rate_per_tick": 60 / 60,
            "destination_fixture_ids": ["delivery-sink-a", "delivery-sink-b"],
        },
    })

    assert max(candidate["features"]["drill_count"] for candidate in stage_two) == 60
    assert all(candidate["features"]["sink_count"] == 1 for candidate in stage_two)
    assert all(candidate["features"]["drill_count"] == 120 for candidate in stage_three)
    assert all(candidate["features"]["sink_count"] == 2 for candidate in stage_three)


def test_missing_capacity_metrics_remain_unknown_in_the_observation() -> None:
    observation = _observation({
        "metrics": {"rate_per_tick": 0.0, "sustained_ticks": 0, "resource_remaining": 10},
        "objective": {"target_rate_per_tick": 1 / 60},
    })

    assert "placed_mining_drills" not in observation
    assert "mining_drill_blocked_ticks" not in observation
    assert observation["capacity_audit_available"] == 0.0

class FakeBridge:
    def __init__(self, fail_execution: bool = False, failed_placements: int = 0):
        self.fail_execution = fail_execution
        self.failed_placements = failed_placements
        self.recycled = []
        self.observations = 0

    def provision(self, episode_id, scenario):
        return {"started_tick": 100, "scenario_hash": scenario["scenario_hash"]}

    def observe(self, episode_id):
        self.observations += 1
        terminal = self.observations > 1
        return {
            "tick": 700 if terminal else 100,
            "status": "failed" if terminal and self.failed_placements else "completed" if terminal else "ready",
            "elapsed_ticks": 600 if terminal else 0,
            "objective": {"target_rate_per_tick": 1 / 60},
            "metrics": {
                "rate_per_tick": 1 / 60 if terminal else 0,
                "sustained_ticks": 600 if terminal else 0,
                "resource_remaining": 100_000, "delivered_items": 10 if terminal else 0,
                "electric_pole_count": 3, "occupied_footprint_tiles": 60,
                "placed_mining_drills": 3,
                "productive_mining_drills": 3 if terminal else 0,
                "productive_mining_drill_ratio": 1.0 if terminal else 0.0,
                "mining_drill_capacity_ticks": 1_800 if terminal else 0,
                "mining_drill_working_ticks": 1_800 if terminal else 0,
                "mining_drill_blocked_ticks": 0,
                "mining_drill_idle_ticks": 0,
            },
            "failure": {
                "kind": "execution" if terminal and self.failed_placements else "none",
                "reason": "1 placement(s) failed" if terminal and self.failed_placements else "",
            },
        }

    def execute(self, episode_id, authorization, plan):
        assert episode_id.startswith("episode-")
        if self.fail_execution:
            raise RuntimeError("placement failed")
        assert authorization["approved_actions"]
        assert plan["surface"].startswith("training/")
        return {"succeeded_placements": 10 - self.failed_placements,
                "failed_placements": self.failed_placements}

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
    assert transition["version"] == "1.2.0"
    assert transition["metrics"]["productive_mining_drill_ratio"] == 1.0
    assert transition["metrics"]["route_efficiency"] <= 1.0
    assert "unproductive_drill_capacity" in transition["reward"]
    assert len(transition["stage_action_trail"]) == 1
    first_action = transition["stage_action_trail"][0]
    assert first_action["stage_index"] == 1
    assert first_action["selected_candidate"]["action_id"] == transition["chosen_action_id"]
    assert first_action["pre_action_observation"] == transition["observation"]


def test_staged_episode_records_each_decision_and_observes_immediately_after_upgrade():
    scenario = generate_staged_mining_delivery_scenario(322, (5.0, 10.0, 30.0), sustain_ticks=60)

    class StagedBridge(FakeBridge):
        def observe(self, episode_id):
            self.observations += 1
            stage, tick, status = (
                (1, 100, "ready") if self.observations == 1 else
                (1, 110, "running") if self.observations == 2 else
                (2, 700, "running") if self.observations == 3 else
                (2, 710, "running") if self.observations == 4 else
                (2, 720, "completed")
            )
            rate = (5.0 if stage == 1 else 10.0) / 60
            return {
                "tick": tick, "status": status, "elapsed_ticks": tick - 100,
                "objective": {
                    "target_rate_per_tick": rate, "stage_index": stage, "stage_count": 3,
                    "destination_fixture_ids": ["delivery-sink-a"],
                },
                "metrics": {
                    "rate_per_tick": rate, "sustained_ticks": 1, "stage_index": stage,
                    "resource_remaining": 100_000, "delivered_items": 10,
                    "electric_pole_count": 3, "occupied_footprint_tiles": 60,
                    "placed_mining_drills": 3, "productive_mining_drills": 3,
                    "productive_mining_drill_ratio": 1.0, "mining_drill_capacity_ticks": 1_800,
                    "mining_drill_working_ticks": 1_800, "mining_drill_blocked_ticks": 0,
                    "mining_drill_idle_ticks": 0,
                },
                "failure": {"kind": "none", "reason": ""},
            }

    bridge = StagedBridge()
    transition = run_episode(
        bridge, scenario, mining_delivery_candidates(scenario), DeterministicBaseline(), 7,
        episode_id="episode-staged-trail", poll_seconds=0,
    )

    assert [entry["stage_index"] for entry in transition["stage_action_trail"]] == [1, 2]
    assert transition["stage_action_trail"][1]["observed_tick"] == 700
    assert transition["stage_action_trail"][1]["post_execution_tick"] == 710
    assert transition["stage_action_trail"][1]["selected_candidate"]["features"]["drill_count"] >= 20


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


def test_episode_keeps_failed_placements_as_a_negative_transition():
    scenario = generate_mining_delivery_scenario(6)
    bridge = FakeBridge(failed_placements=1)

    transition = run_episode(
        bridge, scenario, mining_delivery_candidates(scenario), DeterministicBaseline(), 3,
        episode_id="episode-placement-failure", poll_seconds=0,
    )

    assert transition["result"]["failure_kind"] == "execution"
    assert transition["metrics"]["placements_failed"] == 1
    assert transition["reward"]["failed_placements"] < 0

def test_episode_uses_five_second_measurement_cadence_by_default(monkeypatch):
    scenario = generate_mining_delivery_scenario(51)
    sleeps = []

    class PollingBridge(FakeBridge):
        def observe(self, episode_id):
            self.observations += 1
            terminal = self.observations >= 4
            return {
                "tick": 700 if terminal else 100,
                "status": "completed" if terminal else "ready",
                "elapsed_ticks": 600 if terminal else 0,
                "objective": {"target_rate_per_tick": 1 / 60},
                "metrics": {
                    "rate_per_tick": 1 / 60 if terminal else 0,
                    "sustained_ticks": 600 if terminal else 0,
                    "resource_remaining": 100_000,
                    "delivered_items": 10 if terminal else 0,
                    "electric_pole_count": 3, "occupied_footprint_tiles": 60,
                    "placed_mining_drills": 3,
                    "productive_mining_drills": 3 if terminal else 0,
                    "productive_mining_drill_ratio": 1.0 if terminal else 0.0,
                    "mining_drill_capacity_ticks": 1_800 if terminal else 0,
                    "mining_drill_working_ticks": 1_800 if terminal else 0,
                    "mining_drill_blocked_ticks": 0,
                    "mining_drill_idle_ticks": 0,
                },
                "failure": {"kind": "none", "reason": ""},
            }

    monkeypatch.setattr("training.episode.time.sleep", sleeps.append)
    run_episode(
        PollingBridge(), scenario, mining_delivery_candidates(scenario),
        DeterministicBaseline(), 2, episode_id="episode-poll-cadence",
    )
    assert sleeps == [5.0, 5.0]

def test_episode_capacity_interrupt_recycles_a_provisioned_world():
    scenario = generate_mining_delivery_scenario(52)

    class PollingBridge(FakeBridge):
        def observe(self, episode_id):
            self.observations += 1
            return {
                "tick": 100, "status": "running", "elapsed_ticks": 0,
                "objective": {"target_rate_per_tick": 1 / 60},
                "metrics": {
                    "rate_per_tick": 0, "sustained_ticks": 0, "resource_remaining": 100_000,
                    "delivered_items": 0, "electric_pole_count": 3, "occupied_footprint_tiles": 60,
                    "placed_mining_drills": 3, "productive_mining_drills": 0,
                    "productive_mining_drill_ratio": 0.0, "mining_drill_capacity_ticks": 0,
                    "mining_drill_working_ticks": 0, "mining_drill_blocked_ticks": 0,
                    "mining_drill_idle_ticks": 0,
                },
                "failure": {"kind": "none", "reason": ""},
            }

    class InterruptingEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return True

    bridge = PollingBridge()
    with pytest.raises(EpisodeCapacityInterrupted, match="capacity changed"):
        run_episode(
            bridge, scenario, mining_delivery_candidates(scenario), DeterministicBaseline(), 2,
            episode_id="episode-capacity", stop_event=InterruptingEvent(),
        )
    assert bridge.recycled == ["episode-capacity"]
