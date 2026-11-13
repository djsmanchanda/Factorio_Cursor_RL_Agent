# Path: tests/test_laya_pm_replay.py | Purpose: Protect the C4-to-PM replay driver's gates and evidence.

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import tools.laya_pm_replay as replay


class FakeSession:
    def __init__(self, outcomes, cells_at):
        self.outcomes = list(outcomes)
        self.cells_at = cells_at
        self.calls = []

    def run(self, operation, **params):
        self.calls.append((operation, params))
        return self.outcomes[min(len(self.calls) - 1, len(self.outcomes) - 1)]

    def economics(self):
        index = len(self.calls)
        cells = [{"recipe": "productivity-module", "machines": 1}] if index >= self.cells_at else []
        return {"cells": cells}


def short(code="material_shortage", stage="conversion_productivity-module"):
    return {"ok": False, "code": code,
            "details": {"stage": stage, "required": {"assembling-machine-2": 7, "steel-chest": 12},
                        "available": {"assembling-machine-2": 6, "steel-chest": 11}}}


def test_plan_rejects_a_port_range_above_the_tcp_limit(tmp_path):
    with patch.object(replay, "preflight", return_value={"kind": "x", "capture_tick": 1}):
        with pytest.raises(replay.ReplayError, match="outside 1..65535"):
            replay.plan_replay(tmp_path / "bank", "gen", tmp_path / "root",
                               game_port=64000, rcon_port=65600, deadline=2 ** 40)


def test_plan_rejects_an_existing_root(tmp_path):
    root = tmp_path / "root"; root.mkdir()
    with patch.object(replay, "preflight", return_value={"kind": "x", "capture_tick": 1}):
        with pytest.raises(replay.ReplayError, match="fresh"):
            replay.plan_replay(tmp_path / "bank", "gen", root,
                               game_port=64000, rcon_port=65000, deadline=2 ** 40)


def test_drive_journals_the_bill_and_stock_delta(tmp_path):
    session = FakeSession([short()], cells_at=99)
    summary = replay.drive(session, tmp_path / "evidence.jsonl", max_steps=3,
                           stock_reader=lambda s: {"steel-chest": 11, "assembling-machine-2": 6},
                           consumption_probe=None)
    assert summary["pm_placed"] is False and summary["pm_evidence_complete"] is False
    first = summary["steps"][0]
    assert first["bill"]["short"] == {"assembling-machine-2": 1, "steel-chest": 1}
    assert first["outcome"] == "material_shortage"
    assert len(summary["steps"]) == 3
    assert (tmp_path / "evidence.jsonl").read_text().count("pm_replay_step") == 3


def test_drive_succeeds_only_with_placement_and_pm_consumption(tmp_path):
    session = FakeSession([{"ok": True, "code": None}], cells_at=3)
    summary = replay.drive(session, tmp_path / "evidence.jsonl", max_steps=5,
                           stock_reader=lambda s: {},
                           consumption_probe=lambda: {"pm_consumption_verified": True,
                                                      "samples": [{"tick": 10, "crafts": 2}]})
    assert summary["pm_placed"] is True
    assert summary["pm_consumption_verified"] is True
    assert summary["pm_evidence_complete"] is True
    assert summary["steps"][-1]["pm_consumption"][0]["crafts"] == 2


def test_a_generic_c6_pass_is_not_pm_consumption(tmp_path):
    session = FakeSession([{"ok": True, "code": None}], cells_at=1)
    summary = replay.drive(session, tmp_path / "evidence.jsonl", max_steps=4,
                           stock_reader=lambda s: {},
                           consumption_probe=lambda: {"pm_consumption_verified": False, "samples": []})
    assert summary["pm_placed"] is True
    assert summary["pm_consumption_verified"] is False
    assert summary["pm_evidence_complete"] is False


def _bundle_with_mods(tmp_path, files):
    import hashlib
    bundle = tmp_path / "bundle"; bundle.mkdir()
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    (bundle / "checkpoint.json").write_text(json.dumps({"provenance": {"mod_hashes": hashes}}))
    mods = tmp_path / "mods"
    for name, data in files.items():
        target = mods / name; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return bundle, mods


def test_mods_must_match_the_bundle_hashes(tmp_path):
    bundle, mods = _bundle_with_mods(tmp_path, {"a/control.lua": b"x", "a/data.lua": b"y"})
    assert replay.check_mods(bundle, mods)["mods_verified"] is True
    (mods / "a/data.lua").write_bytes(b"changed")
    with pytest.raises(replay.ReplayError, match="changed"):
        replay.check_mods(bundle, mods)


def test_a_missing_mod_file_blocks_the_replay(tmp_path):
    bundle, mods = _bundle_with_mods(tmp_path, {"a/control.lua": b"x"})
    (mods / "a/control.lua").unlink()
    with pytest.raises(replay.ReplayError, match="missing"):
        replay.check_mods(bundle, mods)


def test_provenance_receipt_is_immutable_and_plans_one_attempt(tmp_path):
    plan = {"generation": "g", "capture_tick": 514525, "world_sha256": "a" * 64,
            "acceptance_eligible": False, "game_port": 64800, "rcon_port": 63800,
            "deadline": 2 ** 40}
    receipt = replay.provenance_receipt(plan, tmp_path / "bundle", tmp_path / "mods",
                                        {"binary": "f", "sha256": "b" * 64, "version": "v"},
                                        "c" * 40, planned=1)
    assert receipt["kind"] == "laya-pm-replay-provenance-v1"
    assert receipt["evidence_scope"] == "diagnostic_replay_only"
    assert receipt["planned_attempts"] == 1
    assert receipt["capture_tick"] == 514525
    assert receipt["mods_verified"] is True


def test_engine_receipt_hashes_and_reports_the_binary(tmp_path):
    binary = tmp_path / "factorio"
    binary.write_text("#!/bin/sh\necho 'Factorio 2.1.24 (test)'\n")
    binary.chmod(0o755)
    receipt = replay.engine_receipt(binary)
    assert receipt["version"].startswith("Factorio 2.1.24")
    assert len(receipt["sha256"]) == 64


def test_drive_stops_at_the_deadline_without_running_a_step(tmp_path):
    calls = {"n": 0}

    class Counter(FakeSession):
        def run(self, operation, **params):
            calls["n"] += 1
            return super().run(operation, **params)

    session = Counter([{"ok": True, "code": None}], cells_at=99)
    summary = replay.drive(session, tmp_path / "e.jsonl", max_steps=10,
                           stock_reader=lambda s: {}, consumption_probe=None,
                           deadline=100.0, clock=lambda: 101.0)
    assert summary["deadline_reached"] is True
    assert summary["steps"] == []
    assert calls["n"] == 0


def test_materialize_writes_provenance_before_booting(tmp_path, monkeypatch):
    import subprocess
    bundle = tmp_path / "bundle"; bundle.mkdir()
    (bundle / "world.zip").write_bytes(b"world")
    root = tmp_path / "root"
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["provenance_before_boot"] = (root.with_name(root.name + ".provenance.json")).is_file()
        seen["env"] = kwargs.get("env") or {}
        class Done:
            stdout = json.dumps({"root": str(root), "status": "prepared", "tick": 514525}) + "\n"
        return Done()

    monkeypatch.setattr(subprocess, "run", fake_run)
    receipt = replay.materialize(bundle, root, mods_source=tmp_path / "mods",
                                 game_port=64800, rcon_port=63800,
                                 provenance={"kind": "provenance"})
    assert seen["provenance_before_boot"] is True
    assert "--game-port" in seen["command"] and "64800" in seen["command"]
    assert receipt["tick"] == 514525
    assert "LAYA_CHECKPOINT_RUNTIME_ROOT" not in seen["env"]  # only set when runtime_root is passed
    assert json.loads((root / "provenance.json").read_text())["kind"] == "provenance"


def _sidecar_report(path, rows):
    path.write_text(json.dumps({"strict_current": rows}))


def test_sidecar_pm_consumption_requires_c6_v3_and_positive_crafts(tmp_path):
    report = tmp_path / "strict.json"
    _sidecar_report(report, [
        {"tick": 1, "predicate_evidence": {"C6": {"predicate_version": "c6-v3", "status": "passed",
                                                  "evidence": {"machine_consumption": {"productivity-module": 3}}}},
         "statuses": {"C6": True}},
        {"tick": 2, "predicate_evidence": {"C6": {"predicate_version": "c6-v3", "status": "failed",
                                                  "evidence": {"machine_consumption": {}}}},
         "statuses": {"C6": False}},
    ])
    result = replay.sidecar_consumption(report)
    assert result["pm_consumption_verified"] is True
    assert result["samples"][0]["crafts"] == 3


def test_a_bulk_inserter_c6_or_c6_v2_is_not_pm_consumption(tmp_path):
    report = tmp_path / "strict.json"
    _sidecar_report(report, [
        {"tick": 1, "predicate_evidence": {"C6": {"predicate_version": "c6-v2", "status": "passed",
                                                  "evidence": {"consumer_deltas": {"bulk-inserter": 2}}}},
         "statuses": {"C6": True}},
        {"tick": 2, "predicate_evidence": {"C6": {"predicate_version": "c6-v3", "status": "passed",
                                                  "evidence": {"machine_consumption": {"bulk-inserter": 2}}}},
         "statuses": {"C6": True}},
    ])
    assert replay.sidecar_consumption(report)["pm_consumption_verified"] is False


def test_placement_writes_a_receipt_once(tmp_path):
    session = FakeSession([{"ok": True, "code": None}], cells_at=2)
    summary = replay.drive(session, tmp_path / "evidence" / "e.jsonl", max_steps=4,
                           stock_reader=lambda s: {}, consumption_probe=lambda: {"pm_consumption_verified": False})
    receipt_path = tmp_path / "evidence" / "pm-placement.json"
    assert summary["pm_placed"] is True
    assert receipt_path.is_file()
    receipt = json.loads(receipt_path.read_text())
    assert receipt["placement_receipt"] is True
    assert receipt["machines"] == 1
    assert receipt["step"] == 2


def test_sample_consumption_waits_for_a_report_then_terminates(tmp_path, monkeypatch):
    import subprocess
    report = tmp_path / "strict.json"

    class FakeProc:
        def __init__(self, *a, **k):
            _sidecar_report(report, [{"tick": 1, "predicate_evidence": {"C6": {
                "predicate_version": "c6-v3", "status": "passed",
                "evidence": {"machine_consumption": {"productivity-module": 4}}}},
                "statuses": {"C6": True}}])
            self.terminated = False
        def poll(self):
            return None
        def terminate(self):
            self.terminated = True
        def wait(self, timeout=None):
            return 0
        def kill(self):
            pass

    seen = {}
    def fake_popen(*a, **k):
        seen["proc"] = FakeProc()
        return seen["proc"]
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    result = replay.sample_consumption(tmp_path, report, timeout=5, poll=0.01)
    assert result["pm_consumption_verified"] is True
    assert seen["proc"].terminated is True
