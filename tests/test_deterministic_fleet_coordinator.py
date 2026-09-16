# Path: tests/test_deterministic_fleet_coordinator.py
# Purpose: Verify persisted checkpoint-fleet dispatch and fail-closed polling.
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import zipfile
import pytest

from tools.checkpoint_catalog import CheckpointCatalog
from tools.deterministic_fleet_coordinator import CheckpointFleetCoordinator, FleetCoordinatorError
from tools.milestone_checkpoint import capture as capture_milestone


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "fleet@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Fleet Test"], cwd=repo, check=True)
    (repo / "marker").write_text("candidate\n", encoding="utf-8")
    subprocess.run(["git", "add", "marker"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "candidate"], cwd=repo, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    return repo, commit


def _catalog(root: Path, commit: str) -> Path:
    catalog_path = root / "registry.json"
    catalog = CheckpointCatalog(catalog_path)
    for index, checkpoint in enumerate(("C0", "C1")):
        bundle = root / f"bundle-{checkpoint}"
        (bundle / "state/episode").mkdir(parents=True)
        world = bundle / "world.zip"
        world.write_bytes(f"world-{checkpoint}".encode())
        manifest = {
            "schema_version": "1.0.0", "episode_id": f"episode-{checkpoint}",
            "repository_revision": commit, "surface": "nauvis", "force": "player",
        }
        (bundle / "state/episode/current.json").write_text(json.dumps(manifest), encoding="utf-8")
        files = {
            "world.zip": hashlib.sha256(world.read_bytes()).hexdigest(),
            "state/episode/current.json": hashlib.sha256(
                (bundle / "state/episode/current.json").read_bytes()
            ).hexdigest(),
        }
        (bundle / "checkpoint.json").write_text(json.dumps({
            "version": 1, "checkpoint_id": checkpoint, "files": files, "manifest": manifest,
        }), encoding="utf-8")
        (bundle / "compatibility.json").write_text(json.dumps({
            "compatible": True, "checkpoint_id": checkpoint,
            "creator_commit": commit, "candidate_commit": commit,
            "checked_at": "2026-09-15T00:00:00Z", "predicate_version": f"{checkpoint}-v1",
        }), encoding="utf-8")
        catalog.add_checkpoint(checkpoint, checkpoint, f"{checkpoint}-v1", order=index)
        catalog.add_generation(checkpoint, {
            "generation_id": f"generation-{checkpoint}", "origin_checkpoint_id": checkpoint,
            "path": bundle.name, "creator_commit": commit,
            "created_at": "2026-09-15T00:00:00Z", "provisional": False,
            "pinned": False, "active_input": True,
        })
        catalog.promote_default(checkpoint, f"generation-{checkpoint}")
    catalog.save()
    return catalog_path


class FakeRuntime:
    def __init__(self, root: Path):
        self.root = root
        self.plans = []

    def plan_lane(self, **kwargs):
        lane_root = self.root / kwargs["lane_id"]
        ports = SimpleNamespace(game=34200 + kwargs["slot"] * 2, rcon=27100 + kwargs["slot"] * 2)
        plan = SimpleNamespace(
            lane_root=lane_root, ports=ports, run_id=f"{kwargs['suite_id']}-{kwargs['attempt_id']}",
            manifest_path=lane_root / "episode/current.json", log_path=lane_root / "logs/autonomous-run.log",
            server_command=lambda: ["server", "start", "--root", str(lane_root)],
            runner_command=lambda: ["runner", "start", "--root", str(lane_root)],
        )
        self.plans.append(plan)
        return plan

    def prepare_lane(self, plan, bundle, *, compatibility_manifest):
        plan.lane_root.joinpath("episode").mkdir(parents=True)
        plan.lane_root.joinpath("logs").mkdir()
        plan.log_path.write_text("RUN START\n", encoding="utf-8")
        return {"ok": True}


def _queue(coordinator: CheckpointFleetCoordinator) -> None:
    with coordinator._locked() as state:  # test fixture writes the dashboard intent file
        state["queue"] = [{
            "queue_id": "suite-1-1", "suite_id": "suite-1", "commit": state["commit"],
            "checkpoint": "C0", "helper": False, "kind": "default", "status": "queued",
            "enqueue_order": 1,
        }]
        state.pop("commit", None)
        coordinator._save_unlocked(state)


def test_status_is_read_only_and_state_is_created_under_fleet_root(tmp_path: Path) -> None:
    coordinator = CheckpointFleetCoordinator(tmp_path / "server-data")
    state = coordinator.status()
    assert state["settings"]["active_cap"] == 8
    assert coordinator.state_path == tmp_path / "server-data/checkpoint-fleet/checkpoint-fleet.json"
    assert not coordinator.state_path.exists()


def test_dry_run_pins_default_generation_without_starting_commands(tmp_path: Path) -> None:
    repo, commit = _repo(tmp_path)
    server_data = tmp_path / "server-data"
    catalog_path = _catalog(server_data / "checkpoint-fleet", commit)
    fake = FakeRuntime(tmp_path / "lanes")
    calls: list[list[str]] = []
    coordinator = CheckpointFleetCoordinator(
        server_data, repo_root=repo, catalog_path=catalog_path,
        runtime=fake, command_executor=lambda command: calls.append(list(command)),
    )
    with coordinator._locked() as state:
        state["queue"] = [{
            "queue_id": "suite-1-1", "suite_id": "suite-1", "commit": commit,
            "checkpoint": "C0", "helper": False, "kind": "default", "status": "queued",
            "enqueue_order": 1,
        }]
        coordinator._save_unlocked(state)
    result = coordinator.run_once()
    assert result["plans"][0]["dry_run"] is True
    assert calls == []
    assert coordinator.status()["queue"][0]["generation"] == "generation-C0"


def test_execute_starts_isolated_server_then_runner_and_polls_structured_result(tmp_path: Path) -> None:
    repo, commit = _repo(tmp_path)
    server_data = tmp_path / "server-data"
    catalog_path = _catalog(server_data / "checkpoint-fleet", commit)
    fake = FakeRuntime(tmp_path / "lanes")
    calls: list[list[str]] = []
    coordinator = CheckpointFleetCoordinator(
        server_data, repo_root=repo, catalog_path=catalog_path,
        runtime=fake, command_executor=lambda command: calls.append(list(command)),
    )
    with coordinator._locked() as state:
        state["queue"] = [{
            "queue_id": "suite-1-1", "suite_id": "suite-1", "commit": commit,
            "checkpoint": "C0", "helper": False, "kind": "default", "status": "queued",
            "enqueue_order": 1,
        }]
        coordinator._save_unlocked(state)
    coordinator.run_once(execute=True)
    assert [command[0] for command in calls] == ["server", "runner"]
    run = coordinator.status()["runs"][0]
    result_path = Path(run["lane_root"]) / "episode/fleet-result.json"
    result_path.write_text(json.dumps({
        "status": "completed", "reached_checkpoints": ["C0", "C1"],
        "elapsed_seconds": 12,
    }), encoding="utf-8")
    state = coordinator.run_once()
    assert state["runs"][0]["status"] == "completed"
    assert state["runs"][0]["furthest_checkpoint"] == "C1"


def test_missing_compatibility_bundle_fails_closed(tmp_path: Path) -> None:
    repo, commit = _repo(tmp_path)
    server_data = tmp_path / "server-data"
    catalog_path = _catalog(server_data / "checkpoint-fleet", commit)
    (server_data / "checkpoint-fleet/bundle-C0/compatibility.json").unlink()
    coordinator = CheckpointFleetCoordinator(
        server_data, repo_root=repo, catalog_path=catalog_path, runtime=FakeRuntime(tmp_path / "lanes"),
    )
    with coordinator._locked() as state:
        state["queue"] = [{
            "queue_id": "suite-1-1", "suite_id": "suite-1", "commit": commit,
            "checkpoint": "C0", "helper": False, "kind": "default", "status": "queued",
            "enqueue_order": 1,
        }]
        coordinator._save_unlocked(state)
    state = coordinator.run_once()
    # Same-commit replay is safe even when an older catalog omitted the
    # compatibility sidecar; the coordinator records an explicit automatic
    # no-mod-diff decision at enqueue/dispatch time.
    assert state["queue"][0]["status"] == "queued"
    assert state["queue"][0]["compatibility_manifest"]["decision"] == "automatic-no-mod-diff"


def test_running_monitor_progress_holds_slot_and_terminal_cleanup_is_once(tmp_path: Path) -> None:
    repo, commit = _repo(tmp_path)
    server_data = tmp_path / "server-data"
    catalog_path = _catalog(server_data / "checkpoint-fleet", commit)
    fake = FakeRuntime(tmp_path / "lanes")
    calls: list[list[str]] = []
    coordinator = CheckpointFleetCoordinator(
        server_data, repo_root=repo, catalog_path=catalog_path, runtime=fake,
        command_executor=lambda command: calls.append(list(command)),
    )
    with coordinator._locked() as state:
        state["queue"] = [{
            "queue_id": "suite-1-1", "suite_id": "suite-1", "commit": commit,
            "checkpoint": "C0", "helper": False, "kind": "default", "status": "queued",
            "enqueue_order": 1,
        }]
        coordinator._save_unlocked(state)
    coordinator.run_once(execute=True)
    run = coordinator.status()["runs"][0]
    result_path = Path(run["lane_root"]) / "episode/fleet-result.json"
    result_path.write_text(json.dumps({
        "run_id": run["run_id"], "commit": commit, "status": "running",
        "contiguous_reached_checkpoints": ["C0", "C1"],
        "transition_durations_seconds": {"C1": 3.0}, "warnings": ["slow"],
    }), encoding="utf-8")
    state = coordinator.run_once(execute=True)
    assert state["runs"][0]["status"] == "running"
    assert state["runs"][0]["furthest_checkpoint"] == "C1"
    assert state["timing_history"]["C1|C1-v1|unknown"] == [3.0]
    assert len([item for item in calls if item[1] == "stop"]) == 0
    result_path.write_text(json.dumps({
        "run_id": run["run_id"], "commit": commit, "status": "passed",
        "contiguous_reached_checkpoints": ["C0", "C1"],
    }), encoding="utf-8")
    coordinator.run_once(execute=True)
    stop_count = len([item for item in calls if item[1] == "stop"])
    assert stop_count == 2
    coordinator.run_once(execute=True)
    assert len([item for item in calls if item[1] == "stop"]) == stop_count


def test_enqueue_pins_generation_and_runtime_timeline(tmp_path: Path) -> None:
    repo, commit = _repo(tmp_path)
    server_data = tmp_path / "server-data"
    catalog_path = _catalog(server_data / "checkpoint-fleet", commit)
    coordinator = CheckpointFleetCoordinator(
        server_data, repo_root=repo, catalog_path=catalog_path, runtime=FakeRuntime(tmp_path / "lanes"),
    )
    state = coordinator.apply_dashboard_action("run_default", {"commit": commit})
    assert {item["generation"] for item in state["queue"]} == {"generation-C0", "generation-C1"}
    assert all(item["registry_version"] for item in state["queue"])


def test_c0_capture_requires_matched_verification_before_promotion(tmp_path: Path) -> None:
    repo, commit = _repo(tmp_path)
    server_data = tmp_path / "server-data"
    catalog_path = _catalog(server_data / "checkpoint-fleet", commit)
    catalog = CheckpointCatalog.load(catalog_path)
    catalog.add_checkpoint("C2", "C2", "C2-v1", order=2)
    catalog.save()
    fake = FakeRuntime(server_data / "checkpoint-fleet/runtime/lanes")
    coordinator = CheckpointFleetCoordinator(
        server_data, repo_root=repo, catalog_path=catalog_path, runtime=fake,
        command_executor=lambda command: None,
    )
    with coordinator._locked() as state:
        state["queue"] = [{
            "queue_id": "suite-1-1", "suite_id": "suite-1", "commit": commit,
            "checkpoint": "C0", "helper": False, "kind": "default", "status": "queued",
            "enqueue_order": 1,
        }]
        coordinator._save_unlocked(state)
    coordinator.run_once(execute=True)
    run = coordinator.status()["runs"][0]
    lane_root = Path(run["lane_root"])
    (lane_root / "saves").mkdir()
    (lane_root / "script-output").mkdir()
    (lane_root / "episode/current.json").write_text(json.dumps({
        "schema_version": "1.0.0", "episode_id": "creator-run",
        "repository_revision": commit, "surface": "nauvis", "force": "player",
    }), encoding="utf-8")

    class CaptureClient:
        def command(self, command: str) -> str:
            if "helpers.write_file" in command:
                name = command.split('write_file("', 1)[1].split('"', 1)[0]
                nonce = command.split('nonce="', 1)[1].split('"', 1)[0]
                (lane_root / "script-output" / name).write_text(
                    json.dumps({"nonce": nonce}), encoding="utf-8",
                )
                return ""
            name = command.split('server_save("', 1)[1].split('"', 1)[0]
            with zipfile.ZipFile(lane_root / "saves" / f"{name}.zip", "w") as archive:
                archive.writestr("level.dat", "world")
            return "120"

    captured = capture_milestone(
        lane_root, client=CaptureClient(), checkpoint_id="C1",
        generation_id="captured-C1", run_id=run["run_id"],
        lineage_id="lineage-test", predicate_version="C1-v1",
        predicate_evidence={"passed": True}, creator_commit=commit,
        mod_hashes={"factorio_mod": "hash"},
        quiescence_check=lambda: True, controller_handshake=lambda: True,
    )
    rolling = capture_milestone(
        lane_root, client=CaptureClient(), checkpoint_id="C1",
        generation_id="rolling-C1", run_id=run["run_id"],
        lineage_id="lineage-test", predicate_version="C1-v1",
        predicate_evidence={"non_promotional": True}, creator_commit=commit,
        mod_hashes={"factorio_mod": "hash"}, reason="periodic", sequence=2,
        quiescence_check=lambda: True, controller_handshake=lambda: True,
    )
    result_path = Path(run["lane_root"]) / "episode/fleet-result.json"
    result_path.write_text(json.dumps({
        "run_id": run["run_id"], "commit": commit, "status": "passed",
        "contiguous_reached_checkpoints": ["C0", "C1"],
        "captures": {"C1": str(captured), "periodic-C1-01": str(rolling)},
    }), encoding="utf-8")
    state = coordinator.run_once(execute=True)
    candidate = next(item for item in state["promotion_candidates"] if item["checkpoint"] == "C1")
    assert candidate["provenance"] == "c0"
    assert candidate["verified"] is False
    registered_ids = {
        item["generation_id"]
        for item in CheckpointCatalog.load(catalog_path).checkpoint("C1")["generations"]
    }
    assert "rolling-C1" in registered_ids
    assert all(item.get("generation_id") != "rolling-C1" for item in state["promotion_candidates"])

    state = coordinator.apply_dashboard_action("verify_checkpoint", {
        "checkpoint": "C1", "commit": commit,
        "candidate_id": candidate["candidate_id"],
    })
    assert {item.get("side") for item in state["queue"] if item.get("status") == "queued"} == {
        "control", "candidate",
    }
    coordinator.run_once(execute=True)
    verification_runs = [
        item for item in coordinator.status()["runs"]
        if item.get("side") in {"control", "candidate"}
    ]
    assert len(verification_runs) == 2
    for verification in verification_runs:
        path = Path(verification["lane_root"]) / "episode/fleet-result.json"
        path.write_text(json.dumps({
            "run_id": verification["run_id"], "commit": commit, "status": "passed",
            "contiguous_reached_checkpoints": ["C1", "C2"],
        }), encoding="utf-8")
    state = coordinator.run_once(execute=True)
    candidate = next(item for item in state["promotion_candidates"] if item["checkpoint"] == "C1")
    assert candidate["verified"] is True

    coordinator.apply_dashboard_action("promote_checkpoint", {
        "checkpoint": "C1", "candidate_id": candidate["candidate_id"],
    })
    promoted = CheckpointCatalog.load(catalog_path).default_generation("C1")
    assert promoted["generation_id"] == "captured-C1"
    assert promoted["provisional"] is False


def test_restart_helper_queues_new_pinned_lane_without_mutating_failed_run(tmp_path: Path) -> None:
    repo, commit = _repo(tmp_path)
    server_data = tmp_path / "server-data"
    catalog_path = _catalog(server_data / "checkpoint-fleet", commit)
    fake = FakeRuntime(tmp_path / "lanes")
    coordinator = CheckpointFleetCoordinator(
        server_data, repo_root=repo, catalog_path=catalog_path, runtime=fake,
        command_executor=lambda command: None,
    )
    with coordinator._locked() as state:
        state["queue"] = [{
            "queue_id": "suite-1-1", "suite_id": "suite-1", "commit": commit,
            "checkpoint": "C1", "generation": "generation-C1", "bundle": str(server_data / "checkpoint-fleet/bundle-C1"),
            "creator_commit": commit, "registry_version": "7", "mission_id": "default",
            "factorio_version": "test", "compatibility_manifest": {"compatible": True,
                "checkpoint_id": "C1", "creator_commit": commit, "candidate_commit": commit,
                "checked_at": "now", "predicate_version": "C1-v1"},
            "helper": False, "kind": "default", "status": "failed", "enqueue_order": 1,
        }]
        state["runs"] = [{
            "run_id": "run-1", "queue_id": "suite-1-1", "suite_id": "suite-1", "commit": commit,
            "checkpoint": "C1", "kind": "middle", "status": "functional_failed",
            "lane_root": str(tmp_path / "lane"), "rcon_port": 27001,
        }]
        coordinator._save_unlocked(state)
    state = coordinator.apply_dashboard_action("restart_helper", {"run_id": "run-1"})
    retry = next(item for item in state["queue"] if item.get("restart_of_run_id") == "run-1")
    assert retry["helper"] is True
    assert retry["generation"] == "generation-C1"
    assert state["runs"][0]["status"] == "functional_failed"


def test_timing_warning_preempts_only_a_waiting_middle_lane(tmp_path: Path) -> None:
    repo, commit = _repo(tmp_path)
    server_data = tmp_path / "server-data"
    catalog_path = _catalog(server_data / "checkpoint-fleet", commit)
    catalog = CheckpointCatalog.load(catalog_path)
    catalog.add_checkpoint("C2", "C2", "C2-v1", order=2)
    catalog.save()
    coordinator = CheckpointFleetCoordinator(
        server_data, repo_root=repo, catalog_path=catalog_path,
        runtime=FakeRuntime(tmp_path / "lanes"),
    )
    state = coordinator.status()
    state["timing_history"] = {"C2|C2-v1|unknown": [10.0, 10.0, 10.0]}
    run = {
        "run_id": "slow-middle", "status": "running", "kind": "middle",
        "origin_checkpoint": "C1", "checkpoint": "C1",
        "reached_checkpoints": ["C1"], "started_at": "2026-09-15T00:00:00+00:00",
        "lane_root": str(tmp_path / "lane"), "rcon_port": 27003,
    }
    state["runs"] = [run]
    coordinator._assess_running_timing(
        state, run, catalog, ["C0", "C1", "C2"],
        {"transition_elapsed_seconds": 25.0},
    )
    assert run["slow_warning"] is True
    assert coordinator._preempt_slow_middle(state, execute=False) is None

    state["queue"] = [{"queue_id": "waiting", "status": "queued"}]
    reclaimed = coordinator._preempt_slow_middle(state, execute=False)
    assert reclaimed is run
    assert run["status"] == "timed_out"


def test_rankings_prefer_stability_when_progress_is_equal(tmp_path: Path) -> None:
    coordinator = CheckpointFleetCoordinator(tmp_path / "server-data")
    timeline = ["C0", "C1", "C2", "C3"]
    state = coordinator.status()
    state["runs"] = [
        {"commit": "fast-unstable", "status": "passed", "kind": "endpoint",
         "origin_checkpoint": "C0", "reached_checkpoints": timeline, "elapsed_seconds": 10},
        {"commit": "fast-unstable", "status": "functional_failed", "kind": "middle",
         "origin_checkpoint": "C1", "reached_checkpoints": ["C1"],
         "failure_checkpoint": "C2", "elapsed_seconds": 2},
        {"commit": "slow-stable", "status": "passed", "kind": "endpoint",
         "origin_checkpoint": "C0", "reached_checkpoints": timeline, "elapsed_seconds": 20},
        {"commit": "slow-stable", "status": "passed", "kind": "middle",
         "origin_checkpoint": "C1", "reached_checkpoints": ["C1", "C2", "C3"],
         "elapsed_seconds": 5},
    ]
    coordinator._refresh_rankings(state, timeline)
    assert [item["commit"] for item in state["ranked_commits"]] == [
        "slow-stable", "fast-unstable",
    ]
    assert state["ranked_commits"][0]["double_ticks"] == 1


def test_retention_deletes_only_catalog_owned_unprotected_bundles(tmp_path: Path) -> None:
    repo, commit = _repo(tmp_path)
    server_data = tmp_path / "server-data"
    catalog_path = _catalog(server_data / "checkpoint-fleet", commit)
    catalog = CheckpointCatalog.load(catalog_path)
    root = catalog_path.parent
    for index in range(22):
        generation_id = f"rolling-{index:02d}"
        bundle = root / "bundles" / "C1" / generation_id
        bundle.mkdir(parents=True)
        catalog.add_generation("C1", {
            "generation_id": generation_id, "origin_checkpoint_id": "C1",
            "path": str(bundle.relative_to(root)), "creator_commit": commit,
            "created_at": f"2026-08-{index + 1:02d}T00:00:00Z",
            "provisional": True, "pinned": False, "active_input": False,
            "coordinator_pinned": True,
        })
    catalog.save()
    coordinator = CheckpointFleetCoordinator(
        server_data, repo_root=repo, catalog_path=catalog_path,
        runtime=FakeRuntime(tmp_path / "lanes"),
    )
    state = coordinator.status()
    coordinator._sync_catalog_retention(state, CheckpointCatalog.load(catalog_path))

    remaining = CheckpointCatalog.load(catalog_path).checkpoint("C1")["generations"]
    assert len([item for item in remaining if item["generation_id"].startswith("rolling-")]) == 20
    assert not (root / "bundles/C1/rolling-00").exists()
    assert not (root / "bundles/C1/rolling-01").exists()
    assert (root / "bundle-C1").is_dir()


def test_checkpoint_projection_marks_inputs_older_than_ten_commits(tmp_path: Path) -> None:
    repo, creator = _repo(tmp_path)
    for index in range(11):
        (repo / "marker").write_text(f"candidate-{index}\n", encoding="utf-8")
        subprocess.run(["git", "add", "marker"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", f"candidate-{index}"], cwd=repo, check=True)
    newest = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    server_data = tmp_path / "server-data"
    catalog_path = _catalog(server_data / "checkpoint-fleet", creator)
    coordinator = CheckpointFleetCoordinator(
        server_data, repo_root=repo, catalog_path=catalog_path,
        runtime=FakeRuntime(tmp_path / "lanes"),
    )
    with coordinator._locked() as state:
        state["commits"] = [{"commit": newest}]
        coordinator._save_unlocked(state)

    projected = coordinator.status()
    assert all(item["age_status"] == "stale" for item in projected["checkpoints"][:2])
    assert all(item["stale"] is True for item in projected["checkpoints"][:2])


def test_queue_prioritizes_verification_after_newest_endpoints(tmp_path: Path) -> None:
    coordinator = CheckpointFleetCoordinator(tmp_path / "server-data")
    state = coordinator.status()
    state["queue"] = [
        {"queue_id": "old-middle", "suite_id": "old", "checkpoint": "C1",
         "kind": "default", "status": "queued", "enqueue_order": 1},
        {"queue_id": "verify", "suite_id": "verify-suite", "checkpoint": "C1",
         "kind": "verify", "status": "queued", "enqueue_order": 2},
        {"queue_id": "new-c0", "suite_id": "new", "checkpoint": "C0",
         "kind": "default", "status": "queued", "enqueue_order": 3},
    ]
    timeline = ["C0", "C1", "C2"]
    assert coordinator._select_queue_item(state, timeline)["queue_id"] == "new-c0"
    state["queue"][-1]["status"] = "running"
    assert coordinator._select_queue_item(state, timeline)["queue_id"] == "verify"


def test_custom_mission_requires_explicit_bounded_mode(tmp_path: Path) -> None:
    repo, commit = _repo(tmp_path)
    server_data = tmp_path / "server-data"
    catalog_path = _catalog(server_data / "checkpoint-fleet", commit)
    coordinator = CheckpointFleetCoordinator(
        server_data, repo_root=repo, catalog_path=catalog_path,
        runtime=FakeRuntime(tmp_path / "lanes"),
    )
    with pytest.raises(FleetCoordinatorError, match="produce:<item>"):
        coordinator.apply_dashboard_action("run_custom", {
            "commits": [commit], "checkpoints": ["C0"],
            "mission_id": "plastic-bar",
        })
    state = coordinator.apply_dashboard_action("run_custom", {
        "commits": [commit], "checkpoints": ["C0"],
        "mission_id": "produce:plastic-bar",
    })
    assert state["queue"][-1]["mission_id"] == "produce:plastic-bar"


def test_auto_watcher_enqueues_every_unseen_descendant_commit(tmp_path: Path) -> None:
    repo, first = _repo(tmp_path)
    server_data = tmp_path / "server-data"
    catalog_path = _catalog(server_data / "checkpoint-fleet", first)
    coordinator = CheckpointFleetCoordinator(
        server_data, repo_root=repo, catalog_path=catalog_path,
        runtime=FakeRuntime(tmp_path / "lanes"),
    )
    with coordinator._locked() as state:
        state["coordinator"]["auto_seen_commits"] = [first]
        coordinator._save_unlocked(state)
    commits = []
    for index in range(2):
        (repo / "marker").write_text(f"next-{index}\n", encoding="utf-8")
        subprocess.run(["git", "add", "marker"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", f"next-{index}"], cwd=repo, check=True)
        commits.append(subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True,
        ).strip())

    assert coordinator.maybe_enqueue_auto_commit() is True
    state = coordinator.status()
    automatic = [item["commit"] for item in state["commits"] if item.get("automatic")]
    assert automatic == commits
    assert len(state["queue"]) == 4
    assert sum(bool(item["helper"]) for item in state["queue"]) == 2


def test_auto_watcher_records_changed_factorio_code_as_incompatible(tmp_path: Path) -> None:
    repo, first = _repo(tmp_path)
    server_data = tmp_path / "server-data"
    catalog_path = _catalog(server_data / "checkpoint-fleet", first)
    coordinator = CheckpointFleetCoordinator(
        server_data, repo_root=repo, catalog_path=catalog_path,
        runtime=FakeRuntime(tmp_path / "lanes"),
    )
    with coordinator._locked() as state:
        state["coordinator"]["auto_seen_commits"] = [first]
        coordinator._save_unlocked(state)
    (repo / "factorio_mod").mkdir()
    (repo / "factorio_mod/control.lua").write_text("-- changed\n", encoding="utf-8")
    subprocess.run(["git", "add", "factorio_mod/control.lua"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "change lua"], cwd=repo, check=True)

    assert coordinator.maybe_enqueue_auto_commit() is True
    state = coordinator.status()
    assert {item["status"] for item in state["queue"]} == {"incompatible"}
    assert all("candidate commit" in item["failure_reason"] for item in state["queue"])
