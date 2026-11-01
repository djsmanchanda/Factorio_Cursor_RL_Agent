# Path: tests/test_linux_deterministic_server.py
# Purpose: Pin the native deterministic-server isolation, copied-save, and loopback-RCON contracts.

import hashlib
import json
import os
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


def test_linux_deterministic_server_can_bound_update_threads_affinity_and_priority() -> None:
    source = MANAGER.read_text(encoding="utf-8")

    assert '--update-threads) UPDATE_THREADS="${2:?missing --update-threads value}"' in source
    assert 'update-runner-threads-count=$UPDATE_THREADS' in source
    assert 'taskset -c "$CPU_AFFINITY" "${launch[@]}"' in source
    assert 'nice -n "$NICE_LEVEL" "${launch[@]}"' in source
    assert '"factorio_update_threads": $threads_json' in source
    assert '"cpu_affinity": $affinity_json' in source
    assert 'runtime-settings.json' in source
    assert 'update threads must be between 1 and 64' in source


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


def test_linux_deterministic_server_fleet_mode_records_a_separate_atomic_token_sidecar() -> None:
    source = MANAGER.read_text(encoding="utf-8")

    # The legacy PID file stays a bare integer for every existing caller.
    assert 'echo $! > "$PID_PATH"' in source
    assert 'START_TOKEN_PATH="$DATA_ROOT/factorio.pid.start-token.json"' in source
    assert 'stat rest' in source and 'fields[19]' in source
    assert 'write_fleet_start_token "$started_pid"' in source
    assert 'cleanup_failed_fleet_start "$started_pid"' in source
    assert "printf '{\"pid\":%s,\"creation_token\":%s,\"boot_id\":\"%s\"}\\n' \"$pid\" \"$token\" \"$boot\"" in source
    assert 'temporary="$START_TOKEN_PATH.tmp.$$"' in source
    assert 'mv -f "$temporary" "$START_TOKEN_PATH"' in source
    assert 'BOOT_ID_PATH="/proc/sys/kernel/random/boot_id"' in source
    assert 'RECORDED_BOOT_ID' in source
    assert 'process_presence' in source
    assert 'remove_fleet_records' in source
    assert 'fleet_server_running' in source
    assert 'refusing to signal it' in source
    assert 'refusing mismatched cleanup' in source
    assert 'refusing to infer lifecycle state' in source
    assert 'refusing to use an unbound fleet record' in source
    # Non-fleet lifecycle behavior never consults the sidecar.
    assert 'if (( FLEET_MODE )); then\n    fleet_server_running' in source
    subprocess.run(["bash", "-n", str(MANAGER)], check=True)


def _run_manager(
    action: str, root: Path, *extra: str, env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(MANAGER), action, "--root", str(root), *extra],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, **(env or {})},
    )


def _proc_start_token(pid: int) -> int:
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    return int(stat[stat.rfind(")") + 2:].split()[19])


def _boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()


def _token_record(
    pid: int, *, token: int | None = None, boot_id: str | None = None,
) -> dict[str, object]:
    return {
        "pid": pid,
        "creation_token": _proc_start_token(pid) if token is None else token,
        "boot_id": _boot_id() if boot_id is None else boot_id,
    }


def _alive(process: subprocess.Popen[bytes]) -> bool:
    return process.poll() is None


def test_fleet_mode_status_fails_closed_without_a_token_record(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir()
    process = subprocess.Popen(["sleep", "60"])
    try:
        (root / "factorio.pid").write_text(f"{process.pid}\n", encoding="utf-8")
        result = _run_manager("status", root, "--fleet-mode")
        assert result.returncode != 0
        assert "missing, corrupt, or has duplicate fields" in result.stderr
        assert _alive(process)
        assert (root / "factorio.pid").exists()
    finally:
        process.kill()
        process.wait()


def test_fleet_mode_refuses_reused_pid_and_mismatched_records(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir()
    process = subprocess.Popen(["sleep", "60"])
    try:
        pid_path = root / "factorio.pid"
        token_path = root / "factorio.pid.start-token.json"
        pid_path.write_text(f"{process.pid}\n", encoding="utf-8")
        token_path.write_text(
            json.dumps(_token_record(process.pid, token=_proc_start_token(process.pid) + 1)) + "\n",
            encoding="utf-8",
        )
        result = _run_manager("stop", root, "--fleet-mode")
        assert result.returncode != 0
        assert "was reused" in result.stderr
        assert _alive(process)
        assert pid_path.exists() and token_path.exists()

        token_path.write_text(
            json.dumps({
                "pid": process.pid + 1,
                "creation_token": _proc_start_token(process.pid),
                "boot_id": _boot_id(),
            }) + "\n",
            encoding="utf-8",
        )
        result = _run_manager("stop", root, "--fleet-mode")
        assert result.returncode != 0
        assert "refusing mismatched cleanup" in result.stderr
        assert _alive(process)
        assert pid_path.exists() and token_path.exists()
    finally:
        process.kill()
        process.wait()


def test_fleet_mode_fails_closed_on_a_boot_mismatch_while_the_pid_is_live(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir()
    process = subprocess.Popen(["sleep", "60"])
    try:
        pid_path = root / "factorio.pid"
        token_path = root / "factorio.pid.start-token.json"
        pid_path.write_text(f"{process.pid}\n", encoding="utf-8")
        token_path.write_text(
            json.dumps(_token_record(
                process.pid, boot_id="00000000-0000-0000-0000-000000000000",
            )) + "\n",
            encoding="utf-8",
        )
        result = _run_manager("status", root, "--fleet-mode")
        assert result.returncode != 0
        assert "indistinguishable from a reboot" in result.stderr
        assert _alive(process)
        assert pid_path.exists() and token_path.exists()
    finally:
        process.kill()
        process.wait()


def test_fleet_mode_rejects_corrupt_or_duplicate_token_records(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir()
    process = subprocess.Popen(["sleep", "60"])
    try:
        pid_path = root / "factorio.pid"
        token_path = root / "factorio.pid.start-token.json"
        pid_path.write_text(f"{process.pid}\n", encoding="utf-8")
        pid = process.pid
        token = _proc_start_token(pid)
        boot = _boot_id()
        variants = [
            f'{{"pid":{pid},"creation_token":{token},"boot_id":"{boot}","pid":{pid}}}',
            f'{{"pid":{pid},"pid":{pid},"creation_token":{token},"boot_id":"{boot}"}}',
            f'{{"pid":{pid},"creation_token":{token},"boot_id":"{boot}","extra":1}}',
            f'{{"pid":{pid},"creation_token":{token},"boot_id":"{boot}"}} trailing',
            f'{{"pid":{pid},"creation_token":{token}}}',
            f'{{"pid":{pid},"creation_token":{token},"boot_id":"not-a-boot-id"}}',
            f'{{"pid":{pid},"creation_token":{token},"boot_id":"{boot}"',
            "",
        ]
        for text in variants:
            token_path.write_text(text + "\n", encoding="utf-8")
            result = _run_manager("status", root, "--fleet-mode")
            assert result.returncode != 0, text
            assert "missing, corrupt, or has duplicate fields" in result.stderr, text
            assert _alive(process), text
            assert pid_path.exists() and token_path.exists(), text
    finally:
        process.kill()
        process.wait()


def _source_manager(root: Path, body: str) -> subprocess.CompletedProcess[str]:
    script = 'source "$1" status --root "$2" --fleet-mode\n' + body
    return subprocess.run(
        ["bash", "-c", script, "_", str(MANAGER), str(root)],
        capture_output=True, text=True, timeout=60,
    )


def test_fleet_pid_state_separates_owned_gone_and_unknown(tmp_path: Path) -> None:
    cases = [
        ("absent", "printf '222\\n'", "gone"),
        ("unknown", "printf '222\\n'", "unknown"),
        ("present", "printf '111\\n'", "owned"),
        ("present", "printf '222\\n'", "gone"),
        ("present", "return 1", "unknown"),
    ]
    for presence, token_body, expected in cases:
        body = (
            f"process_presence() {{ printf '%s\\n' '{presence}'; }}\n"
            f"process_start_token() {{ {token_body}; }}\n"
            "fleet_pid_state 4242 111\n"
            'printf "STATE=%s\\n" "$FLEET_PID_STATE"\n'
        )
        result = _source_manager(tmp_path, body)
        assert result.returncode == 0, result.stderr
        assert f"STATE={expected}" in result.stdout, (presence, token_body, result.stdout)


def test_fleet_pid_state_does_not_read_stat_for_an_absent_pid(tmp_path: Path) -> None:
    marker = tmp_path / "token-called"
    body = (
        "process_presence() { printf 'absent\\n'; }\n"
        f"process_start_token() {{ touch '{marker}'; printf '111\\n'; }}\n"
        "fleet_pid_state 4242 111\n"
        'printf "STATE=%s\\n" "$FLEET_PID_STATE"\n'
    )
    result = _source_manager(tmp_path, body)
    assert result.returncode == 0, result.stderr
    assert "STATE=gone" in result.stdout
    assert not marker.exists()


def test_fleet_stop_unknown_liveness_fails_closed_without_signaling_or_deleting(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir()
    pid_path = root / "factorio.pid"
    token_path = root / "factorio.pid.start-token.json"
    pid_path.write_text("4242\n", encoding="utf-8")
    token_path.write_text(
        json.dumps({"pid": 4242, "creation_token": 111, "boot_id": _boot_id()}) + "\n",
        encoding="utf-8",
    )
    kill_log = root / "kills"
    body = (
        "process_presence() { printf 'unknown\\n'; }\n"
        f"kill() {{ printf '%s\\n' \"$*\" >> '{kill_log}'; }}\n"
        "stop_server\n"
    )
    result = _source_manager(root, body)
    assert result.returncode != 0
    assert "refusing to infer lifecycle state" in result.stderr
    assert pid_path.exists() and token_path.exists()
    assert not kill_log.exists()


def test_fleet_stop_cleans_records_without_signaling_when_identity_is_gone(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir()
    pid_path = root / "factorio.pid"
    token_path = root / "factorio.pid.start-token.json"
    pid_path.write_text("4242\n", encoding="utf-8")
    token_path.write_text("{}\n", encoding="utf-8")
    kill_log = root / "kills"
    body = (
        "fleet_server_running() { RECORDED_TOKEN=111; return 0; }\n"
        "fleet_pid_state() { FLEET_PID_STATE=gone; }\n"
        f"kill() {{ printf '%s\\n' \"$*\" >> '{kill_log}'; }}\n"
        "stop_server\n"
    )
    result = _source_manager(root, body)
    assert result.returncode == 0, result.stderr
    assert "already stopped" in result.stdout
    assert not pid_path.exists() and not token_path.exists()
    assert not kill_log.exists()


def test_fleet_stop_fails_closed_if_liveness_becomes_unknown_after_sigint(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir()
    pid_path = root / "factorio.pid"
    token_path = root / "factorio.pid.start-token.json"
    pid_path.write_text("4242\n", encoding="utf-8")
    token_path.write_text("{}\n", encoding="utf-8")
    counter = root / "count"
    kill_log = root / "kills"
    body = (
        "fleet_server_running() { RECORDED_TOKEN=111; return 0; }\n"
        "fleet_pid_state() {\n"
        f"  n=$(( $(cat '{counter}' 2>/dev/null || printf 0) + 1 ))\n"
        f"  printf '%s\\n' \"$n\" > '{counter}'\n"
        '  if [[ "$n" -eq 1 ]]; then FLEET_PID_STATE=owned; else FLEET_PID_STATE=unknown; fi\n'
        "}\n"
        f"kill() {{ printf '%s\\n' \"$*\" >> '{kill_log}'; }}\n"
        "stop_server\n"
    )
    result = _source_manager(root, body)
    assert result.returncode != 0
    assert "became unverifiable after SIGINT" in result.stderr
    assert pid_path.exists() and token_path.exists()
    assert kill_log.read_text(encoding="utf-8") == "-INT 4242\n"


def test_fleet_status_fails_closed_when_a_live_sidecar_has_no_pid_file(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir()
    process = subprocess.Popen(["sleep", "60"])
    try:
        token_path = root / "factorio.pid.start-token.json"
        token_path.write_text(
            json.dumps(_token_record(process.pid)) + "\n", encoding="utf-8",
        )
        result = _run_manager("status", root, "--fleet-mode")
        assert result.returncode != 0
        assert "names live PID" in result.stderr
        assert "is missing" in result.stderr
        assert token_path.exists()
        assert _alive(process)
    finally:
        process.kill()
        process.wait()


def test_fleet_status_fails_closed_on_a_corrupt_sidecar_without_a_pid_file(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir()
    token_path = root / "factorio.pid.start-token.json"
    token_path.write_text('{"pid":4242}\n', encoding="utf-8")
    result = _run_manager("status", root, "--fleet-mode")
    assert result.returncode != 0
    assert "exists without" in result.stderr
    assert token_path.exists()


def test_fleet_sidecar_without_a_pid_file_cleans_only_a_provably_gone_record(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir()
    token_path = root / "factorio.pid.start-token.json"
    token_path.write_text(
        json.dumps({"pid": 4242, "creation_token": 111, "boot_id": _boot_id()}) + "\n",
        encoding="utf-8",
    )
    body = (
        "process_presence() { printf 'absent\\n'; }\n"
        "if fleet_server_running; then echo RUNNING; else echo STOPPED; fi\n"
    )
    result = _source_manager(root, body)
    assert result.returncode == 0, result.stderr
    assert "STOPPED" in result.stdout
    assert not token_path.exists()


def test_fleet_pid_absent_with_a_sidecar_naming_another_live_pid_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir()
    process = subprocess.Popen(["sleep", "60"])
    try:
        pid_path = root / "factorio.pid"
        token_path = root / "factorio.pid.start-token.json"
        pid_path.write_text("4242\n", encoding="utf-8")
        token_path.write_text(
            json.dumps(_token_record(process.pid)) + "\n", encoding="utf-8",
        )
        body = (
            "process_presence() { if [[ \"$1\" == 4242 ]]; then printf 'absent\\n'; "
            "else printf 'present\\n'; fi; }\n"
            "process_start_token() { printf '111\\n'; }\n"
            "fleet_server_running\n"
        )
        result = _source_manager(root, body)
        assert result.returncode != 0
        assert "refusing mismatched cleanup" in result.stderr
        assert pid_path.exists() and token_path.exists()
        assert _alive(process)
    finally:
        process.kill()
        process.wait()


def test_fleet_start_cleanup_without_a_captured_token_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir()
    pid_path = root / "factorio.pid"
    pid_path.write_text("4242\n", encoding="utf-8")
    kill_log = root / "kills"
    body = (
        "process_presence() { printf 'present\\n'; }\n"
        f"kill() {{ printf '%s\\n' \"$*\" >> '{kill_log}'; }}\n"
        "cleanup_failed_fleet_start 4242\n"
    )
    result = _source_manager(root, body)
    assert result.returncode != 0
    assert "could not establish the identity" in result.stderr
    assert pid_path.exists()
    assert not kill_log.exists()


def test_fleet_start_cleanup_without_a_captured_token_cleans_a_provably_gone_pid(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir()
    pid_path = root / "factorio.pid"
    pid_path.write_text("4242\n", encoding="utf-8")
    kill_log = root / "kills"
    body = (
        "process_presence() { printf 'absent\\n'; }\n"
        f"kill() {{ printf '%s\\n' \"$*\" >> '{kill_log}'; }}\n"
        "cleanup_failed_fleet_start 4242\n"
    )
    result = _source_manager(root, body)
    assert result.returncode != 0
    assert "exited before its creation token" in result.stderr
    assert not pid_path.exists()
    assert not kill_log.exists()


def test_fleet_start_cleanup_reaps_only_an_owned_process_that_then_exits(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir()
    pid_path = root / "factorio.pid"
    pid_path.write_text("4242\n", encoding="utf-8")
    counter = root / "count"
    kill_log = root / "kills"
    body = (
        "FLEET_START_TOKEN=111\n"
        "fleet_pid_state() {\n"
        f"  n=$(( $(cat '{counter}' 2>/dev/null || printf 0) + 1 ))\n"
        f"  printf '%s\\n' \"$n\" > '{counter}'\n"
        '  if [[ "$n" -eq 1 ]]; then FLEET_PID_STATE=owned; else FLEET_PID_STATE=gone; fi\n'
        "}\n"
        "sleep() { :; }\n"
        f"kill() {{ printf '%s\\n' \"$*\" >> '{kill_log}'; }}\n"
        "cleanup_failed_fleet_start 4242\n"
    )
    result = _source_manager(root, body)
    assert result.returncode != 0
    assert "terminated the process we started" in result.stderr
    assert not pid_path.exists()
    assert kill_log.read_text(encoding="utf-8") == "-TERM 4242\n"


def test_fleet_start_cleanup_fails_closed_if_ownership_becomes_unknown(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir()
    pid_path = root / "factorio.pid"
    pid_path.write_text("4242\n", encoding="utf-8")
    counter = root / "count"
    kill_log = root / "kills"
    body = (
        "FLEET_START_TOKEN=111\n"
        "fleet_pid_state() {\n"
        f"  n=$(( $(cat '{counter}' 2>/dev/null || printf 0) + 1 ))\n"
        f"  printf '%s\\n' \"$n\" > '{counter}'\n"
        '  if [[ "$n" -eq 1 ]]; then FLEET_PID_STATE=owned; else FLEET_PID_STATE=unknown; fi\n'
        "}\n"
        "sleep() { :; }\n"
        f"kill() {{ printf '%s\\n' \"$*\" >> '{kill_log}'; }}\n"
        "cleanup_failed_fleet_start 4242\n"
    )
    result = _source_manager(root, body)
    assert result.returncode != 0
    assert "became unverifiable while stopping" in result.stderr
    assert pid_path.exists()
    assert kill_log.read_text(encoding="utf-8") == "-TERM 4242\n"


_DETACHED_HELPER = """
import os
import signal
import sys
import time

pid_path = sys.argv[1]
ready_read, ready_write = os.pipe()
if os.fork() > 0:
    os.close(ready_write)
    if os.read(ready_read, 1) != b'1':
        os._exit(1)
    os._exit(0)
os.close(ready_read)
os.setsid()
if os.fork() > 0:
    os._exit(0)
signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
with open(pid_path, "w", encoding="utf-8") as handle:
    handle.write(str(os.getpid()))
os.write(ready_write, b'1')
os.close(ready_write)
time.sleep(60)
"""


def _detached_interruptible_helper(tmp_path: Path) -> int:
    import sys

    pid_path = tmp_path / "helper.pid"
    subprocess.run([sys.executable, "-c", _DETACHED_HELPER, str(pid_path)], check=True, timeout=10)
    return int(pid_path.read_text(encoding="utf-8"))


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_fleet_mode_accepts_a_matching_record_and_cleans_both_files(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir()
    helper_pid = _detached_interruptible_helper(tmp_path)
    try:
        pid_path = root / "factorio.pid"
        token_path = root / "factorio.pid.start-token.json"
        pid_path.write_text(f"{helper_pid}\n", encoding="utf-8")
        token_path.write_text(
            json.dumps(_token_record(helper_pid)) + "\n",
            encoding="utf-8",
        )
        status = _run_manager("status", root, "--fleet-mode")
        assert status.returncode == 0
        assert f"running isolated deterministic server PID {helper_pid}" in status.stdout

        stop = _run_manager("stop", root, "--fleet-mode")
        assert stop.returncode == 0
        assert "stopped isolated deterministic server" in stop.stdout
        assert not pid_path.exists() and not token_path.exists()
        assert not _pid_alive(helper_pid)
    finally:
        if _pid_alive(helper_pid):
            os.kill(helper_pid, 9)


def test_non_fleet_status_still_uses_only_the_legacy_pid_file(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir()
    process = subprocess.Popen(["sleep", "60"])
    try:
        (root / "factorio.pid").write_text(f"{process.pid}\n", encoding="utf-8")
        result = _run_manager("status", root)
        assert result.returncode == 0
        assert f"running isolated deterministic server PID {process.pid}" in result.stdout
        assert not (root / "factorio.pid.start-token.json").exists()
    finally:
        process.kill()
        process.wait()


def test_non_fleet_status_leaves_a_fleet_sidecar_untouched(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir()
    process = subprocess.Popen(["sleep", "60"])
    try:
        pid_path = root / "factorio.pid"
        token_path = root / "factorio.pid.start-token.json"
        pid_path.write_text(f"{process.pid}\n", encoding="utf-8")
        token_path.write_text(json.dumps(_token_record(process.pid)) + "\n", encoding="utf-8")
        result = _run_manager("status", root)
        assert result.returncode == 0
        assert _alive(process)
        assert pid_path.exists() and token_path.exists()
    finally:
        process.kill()
        process.wait()


_FAKE_FACTORIO = """#!/usr/bin/env python3
import os
import signal
import sys
import time

console = ""
arguments = sys.argv[1:]
for index, argument in enumerate(arguments):
    if argument == "--console-log":
        console = arguments[index + 1]
root = os.path.dirname(os.path.dirname(console))
with open(os.path.join(root, "fake-server.pid"), "w", encoding="utf-8") as handle:
    handle.write(str(os.getpid()))
with open(os.path.join(root, "factorio-current.log"), "a", encoding="utf-8") as log:
    log.write("Starting RCON interface\\n")
signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
while True:
    time.sleep(0.2)
"""


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def test_fleet_start_writes_an_atomic_token_sidecar_and_stop_reaps_it(tmp_path: Path) -> None:
    import shutil

    if shutil.which("ss") is None or shutil.which("setsid") is None:
        import pytest

        pytest.skip("fleet start test requires ss and setsid")

    root = tmp_path / "state"
    (root / "saves").mkdir(parents=True)
    (root / "saves" / "mod_playground.zip").write_bytes(b"save")
    (root / "rcon-password").write_text("secret\n", encoding="utf-8")
    fake = tmp_path / "factorio"
    fake.write_text(_FAKE_FACTORIO, encoding="utf-8")
    fake.chmod(0o755)
    game_port, rcon_port = _free_port(), _free_port()
    while rcon_port == game_port:
        rcon_port = _free_port()

    start = _run_manager(
        "start", root, "--fleet-mode", "--factorio-bin", str(fake),
        "--read-data", str(tmp_path / "read-data"),
        "--game-port", str(game_port), "--rcon-port", str(rcon_port),
    )
    try:
        assert start.returncode == 0, start.stderr
        pid_text = (root / "factorio.pid").read_text(encoding="utf-8")
        assert pid_text == f"{int(pid_text)}\n"
        pid = int(pid_text)
        record = json.loads((root / "factorio.pid.start-token.json").read_text(encoding="utf-8"))
        assert record == {
            "pid": pid,
            "creation_token": _proc_start_token(pid),
            "boot_id": _boot_id(),
        }

        stop = _run_manager("stop", root, "--fleet-mode")
        assert stop.returncode == 0, stop.stderr
        assert not (root / "factorio.pid").exists()
        assert not (root / "factorio.pid.start-token.json").exists()
    finally:
        if (root / "factorio.pid").exists():
            _run_manager("stop", root, "--fleet-mode")


def test_fleet_start_token_write_failure_reaps_only_the_owned_child(tmp_path: Path) -> None:
    import shutil
    import time

    if shutil.which("ss") is None or shutil.which("setsid") is None:
        import pytest

        pytest.skip("fleet start test requires ss and setsid")

    root = tmp_path / "state"
    (root / "saves").mkdir(parents=True)
    (root / "saves" / "mod_playground.zip").write_bytes(b"save")
    (root / "rcon-password").write_text("secret\n", encoding="utf-8")
    # A shim on PATH makes only the sidecar's atomic rename fail, after the
    # child has been spawned.
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    real_mv = shutil.which("mv")
    shim = shim_dir / "mv"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        'case "${@: -1}" in\n'
        '  *factorio.pid.start-token.json*) echo "injected mv failure" >&2; exit 1 ;;\n'
        "esac\n"
        f'exec "{real_mv}" "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    fake = tmp_path / "factorio"
    fake.write_text(_FAKE_FACTORIO, encoding="utf-8")
    fake.chmod(0o755)
    game_port, rcon_port = _free_port(), _free_port()
    while rcon_port == game_port:
        rcon_port = _free_port()

    sentinel = subprocess.Popen(["sleep", "60"])
    try:
        start = _run_manager(
            "start", root, "--fleet-mode", "--factorio-bin", str(fake),
            "--read-data", str(tmp_path / "read-data"),
            "--game-port", str(game_port), "--rcon-port", str(rcon_port),
            env={"PATH": f"{shim_dir}:{os.environ['PATH']}"},
        )
        assert start.returncode != 0
        assert "could not record a trustworthy creation token" in start.stderr
        assert _alive(sentinel)
        assert not (root / "factorio.pid").exists()
        fake_pid_path = root / "fake-server.pid"
        if fake_pid_path.exists():
            fake_pid = int(fake_pid_path.read_text(encoding="utf-8"))
            deadline = time.time() + 5
            while time.time() < deadline and _pid_alive(fake_pid):
                time.sleep(0.05)
            assert not _pid_alive(fake_pid)
    finally:
        sentinel.kill()
        sentinel.wait()
        if (root / "factorio.pid").exists():
            _run_manager("stop", root, "--fleet-mode")
