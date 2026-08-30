# Path: tests/test_landfill_executor_contract.py
# Purpose: Keep the build-plan landfill action aligned with the Lua executor.

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

def test_lua_executor_creates_landfill_only_as_a_tile_ghost() -> None:
    executor = (REPO_ROOT / "factorio_mod" / "layout_executor.lua").read_text(encoding="utf-8")

    assert 'place_tile_ghost = "project_more_ghosts"' in executor
    assert 'name = "tile-ghost"' in executor
    assert 'inner_name = action.tile' in executor
    assert 'landfill_target_is_not_water' in executor
    assert "surface.set_tiles" not in executor
