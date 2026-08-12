# Path: tests/test_windows_training_worker.py
# Purpose: Keep the optional native-Windows training worker isolated from live WSL runs.

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / "scripts" / "manage_windows_training_worker.ps1"


def test_windows_worker_uses_a_distinct_profile_and_ports() -> None:
    source = MANAGER.read_text(encoding="utf-8")
    assert "Factorio-training-win-01" in source
    assert "[int]$GamePort = 35006" in source
    assert "[int]$RconPort = 28006" in source
    assert "[int]$SlotsPerWorker = 16" in source
    assert 'instance_id = "factorio-training-win-01"' in source
    assert 'surface_prefix = "training/"' in source
    assert 'force_prefix = "training-"' in source


def test_windows_worker_does_not_touch_wsl_profile() -> None:
    source = MANAGER.read_text(encoding="utf-8")
    assert 'Join-Path $RepoRoot "factorio_training_lab\\control.lua"' in source
    assert 'Join-Path $RepoRoot "factorio_mod\\control.lua"' in source
    assert 'manage_wsl_training_worker' not in source


def test_windows_worker_requires_explicit_password_and_emits_controller_slots() -> None:
    source = MANAGER.read_text(encoding="utf-8")
    assert 'worker_id = "training-win-01-slot-{0:D2}"' in source
    assert 'script_output = ((Paths).ScriptOutput)' in source
    assert 'if ([string]::IsNullOrWhiteSpace($RconPassword))' in source
    assert 'FACTORIO_TRAINING_RCON_PASSWORD' in source
    assert '"--rcon-password", $RconPassword' in source
