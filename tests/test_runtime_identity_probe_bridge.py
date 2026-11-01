# Path: tests/test_runtime_identity_probe_bridge.py
# Purpose: Pin the nonce-correlated, read-only GameBridge runtime identity probe contract.

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.game_bridge import (
    RUNTIME_IDENTITY_REPORT_SUBDIR,
    BridgeError,
    GameBridge,
)


def _bridge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GameBridge:
    """A GameBridge with its RCON socket stubbed out -- collection is filesystem work."""
    monkeypatch.setattr(
        "orchestrator.game_bridge.RconClient", lambda *_a, **_kw: object()
    )
    return GameBridge(script_output=tmp_path, poll_interval=0.0)


def _uuid_sequence(monkeypatch: pytest.MonkeyPatch, *values: str) -> None:
    import orchestrator.game_bridge as game_bridge

    ids = iter(values)
    monkeypatch.setattr(
        game_bridge.uuid, "uuid4", lambda: SimpleNamespace(hex=next(ids)),
    )


def _report(
    nonce: str, *, surface: str = "nauvis", force: str = "player",
) -> dict:
    return {
        "schema_version": "1.0.0",
        "kind": "runtime_identity_probe",
        "ok": True,
        "nonce": nonce,
        "tick": 1000,
        "surface": {"name": surface, "index": 1},
        "force": {"name": force, "index": 2},
        "active_mods": {
            "base": "2.0.77",
            "factorio_cursor_rl_agent": "0.2.0",
        },
        "active_mods_count": 2,
        "ownership_claimed": False,
        "dispatch_authorized": False,
    }


def _write_response(directory: Path, name: str, payload: dict | str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(
        payload if isinstance(payload, str) else json.dumps(payload),
        encoding="utf-8",
    )
    return path


def _command_payload(command: str) -> dict:
    return json.loads(command.split(" ", 1)[1])


def test_probe_sends_one_fresh_nonce_correlated_read_only_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _uuid_sequence(monkeypatch, "nonce42")
    bridge = _bridge(tmp_path, monkeypatch)
    report_dir = tmp_path / RUNTIME_IDENTITY_REPORT_SUBDIR
    calls: list[str] = []

    def _fake_command(command: str) -> str:
        calls.append(command)
        payload = _command_payload(command)
        _write_response(
            report_dir,
            f"runtime_identity_{payload['nonce']}.json",
            _report(payload["nonce"], surface=payload["surface"], force=payload["force"]),
        )
        return ""

    monkeypatch.setattr(bridge, "command", _fake_command)

    collected = bridge.runtime_identity_probe("nauvis", "player")

    assert collected["nonce"] == "nonce42"
    assert collected["dispatch_authorized"] is False
    assert calls == [
        '/runtime_identity_probe '
        '{"nonce":"nonce42","surface":"nauvis","force":"player"}'
    ]


def test_probe_generates_a_fresh_nonce_for_every_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _uuid_sequence(monkeypatch, "nonce-a", "nonce-b")
    bridge = _bridge(tmp_path, monkeypatch)
    report_dir = tmp_path / RUNTIME_IDENTITY_REPORT_SUBDIR
    collected_names: list[str] = []

    def _fake_command(command: str) -> str:
        payload = _command_payload(command)
        path = _write_response(
            report_dir,
            f"runtime_identity_{payload['nonce']}.json",
            _report(payload["nonce"]),
        )
        collected_names.append(path.name)
        return ""

    monkeypatch.setattr(bridge, "command", _fake_command)

    first = bridge.runtime_identity_probe("nauvis", "player")
    second = bridge.runtime_identity_probe("nauvis", "player")

    assert first["nonce"] == "nonce-a"
    assert second["nonce"] == "nonce-b"
    assert collected_names == [
        "runtime_identity_nonce-a.json", "runtime_identity_nonce-b.json",
    ]


def test_validated_report_is_not_reread_from_mutated_file(tmp_path, monkeypatch):
    _uuid_sequence(monkeypatch, "nonce-safe")
    bridge = _bridge(tmp_path, monkeypatch)
    report_dir = tmp_path / RUNTIME_IDENTITY_REPORT_SUBDIR

    def fake_command(command):
        payload = _command_payload(command)
        _write_response(report_dir, f"runtime_identity_{payload['nonce']}.json", _report(payload["nonce"]))
        return ""

    monkeypatch.setattr(bridge, "command", fake_command)
    report = bridge.runtime_identity_probe("nauvis", "player")
    changed = _report("nonce-safe")
    changed["dispatch_authorized"] = True
    changed["active_mods"]["base"] = "forged"
    _write_response(report_dir, "runtime_identity_nonce-safe.json", changed)
    assert report["dispatch_authorized"] is False
    assert report["active_mods"]["base"] != "forged"


@pytest.mark.parametrize(
    "surface, force",
    [("", "player"), ("nauvis", ""), (None, "player"), ("nauvis", None)],
)
def test_probe_rejects_invalid_targets_before_collection(
    surface: str | None, force: str | None,
) -> None:
    bridge = GameBridge.__new__(GameBridge)
    bridge._run_and_collect = lambda *_args: pytest.fail(  # type: ignore[method-assign]
        "invalid targets must not collect"
    )

    with pytest.raises(ValueError, match="non-empty string"):
        bridge.runtime_identity_probe(surface, force)  # type: ignore[arg-type]


def test_probe_times_out_when_the_response_is_written_to_the_wrong_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _uuid_sequence(monkeypatch, "nonce42")
    bridge = _bridge(tmp_path, monkeypatch)
    wrong_directory = tmp_path / "factorio_mod" / "runtime_identity_probe_reports"

    def _fake_command(command: str) -> str:
        payload = _command_payload(command)
        _write_response(
            wrong_directory,
            f"runtime_identity_{payload['nonce']}.json",
            _report(payload["nonce"]),
        )
        return ""

    monkeypatch.setattr(bridge, "command", _fake_command)

    with pytest.raises(BridgeError, match="Timed out"):
        bridge.runtime_identity_probe("nauvis", "player", timeout=0.05)


def test_probe_times_out_when_the_mod_writes_no_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _uuid_sequence(monkeypatch, "nonce42")
    bridge = _bridge(tmp_path, monkeypatch)
    monkeypatch.setattr(bridge, "command", lambda _command: "")

    with pytest.raises(BridgeError, match="Timed out"):
        bridge.runtime_identity_probe("nauvis", "player", timeout=0.05)


def test_probe_refuses_a_response_path_that_already_existed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _uuid_sequence(monkeypatch, "nonce42")
    bridge = _bridge(tmp_path, monkeypatch)
    report_dir = tmp_path / RUNTIME_IDENTITY_REPORT_SUBDIR
    _write_response(
        report_dir, "runtime_identity_nonce42.json", _report("nonce42"),
    )
    calls: list[str] = []

    def _fake_command(command: str) -> str:
        calls.append(command)
        return ""

    monkeypatch.setattr(bridge, "command", _fake_command)

    with pytest.raises(BridgeError, match="already exists"):
        bridge.runtime_identity_probe("nauvis", "player")
    assert calls == []


def test_probe_rejects_a_symlinked_response_after_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _uuid_sequence(monkeypatch, "nonce42")
    bridge = _bridge(tmp_path, monkeypatch)
    report_dir = tmp_path / RUNTIME_IDENTITY_REPORT_SUBDIR
    target = tmp_path / "outside-report.json"
    target.write_text(json.dumps(_report("nonce42")), encoding="utf-8")

    def _fake_command(command: str) -> str:
        payload = _command_payload(command)
        report_dir.mkdir(parents=True, exist_ok=True)
        link = report_dir / f"runtime_identity_{payload['nonce']}.json"
        try:
            os.symlink(target, link)
        except OSError as error:  # pragma: no cover - platform without symlinks
            pytest.skip(f"symlinks are unavailable: {error}")
        return ""

    monkeypatch.setattr(bridge, "command", _fake_command)

    with pytest.raises(BridgeError, match="symbolic link"):
        bridge.runtime_identity_probe("nauvis", "player")


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("schema_version", "2.0.0", "schema_version"),
        ("kind", "other_report", "kind"),
        ("nonce", "someone-else", "nonce"),
        ("tick", "1000", "tick"),
        ("tick", True, "tick"),
        ("tick", -1, "tick"),
        ("surface", {"name": "other", "index": 1}, "surface.name"),
        ("surface", None, "surface object"),
        ("surface", {"name": "nauvis", "index": "1"}, "surface.index"),
        ("force", {"name": "other", "index": 2}, "force.name"),
        ("active_mods", {}, "active_mods object"),
        ("active_mods", {"base": 2}, "active_mods entry"),
        ("active_mods_count", 3, "active_mods_count"),
        ("active_mods_count", True, "active_mods_count"),
        ("ownership_claimed", True, "ownership_claimed"),
        ("ownership_claimed", None, "ownership_claimed"),
        ("dispatch_authorized", True, "dispatch_authorized"),
        ("dispatch_authorized", 0, "dispatch_authorized"),
    ],
)
def test_probe_rejects_forged_or_malformed_report_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    field: str, value, message: str,
) -> None:
    _uuid_sequence(monkeypatch, "nonce42")
    bridge = _bridge(tmp_path, monkeypatch)
    report_dir = tmp_path / RUNTIME_IDENTITY_REPORT_SUBDIR
    payload = _report("nonce42")
    payload[field] = value

    def _fake_command(command: str) -> str:
        request = _command_payload(command)
        _write_response(
            report_dir,
            f"runtime_identity_{request['nonce']}.json",
            payload,
        )
        return ""

    monkeypatch.setattr(bridge, "command", _fake_command)

    with pytest.raises(BridgeError, match=message):
        bridge.runtime_identity_probe("nauvis", "player")


def test_probe_rejects_a_correlated_failure_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _uuid_sequence(monkeypatch, "nonce42")
    bridge = _bridge(tmp_path, monkeypatch)
    report_dir = tmp_path / RUNTIME_IDENTITY_REPORT_SUBDIR
    payload = {
        "schema_version": "1.0.0",
        "kind": "runtime_identity_probe",
        "ok": False,
        "nonce": "nonce42",
        "tick": 1000,
        "surface": "missing",
        "force": "player",
        "error": "Unknown surface: missing",
        "ownership_claimed": False,
        "dispatch_authorized": False,
    }

    def _fake_command(command: str) -> str:
        request = _command_payload(command)
        _write_response(
            report_dir,
            f"runtime_identity_{request['nonce']}.json",
            payload,
        )
        return ""

    monkeypatch.setattr(bridge, "command", _fake_command)

    with pytest.raises(BridgeError, match="Unknown surface: missing"):
        bridge.runtime_identity_probe("nauvis", "player")


def test_probe_does_not_accept_unparseable_json_as_a_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _uuid_sequence(monkeypatch, "nonce42")
    bridge = _bridge(tmp_path, monkeypatch)
    report_dir = tmp_path / RUNTIME_IDENTITY_REPORT_SUBDIR

    def _fake_command(command: str) -> str:
        request = _command_payload(command)
        _write_response(
            report_dir,
            f"runtime_identity_{request['nonce']}.json",
            "{not-json",
        )
        return ""

    monkeypatch.setattr(bridge, "command", _fake_command)

    with pytest.raises(BridgeError, match="not readable JSON"):
        bridge.runtime_identity_probe("nauvis", "player", timeout=0.05)
