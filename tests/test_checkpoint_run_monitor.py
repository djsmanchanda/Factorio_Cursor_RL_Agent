# Path: tests/test_checkpoint_run_monitor.py | Purpose: Structured lane monitor contracts.
from __future__ import annotations

import json
from pathlib import Path
import pytest

from orchestrator.checkpoint_observer import (
    CallableObservationAdapter,
    GameBridgeObservationAdapter,
    ObservationUnsupported,
)
from tools.checkpoint_run_monitor import CheckpointRunMonitor
from tools.checkpoint_run_monitor import CheckpointMonitorError
from tools.checkpoint_run_monitor import lane_milestone_capture


def _manifest(origin: str = "C0") -> dict:
    return {
        "run_id": "run-1",
        "candidate_repository_revision": "abcdef01",
        "checkpoint_fleet": {
            "ordered_checkpoint_ids": ["C0", "C1", "C2"],
            "predicate_versions": {"C0": "c0-v1", "C1": "c1-v1", "C2": "c2-v1"},
            "origin_checkpoint_id": origin,
            "result_path": "episode/fleet-result.json",
            "capture_policy": {"on_milestone": True},
        },
    }


def _c1_snapshot(tick: int = 1) -> dict:
    return {
        "tick": tick,
        "base_valid": True,
        "starters": {
            name: {"present": True, "healthy": True, "producing": True}
            for name in ("iron", "copper", "stone")
        },
        "mall_assemblers": [
            {"recipe": "iron-gear-wheel", "working": True},
            {"recipe": "copper-cable", "working": True},
            {"recipe": "electronic-circuit", "working": True},
            {"recipe": "transport-belt", "working": True},
        ],
    }


def test_monitor_records_contiguous_progress_and_uses_safe_capture(tmp_path: Path) -> None:
    values = [{"tick": 0, "base_valid": True}, _c1_snapshot()]
    captures = []
    monitor = CheckpointRunMonitor(
        _manifest(),
        root=tmp_path,
        adapter=CallableObservationAdapter(lambda: values.pop(0)),
        capture=lambda request: captures.append(request.checkpoint_id) or "bundle-C1",
    )
    assert monitor.observe_boundary()["contiguous_reached_checkpoints"] == ["C0"]
    result = monitor.observe_boundary()
    assert result["contiguous_reached_checkpoints"] == ["C0", "C1"]
    assert result["passed_checkpoints"] == ["C1"]
    assert captures == ["C1"]
    monitor.finish("functional_failed", "later blocker")
    stored = json.loads((tmp_path / "episode/fleet-result.json").read_text())
    assert stored["status"] == "functional_failed"
    assert stored["failure_reason"] == "later blocker"


def test_monitor_fail_closed_without_structured_live_observation(tmp_path: Path) -> None:
    monitor = CheckpointRunMonitor(_manifest(), root=tmp_path)
    result = monitor.observe_boundary()
    assert result["status"] == "incompatible"
    assert result["unsupported_evidence"]
    assert "log" not in json.dumps(result["unsupported_evidence"]).lower()


def test_game_bridge_adapter_does_not_infer_from_legacy_bridge() -> None:
    try:
        GameBridgeObservationAdapter(object()).observe()
    except ObservationUnsupported as error:
        assert "structured checkpoint observation API" in str(error)
    else:  # pragma: no cover - assertion is the contract
        raise AssertionError("legacy bridge unexpectedly accepted as structured telemetry")


def test_monitor_rejects_registry_predicate_version_drift(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["checkpoint_fleet"]["predicate_versions"]["C1"] = "stale-c1-v0"
    with pytest.raises(CheckpointMonitorError, match="predicate version"):
        CheckpointRunMonitor(manifest, root=tmp_path)


def test_live_capture_receives_boundary_client_and_records_lane_provenance(tmp_path: Path) -> None:
    import zipfile

    root = tmp_path / "lane"
    for directory in ("episode", "logs", "saves", "script-output"):
        (root / directory).mkdir(parents=True)
    lane_manifest = {
        "episode_id": "creator-1",
        "lineage_id": "lineage-1",
        "repository_revision": "abcdef0123456789",
        "candidate_repository_revision": "abcdef0123456789",
        "deployed_factorio_mod_sha256": "mod-hash",
        "deployed_factorio_training_lab_sha256": "lab-hash",
        "started_at": "2026-09-15T09:10:00+05:30",
    }
    (root / "episode/current.json").write_text(json.dumps(lane_manifest), encoding="utf-8")
    manifest = _manifest()
    manifest["checkpoint_fleet"]["capture_policy"] = {"on_milestone": True}
    values = [{"tick": 0, "base_valid": True}, _c1_snapshot(20)]
    monitor = CheckpointRunMonitor(
        manifest,
        root=root,
        adapter=CallableObservationAdapter(lambda: values.pop(0)),
        capture=lane_milestone_capture(root, manifest),
    )

    class Client:
        def command(self, command: str) -> str:
            if "helpers.write_file" in command:
                name = command.split('write_file("', 1)[1].split('"', 1)[0]
                nonce = command.split('nonce="', 1)[1].split('"', 1)[0]
                (root / "script-output" / name).write_text(
                    json.dumps({"nonce": nonce}), encoding="utf-8"
                )
                return ""
            name = command.split('server_save("', 1)[1].split('"', 1)[0]
            with zipfile.ZipFile(root / "saves" / f"{name}.zip", "w") as archive:
                archive.writestr("level.dat", "world")
            return "20"

    client = Client()
    monitor.observe_boundary(client, object())
    result = monitor.observe_boundary(client, object())
    assert result["contiguous_reached_checkpoints"] == ["C0", "C1"]
    bundle = Path(result["captures"]["C1"])
    payload = json.loads((bundle / "checkpoint.json").read_text(encoding="utf-8"))
    assert payload["provenance"]["creator_commit"] == lane_manifest["candidate_repository_revision"]
    assert payload["provenance"]["mod_hashes"]["deployed_factorio_mod_sha256"] == "mod-hash"
    assert payload["save_name"].startswith("checkpoint1_abcdef01_09_15_09am")


def test_periodic_capture_is_distinct_from_milestone_capture(tmp_path: Path) -> None:
    now = [0.0]
    values = [{"tick": 0, "base_valid": True}, _c1_snapshot(20)]
    requests = []
    manifest = _manifest()
    manifest["checkpoint_fleet"]["capture_policy"] = {
        "on_milestone": True, "periodic_seconds": 5,
    }
    monitor = CheckpointRunMonitor(
        manifest,
        root=tmp_path,
        adapter=CallableObservationAdapter(lambda: values.pop(0)),
        capture=lambda request, **_context: requests.append(request) or request.reason,
        clock=lambda: now[0],
    )
    monitor.observe_boundary()
    now[0] = 6.0
    result = monitor.observe_boundary()
    assert [request.reason for request in requests] == ["milestone", "periodic"]
    assert "C1" in result["captures"]
    assert any(key.startswith("periodic-C1-") for key in result["captures"])


def test_terminal_capture_uses_last_structured_boundary(tmp_path: Path) -> None:
    values = [{"tick": 0, "base_valid": True}, _c1_snapshot(20)]
    requests = []
    monitor = CheckpointRunMonitor(
        _manifest(),
        root=tmp_path,
        adapter=CallableObservationAdapter(lambda: values.pop(0)),
        capture=lambda request, **_context: requests.append(request) or request.reason,
    )
    client = object()
    monitor.observe_boundary(client, object())
    monitor.observe_boundary(client, object())
    result = monitor.finish("functional_failed", "later blocker", client=client)

    assert [request.reason for request in requests] == ["milestone", "terminal"]
    assert any(key.startswith("terminal-C1-") for key in result["captures"])
