#!/usr/bin/env bash
# Path: scripts/manage_linux_deterministic_runner.sh
# Purpose: Start, stop, and inspect the isolated deterministic Python runner without Windows process tools.

set -euo pipefail

usage() {
  cat <<'EOF'
usage: manage_linux_deterministic_runner.sh {start|stop|restart|status} [options]

Options:
  --root PATH         Isolated deterministic server state (default: ~/.local/share/factorio-rl/deterministic).
  --python PATH       Python interpreter for autonomous_run.py (default: python3).
  --rcon-port PORT    Loopback RCON port (default: 27017).
  --technology NAME   Research target (default: mining-productivity-4).
  --queue-file PATH   Process this persisted research queue instead of one target.

The runner reads the RCON secret from the isolated server root. It does not
write to the normal Factorio profile or expose the secret in its command line.
EOF
}

die() {
  echo "native Linux deterministic runner: $*" >&2
  exit 1
}

require_port() {
  [[ "$1" =~ ^[0-9]+$ ]] && (( 10#$1 >= 1 && 10#$1 <= 65535 )) \
    || die "RCON port must be an integer between 1 and 65535"
}

ACTION="${1:-}"
[[ -n "$ACTION" ]] || { usage >&2; exit 2; }
if [[ "$ACTION" == "-h" || "$ACTION" == "--help" ]]; then
  usage
  exit 0
fi
shift || true

STATE_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/factorio-rl/deterministic"
PYTHON_BIN="python3"
RCON_PORT=27017
TECHNOLOGY="mining-productivity-4"
QUEUE_FILE=""

while (($#)); do
  case "$1" in
    --root) STATE_ROOT="${2:?missing --root value}"; shift 2 ;;
    --python) PYTHON_BIN="${2:?missing --python value}"; shift 2 ;;
    --rcon-port) RCON_PORT="${2:?missing --rcon-port value}"; shift 2 ;;
    --technology) TECHNOLOGY="${2:?missing --technology value}"; shift 2 ;;
    --queue-file) QUEUE_FILE="${2:?missing --queue-file value}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

case "$ACTION" in
  start|stop|restart|status) ;;
  *) usage >&2; exit 2 ;;
esac
require_port "$RCON_PORT"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRET_PATH="$STATE_ROOT/rcon-password"
SCRIPT_OUTPUT="$STATE_ROOT/script-output"
LOG_PATH="$STATE_ROOT/logs/autonomous-run.log"
PID_PATH="$STATE_ROOT/logs/autonomous-run.pid"
CONSOLE_LOG="$STATE_ROOT/logs/autonomous-run-console.log"

cd "$REPO_ROOT"

runner_pid() {
  "$PYTHON_BIN" - "$PID_PATH" <<'PY'
import sys
from pathlib import Path

from tools.runner_process import running_runner_pid

pid = running_runner_pid(Path(sys.argv[1]))
if pid is not None:
    print(pid)
PY
}

rcon_available() {
  ss -ltnH "sport = :$RCON_PORT" | grep -q .
}

start_runner() {
  [[ -x "$(command -v "$PYTHON_BIN")" ]] || die "Python interpreter is unavailable: $PYTHON_BIN"
  [[ -s "$SECRET_PATH" ]] || die "RCON secret is missing: $SECRET_PATH"
  [[ -d "$SCRIPT_OUTPUT" ]] || die "script-output directory is missing: $SCRIPT_OUTPUT"
  rcon_available || die "RCON is not listening on 127.0.0.1:$RCON_PORT"
  local existing
  existing="$(runner_pid)"
  [[ -z "$existing" ]] || die "runner is already running (PID $existing)"
  mkdir -p "$STATE_ROOT/logs"
  (
    cd "$REPO_ROOT"
    if [[ -n "$QUEUE_FILE" ]]; then
      queue_args=(research-queue --queue-file "$QUEUE_FILE")
    else
      queue_args=(research "$TECHNOLOGY")
    fi
    exec setsid "$PYTHON_BIN" -u "$REPO_ROOT/tools/autonomous_run.py" \
      "${queue_args[@]}" --surface nauvis --force player \
      --rcon-host 127.0.0.1 --rcon-port "$RCON_PORT" \
      --rcon-secret-file "$SECRET_PATH" --script-output "$SCRIPT_OUTPUT" \
      --reference-point 3 -1 --max-iterations 100 --log-file "$LOG_PATH" \
      > "$CONSOLE_LOG" 2>&1 < /dev/null
  ) &
  for _ in $(seq 1 20); do
    local started
    started="$(runner_pid)"
    if [[ -n "$started" ]]; then
      if [[ -n "$QUEUE_FILE" ]]; then
        echo "started native Linux deterministic runner PID $started queue=$QUEUE_FILE"
      else
        echo "started native Linux deterministic runner PID $started technology=$TECHNOLOGY"
      fi
      return
    fi
    sleep 0.1
  done
  die "runner did not publish a PID record; inspect $CONSOLE_LOG"
}

stop_runner() {
  local current
  current="$(runner_pid)"
  if [[ -z "$current" ]]; then
    echo "native Linux deterministic runner is already stopped"
    return
  fi
  kill -TERM "$current"
  for _ in $(seq 1 100); do
    [[ -z "$(runner_pid)" ]] && {
      echo "stopped native Linux deterministic runner"
      return
    }
    sleep 0.1
  done
  die "runner PID $current did not stop after SIGTERM"
}

status_runner() {
  local current
  current="$(runner_pid)"
  if [[ -n "$current" ]]; then
    echo "running native Linux deterministic runner PID $current technology=$TECHNOLOGY"
  else
    echo "native Linux deterministic runner is stopped"
  fi
}

case "$ACTION" in
  start) start_runner ;;
  stop) stop_runner ;;
  restart) stop_runner; start_runner ;;
  status) status_runner ;;
esac
