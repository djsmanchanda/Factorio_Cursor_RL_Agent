# Path: tests/test_linux_deterministic_runner.py
# Purpose: Pin the native deterministic runner's bounded lifecycle and secret-file contract.

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / "scripts" / "manage_linux_deterministic_runner.sh"
RUNNER = ROOT / "tools" / "autonomous_run.py"


def test_linux_deterministic_runner_uses_a_secret_file_and_pid_record() -> None:
    source = MANAGER.read_text(encoding="utf-8")

    assert 'STATE_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/factorio-rl/deterministic"' in source
    assert 'SECRET_PATH="$STATE_ROOT/rcon-password"' in source
    assert 'PID_PATH="$STATE_ROOT/logs/autonomous-run.pid"' in source
    assert '--rcon-secret-file "$SECRET_PATH"' in source
    assert '--rcon-password' not in source
    assert 'running_runner_pid' in source


def test_linux_deterministic_runner_exposes_only_bounded_actions() -> None:
    source = MANAGER.read_text(encoding="utf-8")

    assert 'usage: manage_linux_deterministic_runner.sh {start|stop|restart|status}' in source
    assert 'start|stop|restart|status) ;;' in source
    assert 'kill -TERM "$current"' in source
    assert '"$PYTHON_BIN" -u "$REPO_ROOT/tools/autonomous_run.py"' in source


def test_linux_deterministic_runner_survives_manager_exit() -> None:
    source = MANAGER.read_text(encoding="utf-8")
    assert 'RUNNER_UNIT="factorio-rl-deterministic-runner-${RCON_PORT}.service"' in source
    assert "systemd-run --user --quiet" in source
    assert '--unit="$RUNNER_UNIT" --collect' in source
    assert 'systemctl --user stop "$RUNNER_UNIT"' in source
    assert "service_active" in source


def test_linux_deterministic_runner_supports_persisted_research_queue() -> None:
    source = MANAGER.read_text(encoding="utf-8")

    assert '--queue-file PATH' in source
    assert 'runner_args=(research-queue --queue-file "$QUEUE_FILE")' in source
    assert '"${runner_args[@]}" --surface nauvis --force player' in source


def test_runner_logs_trapped_signal_terminations() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    assert "_raise_termination_signal" in source
    assert 'signal.signal(_signal_number, _raise_termination_signal)' in source
    assert 'getattr(signal, "SIGTERM", None)' in source
    assert 'getattr(signal, "SIGHUP", None)' in source


def test_runner_uses_a_constant_size_liveness_sidecar() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    assert "autonomous-run.heartbeat.json" in source
    assert "_write_runner_heartbeat(heartbeat_path)" in source
    assert "heartbeat_stop.wait(10.0)" in source
    assert "RUN HEARTBEAT pid=" not in source
    assert "libc.prctl(1, signal.SIGTERM)" in source
