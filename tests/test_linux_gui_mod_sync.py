# Path: tests/test_linux_gui_mod_sync.py
# Purpose: Execute the GUI mod-sync script and prove both runtime mods plus the
# training lab deploy with an identical enabled set while other mods survive.

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYNC = ROOT / "scripts" / "sync_linux_gui_mods.sh"


def test_gui_mod_sync_deploys_and_enables_all_three_project_mods(tmp_path: Path) -> None:
    mods_dir = tmp_path / "mods"
    build_dir = tmp_path / "build"
    mods_dir.mkdir()
    # A pre-existing unrelated mod entry must be preserved and left disabled.
    (mods_dir / "mod-list.json").write_text(
        json.dumps({"mods": [{"name": "unrelated", "enabled": False}]}) + "\n"
    )

    result = subprocess.run(
        ["bash", str(SYNC), "--mods-dir", str(mods_dir), "--build-dir", str(build_dir)],
        check=True, capture_output=True, text=True,
    )
    assert "synchronized" in result.stdout

    names = ("factorio_cursor_rl_agent", "factorio_laya_agent", "factorio_training_lab")
    for name in names:
        assert (mods_dir / name / "info.json").is_file(), name
        assert (mods_dir / name / "control.lua").is_file(), name

    entries = {mod["name"]: mod for mod in json.loads((mods_dir / "mod-list.json").read_text())["mods"]}
    assert all(entries[name]["enabled"] is True for name in names)
    assert entries["unrelated"]["enabled"] is False


def test_gui_mod_sync_built_deterministic_mod_matches_shared_source(tmp_path: Path) -> None:
    mods_dir = tmp_path / "mods"
    build_dir = tmp_path / "build"
    mods_dir.mkdir()

    subprocess.run(
        ["bash", str(SYNC), "--mods-dir", str(mods_dir), "--build-dir", str(build_dir)],
        check=True, capture_output=True, text=True,
    )

    # The deterministic artifact is the shared source verbatim.
    assert (build_dir / "factorio_cursor_rl_agent" / "namespace_override.lua").read_text() == (
        ROOT / "factorio_mod" / "namespace_override.lua"
    ).read_text()
    laya = (build_dir / "factorio_laya_agent" / "namespace_override.lua").read_text()
    assert 'command_prefix = "laya_"' in laya
    assert 'output_root = "factorio_laya_mod"' in laya
