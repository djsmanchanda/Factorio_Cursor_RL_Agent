# Path: tests/test_training_store.py
# Purpose: Verify durable SQLite training evidence and foreign-key boundaries.

from __future__ import annotations

import sqlite3

import pytest

from training.scenarios.mining_delivery import generate_mining_delivery_scenario
from training.store import TrainingStore


def transition(scenario: dict, policy_hash: str) -> dict:
    return {
        "version": "1.1.0", "episode_id": "episode-1",
        "scenario_id": scenario["scenario_id"], "scenario_hash": scenario["scenario_hash"],
        "scenario_seed": scenario["seed"], "policy_id": "policy-1",
        "policy_hash": policy_hash, "started_tick": 1, "ended_tick": 61,
        "observation": {"rate": 0},
        "candidates": [{"action_id": "a", "plan_hash": "sha256:" + "a" * 64,
                        "features": {"cost": 1}}],
        "chosen_action_id": "a",
        "result": {"status": "completed", "failure_kind": "none", "reason": "done"},
        "metrics": {"initial_rate_per_tick": 0, "final_rate_per_tick": 1 / 60,
                    "delivered_items": 60, "material_cost": 1,
                    "placements_succeeded": 1, "placements_failed": 0},
        "reward": {"completion": 10, "throughput": 1, "elapsed_ticks": -1,
                   "materials": -1, "infrastructure": 0,
                   "failed_placements": 0, "total": 9},
        "next_observation": {"rate": 1},
    }


def test_store_persists_complete_evidence_and_wal(tmp_path) -> None:
    scenario = generate_mining_delivery_scenario(1)
    path = tmp_path / "experience.db"
    with TrainingStore(path) as store:
        store.save_scenario(scenario, "train", scenario["scenario_hash"])
        store.save_policy("policy-1", "diagonal_linucb", 0, {"alpha": 1}, {})
        store.start_episode("episode-1", scenario["scenario_id"], "policy-1", "worker-1", 7)
        stored_policy_hash = store.rows("policies")[0]["policy_hash"]
        store.save_transition(transition(scenario, stored_policy_hash))
        store.finish_episode("episode-1", "completed", 1, 61, {"reward": 9})
        store.save_evaluation("eval-1", "policy-1", "holdout", "set", {"episodes": 20}, True)
        store.promote("mining_delivery", "policy-1", "eval-1")
        store.save_llm_runtime_sample({
            "model": "local", "context_size": 8192, "n_cpu_moe": 40,
            "elapsed_ms": 250, "prompt_tokens": 100, "completion_tokens": 20,
        })
        assert store.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert len(store.rows("transitions")) == 1
        assert len(store.rows("champions")) == 1
        assert len(store.rows("llm_runtime_samples")) == 1

    with TrainingStore(path) as reopened:
        assert reopened.rows("episodes")[0]["status"] == "completed"


def test_store_rejects_orphan_episode(tmp_path) -> None:
    with TrainingStore(tmp_path / "experience.db") as store:
        with pytest.raises(sqlite3.IntegrityError):
            store.start_episode("orphan", "missing", "missing", "worker", 1)


def test_store_tracks_queue_progress_and_bounded_guidance(tmp_path) -> None:
    scenario = generate_mining_delivery_scenario(2)
    with TrainingStore(tmp_path / "experience.db") as store:
        store.save_scenario(scenario, "train", scenario["scenario_hash"])
        store.save_policy("policy-1", "diagonal_linucb", 0, {"alpha": 1}, {})
        store.start_episode(
            "episode-queued", scenario["scenario_id"], "policy-1", "worker-1", 7,
            status="queued",
        )
        store.mark_episode_running("episode-queued")
        assert store.rows("episodes")[0]["status"] == "running"
        store.save_guidance("guidance-1", "reliability", "Prefer robust candidates.", 3)
        assert store.active_guidance(generation=2)[0]["guidance_id"] == "guidance-1"
        assert store.active_guidance(generation=4) == []
        store.dismiss_guidance("guidance-1")
        assert store.active_guidance() == []
        with pytest.raises(ValueError, match="1000"):
            store.save_guidance("too-long", "general", "x" * 1001)
