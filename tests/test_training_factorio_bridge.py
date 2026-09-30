# Path: tests/test_training_factorio_bridge.py
# Purpose: Pin loopback isolation and request-bound training report collection.

from __future__ import annotations

import json

import pytest

import training.factorio_bridge as factorio_bridge

from planners.sandbox_infrastructure import build_layout_authorization
from training.candidates import mining_delivery_candidates
from training.factorio_bridge import (
    _MAX_UPLOAD_CHUNKS, _REPORT_RETENTION, _UPLOAD_CHUNK_BYTES,
    FactorioTrainingBridge, TrainingBridgeError,
)
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
            "version": "1.1.0", "kind": report_kind, "request_id": payload["request_id"],
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
                    "electric_pole_count": 0, "occupied_footprint_tiles": 0,
                    "placed_mining_drills": 0, "productive_mining_drills": 0,
                    "productive_mining_drill_ratio": 0,
                    "mining_drill_capacity_ticks": 0, "mining_drill_working_ticks": 0,
                    "mining_drill_blocked_ticks": 0, "mining_drill_idle_ticks": 0,
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


def test_bridge_bounds_shared_report_history(tmp_path):
    report_dir = tmp_path / "factorio_training_lab" / "reports"
    report_dir.mkdir(parents=True)
    for index in range(20):
        (report_dir / f"observation_{index:04d}.json").write_text("{}", encoding="utf-8")
    bridge = FactorioTrainingBridge(
        tmp_path, host="127.0.0.1", port=27015, password="x",
        rcon=ReportRcon(tmp_path),
    )
    bridge._retain_reports = 5

    assert bridge._prune_reports() == 15
    assert len(list(report_dir.glob("*.json"))) == 5
    assert _REPORT_RETENTION >= 100


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


def test_failed_recycle_reports_error_without_waiting_for_success(tmp_path, monkeypatch):
    class FailedRecycle:
        def command(self, command):
            payload = json.loads(command.split(' ',1)[1])
            directory = tmp_path / 'factorio_training_lab/reports'
            directory.mkdir(parents=True)
            report = {'version':'1.1.0','kind':'recycle','request_id':payload['request_id'],
                      'episode_id':payload['episode_id'],'tick':1,'ok':False,'status':'failed',
                      'error':'episode surface is missing'}
            (directory/'failed.json').write_text(json.dumps(report))
            return ''
    def unexpected_wait(_):
        raise AssertionError('a matching terminal failure must not wait for completed status')
    monkeypatch.setattr('training.factorio_bridge.time.sleep',unexpected_wait)
    bridge = FactorioTrainingBridge(tmp_path,host='127.0.0.1',port=28005,password='test',rcon=FailedRecycle())
    with pytest.raises(TrainingBridgeError,match='episode surface is missing'):
        bridge.recycle('failed-episode')


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

class TickRcon:
    """Fake RCON answering the /sc scripts used by stepped-mode tick advance.

    `catch_up_after` simulates the CPU needing a couple of polls before the
    requested ticks have actually finished running.
    """

    def __init__(self, *, paused: bool = True, start_tick: int = 100, catch_up_after: int = 0):
        self.tick = start_tick
        self.paused = paused
        self.catch_up_after = catch_up_after
        self._pending = 0
        self._reads_since_request = 0
        self.commands: list[str] = []

    def command(self, text: str) -> str:
        self.commands.append(text)
        if text == "/sc rcon.print(game.tick)":
            if self._pending:
                self._reads_since_request += 1
                if self._reads_since_request > self.catch_up_after:
                    self.tick += self._pending
                    self._pending = 0
            return str(self.tick)
        if "game.ticks_to_run" in text:
            if not self.paused:
                return "Error: stepped bridge requires game.tick_paused"
            marker = "game.tick + "
            start = text.index(marker) + len(marker)
            end = text.index(" ", start)
            requested = int(text[start:end])
            self._pending = requested
            self._reads_since_request = 0
            return str(self.tick + requested)
        return ""

    def close(self) -> None:
        pass


def test_advance_ticks_returns_immediately_for_zero_or_negative(tmp_path):
    rcon = TickRcon()
    bridge = FactorioTrainingBridge(tmp_path, host="127.0.0.1", port=27015, password="x", rcon=rcon)

    assert bridge.advance_ticks("episode-1", 0) == 100
    assert not any("ticks_to_run" in command for command in rcon.commands)


def test_advance_ticks_polls_until_the_target_tick_is_reached(tmp_path):
    rcon = TickRcon(start_tick=200, catch_up_after=2)
    bridge = FactorioTrainingBridge(
        tmp_path, host="127.0.0.1", port=27015, password="x", rcon=rcon, poll_interval=0.001,
    )

    result = bridge.advance_ticks("episode-1", 60)

    assert result == 260
    tick_reads = [command for command in rcon.commands if command == "/sc rcon.print(game.tick)"]
    assert len(tick_reads) >= 2  # actually polled, not just trusted the first read


def test_advance_ticks_fails_fast_if_the_instance_is_not_paused(tmp_path):
    rcon = TickRcon(paused=False)
    bridge = FactorioTrainingBridge(tmp_path, host="127.0.0.1", port=27015, password="x", rcon=rcon)

    with pytest.raises(TrainingBridgeError, match="tick_paused"):
        bridge.advance_ticks("episode-1", 60)


def test_bridge_tick_reads_without_advancing(tmp_path):
    rcon = TickRcon(start_tick=42)
    bridge = FactorioTrainingBridge(tmp_path, host="127.0.0.1", port=27015, password="x", rcon=rcon)

    assert bridge.tick() == 42
    assert bridge.tick() == 42


def test_bridge_throttles_pruning_across_shared_slots(tmp_path, monkeypatch):
    monkeypatch.setattr(factorio_bridge, "_NEXT_REPORT_PRUNE", {})
    first = FactorioTrainingBridge(
        tmp_path, host="127.0.0.1", port=27015, password="x", rcon=ReportRcon(tmp_path),
    )
    second = FactorioTrainingBridge(
        tmp_path, host="127.0.0.1", port=27015, password="x", rcon=ReportRcon(tmp_path),
    )
    calls = []
    monkeypatch.setattr(first, "_prune_reports", lambda keep=None: calls.append("first") or 0)
    monkeypatch.setattr(second, "_prune_reports", lambda keep=None: calls.append("second") or 0)
    monkeypatch.setattr(factorio_bridge.time, "monotonic", lambda: 100.0)

    assert first._maybe_prune_reports() == 0
    assert second._maybe_prune_reports() == 0
    assert calls == ["first"]

    monkeypatch.setattr(factorio_bridge.time, "monotonic", lambda: 106.0)
    kept = tmp_path / "factorio_training_lab" / "reports" / "newest.json"
    assert second._maybe_prune_reports(kept) == 0
    assert calls == ["first", "second"]
