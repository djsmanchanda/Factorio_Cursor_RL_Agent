# Path: tests/test_laya_strict_sidecar.py | Purpose: Protect the runner-independent strict sidecar: baseline, latch, stop rules and the disposable-only guard.
from __future__ import annotations

import json

import pytest

from orchestrator.checkpoint_milestones import FactorySnapshot
from tools.laya_strict_sidecar import disposable_endpoint, run

pytestmark = pytest.mark.fast
IDENTITY = {"revision": "test", "predicate_sha256": "0"}


def _snapshot(tick, plastic, delivered):
    return FactorySnapshot.from_mapping({"tick": tick, "base_valid": True,
                                         "production": {"plastic-bar": {"produced": plastic}},
                                         "providers": {"plastic-bar": {"available": delivered}}})


def test_first_sample_is_the_baseline_and_c4_latches_at_its_first_pass(tmp_path):
    samples = iter([_snapshot(100, 0, 0), _snapshot(200, 5, 3), _snapshot(300, 9, 6)])
    def observe():
        try:
            return next(samples)
        except StopIteration:
            raise ConnectionRefusedError("server stopped") from None
    out = tmp_path / "strict.json"
    summary = run(observe, out, interval=1, connect_timeout=5, max_failures=2, stopped=lambda: False,
                  sleep=lambda _: None, identity=IDENTITY)
    assert summary["baseline_tick"] == 100 and summary["strict_reached"]["C4"] == 200
    assert summary["stop"] == "world_unreachable: ConnectionRefusedError"
    assert json.loads(out.read_text())["strict_current"][-1]["statuses"]["C4"] is True
    assert summary["strict_current"][0]["predicate_evidence"]["C9"]["predicate_version"] == "c9-v1"


def test_a_world_never_reached_stops_after_the_connect_timeout(tmp_path):
    def observe():
        raise OSError("not up")
    summary = run(observe, tmp_path / "s.json", interval=10, connect_timeout=30, max_failures=2,
                  stopped=lambda: False, sleep=lambda _: None, identity=IDENTITY)
    assert summary["stop"] == "never_observed: OSError" and summary["strict_current"] == []


def test_only_explicitly_disposable_worlds_are_measured(tmp_path):
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / "rcon-password").write_text("pw\n")
    (tmp_path / "manifest.json").write_text(json.dumps({"rcon_port": 27001, "production_authority": False}))
    assert disposable_endpoint(tmp_path) == (27001, "pw")
    (tmp_path / "manifest.json").write_text(json.dumps({"rcon_port": 27001}))
    with pytest.raises(ValueError, match="production_authority"):
        disposable_endpoint(tmp_path)
