# Path: tests/test_factorio_runtime_upgrade.py
# Purpose: Protect runtime upgrades, explicit old-runtime replay and operator settings.

import configparser
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/factorio_runtime_config.sh"
PWSH = os.environ.get("FACTORIO_PWSH_TEST_BIN") or shutil.which("pwsh")


@pytest.mark.parametrize("version", ["2.1.21", "2.1.20"])
def test_runtime_config_moves_both_paths_and_preserves_other_settings(tmp_path, version):
    config = tmp_path / "config.ini"
    config.write_text(
        "; operator settings\n[path]\nread-data=/old/data\nwrite-data=/old/world\n"
        "cache-data=/custom/cache\n[network]\nmax-heartbeats-per-second=120\n"
        "[other]\nread-data=unrelated-value\n"
    )
    runtime = tmp_path / f"factorio-{version}" / "data"
    state = tmp_path / "world with spaces"
    subprocess.run([
        "bash", "-c", 'source "$1"; write_factorio_runtime_config "$2" "$3" "$4"',
        "runtime-config", str(HELPER), str(config), str(runtime), str(state),
    ], check=True, capture_output=True, text=True)

    result = configparser.ConfigParser()
    result.read(config)
    assert result["path"]["read-data"] == str(runtime)
    assert result["path"]["write-data"] == str(state)
    assert result["path"]["cache-data"] == "/custom/cache"
    assert result["network"]["max-heartbeats-per-second"] == "120"
    assert result["other"]["read-data"] == "unrelated-value"
    assert config.read_text().startswith("; operator settings\n")


@pytest.mark.parametrize("manager_name,training", [
    ("manage_linux_deterministic_server.sh", False),
    ("manage_linux_training_worker.sh", True),
])
def test_stopped_mod_deployment_preserves_operator_settings(tmp_path, manager_name, training):
    repo = tmp_path / "repository"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    for name in (manager_name, "factorio_runtime_config.sh", "sync_linux_gui_mods.sh"):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    for name in ("factorio_mod", "factorio_training_lab"):
        mod = repo / name
        mod.mkdir()
        (mod / "control.lua").write_text("-- test fixture\n")
        shutil.copy2(ROOT / name / "info.json", mod / "info.json")
    root = tmp_path / "state"
    state = root / "01/worker" if training else root
    state.mkdir(parents=True)
    settings = state / "server-settings.json"
    settings.write_text('{"name":"custom world","autosave_interval":7}\n')
    config = state / "config.ini"
    config.write_text("[path]\nread-data=/old/data\nwrite-data=/old/state\n[network]\nmax-heartbeats-per-second=120\n")
    command = ["bash", str(scripts / manager_name), "deploy", "--root", str(root),
               "--runtime-root", str(tmp_path / "factorio-2.1.21"), "--gui-mods", str(tmp_path / "gui-mods")]
    if training:
        command += ["--worker-count", "1", "--stagger-seconds", "0"]
    subprocess.run(command, check=True, capture_output=True, text=True)

    assert settings.read_text() == '{"name":"custom world","autosave_interval":7}\n'
    result = configparser.ConfigParser()
    result.read(config)
    assert result["path"]["read-data"] == str(tmp_path / "factorio-2.1.21/data")
    assert result["path"]["write-data"] == str(state)
    assert result["network"]["max-heartbeats-per-second"] == "120"
    mods = json.loads((state / "mods/mod-list.json").read_text())["mods"]
    assert {mod["name"] for mod in mods if mod["enabled"]} >= {
        "base", "elevated-rails", "quality", "recycler", "space-age",
        "factorio_cursor_rl_agent", "factorio_training_lab",
    }


def test_dashboard_server_lifecycle_forwards_selected_runtime(monkeypatch):
    from types import SimpleNamespace
    from tools import dashboard_runtime

    manager = object.__new__(dashboard_runtime.OperationManager)
    manager.config = SimpleNamespace(
        server_manager=Path("/native/manage-server"), server_data=Path("/native/world"),
        game_port=34199, rcon_port=27017, runtime_root=Path("/native/factorio-2.1.20"),
    )
    commands = []
    manager._run_checked = commands.append
    monkeypatch.setattr(Path, "is_file", lambda _: True)
    monkeypatch.setattr(dashboard_runtime.os, "access", lambda *_: True)
    manager._run_native_server_manager("start")
    assert commands[0][-2:] == ["--runtime-root", "/native/factorio-2.1.20"]


def test_wsl_config_upgrade_preserves_operator_settings(tmp_path):
    state = tmp_path / "worker"
    (state / "mods").mkdir(parents=True)
    config = state / "config.ini"
    config.write_text("[path]\nread-data=/old/data\nwrite-data=/old/world\ncache-data=/cache\n[network]\nmax-heartbeats-per-second=120\n")
    settings = state / "server-settings.json"
    settings.write_text('{"name":"custom WSL worker"}\n')
    source = (ROOT / "scripts/wsl/training_worker.sh").read_text().split('\naction="${1:-}"')[0]
    source = source.replace('source "$(dirname "${BASH_SOURCE[0]}")/../factorio_runtime_config.sh"', 'source "$1"')
    source += '\nDATA_ROOT="$2"; RUNTIME_ROOT="$3"; WORKER_INDEX=01; write_worker_config\n'
    runtime = tmp_path / "factorio-2.1.21"
    subprocess.run(["bash", "-c", source, "wsl-config-test", str(HELPER), str(state), str(runtime)],
                   check=True, capture_output=True, text=True)
    result = configparser.ConfigParser()
    result.read(config)
    assert result["path"]["read-data"] == str(runtime / "data")
    assert result["path"]["write-data"] == str(state)
    assert result["path"]["cache-data"] == "/cache"
    assert result["network"]["max-heartbeats-per-second"] == "120"
    assert settings.read_text() == '{"name":"custom WSL worker"}\n'


@pytest.mark.skipif(PWSH is None, reason="PowerShell is unavailable")
def test_windows_config_upgrade_preserves_operator_settings(tmp_path):
    state = tmp_path / "worker"
    state.mkdir()
    config = state / "config.ini"
    config.write_text("; operator settings\n[path]\nread-data=/old/data\nwrite-data=/old/world\ncache-data=/cache\n[network]\nmax-heartbeats-per-second=120\n[other]\nread-data=unrelated-value\n")
    source = (ROOT / "scripts/manage_windows_training_worker.ps1").read_text()
    functions = source[source.index("function Write-Utf8NoBom"):source.index("switch ($Action)")]
    harness = tmp_path / "windows-config-test.ps1"
    harness.write_text('param([string]$State, [string]$Runtime)\n$ErrorActionPreference="Stop"\n' + functions
                       + '\n$ServerData=$State; $ReadData=$Runtime; Write-RuntimeConfig\n')
    runtime = tmp_path / "factorio-2.1.21/data"
    subprocess.run([PWSH, "-NoProfile", "-File", str(harness), "-State", str(state), "-Runtime", str(runtime)],
                   check=True, capture_output=True, text=True)
    result = configparser.ConfigParser()
    result.read(config)
    assert result["path"]["read-data"] == str(runtime)
    assert result["path"]["write-data"] == str(state)
    assert result["path"]["cache-data"] == "/cache"
    assert result["network"]["max-heartbeats-per-second"] == "120"
    assert result["other"]["read-data"] == "unrelated-value"
    assert config.read_text().startswith("; operator settings\n")


def test_saved_world_freezes_a_self_contained_manager(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from tools import run_laya_saved_world

    source = tmp_path / "source.zip"
    source.write_bytes(b"immutable test input")
    mods = tmp_path / "mods"
    for name in ("factorio_cursor_rl_agent", "factorio_training_lab"):
        directory = mods / name
        directory.mkdir(parents=True)
        (directory / "control.lua").write_text("-- test mod\n")
    root = tmp_path / "disposable-host"
    monkeypatch.setattr(run_laya_saved_world, "owned_root", lambda _: root)

    def lifecycle(host, action):
        # Exercise dependency loading from the actual frozen manager without
        # launching Factorio or using an installed server/root.
        if action == "bootstrap":
            subprocess.run([
                "bash", str(host / "frozen/scripts/manage_linux_deterministic_server.sh"),
                "status", "--root", str(host / "server"), "--fleet-mode",
            ], check=True, capture_output=True, text=True)
            save = host / "server/saves/mod_playground.zip"
            save.parent.mkdir(parents=True)
            shutil.copy2(host / "baseline.zip", save)
        return "offline lifecycle fixture"

    monkeypatch.setattr(run_laya_saved_world, "manager", lifecycle)
    result = run_laya_saved_world.prepare(SimpleNamespace(
        root=root, source_save=source, mods_source=mods, runtime="disposable_mall",
        game_port=34198, rcon_port=27018, mall={"version": "test-mall"},
    ))
    assert result["status"] == "mall_host_prepared"
    assert (root / "frozen/scripts/factorio_runtime_config.sh").read_bytes() == HELPER.read_bytes()
    assert source.read_bytes() == b"immutable test input"
