# Path: tests/test_persistent_intermediates.py
# Purpose: Prove starter reserves bootstrap persistent intermediate producers.

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]

from orchestrator import autonomous_builder as builder  # noqa: E402


@pytest.fixture(autouse=True)
def clear_managed_sources():
    builder.MANAGED_INTERMEDIATE_SOURCES.clear()
    yield
    builder.MANAGED_INTERMEDIATE_SOURCES.clear()


def _plan(item: str, ingredient: str) -> SimpleNamespace:
    return SimpleNamespace(
        spec={
            "ingredients": [ingredient], "amounts": [1],
            "product_amount": 1, "craft_time": 0.5,
        },
        promote_to_line=False,
        production_target=1,
    )



def test_compact_mall_seeds_from_one_craft_not_full_stock_target(monkeypatch):
    """A belt cell must be placeable before its whole reserve is available."""
    spec = {
        "ingredients": ["iron-gear-wheel", "iron-plate"],
        "amounts": [1, 1], "product_amount": 2, "craft_time": 0.5,
    }
    plan = SimpleNamespace(spec=spec, promote_to_line=False, production_target=116)
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_args: {"iron-gear-wheel": 1, "iron-plate": 1},
    )
    monkeypatch.setattr(builder, "_has_producer", lambda *_args: False)

    result = builder._ingredient_sources(
        object(), object(), "nauvis", "player", "transport-belt",
        (0.0, 0.0), lambda _message: None, plan, upgrade_bootstrap=False,
    )

    assert result == {}

def test_stocked_iron_stick_schedules_a_real_producer(monkeypatch):
    calls = []
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_args: {"iron-stick": 20},
    )
    monkeypatch.setattr(builder, "_has_producer", lambda *_args: False)
    monkeypatch.setattr(
        builder, "ensure_produced",
        lambda *args, **kwargs: calls.append((args, kwargs)) or None,
    )

    result = builder._ingredient_sources(
        object(), object(), "nauvis", "player", "medium-electric-pole",
        (0.0, 0.0), lambda _message: None, _plan("medium-electric-pole", "iron-stick"),
        upgrade_bootstrap=False,
    )

    assert result is None
    assert len(calls) == 1
    assert calls[0][0][4] == "iron-stick"
    assert calls[0][1]["upgrade_bootstrap"] is False


def test_steel_line_reuses_the_real_iron_provider_not_starter_storage(monkeypatch):
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_args: {"iron-plate": 100},
    )
    monkeypatch.setattr(builder, "_has_producer", lambda *_args: True)
    calls = []
    monkeypatch.setattr(
        builder, "ensure_produced",
        lambda *args, **kwargs: calls.append((args, kwargs)) or (12.5, 43.5),
    )

    result = builder._ingredient_sources(
        object(), object(), "nauvis", "player", "steel-plate",
        (0.0, 0.0), lambda _message: None, _plan("steel-plate", "iron-plate"),
        upgrade_bootstrap=False,
    )

    assert result == {"iron-plate": (12.5, 43.5)}
    assert calls[0][0][4] == "iron-plate"
    assert calls[0][1]["upgrade_bootstrap"] is True


def test_steel_stage_records_its_output_as_a_persistent_source(monkeypatch):
    monkeypatch.setattr(
        builder, "_iron_capacity_for_fast_belts", lambda *_args: (12, 12),
    )
    monkeypatch.setattr(
        builder, "_ingredient_sources", lambda *_args, **_kwargs: {"iron-plate": (1.5, 2.5)},
    )
    calls = []
    monkeypatch.setattr(
        builder, "build_conversion_stage",
        lambda *args, **kwargs: calls.append((args, kwargs)) or (9.5, 8.5),
    )
    plan = SimpleNamespace(
        existing=None, spec={"machine": "electric-furnace"},
        promote_to_line=False, promoted_count=None, mall_storage_limit=1,
    )

    builder._build_assembled_stage(
        object(), object(), "nauvis", "player", "steel-plate", (0.0, 0.0),
        lambda _message: None, plan, None, upgrade_bootstrap=False,
    )

    assert builder.MANAGED_INTERMEDIATE_SOURCES == {"steel-plate": (9.5, 8.5)}
    assert calls[0][0][6] == (1.5, 2.5)
    assert calls[0][1]["machine_count"] == 1
    assert calls[0][1]["allow_logistic_inputs"] is False


def test_existing_single_steel_furnace_completes_the_starter(monkeypatch):
    monkeypatch.setattr(
        builder, "_ingredient_sources", lambda *_args, **_kwargs: {"iron-plate": (4.5, 5.5)},
    )
    monkeypatch.setattr(
        builder, "_iron_capacity_for_fast_belts", lambda *_args: (12, 12),
    )
    calls = []
    monkeypatch.setattr(
        builder, "build_conversion_stage",
        lambda *args, **kwargs: calls.append((args, kwargs)) or (9.5, 8.5),
    )
    plan = SimpleNamespace(
        existing=SimpleNamespace(machine_count=1),
        spec={"machine": "electric-furnace"}, promote_to_line=False,
        promoted_count=None, mall_storage_limit=1,
    )

    builder._build_assembled_stage(
        object(), object(), "nauvis", "player", "steel-plate", (0.0, 0.0),
        lambda _message: None, plan, None, upgrade_bootstrap=False,
    )

    assert calls == []


def test_one_furnace_steel_starter_uses_the_opening_iron_line(monkeypatch):
    monkeypatch.setattr(
        builder, "_ingredient_sources", lambda *_args, **_kwargs: {"iron-plate": (4.5, 5.5)},
    )
    monkeypatch.setattr(
        builder, "_iron_capacity_for_fast_belts", lambda *_args: (6, 12),
    )
    expansions = []
    monkeypatch.setattr(
        builder, "build_mining_stage",
        lambda *args, **kwargs: expansions.append((args, kwargs)),
    )
    builds = []
    monkeypatch.setattr(
        builder, "build_conversion_stage",
        lambda *_args, **kwargs: builds.append(kwargs) or (9.5, 8.5),
    )
    plan = SimpleNamespace(
        existing=None, spec={"machine": "electric-furnace"},
        promote_to_line=False, promoted_count=None, mall_storage_limit=1,
    )

    builder._build_assembled_stage(
        object(), object(), "nauvis", "player", "steel-plate", (0.0, 0.0),
        lambda _message: None, plan, None, upgrade_bootstrap=False,
    )

    assert expansions == []
    assert builds[0]["machine_count"] == 1


def test_steel_feed_is_continuous_belt_even_for_partial_upgrade(monkeypatch):
    monkeypatch.setattr(
        builder, "_transport_mode", lambda *_args: "logistic",
    )
    monkeypatch.setattr(
        builder, "_direct_single_belt_feed",
        lambda *_args: (8.5, 9.5),
    )
    monkeypatch.setattr(
        builder, "_swap_infinity_chests",
        lambda *_args: pytest.fail("steel fell back to a requester feed"),
    )

    modes, feeds, _preflighted, direct = builder._conversion_feed_plan(
        object(), object(), "nauvis", "player", "steel-plate", {},
        {"iron-plate": (1.5, 2.5)}, 4, "transport-belt", "east",
        lambda _message: None, allow_logistic_inputs=True,
        max_belt_route_tiles=None,
    )

    assert modes == {"iron-plate": "belt"}
    assert feeds == {"iron-plate": (8.5, 9.5)}
    assert direct is True
