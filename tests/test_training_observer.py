# Path: tests/test_training_observer.py
# Purpose: Verify the read-only training snapshot and bounded human guidance controls.

from __future__ import annotations

import json
import threading
from concurrent.futures import Future
from http.server import ThreadingHTTPServer
from queue import Queue
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tools.run_training_batch import _collect_results
from tools.training_observer import _handler, main
from training.observation import build_training_snapshot
from training.scenarios.mining_delivery import generate_mining_delivery_scenario
from training.store import TrainingStore
from training.telemetry import WorkerTelemetry, publish_best_effort, read_live_workers


def _failed_transition(scenario: dict, policy_hash: str) -> dict:
    return {
        "version": "1.1.0", "episode_id": "episode-observer",
        "scenario_id": scenario["scenario_id"], "scenario_seed": scenario["seed"],
        "scenario_hash": scenario["scenario_hash"], "policy_id": "policy-observer",
        "policy_hash": policy_hash, "started_tick": 10, "ended_tick": 70,
        "observation": {"rate": 0},
        "candidates": [{"action_id": "candidate-a", "plan_hash": "sha256:" + "a" * 64,
                        "features": {"material_cost": 2}}],
        "chosen_action_id": "candidate-a",
        "result": {"status": "failed", "failure_kind": "timeout",
                   "reason": "target rate was not sustained"},
        "metrics": {"initial_rate_per_tick": 0, "final_rate_per_tick": 0,
                    "delivered_items": 0, "material_cost": 2,
                    "placements_succeeded": 1, "placements_failed": 1},
        "reward": {"completion": 0, "throughput": 0, "elapsed_ticks": -1,
                   "materials": -2, "infrastructure": 0,
                   "failed_placements": -1, "total": -4},
        "next_observation": {"rate": 0},
    }


def _populate(path) -> None:
    scenario = generate_mining_delivery_scenario(21)
    with TrainingStore(path) as store:
        store.save_scenario(scenario, "train", scenario["scenario_hash"])
        store.save_policy("policy-observer", "diagonal_linucb", 2, {"alpha": 1}, {})
        store.start_episode(
            "episode-observer", scenario["scenario_id"], "policy-observer", "training-01", 3,
        )
        digest = store.rows("policies")[0]["policy_hash"]
        transition = _failed_transition(scenario, digest)
        store.save_transition(transition)
        store.finish_episode("episode-observer", "failed", 10, 70, {"reward": -4})
        store.save_guidance("guidance-1", "throughput", "Prefer direct belt candidates.", 4)
        store.save_research_proposal(
            "proposal-1", "policy-observer", "local-model", "accepted",
            {"hypothesis": "explore slightly more", "changes": {"alpha": 1.2}},
        )
        store.save_llm_runtime_sample({
            "model": "local-model", "context_size": 8192, "n_cpu_moe": 30,
            "elapsed_ms": 100, "prompt_tokens": 20, "completion_tokens": 5,
        })


def test_snapshot_combines_durable_evidence_and_atomic_live_state(tmp_path) -> None:
    database, live = tmp_path / "experience.db", tmp_path / "live"
    _populate(database)
    WorkerTelemetry(live, "training-01").publish({
        "phase": "measuring", "episode_id": "episode-live", "scenario_id": "scenario-live",
        "policy_id": "policy-observer", "report": {
            "status": "running", "tick": 90, "elapsed_ticks": 80,
            "objective": {"target_rate_per_tick": 1 / 60, "sustain_ticks": 600},
            "metrics": {"rate_per_tick": 1 / 120, "sustained_ticks": 120},
        },
    })
    WorkerTelemetry(live, "autoresearch").publish({
        "kind": "autoresearch", "phase": "requesting_model", "model": "local-model",
        "guidance_count": 1,
    })
    snapshot = build_training_snapshot(database, live)
    assert snapshot["database_present"] is True
    assert snapshot["summary"]["episodes"] == 1
    assert snapshot["summary"]["generation"] == 2
    assert snapshot["active_workers"] == 1
    assert snapshot["live_workers"][0]["rate_ratio"] == 0.5
    assert snapshot["autoresearch_live"]["phase"] == "requesting_model"
    assert {item["kind"] for item in snapshot["bottlenecks"]} >= {
        "timeout", "placement_failure", "no_delivery", "no_throughput_gain",
    }
    assert snapshot["guidance"][0]["message"] == "Prefer direct belt candidates."
    assert snapshot["proposals"][0]["proposal_id"] == "proposal-1"


def test_nudge_and_dismiss_are_cli_only_and_persisted(tmp_path, capsys) -> None:
    database = tmp_path / "experience.db"
    assert main([
        "--database", str(database), "nudge", "--focus", "efficiency",
        "--message", "Penalize unnecessary pole count in the next experiment.",
    ]) == 0
    guidance_id = json.loads(capsys.readouterr().out)["guidance_id"]
    with TrainingStore(database) as store:
        assert store.active_guidance()[0]["focus"] == "efficiency"
    assert main(["--database", str(database), "dismiss", guidance_id]) == 0
    capsys.readouterr()
    with TrainingStore(database) as store:
        assert store.active_guidance() == []


def test_worker_telemetry_rejects_unsafe_names_and_skips_partial_files(tmp_path) -> None:
    with pytest.raises(ValueError, match="filesystem-safe"):
        WorkerTelemetry(tmp_path, "../nauvis")
    published = WorkerTelemetry(tmp_path, "training-01").publish({
        "phase": "ready", "worker_id": "forged", "updated_utc": "1970-01-01T00:00:00Z",
    })
    assert published["worker_id"] == "training-01"
    assert published["updated_utc"] != "1970-01-01T00:00:00Z"
    (tmp_path / "partial.json").write_text("{", encoding="utf-8")
    assert [item["worker_id"] for item in read_live_workers(tmp_path)] == ["training-01"]


def test_batch_persists_worker_results_before_all_workers_finish(tmp_path) -> None:
    scenario = generate_mining_delivery_scenario(22)
    events, done = Queue(), Future()
    done.set_result(None)
    with TrainingStore(tmp_path / "experience.db") as store:
        store.save_scenario(scenario, "train", scenario["scenario_hash"])
        store.save_policy("policy-observer", "diagonal_linucb", 0, {"alpha": 1}, {})
        store.start_episode(
            "episode-observer", scenario["scenario_id"], "policy-observer", "training-01", 3,
            status="queued",
        )
        events.put(("running", "episode-observer", None, None))
        events.put(("result", "episode-observer", None, "worker failed"))
        results, completed, failed = _collect_results({done: ["episode-observer"]}, events, store)
        assert (completed, failed, len(results)) == (0, 1, 1)
        assert store.rows("episodes")[0]["status"] == "failed"

def test_http_dashboard_is_loopback_read_only(tmp_path) -> None:
    database, live = tmp_path / "experience.db", tmp_path / "live"
    _populate(database)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(database, live))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(f"{base}/api/snapshot", timeout=2) as response:
            assert json.loads(response.read())["summary"]["episodes"] == 1
            assert response.headers["X-Content-Type-Options"] == "nosniff"
        with pytest.raises(HTTPError) as rejected:
            urlopen(Request(f"{base}/api/snapshot", method="POST"), timeout=2)
        assert rejected.value.code == 405
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

def test_telemetry_failure_is_best_effort() -> None:
    class BrokenPublisher:
        def publish(self, _event):
            raise OSError("disk full")

    assert publish_best_effort(BrokenPublisher(), {"phase": "running"}) is False
    assert publish_best_effort(None, {"phase": "running"}) is False


def _queued_episodes(store: TrainingStore, count: int) -> list[str]:
    scenario = generate_mining_delivery_scenario(23)
    store.save_scenario(scenario, "train", scenario["scenario_hash"])
    store.save_policy("policy-worker-failure", "diagonal_linucb", 0, {"alpha": 1}, {})
    identifiers = [f"episode-worker-{index}" for index in range(count)]
    for identifier in identifiers:
        store.start_episode(
            identifier, scenario["scenario_id"], "policy-worker-failure", "training-01", 3,
            status="queued",
        )
    return identifiers


def test_worker_failure_before_first_episode_finalizes_all_assigned_jobs(tmp_path) -> None:
    future, events = Future(), Queue()
    future.set_exception(ConnectionError("handshake refused"))
    with TrainingStore(tmp_path / "experience.db") as store:
        identifiers = _queued_episodes(store, 2)
        results, completed, failed = _collect_results({future: identifiers}, events, store)
        assert (completed, failed, len(results)) == (0, 2, 2)
        assert {row["status"] for row in store.rows("episodes")} == {"failed"}


def test_worker_failure_between_episodes_preserves_prior_result(tmp_path) -> None:
    future, events = Future(), Queue()
    future.set_exception(RuntimeError("worker exited"))
    with TrainingStore(tmp_path / "experience.db") as store:
        identifiers = _queued_episodes(store, 2)
        events.put(("result", identifiers[0], None, "episode failure"))
        results, _completed, failed = _collect_results({future: identifiers}, events, store)
        assert failed == 2
        assert {result[0] for result in results} == set(identifiers)

def test_corrupt_database_is_visible_instead_of_looking_empty(tmp_path) -> None:
    database = tmp_path / "experience.db"
    database.write_text("not a sqlite database", encoding="utf-8")
    snapshot = build_training_snapshot(database, tmp_path / "live")
    assert snapshot["database_present"] is True
    assert "DatabaseError" in snapshot["database_error"]
    assert snapshot["summary"]["episodes"] == 0