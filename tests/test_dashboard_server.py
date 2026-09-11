# Path: tests/test_dashboard_server.py
# Purpose: Protect the dashboard's local RCON-secret loading contract.

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import dashboard_server
from tools.dashboard_server import DashboardHandler, _rcon_password


def test_rcon_secret_file_overrides_command_line_password(tmp_path: Path) -> None:
    secret = tmp_path / "rcon-password"
    secret.write_text("local-secret\n", encoding="utf-8")

    assert _rcon_password(secret, "fallback-password") == "local-secret"


def test_rcon_secret_file_fails_closed_when_empty_or_unreadable(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.write_text("\n", encoding="utf-8")

    with pytest.raises(ValueError, match="empty"):
        _rcon_password(empty, "fallback-password")
    with pytest.raises(ValueError, match="unavailable"):
        _rcon_password(tmp_path / "missing", "fallback-password")


def test_stop_console_endpoint_replies_before_shutting_down(monkeypatch) -> None:
    payload = json.dumps({"confirmation": "STOP_OPERATIONS_CONSOLE"}).encode()
    handler = object.__new__(DashboardHandler)
    handler.path = "/api/actions/stop_console"
    handler.headers = {
        "X-Action-Token": "secret",
        "Content-Length": str(len(payload)),
    }
    handler.action_token = "secret"
    handler.rfile = io.BytesIO(payload)
    replies: list[tuple[int, dict]] = []
    shutdowns: list[str] = []
    handler._json = lambda code, body: replies.append((code, body))
    handler.server = SimpleNamespace(shutdown=lambda: shutdowns.append("shutdown"))

    class ImmediateThread:
        def __init__(self, *, target, daemon):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(dashboard_server.threading, "Thread", ImmediateThread)

    handler.do_POST()

    assert replies == [(202, {"accepted": True, "action": "stop_console"})]
    assert shutdowns == ["shutdown"]


def test_helper_feedback_validation_error_returns_conflict() -> None:
    payload = json.dumps({"run_id": "missing"}).encode()
    handler = object.__new__(DashboardHandler)
    handler.path = "/api/actions/helper_feedback"
    handler.headers = {
        "X-Action-Token": "secret",
        "Content-Length": str(len(payload)),
    }
    handler.action_token = "secret"
    handler.rfile = io.BytesIO(payload)
    handler.manager = SimpleNamespace(
        submit_helper_feedback=lambda _payload: (_ for _ in ()).throw(
            ValueError("feedback is invalid")
        )
    )
    replies: list[tuple[int, dict]] = []
    handler._json = lambda code, body: replies.append((code, body))

    handler.do_POST()

    assert replies == [(409, {"error": "feedback is invalid"})]


def test_logistic_inventory_endpoint_returns_the_live_report() -> None:
    handler = object.__new__(DashboardHandler)
    handler.path = "/api/logistic-inventory"
    handler.manager = SimpleNamespace(logistic_inventory=lambda: {"ok": True, "tick": 99})
    replies: list[tuple[int, dict]] = []
    handler._json = lambda code, body: replies.append((code, body))

    handler.do_GET()

    assert replies == [(200, {"ok": True, "tick": 99})]


def test_inventory_history_endpoint_returns_retained_series() -> None:
    handler = object.__new__(DashboardHandler)
    handler.path = "/api/inventory-history"
    handler.manager = SimpleNamespace(inventory_history=lambda: {"runs": [{"id": "run"}]})
    replies: list[tuple[int, dict]] = []
    handler._json = lambda code, body: replies.append((code, body))

    handler.do_GET()

    assert replies == [(200, {"runs": [{"id": "run"}]})]


def test_evidence_routes_use_fixed_manager_sources():
    from types import SimpleNamespace
    handler = object.__new__(DashboardHandler)
    calls = []
    handler._json = lambda status, payload: calls.append((status, payload))
    handler.manager = SimpleNamespace(
        run_context=lambda: {'text': 'current evidence'},
        search_run_history=lambda query: {'text': query},
    )
    handler.path = '/api/run-context'
    handler.do_GET()
    handler.path = '/api/run-history?q=electric-mining-drill'
    handler.do_GET()
    assert calls == [(200, {'text': 'current evidence'}), (200, {'text': 'electric-mining-drill'})]


def test_observation_loop_status_endpoint():
    handler = object.__new__(DashboardHandler)
    handler.path = '/api/observation-loop'
    expected = {'phase': 'observing', 'completed_runs': 1, 'acceptance_streak': 0}
    handler.manager = SimpleNamespace(observation_loop=lambda: expected)
    replies = []
    handler._json = lambda code, body: replies.append((code, body))
    handler.do_GET()
    assert replies == [(200, expected)]


@pytest.mark.parametrize('action', ['start_observation_loop', 'resume_observation_loop', 'stop_observation_loop'])
@pytest.mark.parametrize('authorized', [True, False])
def test_loop_actions_require_token_and_use_existing_action_dispatch(action, authorized):
    handler = object.__new__(DashboardHandler)
    handler.path = f'/api/actions/{action}'
    handler.headers = {'X-Action-Token': 'secret' if authorized else 'wrong', 'Content-Length': '2'}
    handler.action_token = 'secret'
    handler.rfile = io.BytesIO(b'{}')
    calls, replies = [], []
    handler.manager = SimpleNamespace(start=lambda *args: calls.append(args))
    handler._json = lambda code, body: replies.append((code, body))
    handler.do_POST()
    assert calls == ([(action, '')] if authorized else [])
    assert replies[0][0] == (202 if authorized else 403)
