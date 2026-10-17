# Path: tests/test_plastic_chest_reserve.py | Purpose: Protect the pre-advanced-circuit chest contract: the kit's last passive-provider chest is held for plastic, and mall growth fills top/bottom halves before new pairs.
from pathlib import Path

from orchestrator import autonomous_builder as builder
from orchestrator import stage_services
from orchestrator.material_reservations import MaterialReservationLedger


def _ledger(tmp_path: Path, monkeypatch, stock, *, advanced_circuits=False):
    ledger = MaterialReservationLedger(
        tmp_path / "script-output" / "factorio_cursor_rl",
        episode_id="episode-plastic-chest", surface="nauvis", force="player",
    )
    monkeypatch.setattr(builder, "_MATERIAL_RESERVATION_LEDGER", ledger)
    monkeypatch.setattr(builder, "_production_started", lambda _c, _s, _f, item: advanced_circuits and item == "advanced-circuit")
    monkeypatch.setattr(builder, "_transferable_or_available_stock", lambda *_a: stock)
    return ledger


def test_the_last_kit_chest_is_held_for_plastic_against_mall_pairs(tmp_path, monkeypatch):
    # Reset cohort lifecycle-s76604: mall pairs spent the kit, then the iron refinery and plastic waited on each other.
    stock = {"passive-provider-chest": 1}
    ledger = _ledger(tmp_path, monkeypatch, stock)
    builder._reserve_plastic_bootstrap_chest(object(), "nauvis", "player", lambda _m: None)
    ledger.declare("paired_mall_inserter", {"passive-provider-chest": 1}, stock, priority=100)

    assert ledger.projects[builder._PLASTIC_CHEST_PROJECT].reserved == {"passive-provider-chest": 1}
    assert ledger.allocatable_stock(stock, claimant="paired_mall_inserter")["passive-provider-chest"] == 0
    assert ledger.allocatable_stock(stock, claimant=builder._PLASTIC_CHEST_PROJECT)["passive-provider-chest"] == 1


def test_no_chest_is_held_once_advanced_circuits_flow(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path, monkeypatch, {"passive-provider-chest": 5}, advanced_circuits=True)
    builder._reserve_plastic_bootstrap_chest(object(), "nauvis", "player", lambda _m: None)
    assert builder._PLASTIC_CHEST_PROJECT not in ledger.projects


def test_the_packets_full_bill_keeps_the_fences_priority(tmp_path, monkeypatch):
    stock = {"passive-provider-chest": 6, "chemical-plant": 2}
    ledger = _ledger(tmp_path, monkeypatch, stock)
    builder._reserve_plastic_bootstrap_chest(object(), "nauvis", "player", lambda _m: None)
    monkeypatch.setattr(stage_services, "active_material_ledger", lambda: ledger)
    monkeypatch.setattr(stage_services.live_base, "transferable_items", lambda *_a: stock)
    plan = {"phases": [{"actions": [
        {"action_type": "place_ghost", "entity": entity, "position": {"x": x, "y": 0}}
        for x, entity in enumerate(["passive-provider-chest", "passive-provider-chest", "chemical-plant"])
    ]}]}
    # The packet's own submission expands the fenced bill at the default priority 50; the ledger keeps the higher one.
    stage_services.assert_affordable(object(), "nauvis", "player", plan, builder._PLASTIC_CHEST_PROJECT,
                                     lambda _m: None, reserve_project=True)
    project = ledger.projects[builder._PLASTIC_CHEST_PROJECT]
    assert project.required == {"passive-provider-chest": 2, "chemical-plant": 1}
    assert project.priority == builder._STEEL_STARTER_RESERVATION_PRIORITY
    # A runner restart does not shrink the expanded bill back to one chest.
    builder._reserve_plastic_bootstrap_chest(object(), "nauvis", "player", lambda _m: None)
    assert ledger.projects[builder._PLASTIC_CHEST_PROJECT].required == project.required


def test_top_bottom_growth_does_not_wait_for_the_iron_pioneer(monkeypatch):
    monkeypatch.setitem(builder.LINE_RECIPES, "long-handed-inserter", {
        "machine": "assembling-machine-1", "ingredients": ["iron-plate"], "amounts": [1],
        "product_amount": 1, "craft_time": 0.5,
    })
    monkeypatch.setattr(builder, "_STARTUP_METAL_STARTERS_OBSERVED", True)
    monkeypatch.setattr(builder, "_bootstrap_state", lambda _recipe: None)  # iron pioneer not released
    monkeypatch.setattr(builder, "_independent_mall_ready", lambda *_a: False)
    assert builder._dense_bootstrap_mall_enabled(object(), "nauvis", "player")
