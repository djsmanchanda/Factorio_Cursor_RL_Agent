# Path: tests/test_bootstrap_supply.py
# Purpose: Prove reduced-v1 receives one exact requester seed through existing logistics.

from __future__ import annotations

import subprocess

from orchestrator import bootstrap_supply
from orchestrator.bootstrap_profiles import (
    BOOTSTRAP_PROFILES, bootstrap_profile,
)


def test_reduced_profile_declares_exactly_two_requesters() -> None:
    assert BOOTSTRAP_PROFILES == ("reduced-v1", "supplied-v1")
    assert bootstrap_profile("reduced-v1").seed_stock == {
        "requester-chest": 2,
    }
    assert bootstrap_profile("supplied-v1").seed_stock == {}


def test_seed_uses_only_existing_connected_supply_chests(monkeypatch) -> None:
    observations = iter(({}, {"requester-chest": 2}))
    monkeypatch.setattr(
        bootstrap_supply.live_base, "available_items", lambda *_a: next(observations),
    )

    class Client:
        command_text = ""

        def command(self, command: str) -> str:
            self.command_text = command
            return "OK"

    client = Client()
    result = bootstrap_supply.ensure_bootstrap_supply(
        client, "nauvis", "player", {"requester-chest": 2}, (3.0, -1.0),
    )

    assert result.before == {"requester-chest": 0}
    assert result.inserted == {"requester-chest": 2}
    assert "passive-provider-chest" in client.command_text
    assert "storage-chest" in client.command_text
    assert "find_logistic_network_by_position" in client.command_text
    assert "surface.create_entity" not in client.command_text
    assert "count=remaining" in client.command_text


def test_generated_seed_lua_is_syntactically_valid(tmp_path, monkeypatch) -> None:
    observations = iter(({}, {"requester-chest": 2}))
    monkeypatch.setattr(
        bootstrap_supply.live_base, "available_items", lambda *_a: next(observations),
    )

    class Client:
        command_text = ""

        def command(self, command: str) -> str:
            self.command_text = command
            return "OK"

    client = Client()
    bootstrap_supply.ensure_bootstrap_supply(
        client, "nauvis", "player", {"requester-chest": 2}, (3.0, -1.0),
    )
    script = tmp_path / "bootstrap_supply.lua"
    script.write_text(client.command_text.removeprefix("/sc "), encoding="utf-8")

    result = subprocess.run(
        ["luac", "-p", str(script)], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_existing_seed_is_not_topped_up(monkeypatch) -> None:
    monkeypatch.setattr(
        bootstrap_supply.live_base, "available_items",
        lambda *_a: {"requester-chest": 2},
    )

    class Client:
        def command(self, _command: str) -> str:
            raise AssertionError("already-stocked profile must not mutate Factorio")

    result = bootstrap_supply.ensure_bootstrap_supply(
        Client(), "nauvis", "player", {"requester-chest": 2}, (0.0, 0.0),
    )

    assert result.inserted == {}
