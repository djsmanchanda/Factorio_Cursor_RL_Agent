# Path: tests/test_wsl_training_worker.py
# Purpose: Keep the WSL worker contract explicit, loopback-only, and credential non-interactive.

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "scripts" / "wsl" / "training_worker.sh"
MANAGER = ROOT / "scripts" / "manage_wsl_training_worker.ps1"
RUNNER = ROOT / "scripts" / "run_wsl_training_batch.ps1"
EXAMPLE = ROOT / "training-workers-wsl.example.json"


def test_wsl_worker_keeps_runtime_native_and_reports_windows_visible() -> None:
    source = SHELL.read_text(encoding="utf-8")

    assert 'WORKER_ROOT="$HOME/factorio-training-01"' in source
    assert 'ln -sfn "$bridge_root/script-output" "$DATA_ROOT/script-output"' in source
    assert 'rm -rf "$DATA_ROOT/mods/factorio_training_lab"' in source
    assert 'rm -rf "$DATA_ROOT/mods/factorio_cursor_rl_agent"' in source
    assert '$repo_root/factorio_mod/control.lua' in source


def test_wsl_worker_is_loopback_only_and_uses_automatic_secret() -> None:
    source = SHELL.read_text(encoding="utf-8")

    assert '--bind "127.0.0.1" --port "$GAME_PORT"' in source
    assert '--rcon-bind "127.0.0.1"' in source
    assert '--rcon-password "$(cat "$SECRET_PATH")"' in source
    assert 'od -An -N32 -tx1 /dev/urandom' in source


def test_windows_helpers_keep_credentials_out_of_commands() -> None:
    manager = MANAGER.read_text(encoding="utf-8")
    runner = RUNNER.read_text(encoding="utf-8")

    assert '--rcon-password' not in manager
    assert 'Protect-SecretFile' in manager
    assert 'Assert-TrainingPortsAvailable' in manager
    assert '--rcon-secret-file $secretFile' in runner
    assert 'FACTORIO_TRAINING_RCON_PASSWORD' not in runner


def test_wsl_worker_example_requires_local_path_materialization() -> None:
    example = EXAMPLE.read_text(encoding="utf-8")

    assert '"host": "127.0.0.1"' in example
    assert '"game_port": 35001' in example
    assert 'REPLACE_WITH_LOCALAPPDATA' in example