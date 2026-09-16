#!/usr/bin/env bash
# Path: scripts/manage_linux_deterministic_fleet.sh
# Purpose: Install and control the persistent deterministic checkpoint-fleet service.

set -euo pipefail

usage() {
  cat <<'EOF'
usage: manage_linux_deterministic_fleet.sh {init|install|start|stop|restart|status|uninstall} [options]

Options:
  --server-data PATH   Stable deterministic state root
                       (default: ~/.local/share/factorio-rl/deterministic).
  --source-save PATH   Tracked C0 source save for init (default: repository
                       saves/mod_playground.zip).
  --repo-root PATH     Repository containing the coordinator (default: this repo).
  --poll-interval N    Coordinator polling interval in seconds (default: 5).
  --helper-api-url URL Loopback Operations Console URL exposed to lane helpers
                       (default: http://127.0.0.1:9137).

The user service is the only supported execution path for live fleet lanes. It
passes --execute to the coordinator; direct coordinator status and dry-run
commands never start Factorio. init creates an immutable local C0 bundle and
does not modify the source save or repository saves directory.
EOF
}

die() { echo "native Linux deterministic fleet: $*" >&2; exit 1; }

ACTION="${1:-}"
[[ -n "$ACTION" ]] || { usage >&2; exit 2; }
if [[ "$ACTION" == "-h" || "$ACTION" == "--help" ]]; then usage; exit 0; fi
shift || true

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_DATA="${XDG_DATA_HOME:-$HOME/.local/share}/factorio-rl/deterministic"
SOURCE_SAVE="$REPO_ROOT/saves/mod_playground.zip"
POLL_INTERVAL=5
HELPER_API_URL="http://127.0.0.1:9137"

while (($#)); do
  case "$1" in
    --server-data|--root) SERVER_DATA="${2:?missing path value}"; shift 2 ;;
    --source-save) SOURCE_SAVE="${2:?missing --source-save value}"; shift 2 ;;
    --repo-root) REPO_ROOT="${2:?missing --repo-root value}"; shift 2 ;;
    --poll-interval) POLL_INTERVAL="${2:?missing --poll-interval value}"; shift 2 ;;
    --helper-api-url) HELPER_API_URL="${2:?missing --helper-api-url value}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

REPO_ROOT="$(realpath -m "$REPO_ROOT")"
SERVER_DATA="$(realpath -m "$SERVER_DATA")"
FLEET_ROOT="$SERVER_DATA/checkpoint-fleet"
UNIT="factorio-rl-deterministic-fleet.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_PATH="$UNIT_DIR/$UNIT"
PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="$(command -v python3 || true)"
[[ -n "$PYTHON_BIN" ]] || die "python3 is unavailable"

write_unit() {
  mkdir -p "$UNIT_DIR"
  cat > "$UNIT_PATH" <<EOF
[Unit]
Description=Factorio RL deterministic checkpoint fleet coordinator and commit watcher
After=default.target

[Service]
Type=simple
WorkingDirectory=$REPO_ROOT
ExecStart=$PYTHON_BIN $REPO_ROOT/tools/deterministic_fleet_coordinator.py --server-data $SERVER_DATA --repo-root $REPO_ROOT --helper-api-url $HELPER_API_URL --execute --poll-interval $POLL_INTERVAL watch
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=default.target
EOF
  chmod 600 "$UNIT_PATH"
}

case "$ACTION" in
  init)
    [[ -f "$SOURCE_SAVE" ]] || die "source save is missing: $SOURCE_SAVE"
    exec "$PYTHON_BIN" "$REPO_ROOT/tools/checkpoint_catalog.py" init \
      --server-data "$SERVER_DATA" --source-save "$SOURCE_SAVE" --repo-root "$REPO_ROOT"
    ;;
  install)
    write_unit
    systemctl --user daemon-reload
    systemctl --user enable "$UNIT"
    echo "installed $UNIT at $UNIT_PATH"
    ;;
  start)
    write_unit
    systemctl --user daemon-reload
    systemctl --user enable --now "$UNIT"
    ;;
  stop)
    systemctl --user stop "$UNIT" 2>/dev/null || true
    ;;
  restart)
    write_unit
    systemctl --user daemon-reload
    systemctl --user restart "$UNIT"
    ;;
  status)
    if systemctl --user is-active --quiet "$UNIT"; then
      systemctl --user --no-pager status "$UNIT"
    else
      echo "$UNIT inactive"
      if [[ -f "$FLEET_ROOT/coordinator-heartbeat.json" ]]; then
        exec "$PYTHON_BIN" "$REPO_ROOT/tools/deterministic_fleet_coordinator.py" \
          --server-data "$SERVER_DATA" --repo-root "$REPO_ROOT" status
      fi
      exit 3
    fi
    ;;
  uninstall)
    systemctl --user disable --now "$UNIT" 2>/dev/null || true
    rm -f "$UNIT_PATH"
    systemctl --user daemon-reload
    ;;
  *) usage >&2; exit 2 ;;
esac
