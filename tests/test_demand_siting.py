# Path: tests/test_demand_siting.py
# Purpose: Prove refinery siting weighs output distance to live requester demand, and falls back to the reference point when nothing asks.

from __future__ import annotations

import pytest

from orchestrator import demand_siting, live_base, stage_extraction
from orchestrator.stage_extraction import plan_local_extraction
from tests.test_extraction_separation import _patch_and_rates


class _FakeRcon:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.commands: list[str] = []

    def command(self, text: str) -> str:
        self.commands.append(text)
        return self.reply


def test_demand_is_the_request_weighted_centroid_of_requester_chests() -> None:
    client = _FakeRcon("0.0 0.0 1;10.0 0.0 3")

    assert demand_siting.demand_point(
        client, "nauvis", "player", "iron-plate", (99.0, 99.0),
    ) == (7.5, 0.0)
    assert client.commands[0].startswith("/sc ")
    assert "'iron-plate'" in client.commands[0]


@pytest.mark.parametrize("reply", ["NONE", ""])
def test_demand_falls_back_when_no_requester_asks_for_the_item(reply: str) -> None:
    assert demand_siting.demand_point(
        _FakeRcon(reply), "nauvis", "player", "iron-plate", (3.0, -1.0),
    ) == (3.0, -1.0)


def test_consumer_distance_is_manhattan() -> None:
    assert demand_siting.consumer_distance((1.0, 2.0), (4.0, -2.0)) == 7.0


def test_planner_prefers_the_legal_site_nearer_live_demand(monkeypatch) -> None:
    # Both candidates cost the same ore-side belt. The reference point (0, 0)
    # favours the west site; live demand at (400, 80) favours the east site.
    _patch_and_rates(monkeypatch)
    monkeypatch.setattr(
        demand_siting, "demand_point", lambda *_args: (400.0, 80.0),
    )
    monkeypatch.setattr(
        live_base, "find_clear_areas",
        lambda *args, **_kwargs: {
            anchor: (80.0, 80.0) if anchor[0] < 0 else (200.0, 80.0)
            for anchor in args[2]
        },
    )
    monkeypatch.setattr(
        stage_extraction, "choose_mining_origin",
        lambda *_args: ((10.0, 20.0), 2),
    )
    messages: list[str] = []

    planned = plan_local_extraction(
        _FakeRcon("NONE"), "nauvis", "player", "iron-plate", (0.0, 0.0), 2,
        belt_type="fast-transport-belt", inserter_type="fast-inserter",
        observe=messages.append,
    )

    assert planned.smelter_origin == (201.0, 80.0)
    assert any(
        message.startswith(
            "DEMAND: iron-plate refinery output (217.5, 95.5) is 198.0 tiles",
        )
        for message in messages
    )


def test_planner_without_requesters_keeps_the_reference_point_site(monkeypatch) -> None:
    _patch_and_rates(monkeypatch)
    monkeypatch.setattr(
        live_base, "find_clear_areas",
        lambda *args, **_kwargs: {
            anchor: (80.0, 80.0) if anchor[0] < 0 else (200.0, 80.0)
            for anchor in args[2]
        },
    )
    monkeypatch.setattr(
        stage_extraction, "choose_mining_origin",
        lambda *_args: ((10.0, 20.0), 2),
    )

    planned = plan_local_extraction(
        _FakeRcon("NONE"), "nauvis", "player", "iron-plate", (0.0, 0.0), 2,
        belt_type="fast-transport-belt", inserter_type="fast-inserter",
    )

    assert planned.smelter_origin == (81.0, 80.0)


def test_unreadable_demand_falls_back_and_says_why():
    class Broken:
        def command(self, _text):
            return "Cannot execute command. Error: attempt to index nil"
    assert demand_siting.demand_point(Broken(), "nauvis", "player", "iron-plate", (3.0, -1.0)) == (3.0, -1.0)
    assert demand_siting.last_error is not None
