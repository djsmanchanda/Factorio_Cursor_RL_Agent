# Path: tests/test_laya_coach_lua.py
# Purpose: Protect the Factorio-side coaching window and its read-only boundary.

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_laya_coach_is_registered_and_exports_bounded_records() -> None:
    control = (ROOT / "factorio_mod" / "control.lua").read_text(encoding="utf-8")
    source = (ROOT / "factorio_mod" / "laya_coach.lua").read_text(encoding="utf-8")
    assert 'require("laya_coach")' in control
    assert "laya_coach.on_init()" in control
    assert "laya_coach.on_configuration_changed()" in control
    assert "on_gui_click" in source
    assert "laya_guidance/" in source
    assert "helpers.write_file" in source
    assert "build_layout_plan" not in source
    assert "execute_construction" not in source
    assert "set_research" not in source
