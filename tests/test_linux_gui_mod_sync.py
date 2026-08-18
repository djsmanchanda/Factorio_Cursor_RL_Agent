# Path: tests/test_linux_gui_mod_sync.py
# Purpose: Pin the GUI profile's matching two-mod deployment contract.

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SYNC = ROOT / "scripts" / "sync_linux_gui_mods.sh"


def test_gui_mod_sync_deploys_and_enables_both_project_mods() -> None:
    source = SYNC.read_text(encoding="utf-8")

    assert 'MOD_NAMES=(factorio_cursor_rl_agent factorio_training_lab)' in source
    assert 'MOD_SOURCES=(factorio_mod factorio_training_lab)' in source
    assert 'rm -rf "$target"' in source
    assert 'cp -a "$REPO_ROOT/${MOD_SOURCES[$index]}" "$target"' in source
    assert 'enabled = {"factorio_cursor_rl_agent", "factorio_training_lab"}' in source
    assert 'temporary.replace(path)' in source
