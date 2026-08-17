# Path: tests/test_linux_deterministic_server.py
# Purpose: Pin the native deterministic-server isolation, copied-save, and loopback-RCON contracts.

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / "scripts" / "manage_linux_deterministic_server.sh"


def test_linux_deterministic_server_uses_an_isolated_root_and_copied_save() -> None:
    source = MANAGER.read_text(encoding="utf-8")

    assert 'STATE_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/factorio-rl/deterministic"' in source
    assert 'SAVE_PATH="$DATA_ROOT/saves/mod_playground.zip"' in source
    assert '[[ -n "$SOURCE_SAVE" ]] || die "bootstrap requires --source-save"' in source
    assert 'mkdir -p "$DATA_ROOT/saves"' in source
    assert 'if [[ ! -f "$SAVE_PATH" ]]; then' in source
    assert 'cp -p "$SOURCE_SAVE" "$SAVE_PATH"' in source
    assert 'rm -f "$SOURCE_SAVE"' not in source
    assert 'write-data=$DATA_ROOT' in source


def test_linux_deterministic_server_keeps_game_and_rcon_loopback_with_local_secret() -> None:
    source = MANAGER.read_text(encoding="utf-8")

    assert 'GAME_PORT=34199' in source
    assert 'RCON_PORT=27017' in source
    assert '--bind "127.0.0.1:$GAME_PORT"' in source
    assert '--rcon-bind "127.0.0.1:$RCON_PORT"' in source
    assert 'od -An -N32 -tx1 /dev/urandom' in source
    assert 'chmod 600 "$SECRET_PATH"' in source
    assert 'mkfifo "$STDIN_PATH"' in source
    assert 'tail -f /dev/null > "$STDIN_PATH" &' in source
    assert 'grep -q "Starting RCON interface" "$DATA_ROOT/factorio-current.log"' in source


def test_linux_deterministic_server_deploys_only_a_stopped_isolated_mod_copy() -> None:
    source = MANAGER.read_text(encoding="utf-8")

    assert 'assert_stopped' in source
    assert 'rm -rf "$MODS_PATH/factorio_cursor_rl_agent"' in source
    assert 'cp -a "$REPO_ROOT/factorio_mod" "$MODS_PATH/factorio_cursor_rl_agent"' in source
    assert '"factorio_cursor_rl_agent","enabled":true' in source
    assert '"factorio_training_lab","enabled":true' not in source
