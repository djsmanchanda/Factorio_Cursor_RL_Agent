# Path: tests/test_training_observer.py
# Purpose: Verify the read-only training snapshot and bounded human guidance controls.

from __future__ import annotations

import json
import threading
from pathlib import Path
from concurrent.futures import Future
from http.server import ThreadingHTTPServer
from queue import Queue
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tools.run_training_batch import _collect_results
from tools.training_observer import _handler, main
from training.observer_control import TrainingSurfaceViewer
from training.scheduler import WorkerSpec
from training.observation import build_training_snapshot
from training.scenarios.mining_delivery import generate_mining_delivery_scenario
from training.store import TrainingStore
from training.telemetry import WorkerTelemetry, publish_best_effort, read_live_workers


def _failed_transition(scenario: dict, policy_hash: str) -> dict:
    return {
        "version": "1.2.0", "episode_id": "episode-observer",
        "scenario_id": scenario["scenario_id"], "scenario_seed": scenario["seed"],
        "scenario_hash": scenario["scenario_hash"], "policy_id": "policy-observer",
        "policy_hash": policy_hash, "started_tick": 10, "ended_tick": 70,
        "observation": {"rate": 0},
        "candidates": [{"action_id": "candidate-a", "plan_hash": "sha256:" + "a" * 64,
                        "features": {"material_cost": 2}}],
        "chosen_action_id": "candidate-a",
        "result": {"status": "failed", "failure_kind": "timeout",
                   "reason": "target rate was not sustained"},
        "metrics": {
            "initial_rate_per_tick": 0, "final_rate_per_tick": 0,
            "delivered_items": 0, "material_cost": 2,
            "placements_succeeded": 1, "placements_failed": 1,
            "electric_pole_count": 3, "collection_belt_tiles": 10,
            "actual_delivery_route_tiles": 40, "shortest_delivery_route_tiles": 32,
            "route_excess_tiles": 8, "route_efficiency": 0.7,
            "occupied_footprint_tiles": 40, "placed_mining_drills": 4,
            "productive_mining_drills": 2, "productive_mining_drill_ratio": 0.5,
            "mining_drill_capacity_ticks": 240, "mining_drill_working_ticks": 120,
            "mining_drill_blocked_ticks": 60, "mining_drill_idle_ticks": 60,
        },
        "reward": {
            "completion": 0, "throughput": 0, "elapsed_ticks": -1,
            "materials": -2, "poles": -0.1, "route_excess": -0.2,
            "land_usage": -0.1, "unproductive_drill_capacity": -0.5,
            "failed_placements": -1, "total": -4.9,
        },
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
        "low_productive_capacity", "route_excess",
    }
    assert snapshot["recent_episodes"][0]["efficiency"]["route_excess_tiles"] == 8
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

class FocusRcon:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.closed = False

    def command(self, command: str) -> str:
        self.commands.append(command)
        payload = json.loads(command.removeprefix("/training_focus "))
        return json.dumps({
            "ok": True, "episode_id": payload["episode_id"],
            "surface": "training/mining-delivery-00000001", "observer_name": "main",
        })

    def close(self) -> None:
        self.closed = True


class CleanupRcon:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.closed = False

    def command(self, command: str) -> str:
        self.commands.append(command)
        payload = json.loads(command.removeprefix("/training_cleanup_orphans "))
        return json.dumps({
            "ok": True, "status": "completed", "request_id": payload["request_id"],
            "recycled": ["training/mining-delivery-0000005c"], "pending": [],
            "connected": ["training/mining-delivery-00000059"], "refused": [],
        })

    def close(self) -> None:
        self.closed = True

def _viewer(rcon: FocusRcon) -> TrainingSurfaceViewer:
    worker = WorkerSpec(
        worker_id="training-01", instance_id="training-01", host="127.0.0.1",
        game_port=35001, rcon_port=28001, script_output=Path("script-output"),
        surface_prefix="training/", force_prefix="training-",
    )
    return TrainingSurfaceViewer(
        {worker.worker_id: worker}, "secret", "main", rcon_factory=lambda *_args, **_kwargs: rcon,
    )


def test_observer_viewer_targets_only_the_configured_training_worker() -> None:
    rcon = FocusRcon()

    result = _viewer(rcon).focus("training-01", "episode-mining-delivery-00000001")

    assert result["surface"] == "training/mining-delivery-00000001"
    assert rcon.closed is True
    assert rcon.commands[0].startswith("/training_focus ")
    with pytest.raises(Exception, match="unknown training worker"):
        _viewer(FocusRcon()).focus("nauvis", "episode-mining-delivery-00000001")


def test_dashboard_cleanup_is_row_scoped_after_view() -> None:
    assets = Path(__file__).resolve().parents[1] / "tools"
    html = (assets / "training_observer.html").read_text(encoding="utf-8")
    script = (assets / "training_observer.js").read_text(encoding="utf-8")
    assert "cleanup-status" in html
    assert "cleanup-worker" not in script
    assert "'View','Remove'" in script
    assert "cleanup-button" in script

def test_observer_recycles_stale_surfaces_on_the_configured_training_worker() -> None:
    rcon = CleanupRcon()

    result = _viewer(rcon).recycle_stale("training-01")

    assert result["recycled"] == ["training/mining-delivery-0000005c"]
    assert result["connected"] == ["training/mining-delivery-00000059"]
    assert rcon.commands[0].startswith("/training_cleanup_orphans ")
    payload = json.loads(rcon.commands[0].split(" ", 1)[1])
    assert payload["confirmation_token"] == "RECYCLE_STALE_TRAINING_SURFACES"
    assert rcon.closed is True

def test_http_dashboard_can_request_stale_cleanup(tmp_path) -> None:
    database, live = tmp_path / "experience.db", tmp_path / "live"
    rcon = CleanupRcon()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(database, live, _viewer(rcon)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        request = Request(
            f"{base}/api/recycle-stale", method="POST", data=json.dumps({
                "worker_id": "training-01", "confirmation": "RECYCLE_STALE_TRAINING_SURFACES",
            }).encode("utf-8"), headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=2) as response:
            result = json.loads(response.read())
        assert result["recycled"] == ["training/mining-delivery-0000005c"]
        assert rcon.commands[0].startswith("/training_cleanup_orphans ")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

def test_http_dashboard_can_request_a_bounded_training_view(tmp_path) -> None:
    database, live = tmp_path / "experience.db", tmp_path / "live"
    rcon = FocusRcon()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(database, live, _viewer(rcon)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        request = Request(
            f"{base}/api/view", method="POST", data=json.dumps({
                "worker_id": "training-01", "episode_id": "episode-mining-delivery-00000001",
            }).encode("utf-8"), headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=2) as response:
            result = json.loads(response.read())
        assert result["surface"] == "training/mining-delivery-00000001"
        assert rcon.commands
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
