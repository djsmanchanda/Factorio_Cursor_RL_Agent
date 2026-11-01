# Path: tests/test_rocket_silo_observation.py
# Purpose: Pin the strict read-only rocket-silo observation contract.

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft7Validator
from orchestrator.game_bridge import BridgeError, GameBridge, ROCKET_SILO_REPORT_SUBDIR

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "rocket_silo_observation.schema.json"


def _available(value):
    return {"state": "available", "value": value}


def _limitations() -> list[str]:
    return [
        "Runtime API 2.1.21 is a static reference; the live engine build is not readable.",
        "Entity facts are only reported while the referenced entity is valid.",
    ]


def _provenance() -> dict:
    return {
        "observer": "rocket_silo_observation",
        "runtime_api_reference": "2.1.21",
        "runtime_api_version": 6,
        "live_engine_version_verified": False,
        "limitations": _limitations(),
    }


def _silo() -> dict:
    return {
        "unit_number": _available(7),
        "position": _available({"x": 1.5, "y": 2.5}),
        "status": {
            "rocket_silo": _available("building_rocket"),
            "entity": _available("working"),
        },
        "parts": {
            "actual": _available(42),
            "required": _available(100),
            "storage_cap": {
                "state": "unavailable",
                "reason": "rocket_parts_storage_cap is unavailable",
            },
        },
        "recipe": {"state": "absent", "reason": "No recipe is active"},
        "auto_launch": {
            "source": "send_to_orbit_automatically",
            "fact": _available(False),
        },
        "rocket": {"state": "absent", "reason": "rocket is not attached"},
        "cargo_pod": {
            "state": "absent",
            "reason": "No valid rocket entity is attached",
        },
    }


def _success() -> dict:
    return {
        "schema_version": "1.0.0",
        "kind": "rocket_silo_observation",
        "ok": True,
        "request_id": "request-42",
        "tick": 1000,
        "surface": "nauvis",
        "force": "player",
        "requested": {
            "unit_number": {"state": "absent", "reason": "No unit_number filter was requested"}
        },
        "silo_count": 1,
        "silos": [_silo()],
        "provenance": _provenance(),
    }


def _failure() -> dict:
    return {
        "schema_version": "1.0.0",
        "kind": "rocket_silo_observation",
        "ok": False,
        "request_id": "request-fail",
        "tick": 1000,
        "surface": "missing",
        "force": "player",
        "error": "Unknown surface: missing",
    }


def _validator() -> Draft7Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft7Validator.check_schema(schema)
    return Draft7Validator(schema)


def _bridge(tmp_path, monkeypatch):
    monkeypatch.setattr("orchestrator.game_bridge.RconClient", lambda *_args, **_kwargs: object())
    monkeypatch.setattr("orchestrator.game_bridge.uuid.uuid4", lambda: SimpleNamespace(hex="fresh42"))
    return GameBridge(tmp_path, poll_interval=0)


def _respond(tmp_path, monkeypatch, bridge, payload):
    def command(text):
        request = json.loads(text.split(" ", 1)[1])
        assert text.startswith("/rocket_silo_observation ")
        directory = tmp_path / ROCKET_SILO_REPORT_SUBDIR
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"rocket_silo_observation_{request['request_id']}.json").write_text(
            json.dumps(payload), encoding="utf-8")
        return ""
    monkeypatch.setattr(bridge, "command", command)


def test_bridge_collects_scoped_report_and_returns_detached_data(tmp_path, monkeypatch):
    bridge = _bridge(tmp_path, monkeypatch)
    report = _success()
    report["request_id"] = "fresh42"
    report["requested"]["unit_number"] = _available(7)
    _respond(tmp_path, monkeypatch, bridge, report)
    result = bridge.observe_rocket_silos("nauvis", "player", unit_number=7)
    assert result == report
    (tmp_path / ROCKET_SILO_REPORT_SUBDIR / "rocket_silo_observation_fresh42.json").write_text("{}")
    assert result["silos"][0]["unit_number"]["value"] == 7


@pytest.mark.parametrize("damage", ["id", "scope", "count", "filter", "unit", "duplicate", "invalid_fact", "failure"])
def test_bridge_rejects_inconsistent_silo_reports(tmp_path, monkeypatch, damage):
    bridge = _bridge(tmp_path, monkeypatch)
    report = _success()
    report["request_id"] = "fresh42"
    if damage == "id":
        report["request_id"] = "wrong"
    elif damage == "scope":
        report["force"] = "other"
    elif damage == "count":
        report["silo_count"] = 2
    elif damage == "filter":
        report["requested"]["unit_number"] = _available(7)
    elif damage == "unit":
        report["requested"]["unit_number"] = _available(7)
        report["silos"][0]["unit_number"] = _available(8)
    elif damage == "duplicate":
        report["silos"].append(copy.deepcopy(report["silos"][0]))
        report["silo_count"] = 2
    elif damage == "invalid_fact":
        report["silos"][0]["parts"]["actual"] = _available(-1)
    else:
        report = {**_failure(), "request_id": "fresh42"}
    _respond(tmp_path, monkeypatch, bridge, report)
    with pytest.raises(BridgeError):
        bridge.observe_rocket_silos("nauvis", "player", unit_number=7 if damage == "unit" else None)


@pytest.mark.parametrize("unit_number", [-1, True, 1.5, "7"])
def test_bridge_rejects_bad_filter_before_command(tmp_path, monkeypatch, unit_number):
    bridge = _bridge(tmp_path, monkeypatch)
    monkeypatch.setattr(bridge, "command", lambda _command: pytest.fail("command sent"))
    with pytest.raises(ValueError):
        bridge.observe_rocket_silos("nauvis", "player", unit_number=unit_number)


def test_bridge_rejects_existing_path_and_symlink(tmp_path, monkeypatch):
    bridge = _bridge(tmp_path, monkeypatch)
    directory = tmp_path / ROCKET_SILO_REPORT_SUBDIR
    directory.mkdir(parents=True)
    target = directory / "rocket_silo_observation_fresh42.json"
    target.write_text("{}")
    monkeypatch.setattr(bridge, "command", lambda _command: pytest.fail("command sent"))
    with pytest.raises(BridgeError, match="already exists"):
        bridge.observe_rocket_silos("nauvis", "player")
    target.unlink()

    def symlink_response(_command):
        target.symlink_to(tmp_path / "other")
        return ""
    monkeypatch.setattr(bridge, "command", symlink_response)
    with pytest.raises(BridgeError, match="safely readable"):
        bridge.observe_rocket_silos("nauvis", "player", timeout=0)


def test_bridge_missing_correlated_file_fails(tmp_path, monkeypatch):
    bridge = _bridge(tmp_path, monkeypatch)
    def unrelated_response(_command):
        directory = tmp_path / ROCKET_SILO_REPORT_SUBDIR
        directory.mkdir(parents=True)
        (directory / "rocket_silo_observation_other.json").write_text(json.dumps(_success()))
        return ""
    monkeypatch.setattr(bridge, "command", unrelated_response)
    with pytest.raises(BridgeError, match="Timed out"):
        bridge.observe_rocket_silos("nauvis", "player", timeout=0)


def test_bridge_accepts_empty_filtered_result_without_inventing_a_silo(tmp_path, monkeypatch):
    bridge = _bridge(tmp_path, monkeypatch)
    report = _success()
    report.update(request_id="fresh42", silo_count=0, silos=[])
    report["requested"]["unit_number"] = _available(7)
    _respond(tmp_path, monkeypatch, bridge, report)
    assert bridge.observe_rocket_silos("nauvis", "player", unit_number=7)["silos"] == []


@pytest.mark.parametrize("payload", ['{"ok":true,"ok":false}', '{"value":NaN}'])
def test_bridge_rejects_ambiguous_json(tmp_path, monkeypatch, payload):
    bridge = _bridge(tmp_path, monkeypatch)
    def command(_command):
        directory = tmp_path / ROCKET_SILO_REPORT_SUBDIR
        directory.mkdir(parents=True)
        (directory / "rocket_silo_observation_fresh42.json").write_text(payload)
        return ""
    monkeypatch.setattr(bridge, "command", command)
    with pytest.raises(BridgeError, match="not valid JSON"):
        bridge.observe_rocket_silos("nauvis", "player", timeout=0)


@pytest.mark.parametrize("payload", [_success(), _failure()])
def test_rocket_silo_observation_schema_accepts_correlated_reports(payload: dict) -> None:
    assert list(_validator().iter_errors(payload)) == []


def test_success_requires_explicit_scope_and_provenance() -> None:
    missing_scope = _success()
    del missing_scope["surface"]
    assert list(_validator().iter_errors(missing_scope))

    missing_provenance = _success()
    del missing_provenance["provenance"]
    assert list(_validator().iter_errors(missing_provenance))

    verified = _success()
    verified["provenance"]["live_engine_version_verified"] = True
    assert list(_validator().iter_errors(verified))

    wrong_api = _success()
    wrong_api["provenance"]["runtime_api_reference"] = "2.1.24"
    assert list(_validator().iter_errors(wrong_api))


def test_report_is_not_an_event_or_launch_authorization() -> None:
    event = copy.deepcopy(_success())
    event["event"] = "on_rocket_launched"
    assert list(_validator().iter_errors(event))

    session = copy.deepcopy(_success())
    session["session_id"] = "session-1"
    assert list(_validator().iter_errors(session))

    authorized = copy.deepcopy(_success())
    authorized["launch_authorized"] = True
    assert list(_validator().iter_errors(authorized))


@pytest.mark.parametrize(
    "request_id",
    ["", "bad id!", "../escape", "x" * 65, "semi;colon"],
)
def test_request_id_must_be_filename_safe(request_id: str) -> None:
    payload = _success()
    payload["request_id"] = request_id
    assert list(_validator().iter_errors(payload))


def test_facts_distinguish_available_absent_and_unavailable() -> None:
    both = copy.deepcopy(_success())
    both["silos"][0]["parts"]["actual"]["reason"] = "ambiguous"
    assert list(_validator().iter_errors(both))

    missing_reason = copy.deepcopy(_success())
    del missing_reason["silos"][0]["parts"]["storage_cap"]["reason"]
    assert list(_validator().iter_errors(missing_reason))

    absent_with_value = copy.deepcopy(_success())
    absent_with_value["silos"][0]["recipe"]["value"] = "rocket-part"
    assert list(_validator().iter_errors(absent_with_value))

    bad_absent_reason = copy.deepcopy(_success())
    bad_absent_reason["silos"][0]["recipe"]["reason"] = ""
    assert list(_validator().iter_errors(bad_absent_reason))


def test_silo_requires_stable_identity_status_parts_and_entity_facts() -> None:
    for field in (
        "unit_number",
        "position",
        "status",
        "parts",
        "recipe",
        "auto_launch",
        "rocket",
        "cargo_pod",
    ):
        payload = copy.deepcopy(_success())
        del payload["silos"][0][field]
        assert list(_validator().iter_errors(payload)), field


def test_auto_launch_must_name_its_runtime_api_source() -> None:
    payload = copy.deepcopy(_success())
    payload["silos"][0]["auto_launch"]["source"] = "launch_rocket"
    assert list(_validator().iter_errors(payload))

    wrong_type = copy.deepcopy(_success())
    wrong_type["silos"][0]["auto_launch"]["fact"]["value"] = 1
    assert list(_validator().iter_errors(wrong_type))


def test_silo_count_must_be_non_negative_and_silos_must_be_an_array() -> None:
    negative = copy.deepcopy(_success())
    negative["silo_count"] = -1
    assert list(_validator().iter_errors(negative))

    not_array = copy.deepcopy(_success())
    not_array["silos"] = {}
    assert list(_validator().iter_errors(not_array))
