# Path: tests/test_runtime_catalog_bridge.py | Purpose: Verify correlated runtime catalog collection without RCON.
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from core.runtime_placement_mapping import mapping_from_runtime_catalog
from orchestrator.game_bridge import BridgeError, GameBridge, RUNTIME_CATALOG_SUBDIR


MODS = {"base": "2.1.21"}


def _report(request_id="request42"):
    return {"kind": "runtime_catalog", "schema_version": "1.1.0", "ok": True,
            "tick": 42, "request_id": request_id,
            "provenance": {"export_command": "export_runtime_catalog", "export_tick": 42,
                           "active_mods": MODS, "active_mods_count": 1,
                           "scope": {"force": "player", "surface": "nauvis", "surface_requested": True,
                                     "entity_scope": "selected_prototype_types", "entity_types": ["pipe"]}},
            "entities": [{"name": "pipe", "type": "pipe",
                          "placement": {"items": [{"name": "pipe", "count": 1}]}}]}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr("orchestrator.game_bridge.uuid.uuid4", lambda: SimpleNamespace(hex="request42"))
    bridge = GameBridge.__new__(GameBridge)
    bridge.script_output = tmp_path
    bridge._poll_interval = 0
    directory = tmp_path / RUNTIME_CATALOG_SUBDIR
    directory.mkdir(parents=True)
    return bridge, directory


def test_collects_exact_bytes_and_feeds_mapping(setup, monkeypatch):
    bridge, directory = setup
    raw = json.dumps(_report(), indent=2).encode()
    commands = []
    def command(text):
        commands.append(text)
        (directory / "runtime_catalog_request42.json").write_bytes(raw)
        return ""
    monkeypatch.setattr(bridge, "command", command)
    result = bridge.export_runtime_catalog("nauvis", "player", timeout=1)
    assert result == raw
    assert commands == ['/export_runtime_catalog {"force":"player","surface":"nauvis","request_id":"request42"}']
    mapping = mapping_from_runtime_catalog(result, expected_sha256=hashlib.sha256(result).hexdigest(),
                                           entity_names=["pipe"], surface="nauvis", force="player", active_mods=MODS)
    assert mapping["entities"] == {"pipe": "pipe"}
    (directory / "runtime_catalog_request42.json").write_bytes(b"changed")
    assert result == raw


@pytest.mark.parametrize("change", [
    {"request_id": "other"}, {"ok": False}, {"schema_version": "1.0.0"},
    {"kind": "other"}, {"tick": True}, {"tick": 43},
])
def test_rejects_wrong_identity_or_tick(setup, monkeypatch, change):
    bridge, directory = setup
    report = _report()
    report.update(change)
    def command(_):
        (directory / "runtime_catalog_request42.json").write_text(json.dumps(report))
        return ""
    monkeypatch.setattr(bridge, "command", command)
    with pytest.raises(BridgeError):
        bridge.export_runtime_catalog("nauvis", "player", timeout=1)


@pytest.mark.parametrize("field,value", [
    ("surface", "other"), ("force", "other"), ("surface_requested", False),
])
def test_rejects_wrong_scope(setup, monkeypatch, field, value):
    bridge, directory = setup
    report = _report()
    report["provenance"]["scope"][field] = value
    monkeypatch.setattr(bridge, "command", lambda _: (directory / "runtime_catalog_request42.json").write_text(json.dumps(report)) and "")
    with pytest.raises(BridgeError, match="provenance"):
        bridge.export_runtime_catalog("nauvis", "player", timeout=1)


def test_existing_response_prevents_command(setup, monkeypatch):
    bridge, directory = setup
    (directory / "runtime_catalog_request42.json").write_text("{}")
    monkeypatch.setattr(bridge, "command", lambda _: pytest.fail("command sent"))
    with pytest.raises(BridgeError, match="already exists"):
        bridge.export_runtime_catalog("nauvis", "player")


def test_symlink_response_is_not_read(setup, monkeypatch, tmp_path):
    bridge, directory = setup
    target = tmp_path / "other.json"
    target.write_text(json.dumps(_report()))
    def command(_):
        (directory / "runtime_catalog_request42.json").symlink_to(target)
        return ""
    monkeypatch.setattr(bridge, "command", command)
    with pytest.raises(BridgeError, match="safely readable"):
        bridge.export_runtime_catalog("nauvis", "player", timeout=1)


def test_other_output_directory_rejected(setup, monkeypatch, tmp_path):
    bridge, _ = setup
    other = tmp_path / "other" / "runtime_catalog_request42.json"
    other.parent.mkdir()
    other.write_text(json.dumps(_report()))
    monkeypatch.setattr(bridge, "command", lambda _: "")
    with pytest.raises(BridgeError, match="Timed out"):
        bridge.export_runtime_catalog("nauvis", "player", timeout=0.01)


def test_duplicate_keys_rejected(setup, monkeypatch):
    bridge, directory = setup
    raw = json.dumps(_report()).replace('"kind": "runtime_catalog"', '"kind": "runtime_catalog", "kind": "runtime_catalog"')
    monkeypatch.setattr(bridge, "command", lambda _: (directory / "runtime_catalog_request42.json").write_text(raw) and "")
    with pytest.raises(BridgeError, match="not valid JSON"):
        bridge.export_runtime_catalog("nauvis", "player", timeout=1)


def test_incomplete_write_is_retried(setup, monkeypatch):
    bridge, directory = setup
    path = directory / "runtime_catalog_request42.json"
    monkeypatch.setattr(bridge, "command", lambda _: path.write_text('{"kind":') and "")
    monkeypatch.setattr("orchestrator.game_bridge.time.sleep", lambda _: path.write_text(json.dumps(_report())))
    assert json.loads(bridge.export_runtime_catalog("nauvis", "player", timeout=1))["request_id"] == "request42"


def test_fifo_is_rejected_without_blocking(setup, monkeypatch):
    bridge, directory = setup
    def command(_):
        os.mkfifo(directory / "runtime_catalog_request42.json")
        return ""
    monkeypatch.setattr(bridge, "command", command)
    with pytest.raises(BridgeError, match="not a regular file"):
        bridge.export_runtime_catalog("nauvis", "player", timeout=1)


def test_command_error_fails_without_reading_report(setup, monkeypatch):
    bridge, _ = setup
    monkeypatch.setattr(bridge, "command", lambda _: "Error: command unavailable")
    with pytest.raises(BridgeError, match="command failed"):
        bridge.export_runtime_catalog("nauvis", "player", timeout=1)
