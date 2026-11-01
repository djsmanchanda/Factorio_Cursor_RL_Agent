# Path: tests/test_optimizer_build_evidence_bridge.py | Purpose: Check correlated optimizer event reports without a live server.
import json

import pytest

from orchestrator.game_bridge import BridgeError, GameBridge, OPTIMIZER_BUILD_EVIDENCE_SUBDIR


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setattr("orchestrator.game_bridge.RconClient", lambda *_args, **_kwargs: object())
    return GameBridge(tmp_path, poll_interval=0)


def collect(bridge, monkeypatch, mutate=lambda report: None):
    def command(text):
        request = json.loads(text.split(" ", 1)[1])
        report = {**request, "version": "optimizer-build-evidence-v2", "source": "robot_built_entity_tags",
                  "ok": True, "tick": 100, "ownership_claimed": False, "dispatch_authorized": False,
                  "entities": [{"entity": "lab", "position": {"x": 1.5, "y": 2.5},
                                "unit_number": 42, "tick": 90, "current": {"state": "not_found"}}]}
        mutate(report)
        directory = bridge.script_output / OPTIMIZER_BUILD_EVIDENCE_SUBDIR
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"optimizer_build_evidence_{request['request_id']}.json").write_text(json.dumps(report))
        return ""
    monkeypatch.setattr(bridge, "command", command)
    return bridge.optimizer_build_evidence(surface="nauvis", force="player", episode_id="episode/1",
                                           project_id="project", transaction_id="a" * 32,
                                           plan_hash="b" * 64, timeout=.1)


def test_correlated_event_report_has_no_ownership_authority(bridge, monkeypatch):
    report = collect(bridge, monkeypatch)
    assert report["tags"]["optimizer_plan_hash"] == "b" * 64
    assert report["entities"][0]["unit_number"] == 42
    assert report["ownership_claimed"] is False and report["dispatch_authorized"] is False


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(request_id="foreign"),
    lambda r: r.update(force="foreign"),
    lambda r: r["tags"].update(optimizer_transaction="c" * 32),
    lambda r: r["tags"].update(optimizer_plan_hash="c" * 64),
    lambda r: r.update(ownership_claimed=True),
    lambda r: r.update(dispatch_authorized=True),
    lambda r: r.update(version="optimizer-build-evidence-v1"),
    lambda r: r.update(ok=False),
    lambda r: r["entities"][0].pop("current"),
    lambda r: r["entities"][0].update(unit_number=True),
    lambda r: r["entities"][0].update(tick=101),
    lambda r: r["entities"][0]["position"].update(x=float("nan")),
])
def test_mismatched_or_invalid_report_fails_closed(bridge, monkeypatch, mutation):
    with pytest.raises(BridgeError):
        collect(bridge, monkeypatch, mutation)


@pytest.mark.parametrize("field,value", [("transaction_id", "a" * 32 + "\n"),
                                        ("plan_hash", "B" * 64), ("episode_id", "é" * 65)])
def test_invalid_request_is_rejected_before_command(bridge, monkeypatch, field, value):
    monkeypatch.setattr(bridge, "command", lambda _text: pytest.fail("unexpected command"))
    kwargs = dict(surface="nauvis", force="player", episode_id="episode", project_id="project",
                  transaction_id="a" * 32, plan_hash="b" * 64)
    kwargs[field] = value
    with pytest.raises(ValueError):
        bridge.optimizer_build_evidence(**kwargs)


def test_v3_bridge_correlates_unitless_events_without_confusing_v2(bridge, monkeypatch):
    def command(text):
        assert text.startswith("/optimizer_build_evidence_v3 ")
        request = json.loads(text.split(" ", 1)[1])
        report = {**request, "version": "optimizer-build-evidence-v3", "source": "robot_built_entity_tags",
                  "ok": True, "tick": 100, "ownership_claimed": False, "dispatch_authorized": False,
                  "entities": [{"entity": "transport-belt", "position": {"x": 1.5, "y": 2.5},
                                "tick": 90, "current": {"state": "identity_unavailable"}}]}
        directory = bridge.script_output / OPTIMIZER_BUILD_EVIDENCE_SUBDIR
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"optimizer_build_evidence_{request['request_id']}.json").write_text(json.dumps(report))
        return ""
    monkeypatch.setattr(bridge, "command", command)
    report = bridge.optimizer_build_evidence(surface="nauvis", force="player", episode_id="episode",
        project_id="project", transaction_id="a" * 32, plan_hash="b" * 64,
        timeout=.1, include_unidentified=True)
    assert report["entities"][0]["current"]["state"] == "identity_unavailable"
    assert "unit_number" not in report["entities"][0]
