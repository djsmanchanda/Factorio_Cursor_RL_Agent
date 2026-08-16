# Path: tests/test_linux_training_worker.py
# Purpose: Keep the native Linux worker isolation and direct-path contracts explicit.

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / "scripts" / "manage_linux_training_worker.sh"
EXAMPLE = ROOT / "training-workers-linux.example.json"


def test_training_mods_declare_the_installed_factorio_major_version() -> None:
    for directory in ("factorio_mod", "factorio_training_lab"):
        payload = json.loads((ROOT / directory / "info.json").read_text(encoding="utf-8"))
        assert payload["factorio_version"] == "2.1"


def test_training_fixtures_use_the_factorio_21_minable_guard() -> None:
    source = (ROOT / "factorio_training_lab" / "episode_world.lua").read_text(encoding="utf-8")

    assert "entity.minable_flag, entity.destructible, entity.rotatable = false, false, false" in source
    assert "entity.minable, entity.destructible, entity.rotatable = false, false, false" not in source


def test_linux_worker_uses_isolated_direct_paths_without_windows_bridge() -> None:
    source = MANAGER.read_text(encoding="utf-8")

    assert 'STATE_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/factorio-rl/training"' in source
    assert 'RUNTIME_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/factorio-rl/runtime/factorio-2.1.14"' in source
    assert 'FACTORIO_BIN="${FACTORIO_BIN:-$RUNTIME_ROOT/bin/x64/factorio}"' in source
    assert 'READ_DATA="${READ_DATA:-$RUNTIME_ROOT/data}"' in source
    assert 'write-data=$DATA_ROOT' in source
    assert '"script_output": "$root/script-output"' in source
    assert 'wsl.exe' not in source
    assert '\\\\wsl$' not in source
    assert 'LOCALAPPDATA' not in source


def test_linux_worker_keeps_rcon_loopback_and_generates_a_local_secret() -> None:
    source = MANAGER.read_text(encoding="utf-8")

    assert '--bind "0.0.0.0:$(game_port "$1")"' in source
    assert '--rcon-bind "127.0.0.1:$(rcon_port "$1")"' in source
    assert 'od -An -N32 -tx1 /dev/urandom' in source
    assert 'chmod 600 "$SECRET_PATH"' in source
    assert 'mkdir -p "$MODS_PATH"' in source
    assert 'mkfifo "$STDIN_PATH"' in source
    assert 'tail -f /dev/null > "$STDIN_PATH" &' in source
    assert 'stop_stdin_keeper' in source
    assert ': > "$DATA_ROOT/factorio-current.log"' in source
    assert ': > "$DATA_ROOT/logs/factorio-console.log"' in source
    assert 'grep -q "Starting RCON interface" "$DATA_ROOT/factorio-current.log"' in source
    assert 'rm -rf "$MODS_PATH/factorio_training_lab" "$MODS_PATH/factorio_cursor_rl_agent"' in source


def test_linux_worker_example_requires_local_path_materialization() -> None:
    example = EXAMPLE.read_text(encoding="utf-8")

    assert '"worker_id": "training-linux-01-slot-01"' in example
    assert '"game_port": 35001' in example
    assert 'REPLACE_WITH_HOME' in example
