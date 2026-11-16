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
        self._tick = 0

    def tick(self):
        self._tick += 1
        return self._tick

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


def test_plan_echoes_the_requested_step_budget(tmp_path):
    with patch.object(replay, "preflight", return_value={"kind": "x", "capture_tick": 1}), \
         patch.object(replay, "port_is_free", return_value=True):
        plan = replay.plan_replay(tmp_path / "bank", "gen", tmp_path / "root",
                                  game_port=64000, rcon_port=65000, deadline=2 ** 40,
                                  max_steps=8)
        assert plan["max_steps"] == 8
        assert plan["max_game_ticks"] == 108000
        with pytest.raises(replay.ReplayError, match="at least one"):
            replay.plan_replay(tmp_path / "bank", "gen", tmp_path / "root",
                               game_port=64000, rcon_port=65000, deadline=2 ** 40,
                               max_steps=0)


@pytest.mark.parametrize('budget', [0, -1, True, 1.5])
def test_invalid_game_tick_budget_is_rejected_before_preflight(tmp_path, budget):
    with patch.object(replay, 'preflight') as preflight:
        with pytest.raises(replay.ReplayError, match='positive integer game-tick budget'):
            replay.plan_replay(tmp_path / 'bank', 'gen', tmp_path / 'world',
                               game_port=31517, rcon_port=31518,
                               deadline=2 ** 40, max_game_ticks=budget)
        preflight.assert_not_called()


@pytest.mark.parametrize('option,budget', [([], 108000), (['--max-game-ticks', '324000'], 324000)])
def test_cli_preserves_game_tick_budget_through_plan_provenance_and_driver(tmp_path, monkeypatch, option, budget):
    from contextlib import contextmanager
    import subprocess

    world = tmp_path / 'world'
    captured = {}

    @contextmanager
    def owned_world(bundle, root, **kwargs):
        captured['provenance'] = kwargs['provenance']
        root.mkdir()
        yield {'root': str(root), 'status': 'prepared', 'tick': 100}

    def run_live(*args, **kwargs):
        captured['driver_budget'] = kwargs['max_game_ticks']
        return {'pm_evidence_complete': False}

    monkeypatch.setattr(replay, 'preflight', lambda *args: {'capture_tick': 100})
    monkeypatch.setattr(replay, 'check_mods', lambda *args: {'mods_verified': True})
    monkeypatch.setattr(replay, 'port_is_free', lambda *args: True)
    monkeypatch.setattr(replay, 'check_replay_capacity', lambda **kwargs: {})
    monkeypatch.setattr(replay, 'engine_receipt', lambda *args: {})
    monkeypatch.setattr(subprocess, 'check_output', lambda *args, **kwargs: 'a' * 40)
    monkeypatch.setattr(replay, 'replay_world', owned_world)
    monkeypatch.setattr(replay, 'reload_receipt', lambda *args: {'reload_verified': True, 'observed_tick': 100})
    monkeypatch.setattr(replay, 'run_live_replay', run_live)
    # The driver is stubbed; the native sampler must never start in this CLI contract test.
    monkeypatch.setattr(replay.StrictSampler, 'close', lambda self: None)
    argv = ['replay', '--bank', str(tmp_path / 'bank'), '--generation', 'g',
            '--root', str(world), '--mods-source', str(tmp_path / 'mods'),
            '--game-port', '31517', '--rcon-port', '31518', '--deadline', str(2 ** 40)] + option
    assert replay.main(argv) == 0
    assert not world.exists()  # planning cannot materialize a world
    assert replay.main(argv + ['--execute']) == 4
    assert captured['driver_budget'] == captured['provenance']['max_game_ticks'] == budget


def test_plan_refuses_a_port_already_in_use(tmp_path):
    with patch.object(replay, "preflight", return_value={"kind": "x", "capture_tick": 1}), \
         patch.object(replay, "port_is_free", side_effect=lambda port, kind: kind != "game"):
        with pytest.raises(replay.ReplayError, match="game port 64000 is already in use"):
            replay.plan_replay(tmp_path / "bank", "gen", tmp_path / "root",
                               game_port=64000, rcon_port=65000, deadline=2 ** 40)
    with patch.object(replay, "preflight", return_value={"kind": "x", "capture_tick": 1}), \
         patch.object(replay, "port_is_free", side_effect=lambda port, kind: kind != "rcon"):
        with pytest.raises(replay.ReplayError, match="rcon port 65000 is already in use"):
            replay.plan_replay(tmp_path / "bank", "gen", tmp_path / "root",
                               game_port=64000, rcon_port=65000, deadline=2 ** 40)


def test_port_is_free_sees_a_real_listener():
    import socket
    for kind, sock_type in (("game", socket.SOCK_DGRAM), ("rcon", socket.SOCK_STREAM)):
        held = socket.socket(socket.AF_INET, sock_type)
        try:
            held.bind(("0.0.0.0", 0))
            if sock_type == socket.SOCK_STREAM:
                held.listen(1)
            assert replay.port_is_free(held.getsockname()[1], kind) is False
        finally:
            held.close()


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
                                                      "samples": [{"tick": 10, "crafts": 2},
                                                                  {"tick": 3700, "crafts": 3}]})
    assert summary["pm_placed"] is True
    assert summary["pm_consumption_verified"] is True
    assert summary["pm_evidence_complete"] is True
    assert summary["acceptance"]["rises"] == 2
    assert summary["steps"][-1]["pm_consumption"][0]["crafts"] == 2


def test_one_verified_probe_sample_does_not_complete_the_drive(tmp_path):
    session = FakeSession([{"ok": True, "code": None}], cells_at=3)
    summary = replay.drive(session, tmp_path / "evidence.jsonl", max_steps=5,
                           stock_reader=lambda s: {},
                           consumption_probe=lambda: {"pm_consumption_verified": True,
                                                      "samples": [{"tick": 10, "crafts": 2}]})
    assert summary["pm_placed"] is True and summary["pm_evidence_complete"] is False


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
    assert result["observations"][0]["window_crafts"] == 3
    assert result["samples"] == []  # a window total is not cumulative evidence


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


def test_steps_retain_typed_details_for_diagnosis(tmp_path):
    rich = short()
    rich["classification"] = "bug"
    rich["state"] = "supply_wait"
    rich["details"]["target_item"] = "advanced-circuit"
    session = FakeSession([rich], cells_at=99)
    summary = replay.drive(session, tmp_path / "e.jsonl", max_steps=1,
                           stock_reader=lambda s: {}, consumption_probe=None)
    step = summary["steps"][0]
    assert step["classification"] == "bug"
    assert step["state"] == "supply_wait"
    assert step["details"]["target_item"] == "advanced-circuit"


def test_the_journal_streams_before_a_later_failure(tmp_path):
    class Boom(FakeSession):
        def run(self, operation, **params):
            if self.calls:
                raise RuntimeError("runner died")
            return super().run(operation, **params)

    session = Boom([{"ok": True, "code": None}], cells_at=99)
    evidence = tmp_path / "e.jsonl"
    with pytest.raises(RuntimeError):
        replay.drive(session, evidence, max_steps=3, stock_reader=lambda s: {}, consumption_probe=None)
    lines = [json.loads(l) for l in evidence.read_text().splitlines() if l.strip()]
    assert [l["event"] for l in lines] == ["pm_replay_step"]
    assert lines[0]["step"] == 1


def test_verify_falls_back_to_the_sidecar_report(tmp_path):
    world = tmp_path / "world"; world.mkdir()
    (world / "manifest.json").write_text(json.dumps({"production_authority": False}))
    (world / "evidence").mkdir()
    _sidecar_report(world / "evidence/strict.json", [
        {"tick": 9, "predicate_evidence": {"C6": {"predicate_version": "c6-v3", "status": "passed",
                                                  "evidence": {"machine_consumption": {"productivity-module": 2}}}},
         "statuses": {"C6": True}}])
    result = replay.consumption(world)
    assert result["source"] == "strict_sidecar"
    assert result["pm_consumption_verified"] is True
    assert result["observations"][0]["window_crafts"] == 2


def test_report_combines_provenance_placement_and_consumption(tmp_path):
    world = tmp_path / "world"; world.mkdir()
    (world / "manifest.json").write_text(json.dumps({"production_authority": False}))
    (world / "provenance.json").write_text(json.dumps({"kind": "provenance"}))
    (world / "evidence").mkdir()
    (world / "evidence/pm-placement.json").write_text(json.dumps({"placement_receipt": True, "machines": 1}))
    _sidecar_report(world / "evidence/strict.json", [
        {"tick": tick, "predicate_evidence": {"C6": {"predicate_version": "c6-v3", "status": "passed",
                                                     "evidence": {"machine_consumption": {"productivity-module": 2}}}},
         "statuses": {"C6": True},
         "machine_crafts": {"generation": "g", "cumulative": {"productivity-module": crafts}}}
        for tick, crafts in ((9, 0), (3609, 1), (7209, 2))])
    (world / "provenance-reload.json").write_text(json.dumps(
        {"kind": "laya-pm-reload-v1", "expected_tick": 514525,
         "observed_tick": 514525, "reload_verified": True}))
    report = replay.replay_report(world)
    assert report["pm_evidence_complete"] is True
    assert report["placement"]["machines"] == 1
    assert report["consumption"]["pm_consumption_verified"] is True
    assert report["reload_verified"] is True
    assert report["reload"]["observed_tick"] == 514525


def test_host_idle_gate_names_active_cohorts_only():
    listing = """laya-cohort-dashboard.service loaded active running
laya-host-scheduler.service loaded active running
laya-trackb-b-amerge-2x-20261112-p2.service loaded active running
laya-pm-replay-20261113.service loaded active running
"""
    assert replay.busy_cohort_units(listing) == ["laya-trackb-b-amerge-2x-20261112-p2.service"]
    with pytest.raises(replay.ReplayError, match="not idle"):
        replay.check_host_idle(lambda args: listing)
    idle = """laya-cohort-dashboard.service loaded active running
laya-host-scheduler.service loaded active running
laya-pm-replay-20261113.service loaded active running
"""
    assert replay.check_host_idle(lambda args: idle) == []


def test_the_session_trace_is_journaled_even_when_a_step_raises(tmp_path):
    class Traced(FakeSession):
        def __init__(self):
            super().__init__([{"ok": True, "code": None}], cells_at=99)
            self.emitted = [["  CELL DELIVERY: moved 8 plastic-bar"], ["  gate refresh"]]
            self.index = 0

        def run(self, operation, **params):
            outcome = super().run(operation, **params)
            if len(self.calls) == 2:
                raise RuntimeError("runner died")
            return outcome

    session = Traced()

    def reader():
        lines = session.emitted[session.index] if session.index < len(session.emitted) else []
        session.index += 1
        return list(lines)

    evidence = tmp_path / "e.jsonl"
    with pytest.raises(RuntimeError):
        replay.drive(session, evidence, max_steps=3, stock_reader=lambda s: {},
                     consumption_probe=None, message_reader=reader)
    rows = [json.loads(line) for line in evidence.read_text().splitlines()]
    traced = [(row["step"], row["line"]) for row in rows if row["event"] == "pm_replay_message"]
    assert traced == [(1, "  CELL DELIVERY: moved 8 plastic-bar"), (2, "  gate refresh")]
    step_row = next(row for row in rows if row["event"] == "pm_replay_step")
    assert step_row["step"] == 1 and step_row["messages"] == 1


def test_capacity_gate_allows_a_busy_host_when_ports_and_memory_are_free():
    capacity = replay.check_replay_capacity(
        game_port=64800, rcon_port=64900, min_available_gb=8.0,
        memory_reader=lambda: 14.7, port_probe=lambda port, kind: True)
    assert capacity["available_gb"] == 14.7


def test_capacity_gate_blocks_low_memory():
    with pytest.raises(replay.ReplayError, match="memory available"):
        replay.check_replay_capacity(game_port=64800, rcon_port=64900, min_available_gb=8.0,
                                     memory_reader=lambda: 2.5,
                                     port_probe=lambda port, kind: True)


def test_capacity_gate_blocks_an_occupied_port():
    with pytest.raises(replay.ReplayError, match="game port 64800 is already in use"):
        replay.check_replay_capacity(
            game_port=64800, rcon_port=64900, min_available_gb=8.0,
            memory_reader=lambda: 14.7,
            port_probe=lambda port, kind: kind != "game")
