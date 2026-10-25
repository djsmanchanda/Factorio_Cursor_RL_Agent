# Path: tests/test_laya_coach_area_lua.py
# Purpose: Protect the coach area tool's wiring and its read-only boundary.

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "factorio_mod"


def test_area_tool_prototype_sends_preselection_events() -> None:
    data = (MOD / "data.lua").read_text(encoding="utf-8")
    assert 'type = "selection-tool"' in data
    assert 'name = "laya-coach-area-tool"' in data
    assert "send_pre_selection_events = true" in data


def test_coach_registers_area_events_and_never_mutates_entities() -> None:
    source = (MOD / "laya_coach.lua").read_text(encoding="utf-8")
    for event in (
        "on_player_start_selected_area", "on_player_change_selected_area",
        "on_player_cancel_selected_area", "on_player_selected_area", "on_player_alt_selected_area",
    ):
        assert f"defines.events.{event}" in source
    assert "count_entities_filtered" in source
    assert "MAX_SAMPLE_ENTITIES" in source
    # The coach observes and labels only.
    for mutation in ("create_entity", "mine_entity", ".mine(", "order_deconstruction", "order_upgrade", ".die(", "set_research"):
        assert mutation not in source
