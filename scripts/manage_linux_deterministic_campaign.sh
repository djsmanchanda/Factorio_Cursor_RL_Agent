#!/usr/bin/env bash
# Path: scripts/manage_linux_deterministic_campaign.sh
# Purpose: Run bounded multi-step lifecycle sequences for deterministic campaigns.

set -euo pipefail

usage() {
  cat <<'EOF'
usage: manage_linux_deterministic_campaign.sh {fresh|cycle|stop|status} [options]

Options:
  --source-save PATH   Required for cycle; replaces only the isolated copied save.
  --root PATH          Isolated deterministic state root.
  --runtime-root PATH  Private Factorio runtime root.
  --gui-mods PATH      Matching Linux GUI mods directory.
  --python PATH        Python interpreter for the runner (default: repository .venv).
  --technology NAME    Research target (default: mining-productivity-4).
  --game-port PORT     Loopback game port (default: 34199).
  --rcon-port PORT     Loopback RCON port (default: 27017).
  --dry-run            Print the exact bounded sequence without executing it.

fresh performs one explicit, verified episode sequence:
  runner stop -> server stop -> deploy -> reset isolated save -> server start
  -> runner start

cycle is retained as a compatibility alias for fresh.

It never modifies the supplied source save. Deployment still updates the
configured Linux GUI mod copy, but does not restart a GUI client.
EOF
}

die() {
  echo "native Linux deterministic campaign: $*" >&2
  exit 1
}

ACTION="${1:-}"
[[ -n "$ACTION" ]] || { usage >&2; exit 2; }
if [[ "$ACTION" == "-h" || "$ACTION" == "--help" ]]; then
  usage
  exit 0
fi
shift || true

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/factorio-rl/deterministic"
RUNTIME_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/factorio-rl/runtime/factorio-2.1.17"
GUI_MODS_PATH="$HOME/.factorio/mods"
PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
SOURCE_SAVE=""
TECHNOLOGY="mining-productivity-4"
EPISODE_ID="episode-$(date -u +%Y%m%dT%H%M%SZ)-${RANDOM}"
GAME_PORT=34199
RCON_PORT=27017
DRY_RUN=false

while (($#)); do
  case "$1" in
    --source-save) SOURCE_SAVE="${2:?missing --source-save value}"; shift 2 ;;
    --root) STATE_ROOT="${2:?missing --root value}"; shift 2 ;;
    --runtime-root) RUNTIME_ROOT="${2:?missing --runtime-root value}"; shift 2 ;;
    --gui-mods) GUI_MODS_PATH="${2:?missing --gui-mods value}"; shift 2 ;;
    --python) PYTHON_BIN="${2:?missing --python value}"; shift 2 ;;
    --technology) TECHNOLOGY="${2:?missing --technology value}"; shift 2 ;;
    --episode-id) EPISODE_ID="${2:?missing --episode-id value}"; shift 2 ;;
    --game-port) GAME_PORT="${2:?missing --game-port value}"; shift 2 ;;
    --rcon-port) RCON_PORT="${2:?missing --rcon-port value}"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

case "$ACTION" in
  fresh|cycle|stop|status) ;;
  *) usage >&2; exit 2 ;;
esac

SERVER_MANAGER="$REPO_ROOT/scripts/manage_linux_deterministic_server.sh"
RUNNER_MANAGER="$REPO_ROOT/scripts/manage_linux_deterministic_runner.sh"
SERVER_OPTIONS=(
  --root "$STATE_ROOT"
  --runtime-root "$RUNTIME_ROOT"
  --gui-mods "$GUI_MODS_PATH"
  --game-port "$GAME_PORT"
  --rcon-port "$RCON_PORT"
)
RUNNER_OPTIONS=(
  --root "$STATE_ROOT"
  --python "$PYTHON_BIN"
  --rcon-port "$RCON_PORT"
  --technology "$TECHNOLOGY"
)

execute() {
  local command="$1"
  shift
  if [[ "$DRY_RUN" == true ]]; then
    printf '%q' "$command"
    printf ' %q' "$@"
    printf '\n'
  else
    "$command" "$@"
  fi
}

start_runner_with_retry() {
  local attempt
  for attempt in 1 2 3; do
    if execute "$RUNNER_MANAGER" start "${RUNNER_OPTIONS[@]}" \
      --episode-manifest "$STATE_ROOT/episode/current.json"; then
      return 0
    fi
    [[ "$DRY_RUN" == true ]] && return 1
    echo "native Linux deterministic campaign: runner start attempt $attempt failed; retrying same episode" >&2
    sleep 3
  done
  die "runner failed to start after three attempts; inspect $STATE_ROOT/logs/autonomous-run-console.log"
}

case "$ACTION" in
  fresh|cycle)
    [[ -n "$SOURCE_SAVE" ]] || die "fresh requires --source-save"
    [[ -f "$SOURCE_SAVE" ]] || die "source save is missing: $SOURCE_SAVE"
    execute "$RUNNER_MANAGER" stop "${RUNNER_OPTIONS[@]}"
    execute "$SERVER_MANAGER" stop "${SERVER_OPTIONS[@]}"
    execute "$SERVER_MANAGER" deploy-if-required "${SERVER_OPTIONS[@]}"
    execute "$SERVER_MANAGER" reset \
      "${SERVER_OPTIONS[@]}" --source-save "$SOURCE_SAVE" \
      --episode-id "$EPISODE_ID" --technology "$TECHNOLOGY"
    execute "$SERVER_MANAGER" start "${SERVER_OPTIONS[@]}"
    start_runner_with_retry
    ;;
  stop)
    execute "$RUNNER_MANAGER" stop "${RUNNER_OPTIONS[@]}"
    execute "$SERVER_MANAGER" stop "${SERVER_OPTIONS[@]}"
    ;;
  status)
    execute "$SERVER_MANAGER" status "${SERVER_OPTIONS[@]}"
    execute "$RUNNER_MANAGER" status "${RUNNER_OPTIONS[@]}"
    ;;
esac
