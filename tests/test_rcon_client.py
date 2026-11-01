# Path: tests/test_rcon_client.py
# Purpose: Protect request/response correlation in the shared Factorio RCON client.

from unittest.mock import Mock

import pytest

from tools.rcon_client import (
    RconClient,
    RconError,
    SERVERDATA_RESPONSE_VALUE,
)


def _client_with_packets(*packets: tuple[int, int, str]) -> RconClient:
    client = RconClient.__new__(RconClient)
    client._send_packet = Mock(return_value=42)
    client._recv_packet = Mock(side_effect=packets)
    return client


def test_command_skips_delayed_response_from_prior_request() -> None:
    """The 2026-09-08 run handed this mall record to find_line as its reply."""
    stale_loan = (
        "mall-bootstrap:v4:copper-cable:splitter:3:3:left:splitter:3:70:3:3:-"
        "|39.5|38.5|assembling-machine-1,splitter|assembling-machine-1,copper-cable"
    )
    client = _client_with_packets(
        (41, SERVERDATA_RESPONSE_VALUE, stale_loan),
        (42, SERVERDATA_RESPONSE_VALUE, "2 1 36.5:38.5,42.5:38.5 7"),
    )

    response = client.command("/sc rcon.print('line survey')")

    assert response == "2 1 36.5:38.5,42.5:38.5 7"
    assert client._recv_packet.call_count == 2


def test_command_rejects_wrong_packet_type_for_matching_request() -> None:
    client = _client_with_packets((42, 99, "not a command response"))

    with pytest.raises(RconError, match="response type 99.*request 42"):
        client.command("/sc rcon.print('line survey')")


def test_socket_validator_runs_before_authentication(monkeypatch):
    sock = Mock()
    events = []
    monkeypatch.setattr("tools.rcon_client.socket.create_connection", lambda *args, **kwargs: sock)
    monkeypatch.setattr(RconClient, "_auth", lambda self, password: events.append(("auth", password)))

    def validate(candidate):
        assert candidate is sock
        events.append(("validated", None))

    client = RconClient("127.0.0.1", 27015, "private", socket_validator=validate)
    assert events == [("validated", None), ("auth", "private")]
    client.close()
    sock.close.assert_called_once()


def test_game_bridge_forwards_validator_before_rcon_auth(monkeypatch, tmp_path):
    from orchestrator.game_bridge import GameBridge

    sock = Mock()
    events = []
    monkeypatch.setattr("tools.rcon_client.socket.create_connection", lambda *args, **kwargs: sock)
    monkeypatch.setattr(RconClient, "_auth", lambda self, password: events.append("auth"))
    bridge = GameBridge(tmp_path, password="private", socket_validator=lambda candidate: events.append("validated"))
    assert events == ["validated", "auth"]
    bridge.close()


@pytest.mark.parametrize("mode", ["raise", "false", "auth_failure"])
def test_failed_validation_or_auth_closes_socket_without_leaking_auth(monkeypatch, mode):
    sock = Mock()
    monkeypatch.setattr("tools.rcon_client.socket.create_connection", lambda *args, **kwargs: sock)
    auth = Mock(side_effect=RconError("auth failed") if mode == "auth_failure" else None)
    monkeypatch.setattr(RconClient, "_auth", auth)

    def validate(_sock):
        if mode == "raise":
            raise ValueError("invalid socket")
        if mode == "false":
            return False

    with pytest.raises((ValueError, RconError)):
        RconClient("127.0.0.1", 27015, "private", socket_validator=validate)
    if mode != "auth_failure":
        auth.assert_not_called()
    sock.close.assert_called_once()
