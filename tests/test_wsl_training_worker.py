# Path: tests/test_wsl_training_worker.py
# Purpose: Keep the indexed WSL-worker network and credential contracts explicit.

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "scripts" / "wsl" / "training_worker.sh"
MANAGER = ROOT / "scripts" / "manage_wsl_training_worker.ps1"
RUNNER = ROOT / "scripts" / "run_wsl_training_batch.ps1"
CAPACITY = ROOT / "scripts" / "benchmark_wsl_training_slots.ps1"
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
    assert 'seed_secret="$HOME/factorio-training-01/rcon-password"' in source
    assert 'if [[ "$WORKER_INDEX" != "01" && -s "$seed_secret" ]]' in source


def test_windows_helpers_create_four_slots_on_one_runtime_without_exposing_credentials() -> None:
    manager = MANAGER.read_text(encoding="utf-8")
    runner = RUNNER.read_text(encoding="utf-8")

    assert '[int]$WorkerCount = 1' in manager
    assert '[int]$SlotsPerWorker = 4' in manager
    assert 'training-wsl-$(WorkerSuffix $index)-slot-$(WorkerSuffix $slot)' in manager
    assert 'return 35000 + $Index' in manager
    assert 'return 28000 + $Index' in manager
    assert '--rcon-password' not in manager
    assert 'Protect-SecretFile' not in manager
    assert 'Assert-TrainingPortsAvailable' in manager
    assert 'factorio-training-01/rcon-password' in runner
    assert '\\wsl$' in runner
    assert '--rcon-secret-file $secretFile' in runner
    assert 'FACTORIO_TRAINING_RCON_PASSWORD' not in runner


def test_wsl_worker_example_requires_local_path_materialization() -> None:
    example = EXAMPLE.read_text(encoding="utf-8")

    assert '"host": "127.0.0.1"' in example
    assert '"game_port": 35001' in example
    assert 'REPLACE_WITH_LOCALAPPDATA' in example

def test_capacity_probe_scales_slots_with_explicit_resource_limits() -> None:
    source = CAPACITY.read_text(encoding="utf-8")

    assert '[int]$MaximumSlots = 20' in source
    assert '[int]$StartSlots = 4' in source
    assert '[double]$MinimumCompletionRate = 0.75' in source
    assert '[int]$MaximumCpuPercent = 90' in source
    assert '[int]$MinimumAvailableMemoryMB = 4096' in source
    assert '-Action configure -WorkerCount 1 -SlotsPerWorker $slots' in source
    assert '--count $slots --attempts-per-scenario $AttemptsPerStage' in source
    assert r'data\training-capacity\slots-' in source
    assert '--database (Join-Path $CapacityDirectory "experience.db")' in source
    assert '$record.elapsed_seconds -gt ($baselineSeconds * $MaximumSlowdown)' in source