# Path: tests/test_recipe_catalog_bridge.py | Purpose: Exercise correlated recipe catalog collection without a server.
import json
from types import SimpleNamespace

import pytest

from orchestrator.game_bridge import BridgeError, GameBridge, RECIPE_CATALOG_SUBDIR


def _payload():
    return {"version": "1.3.0", "force": "player", "tick": 42, "request_id": "req42",
            "active_mods": {"base": "2.1.21"}, "active_mods_count": 1,
            "recipes": [], "raw_resources": []}


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setattr("orchestrator.game_bridge.uuid.uuid4", lambda: SimpleNamespace(hex="req42"))
    instance = GameBridge.__new__(GameBridge)
    instance.script_output = tmp_path
    instance._poll_interval = 0
    directory = tmp_path / RECIPE_CATALOG_SUBDIR
    directory.mkdir(parents=True)
    return instance, directory


def test_correlated_recipe_capture_returns_exact_detached_bytes(bridge, monkeypatch):
    instance, directory = bridge
    raw = json.dumps(_payload(), indent=2).encode()
    calls = []
    def command(text):
        calls.append(text)
        (directory / "recipe_catalog_req42.json").write_bytes(raw)
        return ""
    monkeypatch.setattr(instance, "command", command)
    result = instance.export_recipe_catalog_capture("player", timeout=1)
    assert calls == ['/export_recipe_catalog {"force":"player","request_id":"req42"}']
    assert result == raw
    (directory / "recipe_catalog_req42.json").write_bytes(b"changed")
    assert result == raw


@pytest.mark.parametrize("field,value", [
    ("request_id", "other"), ("version", "1.2.0"), ("force", "other"),
    ("tick", True), ("active_mods_count", True), ("active_mods", {}),
    ("recipes", {}), ("raw_resources", {}),
])
def test_rejects_wrong_identity_or_provenance(bridge, monkeypatch, field, value):
    instance, directory = bridge
    payload = _payload()
    payload[field] = value
    monkeypatch.setattr(instance, "command", lambda _: (directory / "recipe_catalog_req42.json").write_text(json.dumps(payload)) and "")
    with pytest.raises(BridgeError):
        instance.export_recipe_catalog_capture("player", timeout=1)


def test_stale_file_prevents_command(bridge, monkeypatch):
    instance, directory = bridge
    (directory / "recipe_catalog_req42.json").write_text("{}")
    monkeypatch.setattr(instance, "command", lambda _: pytest.fail("command sent"))
    with pytest.raises(BridgeError, match="already exists"):
        instance.export_recipe_catalog_capture("player")


def test_symlink_response_rejected(bridge, monkeypatch, tmp_path):
    instance, directory = bridge
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps(_payload()))
    def command(_):
        (directory / "recipe_catalog_req42.json").symlink_to(target)
        return ""
    monkeypatch.setattr(instance, "command", command)
    with pytest.raises(BridgeError, match="safely readable"):
        instance.export_recipe_catalog_capture("player", timeout=1)


@pytest.mark.parametrize("extra_bytes", [0, 1])
def test_correlated_response_size_boundary(bridge, monkeypatch, extra_bytes):
    instance, directory = bridge
    raw = json.dumps(_payload()).encode()
    monkeypatch.setattr("orchestrator.game_bridge.CORRELATED_REPORT_MAX_BYTES", len(raw))

    def command(_):
        (directory / "recipe_catalog_req42.json").write_bytes(raw + b" " * extra_bytes)
        return ""

    monkeypatch.setattr(instance, "command", command)
    if extra_bytes:
        with pytest.raises(BridgeError, match="exceeds size limit"):
            instance.export_recipe_catalog_capture("player", timeout=1)
    else:
        assert instance.export_recipe_catalog_capture("player", timeout=1) == raw


def test_missing_force_fails_before_command(bridge, monkeypatch):
    instance, _ = bridge
    monkeypatch.setattr(instance, "command", lambda _: pytest.fail("command sent"))
    with pytest.raises(ValueError):
        instance.export_recipe_catalog_capture("")
