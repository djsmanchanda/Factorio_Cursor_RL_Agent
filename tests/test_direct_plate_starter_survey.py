# Path: tests/test_direct_plate_starter_survey.py
# Purpose: Verify direct plate starter recognition and legal small-patch siting.

from __future__ import annotations

from orchestrator import live_base


class _Client:
    def __init__(self, response: str) -> None:
        self.response = response
        self.commands: list[str] = []

    def command(self, command: str) -> str:
        self.commands.append(command)
        return self.response


def test_direct_starter_survey_parses_its_removal_identity() -> None:
    client = _Client("61.5 24.5 north 1")

    starter = live_base.direct_plate_starter(
        client, "nauvis", "player", "copper-plate", "copper-ore", (0.0, 0.0),
    )

    assert starter == live_base.DirectPlateStarter((61.5, 24.5), "north", 1)
    assert "passive-provider-chest" in client.commands[0]
    assert "copper-ore" in client.commands[0]


def test_direct_starter_site_accepts_a_small_patch_but_rejects_mixed_ore() -> None:
    client = _Client("61.5 24.5 north -1")

    site = live_base.direct_plate_starter_site(
        client, "nauvis", "player", "copper-ore", (3.0, -1.0),
    )

    assert site == live_base.DirectPlateStarter((61.5, 24.5), "north", -1)
    lua = client.commands[0]
    assert "math.min(#rs,512)" in lua
    assert "r.name~='copper-ore' then mixed=true" in lua
    assert "can('electric-furnace',fp,nil,1.4)" in lua


def test_direct_starter_surveys_return_none_cleanly() -> None:
    assert live_base.direct_plate_starter(
        _Client("NONE"), "nauvis", "player",
        "iron-plate", "iron-ore", (0.0, 0.0),
    ) is None
    assert live_base.direct_plate_starter_site(
        _Client("NONE"), "nauvis", "player", "iron-ore", (0.0, 0.0),
    ) is None
