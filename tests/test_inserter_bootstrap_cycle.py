# Path: tests/test_inserter_bootstrap_cycle.py | Purpose: Keep support construction out of its own recipe dependency chain.

from types import SimpleNamespace

import pytest

from orchestrator import autonomous_builder as builder
from orchestrator.material_reservations import MaterialReservationLedger


pytestmark = pytest.mark.fast
REFERENCE = (3.0, -1.0)


@pytest.fixture
def bootstrap(monkeypatch, tmp_path):
    # This recipe comes from the live catalog; it is absent from the static seed.
    monkeypatch.setitem(builder.LINE_RECIPES, "long-handed-inserter", {
        "machine": "assembling-machine-1",
        "ingredients": ["inserter", "iron-gear-wheel", "iron-plate"],
        "amounts": [1, 1, 1], "product_amount": 1, "craft_time": 0.5,
    })
    monkeypatch.setitem(builder.LINE_RECIPES, "inserter", {
        **builder.LINE_RECIPES["inserter"], "machine": "assembling-machine-1",
    })
    stock = {
        "assembling-machine-1": 4, "passive-provider-chest": 4,
        "requester-chest": 2, "substation": 2, "inserter": 2,
        "electronic-circuit": 50, "iron-gear-wheel": 50, "iron-plate": 50,
    }
    ledger = MaterialReservationLedger(
        tmp_path, episode_id="inserter-cycle", surface="nauvis", force="player",
    )
    monkeypatch.setattr(builder, "_MATERIAL_RESERVATION_LEDGER", ledger)
    monkeypatch.setattr(builder, "_dense_bootstrap_mall_enabled", lambda *_a: True)
    monkeypatch.setattr(builder, "_independent_mall_ready", lambda *_a: False)
    monkeypatch.setattr(builder, "mall_slot_count", lambda *_a: 4)
    monkeypatch.setattr(builder, "_bootstrap_mall_slot_limit", lambda *_a: 12)
    monkeypatch.setattr(builder, "preview_quad_mall_allocation", lambda *_a: (
        builder.quad_mall_center(REFERENCE), "top",
    ))
    monkeypatch.setattr(builder, "preview_mall_allocation", lambda *_a, **_k: (
        (35, 31), "left",
    ))
    monkeypatch.setattr(builder, "seed_cell_includes_substation", lambda *_a: True)
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: dict(stock))
    monkeypatch.setattr(builder.live_base, "transferable_items", lambda *_a: dict(stock))
    monkeypatch.setattr(builder, "_production_started", lambda *_a: False)
    monkeypatch.setattr(builder, "_material_sources_and_rates", lambda *_a: ({}, {}))
    monkeypatch.setattr(builder, "construction_supply_chain_is_scheduled", lambda *_a: False)
    client = SimpleNamespace(command=lambda *_a: pytest.fail("unexpected RCON call"))
    return client, stock, ledger


def _plan(item):
    return builder._LinePlan(
        existing=None,
        spec={**builder.LINE_RECIPES[item], "machine": "assembling-machine-1"},
        production_target=5, mall_storage_limit=5, fill_provider=False,
        mall_request_multiplier=None, demand=0, saturated=False,
        promoted_count=None, promote_to_line=False, at_size=False,
    )


@pytest.mark.parametrize("recipe,support_stock,expected", [
    ("inserter", 0, False),
    ("iron-gear-wheel", 0, False),
    ("electronic-circuit", 0, False),
    ("long-handed-inserter", 0, False),
    ("transport-belt", 0, True),
    ("inserter", 1, False),
    ("long-handed-inserter", 1, False),
    ("inserter", 2, True),
    ("long-handed-inserter", 2, True),
])
def test_support_recipe_and_its_inputs_use_paired_seed_geometry(
    bootstrap, recipe, support_stock, expected,
):
    client, stock, _ledger = bootstrap
    stock["long-handed-inserter"] = support_stock

    assert builder._quad_bootstrap_mall_enabled(
        client, "nauvis", "player", REFERENCE, recipe,
    ) is expected


def test_regular_seed_is_affordable_without_long_handed_supports(bootstrap):
    client, _stock, _ledger = bootstrap

    assert builder._bootstrap_demand_cell_affordable(
        client, "nauvis", "player", "inserter", 5, REFERENCE,
    ) == (True, {})


def test_regular_then_long_handed_seed_bills_break_the_cycle(bootstrap):
    client, stock, ledger = bootstrap
    # Two regular inserters can fund their paired producer. A dense producer
    # instead asks for two long-handed supports; their paired seed asks for
    # three regular inserters (two for construction and one for its first craft).
    builder._reserve_compact_mall_project(
        client, "nauvis", "player", "inserter", _plan("inserter"),
        5, lambda _message: None, reference_point=REFERENCE,
    )
    regular = ledger.projects[builder._material_project_id("inserter")]
    assert regular.required["inserter"] == 2
    assert "long-handed-inserter" not in regular.required

    # Model the next observation after the regular cell is built and produces
    # enough output for the long-handed seed; no live construction is performed.
    ledger.complete(regular.project_id)
    stock["inserter"] = 3
    builder._reserve_compact_mall_project(
        client, "nauvis", "player", "long-handed-inserter",
        _plan("long-handed-inserter"), 5, lambda _message: None,
        reference_point=REFERENCE,
    )
    long_handed = ledger.projects[builder._material_project_id("long-handed-inserter")]
    assert long_handed.required["inserter"] == 3
    assert "long-handed-inserter" not in long_handed.required


def test_regular_seed_build_matches_the_reserved_geometry(bootstrap, monkeypatch):
    client, _stock, _ledger = bootstrap
    monkeypatch.setattr(builder, "_has_producer", lambda *_a: True)
    built = []
    monkeypatch.setattr(builder, "build_compact_mall_stage", lambda *_a, **kwargs: (
        built.append(kwargs) or (39.5, 31.5)
    ))

    builder._build_assembled_stage(
        client, object(), "nauvis", "player", "inserter", REFERENCE,
        lambda _message: None, _plan("inserter"), None,
        upgrade_bootstrap=False, stock_gate_target=5,
    )

    assert len(built) == 1
    assert built[0]["quad_mall"] is False
