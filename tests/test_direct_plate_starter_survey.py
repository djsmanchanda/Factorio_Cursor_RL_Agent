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
    assert "r.name=='copper-ore' then found=true else return false" in lua
    assert "can('electric-furnace',fp,nil,1.4)" in lua


def test_stone_starter_site_and_identity_require_the_second_drill() -> None:
    response = "54.5 -64.5 north 1 57.5 -67.5"
    survey_client = _Client(response)
    site_client = _Client(response)

    starter = live_base.direct_plate_starter(
        survey_client, "nauvis", "player", "stone-brick", "stone", (0.0, 0.0),
    )
    site = live_base.direct_plate_starter_site(
        site_client, "nauvis", "player", "stone", (0.0, 0.0),
    )

    expected = live_base.DirectPlateStarter(
        (54.5, -64.5), "north", 1, ((57.5, -67.5),),
    )
    assert starter == expected
    assert site == expected
    assert "local two=true" in survey_client.commands[0]
    assert "sdri.direction==sd" in survey_client.commands[0]
    assert "mines_only(sp)" in site_client.commands[0]
    assert "can('electric-mining-drill',sp,sd,1.4)" in site_client.commands[0]


def test_iron_starter_site_and_identity_require_two_complete_lanes() -> None:
    response = "61.5 24.5 north 1 61.5 12.5"
    survey_client = _Client(response)
    site_client = _Client(response)

    starter = live_base.direct_plate_starter(
        survey_client, "nauvis", "player", "iron-plate", "iron-ore", (0.0, 0.0),
    )
    site = live_base.direct_plate_starter_site(
        site_client, "nauvis", "player", "iron-ore", (0.0, 0.0),
    )

    expected = live_base.DirectPlateStarter(
        (61.5, 24.5), "north", 1, ((61.5, 12.5),),
    )
    assert starter == expected
    assert site == expected
    assert "local dual=true" in survey_client.commands[0]
    assert "furnace_ok(sf)" in survey_client.commands[0]
    assert "local dual=true" in site_client.commands[0]
    assert "can('electric-furnace',{cp[1]+3*dx,cp[2]+3*dy},nil,1.4)" in site_client.commands[0]


def test_direct_starter_surveys_return_none_cleanly() -> None:
    assert live_base.direct_plate_starter(
        _Client("NONE"), "nauvis", "player",
        "iron-plate", "iron-ore", (0.0, 0.0),
    ) is None
    assert live_base.direct_plate_starter_site(
        _Client("NONE"), "nauvis", "player", "iron-ore", (0.0, 0.0),
    ) is None
