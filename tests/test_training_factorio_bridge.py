# Path: tests/test_training_factorio_bridge.py
# Purpose: Pin loopback isolation and request-bound training report collection.

from __future__ import annotations

import json

import pytest

from planners.sandbox_infrastructure import build_layout_authorization
from training.candidates import mining_delivery_candidates
from training.factorio_bridge import _MAX_UPLOAD_CHUNKS, _UPLOAD_CHUNK_BYTES, FactorioTrainingBridge, TrainingBridgeError
from training.scenarios.mining_delivery import generate_mining_delivery_scenario


class ReportRcon:
    def __init__(self, root, *, failed_placements: int = 0, wrong_kind: bool = False):
        self.root = root
        self.closed = False
        self.commands = []
        self.uploads = {}
        self.failed_placements = failed_placements
        self.wrong_kind = wrong_kind

    def command(self, command):
        self.commands.append(command)
        if command.startswith("/help"):
            return "available"
        name, raw = command.split(" ", 1)
        payload = json.loads(raw)
        if name == "/training_upload":
            upload = self.uploads.setdefault(payload["upload_id"], {})
            upload[payload["chunk_index"]] = payload["chunk"]
            return ""
        kind = {
            "/training_execute": "execution",
            "/training_observe": "observation",
        }.get(name, name.removeprefix("/training_"))
        report_kind = "recycle" if self.wrong_kind and kind == "provision" else kind
        report = {
            "version": "1.0.0", "kind": report_kind, "request_id": payload["request_id"],
            "episode_id": payload["episode_id"], "tick": 1, "ok": True,
            "status": "completed" if report_kind == "recycle" else "ready",
        }
        if kind == "provision" and report_kind == "provision":
            scenario = payload["scenario"]
            report.update({
                "scenario_id": scenario["scenario_id"], "scenario_hash": scenario["scenario_hash"],
                "surface": scenario["environment"]["surface_name"],
                "force": scenario["environment"]["force_name"], "started_tick": 1,
                "unchanged": False,
                "created": {"surface": True, "force": True, "resource_tiles": 1, "fixtures": 2},
            })
        if kind == "observation":
            report.update({
                "scenario_id": "mining-delivery-00000001",
                "surface": "training/mining-delivery-00000001",
                "force": "training-mining-delivery-00000001",
                "started_tick": 1, "elapsed_ticks": 0,
                "objective": {"item": "iron-ore", "target_rate_per_tick": 1 / 60, "sustain_ticks": 60},
                "metrics": {
                    "delivered_items": 0, "sample_ticks": 0, "sample_items": 0,
                    "rate_per_tick": 0, "sustained_ticks": 0, "resource_remaining": 1,
                    "built_entities": {}, "forbidden_entities": 0, "out_of_bounds_entities": 0,
                    "budget_overruns": 0, "fixtures_valid": True, "power_connected": True,
                },
                "failure": {"kind": "none", "reason": ""},
            })
        if kind == "execution":
            package = json.loads("".join(self.uploads[payload["upload_id"]].values()))
            plan = package["build_plan"]
            failed = self.failed_placements
            report.update({
                "ok": failed == 0, "status": "failed" if failed else "ready",
                "scenario_id": "mining-delivery-00000001", "surface": plan["surface"],
                "force": plan["force"], "execution": {
                    "attempted_placements": 2, "succeeded_placements": 2 - failed,
                    "already_present_placements": 0, "failed_placements": failed,
                    "placement_failures": [{}] * failed,
                },
            })
            if failed:
                report["error"] = f"{failed} placement(s) failed"
        directory = self.root / "factorio_training_lab" / "reports"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{report_kind}_{payload['request_id']}.json").write_text(json.dumps(report), encoding="utf-8")
        return ""

    def close(self):
        self.closed = True


def test_bridge_rejects_non_loopback_host(tmp_path):
    with pytest.raises(ValueError, match="loopback"):
        FactorioTrainingBridge(tmp_path, host="192.0.2.1", port=27015, password="x", rcon=ReportRcon(tmp_path))


def test_bridge_binds_provision_and_recycle_reports(tmp_path):
    rcon = ReportRcon(tmp_path)
    bridge = FactorioTrainingBridge(
        tmp_path, host="127.0.0.1", port=27015, password="x", rcon=rcon,
        poll_interval=0.001, command_timeout=1,
    )
    scenario = generate_mining_delivery_scenario(1)
    bridge.handshake()
    report = bridge.provision("episode-1", scenario)
    assert report["scenario_hash"] == scenario["scenario_hash"]
    assert bridge.recycle("episode-1")["status"] == "completed"
    bridge.close()
    assert rcon.closed


def test_bridge_chunks_large_physical_plan_before_execution(tmp_path):
    rcon = ReportRcon(tmp_path)
    bridge = FactorioTrainingBridge(
        tmp_path, host="127.0.0.1", port=27015, password="x", rcon=rcon,
        poll_interval=0.001, command_timeout=1,
    )
    scenario = generate_mining_delivery_scenario(1)
    plan = mining_delivery_candidates(scenario)[0]["plan"]

    result = bridge.execute("episode-1", build_layout_authorization([plan]), plan)

    assert result["succeeded_placements"] == 2
    uploads = [command for command in rcon.commands if command.startswith("/training_upload ")]
    assert len(uploads) > 1
    assert max(map(len, uploads)) < 4_096
    assert not any(command.startswith("/build_layout_plan ") for command in rcon.commands)


def test_bridge_preserves_failed_execution_as_training_evidence(tmp_path):
    rcon = ReportRcon(tmp_path, failed_placements=1)
    bridge = FactorioTrainingBridge(tmp_path, host="127.0.0.1", port=27015, password="x", rcon=rcon)
    plan = mining_delivery_candidates(generate_mining_delivery_scenario(1))[0]["plan"]

    execution = bridge.execute("episode-1", build_layout_authorization([plan]), plan)

    assert execution["failed_placements"] == 1


def test_bridge_rejects_wrong_report_kind_without_a_timeout(tmp_path):
    bridge = FactorioTrainingBridge(
        tmp_path, host="127.0.0.1", port=27015, password="x", rcon=ReportRcon(tmp_path, wrong_kind=True),
        poll_interval=0.001, command_timeout=0.02,
    )

    with pytest.raises(TrainingBridgeError, match="unexpected training report kind"):
        bridge.provision("episode-1", generate_mining_delivery_scenario(1))


def test_bridge_rejects_uploads_beyond_the_lab_chunk_limit(tmp_path):
    bridge = FactorioTrainingBridge(
        tmp_path, host="127.0.0.1", port=27015, password="x", rcon=ReportRcon(tmp_path),
    )

    with pytest.raises(TrainingBridgeError, match="chunk limit"):
        bridge._upload_plan("episode-1", {"data": "x" * (_UPLOAD_CHUNK_BYTES * _MAX_UPLOAD_CHUNKS)})

def test_bridge_maps_observe_to_the_observation_report_kind(tmp_path):
    bridge = FactorioTrainingBridge(
        tmp_path, host="127.0.0.1", port=27015, password="x", rcon=ReportRcon(tmp_path),
        poll_interval=0.001, command_timeout=1,
    )

    report = bridge.observe("episode-1")

    assert report["kind"] == "observation"