# Path: tests/test_wsl_training_worker.py
# Purpose: Keep the indexed WSL-worker network and credential contracts explicit.

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "scripts" / "wsl" / "training_worker.sh"
MANAGER = ROOT / "scripts" / "manage_wsl_training_worker.ps1"
RUNNER = ROOT / "scripts" / "run_wsl_training_batch.ps1"
EXAMPLE = ROOT / "training-workers-wsl.example.json"


def test_wsl_worker_keeps_runtime_native_and_isolated_by_index() -> None:
    source = SHELL.read_text(encoding="utf-8")

    assert 'WORKER_ROOT="$HOME/factorio-training-$WORKER_INDEX"' in source
    assert 'seed_runtime="$HOME/factorio-training-01/runtime/factorio"' in source
    assert 'cp -a "$seed_runtime" "$RUNTIME_ROOT"' in source
    assert 'GAME_PORT=$((35000 + numeric))' in source
    assert 'RCON_PORT=$((28000 + numeric))' in source
    assert 'ln -sfn "$bridge_root/script-output" "$DATA_ROOT/script-output"' in source
    assert 'rm -rf "$DATA_ROOT/mods/factorio_training_lab"' in source
    assert 'rm -rf "$DATA_ROOT/mods/factorio_cursor_rl_agent"' in source
    assert '$repo_root/factorio_mod/control.lua' in source


def test_wsl_worker_exposes_only_game_udp_and_keeps_rcon_loopback() -> None:
    source = SHELL.read_text(encoding="utf-8")

    assert '--bind "0.0.0.0:$GAME_PORT"' in source
    assert '--rcon-bind "127.0.0.1:$RCON_PORT"' in source
    assert '--rcon-port' not in source
    assert '--rcon-password "$(cat "$SECRET_PATH")"' in source
    assert 'od -An -N32 -tx1 /dev/urandom' in source
    assert 'if [[ ! -s "$bridge_root/rcon-password" ]]' in source


def test_windows_helpers_create_four_workers_without_exposing_credentials() -> None:
    manager = MANAGER.read_text(encoding="utf-8")
    runner = RUNNER.read_text(encoding="utf-8")

    assert '[int]$WorkerCount = 4' in manager
    assert 'return 35000 + $Index' in manager
    assert 'return 28000 + $Index' in manager
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