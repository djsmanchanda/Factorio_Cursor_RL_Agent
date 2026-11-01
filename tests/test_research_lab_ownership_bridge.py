# Path: tests/test_research_lab_ownership_bridge.py | Purpose: Verify correlated, non-authorizing robot-build report collection.
import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from orchestrator.game_bridge import BridgeError, GameBridge, RESEARCH_LAB_OWNERSHIP_SUBDIR

TRANSACTION = "a" * 32


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setattr("orchestrator.game_bridge.RconClient", lambda *_args, **_kwargs: object())
    return GameBridge(tmp_path, poll_interval=0)


def _report(request):
    return {**request, "version": "research-lab-ownership-v1", "source": "robot_built_entity_tags",
            "ok": True, "tick": 100, "entities": [], "ownership_claimed": False,
            "dispatch_authorized": False}


def _collect(bridge, monkeypatch, mutate=lambda report: None):
    def command(text):
        request = json.loads(text.split(" ", 1)[1])
        report = _report(request)
        mutate(report)
        directory = bridge.script_output / RESEARCH_LAB_OWNERSHIP_SUBDIR
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"research_lab_ownership_{request['request_id']}.json").write_text(json.dumps(report))
        return ""
    monkeypatch.setattr(bridge, "command", command)
    return bridge.research_lab_ownership(surface="nauvis", force="player", episode_id="episode/1",
                                         packet_id="packet-1", transaction_id=TRANSACTION, timeout=.1)


def test_correlated_report_is_returned_as_detached_data(bridge, monkeypatch):
    report = _collect(bridge, monkeypatch)
    schema = Path(__file__).resolve().parents[1] / "schemas/research_lab_ownership.schema.json"
    Draft7Validator(json.loads(schema.read_text())).validate(report)
    assert report["transaction_id"] == TRANSACTION
    assert report["ownership_claimed"] is False
    assert report["dispatch_authorized"] is False


@pytest.mark.parametrize("field,value", [
    ("request_id", "wrong"), ("transaction_id", "b" * 32), ("episode_id", "foreign"),
    ("force", "foreign"), ("ok", False), ("tick", True),
    ("ownership_claimed", True), ("dispatch_authorized", True), ("entities", {}),
])
def test_foreign_or_malformed_reports_fail_closed(bridge, monkeypatch, field, value):
    with pytest.raises(BridgeError):
        _collect(bridge, monkeypatch, lambda report: report.__setitem__(field, value))


def test_symlinked_response_is_rejected(bridge, monkeypatch, tmp_path):
    def collect(command, subdir, timeout, *, expected_name):
        directory = tmp_path / subdir
        directory.mkdir(parents=True)
        target = tmp_path / "target.json"
        target.write_text("{}")
        path = directory / expected_name
        path.symlink_to(target)
        return path
    monkeypatch.setattr(bridge, "_run_and_collect", collect)
    with pytest.raises(BridgeError):
        bridge.research_lab_ownership(surface="nauvis", force="player", episode_id="episode",
                                      packet_id="packet", transaction_id=TRANSACTION)
