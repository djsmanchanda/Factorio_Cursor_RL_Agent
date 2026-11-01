# Path: tests/test_rocket_launch_observation.py
# Purpose: Pin the strict rocket-launch observation contract and its explicit-scope bridge command.

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft7Validator

from orchestrator.game_bridge import (
    ROCKET_LAUNCH_EVENT_SUBDIR,
    ROCKET_LAUNCH_REPORT_SUBDIR,
    BridgeError,
    GameBridge,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "rocket_launch_observation.schema.json"


def _launch_event() -> dict:
    return {
        "schema_version": "1.0.0",
        "kind": "rocket_launch",
        "event": "on_rocket_launched",
        "tick": 1100,
        "surface": "training/rocket-1",
        "force": "training-rocket-1",
        "sequence": 1,
        "session_id": "session42",
        "rocket": {
            "name": "rocket",
            "unit_number": 42,
            "position": {"x": 1.5, "y": 2.5},
        },
        "rocket_silo": {
            "name": "rocket-silo",
            "unit_number": 7,
            "position": {"x": 3.5, "y": 4.5},
        },
    }


def _scope_success() -> dict:
    return {
        "schema_version": "1.0.0",
        "kind": "rocket_launch_scope",
        "ok": True,
        "tick": 1000,
        "surface": "training/rocket-1",
        "force": "training-rocket-1",
        "enabled": True,
        "scope_since_tick": 1000,
        "recorded_launches": 0,
        "session_started": True,
        "session_id": "session42",
        "request_id": "request42",
    }


def _scope_failure() -> dict:
    return {
        "schema_version": "1.0.0",
        "kind": "rocket_launch_scope",
        "ok": False,
        "tick": 1000,
        "surface": "missing",
        "force": "training-rocket-1",
        "error": "Unknown surface: missing",
        "request_id": "request-fail",
    }


def _validator() -> Draft7Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft7Validator.check_schema(schema)
    return Draft7Validator(schema)


@pytest.mark.parametrize("payload", [_launch_event(), _scope_success(), _scope_failure()])
def test_rocket_launch_observation_schema_accepts_event_and_scope_reports(payload: dict) -> None:
    assert list(_validator().iter_errors(payload)) == []


def test_rocket_launch_observation_rejects_victory_and_unscoped_claims() -> None:
    event = _launch_event()
    event["victory"] = True
    assert list(_validator().iter_errors(event))

    unscoped = _launch_event()
    del unscoped["surface"]
    assert list(_validator().iter_errors(unscoped))

    inferred = _launch_event()
    inferred["rocket_silo_count"] = 1
    assert list(_validator().iter_errors(inferred))


def test_scope_request_id_must_be_filename_safe() -> None:
    unsafe = _scope_success()
    unsafe["request_id"] = "bad id!"
    assert list(_validator().iter_errors(unsafe))

    too_long = _scope_success()
    too_long["request_id"] = "x" * 65
    assert list(_validator().iter_errors(too_long))


def test_launch_event_requires_a_filename_safe_session_identity() -> None:
    missing = _launch_event()
    del missing["session_id"]
    assert list(_validator().iter_errors(missing))

    unsafe = _launch_event()
    unsafe["session_id"] = "bad id!"
    assert list(_validator().iter_errors(unsafe))

    too_long = _launch_event()
    too_long["session_id"] = "x" * 65
    assert list(_validator().iter_errors(too_long))


def test_scope_success_requires_a_boolean_session_started_flag() -> None:
    missing = _scope_success()
    del missing["session_started"]
    assert list(_validator().iter_errors(missing))

    wrong_type = _scope_success()
    wrong_type["session_started"] = 1
    assert list(_validator().iter_errors(wrong_type))


def test_rocket_launch_observation_requires_an_actual_ascent_event_name() -> None:
    ordered = _launch_event()
    ordered["event"] = "on_rocket_launch_ordered"
    assert list(_validator().iter_errors(ordered))

    cargo = _launch_event()
    cargo["event"] = "on_cargo_pod_finished_ascending"
    assert list(_validator().iter_errors(cargo))


def _bridge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **kwargs) -> GameBridge:
    """A GameBridge with its RCON socket stubbed out -- collection is pure filesystem work."""
    monkeypatch.setattr(
        "orchestrator.game_bridge.RconClient", lambda *_a, **_kw: object()
    )
    return GameBridge(script_output=tmp_path, poll_interval=0.0, **kwargs)


def _scope_response(
    request_id: str, session_id: str = "session42", *,
    surface: str = "nauvis", force: str = "player",
    enabled: bool = True, ok: bool = True, session_started: bool = False,
) -> dict:
    payload = {
        "schema_version": "1.0.0",
        "kind": "rocket_launch_scope",
        "ok": ok,
        "tick": 1000,
        "surface": surface,
        "force": force,
        "enabled": enabled,
        "scope_since_tick": 1000,
        "recorded_launches": 0,
        "session_started": session_started,
        "request_id": request_id,
    }
    if session_id is not None:
        payload["session_id"] = session_id
    if not ok:
        payload.pop("scope_since_tick")
        payload.pop("recorded_launches")
        payload.pop("session_started")
        payload.pop("session_id", None)
        payload["error"] = "response failed"
    return payload


def _command_payload(command: str) -> dict:
    return json.loads(command.split(" ", 1)[1])


def _uuid_sequence(monkeypatch: pytest.MonkeyPatch, *values: str) -> None:
    import orchestrator.game_bridge as game_bridge

    ids = iter(values)
    monkeypatch.setattr(
        game_bridge.uuid, "uuid4", lambda: SimpleNamespace(hex=next(ids)),
    )


def test_rocket_launch_bridge_sends_one_correlated_scoped_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _uuid_sequence(monkeypatch, "request42", "session42")
    bridge = GameBridge.__new__(GameBridge)
    calls: list[tuple[str, Path, float, str | None]] = []

    def collect(
        command: str, subdir: Path, timeout: float, *,
        expected_name: str | None = None,
    ) -> Path:
        calls.append((command, subdir, timeout, expected_name))
        return Path(expected_name or "unexpected.json")

    bridge._run_and_collect = collect  # type: ignore[method-assign]
    bridge._validate_rocket_launch_scope_response = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: None
    )

    assert bridge.rocket_launch_observation("nauvis", "player") == Path(
        "rocket_launch_scope_request42.json"
    )
    assert calls == [
        (
            '/rocket_launch_observation '
            '{"surface":"nauvis","force":"player","enabled":true,'
            '"request_id":"request42","session_id":"session42"}',
            ROCKET_LAUNCH_REPORT_SUBDIR,
            60.0,
            "rocket_launch_scope_request42.json",
        )
    ]


def test_rocket_launch_bridge_generates_a_fresh_session_per_arm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _uuid_sequence(
        monkeypatch,
        "request-a", "session-a", "request-b", "session-b",
    )
    bridge = GameBridge.__new__(GameBridge)
    calls: list[str] = []

    def collect(
        command: str, subdir: Path, timeout: float, *,
        expected_name: str | None = None,
    ) -> Path:
        calls.append(command)
        return Path(expected_name or "unexpected.json")

    bridge._run_and_collect = collect  # type: ignore[method-assign]
    bridge._validate_rocket_launch_scope_response = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: None
    )

    bridge.rocket_launch_observation("nauvis", "player", enabled=False)
    bridge.rocket_launch_observation("nauvis", "player")

    first, second = (_command_payload(command) for command in calls)
    assert (first["request_id"], first["session_id"]) == ("request-a", "session-a")
    assert (second["request_id"], second["session_id"]) == ("request-b", "session-b")


@pytest.mark.parametrize(
    "surface, force",
    [("", "player"), ("nauvis", ""), (None, "player"), ("nauvis", None)],
)
def test_rocket_launch_bridge_rejects_invalid_targets_before_collection(
    surface: str | None, force: str | None,
) -> None:
    bridge = GameBridge.__new__(GameBridge)
    bridge._run_and_collect = lambda *_args: pytest.fail(  # type: ignore[method-assign]
        "invalid targets must not collect"
    )

    with pytest.raises(ValueError, match="non-empty string"):
        bridge.rocket_launch_observation(surface, force)  # type: ignore[arg-type]


def test_rocket_launch_bridge_rejects_non_boolean_enabled() -> None:
    bridge = GameBridge.__new__(GameBridge)
    bridge._run_and_collect = lambda *_args: pytest.fail(  # type: ignore[method-assign]
        "non-boolean enabled must not collect"
    )

    with pytest.raises(ValueError, match="boolean"):
        bridge.rocket_launch_observation("nauvis", "player", enabled=1)  # type: ignore[arg-type]


def _write_scope(directory: Path, request_id: str, payload: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"rocket_launch_scope_{request_id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _scope_payload_for(command: str) -> dict:
    payload = _command_payload(command)
    return _scope_response(
        payload["request_id"], payload["session_id"], session_started=True,
    )


def test_scope_collection_selects_its_exact_response_not_a_newer_launch_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _uuid_sequence(monkeypatch, "request42", "session42")
    bridge = _bridge(tmp_path, monkeypatch)
    report_dir = tmp_path / ROCKET_LAUNCH_REPORT_SUBDIR
    event_dir = tmp_path / ROCKET_LAUNCH_EVENT_SUBDIR
    report_dir.mkdir(parents=True)
    event_dir.mkdir(parents=True)
    legacy_event = report_dir / "rocket_launch_1100_1.json"
    legacy_event.write_text(json.dumps(_launch_event()), encoding="utf-8")
    os.utime(legacy_event, ns=(2_000_000_000, 2_000_000_000))
    durable_event = event_dir / "rocket_launch_1100_1.json"
    durable_event.write_text(json.dumps(_launch_event()), encoding="utf-8")

    def _fake_command(command: str) -> str:
        payload = _scope_payload_for(command)
        _write_scope(report_dir, payload["request_id"], payload)
        return ""

    monkeypatch.setattr(bridge, "command", _fake_command)

    collected = bridge.rocket_launch_observation("nauvis", "player")

    assert collected.name == "rocket_launch_scope_request42.json"
    assert legacy_event.exists(), "a launch event must never be selected or pruned as a response"
    assert durable_event.exists()


def test_failed_scope_response_is_rejected_as_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _uuid_sequence(monkeypatch, "request42", "session42")
    bridge = _bridge(tmp_path, monkeypatch)
    report_dir = tmp_path / ROCKET_LAUNCH_REPORT_SUBDIR

    def _fake_command(command: str) -> str:
        request_id = _command_payload(command)["request_id"]
        _write_scope(
            report_dir, request_id,
            _scope_response(request_id, ok=False),
        )
        return ""

    monkeypatch.setattr(bridge, "command", _fake_command)

    with pytest.raises(BridgeError, match="command failed"):
        bridge.rocket_launch_observation("nauvis", "player")


def test_scope_response_with_the_wrong_request_id_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _uuid_sequence(monkeypatch, "request42", "session42")
    bridge = _bridge(tmp_path, monkeypatch)
    report_dir = tmp_path / ROCKET_LAUNCH_REPORT_SUBDIR

    def _fake_command(command: str) -> str:
        payload = _command_payload(command)
        _write_scope(
            report_dir, payload["request_id"],
            _scope_response(
                "someone-else", payload["session_id"], session_started=True,
            ),
        )
        return ""

    monkeypatch.setattr(bridge, "command", _fake_command)

    with pytest.raises(BridgeError, match="request_id"):
        bridge.rocket_launch_observation("nauvis", "player")


def test_continuing_scope_keeps_its_existing_session_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _uuid_sequence(monkeypatch, "request42", "session-new")
    bridge = _bridge(tmp_path, monkeypatch)
    report_dir = tmp_path / ROCKET_LAUNCH_REPORT_SUBDIR

    def _fake_command(command: str) -> str:
        payload = _command_payload(command)
        _write_scope(
            report_dir, payload["request_id"],
            _scope_response(
                payload["request_id"], "session-old", session_started=False,
            ),
        )
        return ""

    monkeypatch.setattr(bridge, "command", _fake_command)

    collected = bridge.rocket_launch_observation("nauvis", "player")

    assert collected.name == "rocket_launch_scope_request42.json"


def test_disarm_response_without_a_session_identity_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _uuid_sequence(monkeypatch, "request42", "session-new")
    bridge = _bridge(tmp_path, monkeypatch)
    report_dir = tmp_path / ROCKET_LAUNCH_REPORT_SUBDIR

    def _fake_command(command: str) -> str:
        payload = _command_payload(command)
        _write_scope(
            report_dir, payload["request_id"],
            _scope_response(
                payload["request_id"], None, enabled=False,
                session_started=False,
            ),
        )
        return ""

    monkeypatch.setattr(bridge, "command", _fake_command)

    collected = bridge.rocket_launch_observation("nauvis", "player", enabled=False)

    assert collected.name == "rocket_launch_scope_request42.json"


def test_started_session_must_match_the_supplied_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _uuid_sequence(monkeypatch, "request42", "session42")
    bridge = _bridge(tmp_path, monkeypatch)
    report_dir = tmp_path / ROCKET_LAUNCH_REPORT_SUBDIR

    def _fake_command(command: str) -> str:
        payload = _command_payload(command)
        _write_scope(
            report_dir, payload["request_id"],
            _scope_response(
                payload["request_id"], "someone-else", session_started=True,
            ),
        )
        return ""

    monkeypatch.setattr(bridge, "command", _fake_command)

    with pytest.raises(BridgeError, match="started session"):
        bridge.rocket_launch_observation("nauvis", "player")


def test_scope_response_rejects_an_unsafe_session_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _uuid_sequence(monkeypatch, "request42", "session42")
    bridge = _bridge(tmp_path, monkeypatch)
    report_dir = tmp_path / ROCKET_LAUNCH_REPORT_SUBDIR

    def _fake_command(command: str) -> str:
        payload = _command_payload(command)
        _write_scope(
            report_dir, payload["request_id"],
            _scope_response(
                payload["request_id"], "bad id!", session_started=True,
            ),
        )
        return ""

    monkeypatch.setattr(bridge, "command", _fake_command)

    with pytest.raises(BridgeError, match="unsafe session_id"):
        bridge.rocket_launch_observation("nauvis", "player")


def test_same_tick_concurrent_requests_collect_their_own_responses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _uuid_sequence(
        monkeypatch,
        "request-a", "session-a", "request-b", "session-b",
    )
    bridge = _bridge(tmp_path, monkeypatch)
    report_dir = tmp_path / ROCKET_LAUNCH_REPORT_SUBDIR
    tickets = iter([1000, 1000])

    def _fake_command(command: str) -> str:
        payload = _scope_payload_for(command)
        payload["tick"] = next(tickets)
        _write_scope(report_dir, payload["request_id"], payload)
        return ""

    monkeypatch.setattr(bridge, "command", _fake_command)

    first = bridge.rocket_launch_observation("nauvis", "player")
    second = bridge.rocket_launch_observation("nauvis", "player")

    assert first.name == "rocket_launch_scope_request-a.json"
    assert second.name == "rocket_launch_scope_request-b.json"
    reports = [json.loads(path.read_text(encoding="utf-8"))
               for path in sorted(report_dir.glob("*.json"))]
    assert {(report["request_id"], report["session_id"]) for report in reports} == {
        ("request-a", "session-a"), ("request-b", "session-b"),
    }


def test_scope_collection_prunes_only_scope_reports_and_keeps_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _uuid_sequence(monkeypatch, "request42", "session42")
    bridge = _bridge(tmp_path, monkeypatch, retain_reports=1)
    report_dir = tmp_path / ROCKET_LAUNCH_REPORT_SUBDIR
    event_dir = tmp_path / ROCKET_LAUNCH_EVENT_SUBDIR
    report_dir.mkdir(parents=True)
    event_dir.mkdir(parents=True)
    events = []
    for index in range(4):
        path = event_dir / f"rocket_launch_{1000 + index}_1.json"
        path.write_text(json.dumps(_launch_event()), encoding="utf-8")
        os.utime(path, ns=(1_000_000_000 + index, 1_000_000_000 + index))
        events.append(path)
    for index in range(3):
        stale = report_dir / f"rocket_launch_scope_stale{index}.json"
        stale.write_text(json.dumps(_scope_response(f"stale{index}")), encoding="utf-8")

    def _fake_command(command: str) -> str:
        payload = _scope_payload_for(command)
        _write_scope(report_dir, payload["request_id"], payload)
        return ""

    monkeypatch.setattr(bridge, "command", _fake_command)

    bridge.rocket_launch_observation("nauvis", "player")

    assert all(path.exists() for path in events)
    assert len(list(report_dir.glob("*.json"))) == 1


def test_scope_failure_cannot_smuggle_a_launch_record() -> None:
    failure = copy.deepcopy(_scope_failure())
    failure["rocket"] = _launch_event()["rocket"]
    assert list(_validator().iter_errors(failure))
