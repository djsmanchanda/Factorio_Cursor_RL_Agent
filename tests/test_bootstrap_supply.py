# Path: tests/test_bootstrap_supply.py
# Purpose: Ensure deterministic bootstrap never grants missing construction items.

from __future__ import annotations

import pytest

from orchestrator import bootstrap_supply
from orchestrator.bootstrap_profiles import BOOTSTRAP_PROFILES, bootstrap_profile


class ReadOnlyClient:
    def command(self, _command: str) -> str:
        raise AssertionError("bootstrap supply must not mutate Factorio")


def test_profiles_never_declare_item_grants() -> None:
    assert BOOTSTRAP_PROFILES == ("reduced-v1", "supplied-v1")
    for name in BOOTSTRAP_PROFILES:
        assert bootstrap_profile(name).seed_stock == {}


def test_missing_stock_fails_without_inserting_items(monkeypatch) -> None:
    monkeypatch.setattr(
        bootstrap_supply.live_base, "available_items",
        lambda *_a: {"requester-chest": 1},
    )
    with pytest.raises(
        bootstrap_supply.BootstrapSupplyError,
        match=r"item grants are disabled: requester-chest=1/2 \(missing 1\)",
    ):
        bootstrap_supply.ensure_bootstrap_supply(
            ReadOnlyClient(), "nauvis", "player", {"requester-chest": 2}, (3.0, -1.0),
        )


def test_existing_stock_is_not_topped_up(monkeypatch) -> None:
    monkeypatch.setattr(
        bootstrap_supply.live_base, "available_items",
        lambda *_a: {"requester-chest": 3},
    )
    result = bootstrap_supply.ensure_bootstrap_supply(
        ReadOnlyClient(), "nauvis", "player", {"requester-chest": 2}, (0.0, 0.0),
    )
    assert result.before == {"requester-chest": 3}
    assert result.inserted == {}


def test_empty_requirements_do_not_query_or_mutate_network(monkeypatch) -> None:
    def unexpected_query(*_a):
        raise AssertionError("no stock requirement needs no network query")

    monkeypatch.setattr(bootstrap_supply.live_base, "available_items", unexpected_query)
    result = bootstrap_supply.ensure_bootstrap_supply(
        ReadOnlyClient(), "nauvis", "player", {}, (0.0, 0.0),
    )
    assert result == bootstrap_supply.BootstrapSupplyResult({}, {}, {})
