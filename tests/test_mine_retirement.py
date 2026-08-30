# Path: tests/test_mine_retirement.py
# Purpose: Keep managed-mine RCON parsing fail-closed on malformed surveys.

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
from orchestrator.mine_retirement import (  # noqa: E402
    _RETIRABLE_ENTITY_NAMES,
    _managed_entities,
)
from orchestrator.extraction_state import ResourceMine  # noqa: E402


class _Client:
    def __init__(self, response: str):
        self.response = response
        self.commands: list[str] = []

    def command(self, command: str) -> str:
        self.commands.append(command)
        return self.response


def test_managed_entity_survey_parses_valid_records() -> None:
    client = _Client("transport-belt,1.5,-2.5;medium-electric-pole,3,4")
    entities = _managed_entities(
        client, "nauvis", "player", ResourceMine((0.5, 0.5), 1),
    )

    assert entities == [
        {"name": "transport-belt", "position": {"x": 1.5, "y": -2.5}},
        {"name": "medium-electric-pole", "position": {"x": 3.0, "y": 4.0}},
    ]
    assert "{{{" not in client.commands[0]
    assert "position=p" in client.commands[0]


@pytest.mark.parametrize("response", ["Error while running command", "bad-record", "belt,nope,2"])
def test_managed_entity_survey_rejects_malformed_records(response: str) -> None:
    with pytest.raises(RuntimeError, match="managed mine survey"):
        _managed_entities(
            _Client(response), "nauvis", "player", ResourceMine((0.5, 0.5), 1),
        )

def test_retirement_survey_can_exclude_shared_infrastructure() -> None:
    client = _Client("electric-mining-drill,4,5")
    entities = _managed_entities(
        client, "nauvis", "player", ResourceMine((0.5, 0.5), 1),
        _RETIRABLE_ENTITY_NAMES,
    )

    assert entities == [{
        "name": "electric-mining-drill",
        "position": {"x": 4.0, "y": 5.0},
    }]
    allowed = client.commands[0].split("allowed=")[1].split(";local out")[0]
    assert "transport-belt" not in allowed
    assert "medium-electric-pole" not in allowed
