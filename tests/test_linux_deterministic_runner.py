# Path: tests/test_linux_deterministic_runner.py
# Purpose: Pin the native deterministic runner's bounded lifecycle and secret-file contract.

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / "scripts" / "manage_linux_deterministic_runner.sh"


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
    assert 'setsid "$PYTHON_BIN" -u "$REPO_ROOT/tools/autonomous_run.py"' in source
