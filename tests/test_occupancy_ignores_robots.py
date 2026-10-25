# Path: tests/test_occupancy_ignores_robots.py | Purpose: Flying robots are not tile occupants for placement surveys.
from __future__ import annotations

from orchestrator import live_base


class _Client:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def command(self, command: str) -> str:
        self.commands.append(command)
        return ""


def test_occupancy_surveys_skip_flying_robots() -> None:
    """C36: an iron extension was refused on a logistic-robot overhead at survey time."""
    client = _Client()
    live_base.occupied_tiles(client, "nauvis", (0, 0), (4, 4))
    live_base.occupied_tile_owners(client, "nauvis", (0, 0), (4, 4))
    for lua in client.commands:
        assert "e.type~='logistic-robot' and e.type~='construction-robot'" in lua
