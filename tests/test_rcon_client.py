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
