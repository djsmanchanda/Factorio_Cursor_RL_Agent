# Path: tests/test_optimizer_consumer_observation_bridge.py | Purpose: Reject uncorrelated scoped consumer reports.
import json

import pytest

from orchestrator.game_bridge import BridgeError, GameBridge, OPTIMIZER_CONSUMER_OBSERVATION_SUBDIR


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setattr("orchestrator.game_bridge.RconClient", lambda *_args, **_kwargs: object())
    return GameBridge(tmp_path, poll_interval=0)


def collect(bridge, monkeypatch, mutate=lambda _report: None):
    def command(text):
        assert text.startswith("/optimizer_consumer_observation ")
        request = json.loads(text.split(" ", 1)[1])
        report = {"version": "optimizer-consumer-observation-v1", "kind": "optimizer_consumer_observation",
                  "request_id": request["request_id"], "tick": 100, "ok": True,
                  "ownership_claimed": False, "dispatch_authorized": False,
                  "surface": request["surface"], "force": request["force"],
                  "requested": {key: request[key] for key in ("entity", "position", "unit_number")},
                  "observation": {"state": "present", "recipe": {"state": "available", "value": "plastic-bar"},
                                  "products_finished": {"state": "available", "value": 12}}}
        mutate(report)
        directory = bridge.script_output / OPTIMIZER_CONSUMER_OBSERVATION_SUBDIR
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"optimizer_consumer_{request['request_id']}.json").write_text(json.dumps(report))
        return ""
    monkeypatch.setattr(bridge, "command", command)
    return bridge.optimizer_consumer_observation(surface="nauvis", force="player", entity="chemical-plant",
                                                 position={"x": 1.5, "y": 2.5}, unit_number=42, timeout=.1)


def test_correlated_consumer_counter_is_observation_only(bridge, monkeypatch):
    report = collect(bridge, monkeypatch)
    assert report["requested"]["unit_number"] == 42
    assert report["observation"]["products_finished"]["value"] == 12
    assert report["ownership_claimed"] is report["dispatch_authorized"] is False


@pytest.mark.parametrize("change", [
    lambda r: r.update(request_id="other"), lambda r: r.update(surface="foreign"),
    lambda r: r["requested"].update(unit_number=43),
    lambda r: r["requested"].update(unit_number=42.0),
    lambda r: r["requested"]["position"].update(x=2.5),
    lambda r: r.update(tick=True), lambda r: r.update(dispatch_authorized=True),
    lambda r: r["observation"]["products_finished"].update(value=12.0),
    lambda r: r["observation"]["products_finished"].update(value=-1),
])
def test_malformed_or_foreign_report_fails_closed(bridge, monkeypatch, change):
    with pytest.raises(BridgeError):
        collect(bridge, monkeypatch, change)


@pytest.mark.parametrize("change", [
    {"unit_number": True}, {"unit_number": 0}, {"unit_number": 2**53},
    {"position": {"x": float("nan"), "y": 0}}, {"position": {"x": 1e300, "y": 0}},
    {"surface": ""}, {"entity": "x" * 129},
])
def test_invalid_request_is_rejected_before_command(bridge, monkeypatch, change):
    monkeypatch.setattr(bridge, "command", lambda _command: pytest.fail("invalid request reached RCON"))
    kwargs = {"surface": "nauvis", "force": "player", "entity": "chemical-plant",
              "position": {"x": 1.5, "y": 2.5}, "unit_number": 42, **change}
    with pytest.raises(ValueError):
        bridge.optimizer_consumer_observation(**kwargs)
