# Path: tests/test_persistent_intermediates.py
# Purpose: Prove starter reserves bootstrap persistent intermediate producers.

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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


def test_steel_line_can_use_a_stocked_iron_plate_chest(monkeypatch):
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_args: {"iron-plate": 100},
    )
    monkeypatch.setattr(builder, "_has_producer", lambda *_args: True)
    monkeypatch.setattr(
        builder.live_base, "nearest_container",
        lambda *_args, **_kwargs: (12.5, 4.5),
    )

    result = builder._ingredient_sources(
        object(), object(), "nauvis", "player", "steel-plate",
        (0.0, 0.0), lambda _message: None, _plan("steel-plate", "iron-plate"),
        upgrade_bootstrap=False,
    )

    assert result == {"iron-plate": (12.5, 4.5)}


def test_steel_stage_records_its_output_as_a_persistent_source(monkeypatch):
    monkeypatch.setattr(
        builder, "_ingredient_sources", lambda *_args, **_kwargs: {"iron-plate": (1.5, 2.5)},
    )
    monkeypatch.setattr(
        builder, "build_conversion_stage", lambda *_args, **_kwargs: (9.5, 8.5),
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