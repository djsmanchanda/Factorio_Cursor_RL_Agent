# Path: tests/test_linux_deterministic_server.py
# Purpose: Pin the native deterministic-server isolation, copied-save, and loopback-RCON contracts.

import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / "scripts" / "manage_linux_deterministic_server.sh"


def test_linux_deterministic_server_uses_an_isolated_root_and_copied_save() -> None:
    source = MANAGER.read_text(encoding="utf-8")

    assert 'STATE_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/factorio-rl/deterministic"' in source
    assert 'SAVE_PATH="$DATA_ROOT/saves/mod_playground.zip"' in source
    assert 'REPO_SAVE_PATH="$REPO_ROOT/saves/mod_playground.zip"' in source
    assert '[[ -n "$SOURCE_SAVE" ]] || die "bootstrap requires --source-save"' in source
    assert 'mkdir -p "$DATA_ROOT/saves"' in source
    assert 'if [[ ! -f "$SAVE_PATH" ]]; then' in source
    assert 'cp -p "$SOURCE_SAVE" "$SAVE_PATH"' in source
    assert 'rm -f "$SOURCE_SAVE"' not in source
    assert 'write-data=$DATA_ROOT' in source


def test_linux_deterministic_server_snapshots_the_save_before_start() -> None:
    source = MANAGER.read_text(encoding="utf-8")

    assert 'snapshot_save_to_repository()' in source
    assert 'snapshot_source="$SOURCE_SAVE"' in source
    assert 'snapshot_source="$SAVE_PATH"' in source
    assert 'cp -p "$snapshot_source" "$temporary"' in source
    assert 'cmp -s "$snapshot_source" "$temporary"' in source
    assert 'mv "$temporary" "$REPO_SAVE_PATH"' in source
    assert 'snapshot_save_to_repository' in source.split('start_server()', 1)[1]


def test_linux_deterministic_server_keeps_game_and_rcon_loopback_with_local_secret() -> None:
    source = MANAGER.read_text(encoding="utf-8")

    assert 'GAME_PORT=34199' in source
    assert 'RCON_PORT=27017' in source
    assert '--bind "127.0.0.1:$GAME_PORT"' in source
    assert '--rcon-bind "127.0.0.1:$RCON_PORT"' in source
    assert 'od -An -N32 -tx1 /dev/urandom' in source
    assert 'chmod 600 "$SECRET_PATH"' in source
    assert 'mkfifo "$STDIN_PATH"' in source
    assert 'tail -f /dev/null > "$STDIN_PATH" 2>"$DATA_ROOT/logs/factorio-stdin-keeper.log" &' in source
    assert 'grep -q "Starting RCON interface" "$DATA_ROOT/factorio-current.log"' in source


def test_linux_deterministic_server_deploys_stopped_server_and_gui_mod_copies() -> None:
    source = MANAGER.read_text(encoding="utf-8")

    assert 'assert_stopped' in source
    assert 'GUI_MODS_PATH="$HOME/.factorio/mods"' in source
    assert '--gui-mods) GUI_MODS_PATH="${2:?missing --gui-mods value}"; shift 2 ;;' in source
    assert 'sync_mod_copy "$MODS_PATH" factorio_cursor_rl_agent factorio_mod' in source
    assert 'sync_mod_copy "$MODS_PATH" factorio_training_lab factorio_training_lab' in source
    assert 'scripts/sync_linux_gui_mods.sh" --mods-dir "$GUI_MODS_PATH"' in source
    assert '"factorio_cursor_rl_agent","enabled":true' in source
    assert '"factorio_training_lab","enabled":true' in source


def test_linux_deterministic_server_reset_only_replaces_the_isolated_copy() -> None:
    source = MANAGER.read_text(encoding="utf-8")

    assert 'bootstrap|deploy|deploy-if-required|reset|start|stop|status' in source
    assert 'reset requires --source-save' in source
    assert 'mkdir -p "$DATA_ROOT/saves/backups"' in source
    assert 'cp -p "$SAVE_PATH" "$backup"' in source
    assert 'cp -p "$SOURCE_SAVE" "$temporary"' in source
    assert 'cmp -s "$SOURCE_SAVE" "$temporary"' in source
    assert 'sha256_file "$SOURCE_SAVE")" != "$(sha256_file "$temporary")' in source
    assert 'write_episode_manifest' in source
    assert '"episode_id": "$EPISODE_ID"' in source
    assert '"source_save_sha256": "$source_hash"' in source
    assert '"baseline_world_fingerprint": "sha256:$copy_hash"' in source
    assert 'deterministic_hash="$(tree_hash "$MODS_PATH/factorio_cursor_rl_agent")"' in source
    assert 'training_hash="$(tree_hash "$MODS_PATH/factorio_training_lab")"' in source
    # Manifest hashes must be locale-independent to match the Python validator.
    assert 'LC_ALL=C sort -z' in source
    assert '"initial_game_tick": null' in source
    assert 'deterministic-power-state.json' in source
    assert 'autonomous-priorities.json' in source
    assert 'rm -f "$SOURCE_SAVE"' not in source


def test_linux_deterministic_server_deploys_only_changed_project_mods() -> None:
    source = MANAGER.read_text(encoding="utf-8")

    assert 'deploy-if-required' in source
    assert 'diff -qr "$REPO_ROOT/factorio_mod" "$MODS_PATH/factorio_cursor_rl_agent"' in source
    assert 'diff -qr "$REPO_ROOT/factorio_training_lab" "$MODS_PATH/factorio_training_lab"' in source


def test_reset_writes_a_verified_episode_manifest(tmp_path: Path) -> None:
    source = tmp_path / "source.zip"
    source.write_bytes(b"immutable-source")
    root = tmp_path / "state"

    result = subprocess.run([
        "bash", str(MANAGER), "reset",
        "--root", str(root),
        "--runtime-root", str(tmp_path / "runtime"),
        "--gui-mods", str(tmp_path / "gui-mods"),
        "--source-save", str(source),
        "--episode-id", "episode-test",
        "--technology", "mining-productivity-4",
        "--bootstrap-profile", "supplied-v1",
    ], check=True, capture_output=True, text=True)

    assert "reset isolated deterministic save" in result.stdout
    copied = root / "saves" / "mod_playground.zip"
    manifest = json.loads((root / "episode" / "current.json").read_text())
    assert manifest["episode_id"] == "episode-test"
    assert manifest["bootstrap_profile"] == "supplied-v1"
    assert manifest["target_technology"] == "mining-productivity-4"
    assert manifest["source_save_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest["isolated_save_sha256"] == hashlib.sha256(copied.read_bytes()).hexdigest()
    assert manifest["baseline_world_fingerprint"].startswith("sha256:")
    assert len(manifest["deployed_factorio_mod_sha256"]) == 64
    assert 'rm -f "$SOURCE_SAVE"' not in MANAGER.read_text(encoding="utf-8")
    manager_source = MANAGER.read_text(encoding="utf-8")
    assert 'sync_mod\n  mkdir -p "$DATA_ROOT/saves/backups"' in manager_source
