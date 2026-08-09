# Path: tests/test_training_factorio_bridge.py
# Purpose: Pin loopback isolation and request-bound report collection.

import json

import pytest

from training.factorio_bridge import FactorioTrainingBridge
from training.scenarios.mining_delivery import generate_mining_delivery_scenario


class ReportRcon:
    def __init__(self, root):
        self.root = root
        self.closed = False

    def command(self, command):
        if command.startswith("/help"):
            return "available"
        name, raw = command.split(" ", 1)
        payload = json.loads(raw)
        kind = name.removeprefix("/training_")
        report = {
            "version": "1.0.0", "kind": kind, "request_id": payload["request_id"],
            "episode_id": payload["episode_id"], "tick": 1, "ok": True,
            "status": "completed" if kind == "recycle" else "ready",
        }
        if kind == "provision":
            scenario = payload["scenario"]
            report.update({
                "scenario_id": scenario["scenario_id"], "scenario_hash": scenario["scenario_hash"],
                "surface": scenario["environment"]["surface_name"],
                "force": scenario["environment"]["force_name"], "started_tick": 1,
                "unchanged": False,
                "created": {"surface": True, "force": True, "resource_tiles": 1, "fixtures": 2},
            })
        directory = self.root / "factorio_training_lab" / "reports"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{kind}_0000000001_000001.json").write_text(json.dumps(report), encoding="utf-8")
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
