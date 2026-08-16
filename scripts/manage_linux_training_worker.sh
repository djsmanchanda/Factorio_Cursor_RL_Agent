#!/usr/bin/env bash
# Path: scripts/manage_linux_training_worker.sh
# Purpose: Manage isolated native-Linux Factorio training workers without touching real-base data.

set -euo pipefail

usage() {
  cat <<'EOF'
usage: manage_linux_training_worker.sh {bootstrap|configure|deploy|start|stop|status} [options]

Options:
  --worker-count N       Number of isolated Factorio runtimes (default: 1, max: 50).
  --slots-per-worker N   Logical training slots per runtime (default: 1, max: 80).
  --stagger-seconds N    Delay between worker actions (default: 2).
  --root PATH            Native worker-state root (default: ~/.local/share/factorio-rl/training).
  --runtime-root PATH    Private headless Factorio runtime (default: ~/.local/share/factorio-rl/runtime/factorio-2.1.14).
  --factorio-bin PATH    Native Factorio executable (defaults below --runtime-root).
  --read-data PATH       Factorio read-data root (defaults below --runtime-root).
  --source-save PATH     Copy this disposable seed save during bootstrap.

Without --source-save, bootstrap creates a fresh worker-local save. It never reads
or modifies ~/.factorio/saves, the deterministic runtime, or historical data/ evidence.
EOF
}

die() {
  echo "native Linux training worker: $*" >&2
  exit 1
}

require_positive_integer() {
  [[ "$2" =~ ^[0-9]+$ ]] && (( 10#$2 >= 1 && 10#$2 <= $3 )) || die "$1 must be between 1 and $3"
}

ACTION="${1:-}"
[[ -n "$ACTION" ]] || { usage >&2; exit 2; }
shift || true

WORKER_COUNT=1
SLOTS_PER_WORKER=1
STAGGER_SECONDS=2
STATE_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/factorio-rl/training"
RUNTIME_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/factorio-rl/runtime/factorio-2.1.14"
FACTORIO_BIN=""
READ_DATA=""
SOURCE_SAVE=""

while (($#)); do
  case "$1" in
    --worker-count) WORKER_COUNT="${2:?missing --worker-count value}"; shift 2 ;;
    --slots-per-worker) SLOTS_PER_WORKER="${2:?missing --slots-per-worker value}"; shift 2 ;;
    --stagger-seconds) STAGGER_SECONDS="${2:?missing --stagger-seconds value}"; shift 2 ;;
    --root) STATE_ROOT="${2:?missing --root value}"; shift 2 ;;
    --runtime-root) RUNTIME_ROOT="${2:?missing --runtime-root value}"; shift 2 ;;
    --factorio-bin) FACTORIO_BIN="${2:?missing --factorio-bin value}"; shift 2 ;;
    --read-data) READ_DATA="${2:?missing --read-data value}"; shift 2 ;;
    --source-save) SOURCE_SAVE="${2:?missing --source-save value}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

FACTORIO_BIN="${FACTORIO_BIN:-$RUNTIME_ROOT/bin/x64/factorio}"
READ_DATA="${READ_DATA:-$RUNTIME_ROOT/data}"

case "$ACTION" in
  bootstrap|configure|deploy|start|stop|status) ;;
  *) usage >&2; exit 2 ;;
esac
require_positive_integer "worker count" "$WORKER_COUNT" 50
require_positive_integer "slots per worker" "$SLOTS_PER_WORKER" 80
[[ "$STAGGER_SECONDS" =~ ^[0-9]+([.][0-9]+)?$ ]] || die "stagger seconds must be non-negative"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_OUTPUT="$REPO_ROOT/training-workers-linux.json"

worker_suffix() { printf '%02d' "$1"; }
worker_root() { printf '%s/%s/worker' "$STATE_ROOT" "$(worker_suffix "$1")"; }
game_port() { printf '%d' "$((35000 + $1))"; }
rcon_port() { printf '%d' "$((28000 + $1))"; }

write_worker_config() {
  local temporary="$CONFIG_OUTPUT.tmp"
  {
    echo '{'
    echo '  "workers": ['
    local index slot first=1 root
    for ((index = 1; index <= WORKER_COUNT; index++)); do
      root="$(worker_root "$index")"
      for ((slot = 1; slot <= SLOTS_PER_WORKER; slot++)); do
        (( first )) || echo ','
        first=0
        cat <<EOF
    {
      "worker_id": "training-linux-$(worker_suffix "$index")-slot-$(worker_suffix "$slot")",
      "instance_id": "factorio-training-linux-$(worker_suffix "$index")",
      "host": "127.0.0.1",
      "game_port": $(game_port "$index"),
      "rcon_port": $(rcon_port "$index"),
      "script_output": "$root/script-output",
      "surface_prefix": "training/",
      "force_prefix": "training-"
    }
EOF
      done
    done
    echo
    echo '  ]'
    echo '}'
  } > "$temporary"
  mv "$temporary" "$CONFIG_OUTPUT"
  echo "wrote ignored native worker config for $WORKER_COUNT runtime(s) and $((WORKER_COUNT * SLOTS_PER_WORKER)) slot(s): $CONFIG_OUTPUT"
}

configure_paths() {
  local index="$1"
  WORKER_ROOT="$(worker_root "$index")"
  DATA_ROOT="$WORKER_ROOT"
  MODS_PATH="$DATA_ROOT/mods"
  SAVE_PATH="$DATA_ROOT/saves/training-01.zip"
  SECRET_PATH="$DATA_ROOT/rcon-password"
  PID_PATH="$DATA_ROOT/factorio.pid"
  STDIN_PATH="$DATA_ROOT/factorio.stdin"
  STDIN_KEEPER_PID_PATH="$DATA_ROOT/factorio-stdin-keeper.pid"
  CONFIG_PATH="$DATA_ROOT/config.ini"
  SERVER_SETTINGS="$DATA_ROOT/server-settings.json"
}

worker_running() {
  [[ -f "$PID_PATH" ]] && kill -0 "$(<"$PID_PATH")" 2>/dev/null
}

stop_stdin_keeper() {
  if [[ -f "$STDIN_KEEPER_PID_PATH" ]] && kill -0 "$(<"$STDIN_KEEPER_PID_PATH")" 2>/dev/null; then
    kill "$(<"$STDIN_KEEPER_PID_PATH")" 2>/dev/null || true
  fi
  rm -f "$STDIN_KEEPER_PID_PATH" "$STDIN_PATH"
}

assert_stopped() {
  worker_running && die "worker $(basename "$(dirname "$WORKER_ROOT")") is already running (PID $(<"$PID_PATH"))"
  rm -f "$PID_PATH"
  stop_stdin_keeper
}

write_worker_files() {
  mkdir -p "$MODS_PATH" "$DATA_ROOT/saves" "$DATA_ROOT/logs" "$DATA_ROOT/script-output"
  cat > "$CONFIG_PATH" <<EOF
[path]
read-data=$READ_DATA
write-data=$DATA_ROOT
EOF
  cat > "$SERVER_SETTINGS" <<EOF
{
  "name": "Factorio RL Linux Training Worker $(basename "$(dirname "$WORKER_ROOT")")",
  "description": "Private isolated native Linux Factorio RL training worker",
  "visibility": {"public": false, "lan": false},
  "game_password": "",
  "require_user_verification": false,
  "auto_pause": false,
  "auto_pause_when_players_connect": false
}
EOF
  cat > "$MODS_PATH/mod-list.json" <<'EOF'
{"mods":[
  {"name":"base","enabled":true},
  {"name":"elevated-rails","enabled":true},
  {"name":"quality","enabled":true},
  {"name":"space-age","enabled":true},
  {"name":"factorio_cursor_rl_agent","enabled":true},
  {"name":"factorio_training_lab","enabled":true}
]}
EOF
}

sync_mods() {
  assert_stopped
  [[ -f "$REPO_ROOT/factorio_training_lab/control.lua" ]] || die "training mod is missing"
  [[ -f "$REPO_ROOT/factorio_mod/control.lua" ]] || die "deterministic shared mod is missing"
  mkdir -p "$MODS_PATH"
  rm -rf "$MODS_PATH/factorio_training_lab" "$MODS_PATH/factorio_cursor_rl_agent"
  cp -a "$REPO_ROOT/factorio_training_lab" "$MODS_PATH/factorio_training_lab"
  cp -a "$REPO_ROOT/factorio_mod" "$MODS_PATH/factorio_cursor_rl_agent"
  write_worker_files
}

ensure_secret() {
  local seed_secret="$(worker_root 1)/rcon-password"
  if [[ ! -s "$SECRET_PATH" ]]; then
    if [[ "$WORKER_ROOT" != "$(worker_root 1)" && -s "$seed_secret" ]]; then
      cp "$seed_secret" "$SECRET_PATH"
    else
      umask 077
      od -An -N32 -tx1 /dev/urandom | tr -d ' \n' > "$SECRET_PATH"
    fi
  fi
  chmod 600 "$SECRET_PATH"
}

bootstrap_worker() {
  configure_paths "$1"
  [[ -x "$FACTORIO_BIN" ]] || die "Factorio executable is missing: $FACTORIO_BIN"
  [[ -d "$READ_DATA/base" ]] || die "Factorio read-data root is invalid: $READ_DATA"
  mkdir -p "$DATA_ROOT"
  sync_mods
  ensure_secret
  if [[ ! -f "$SAVE_PATH" ]]; then
    if [[ -n "$SOURCE_SAVE" ]]; then
      [[ -f "$SOURCE_SAVE" ]] || die "source save is missing: $SOURCE_SAVE"
      cp "$SOURCE_SAVE" "$SAVE_PATH"
    else
      "$FACTORIO_BIN" --config "$CONFIG_PATH" --mod-directory "$MODS_PATH" \
        --create "$SAVE_PATH" --console-log "$DATA_ROOT/logs/create-console.log" \
        >"$DATA_ROOT/logs/create-stdout.log" 2>"$DATA_ROOT/logs/create-stderr.log"
    fi
  fi
  echo "bootstrapped native Linux training worker $(basename "$(dirname "$WORKER_ROOT")") at $DATA_ROOT"
}

port_available() {
  local port="$1"
  ! ss -ltnH "sport = :$port" | grep -q . && ! ss -lunH "sport = :$port" | grep -q .
}

start_worker() {
  configure_paths "$1"
  assert_stopped
  [[ -x "$FACTORIO_BIN" ]] || die "Factorio executable is missing: $FACTORIO_BIN"
  [[ -f "$SAVE_PATH" ]] || die "worker is not bootstrapped; run bootstrap first"
  [[ -f "$SECRET_PATH" ]] || die "worker RCON secret is missing; run bootstrap first"
  port_available "$(game_port "$1")" || die "game port $(game_port "$1") is already in use"
  port_available "$(rcon_port "$1")" || die "RCON port $(rcon_port "$1") is already in use"
  mkdir -p "$DATA_ROOT/logs"
  : > "$DATA_ROOT/factorio-current.log"
  : > "$DATA_ROOT/logs/factorio-console.log"
  mkfifo "$STDIN_PATH"
  tail -f /dev/null > "$STDIN_PATH" &
  echo $! > "$STDIN_KEEPER_PID_PATH"
  setsid "$FACTORIO_BIN" --config "$CONFIG_PATH" --mod-directory "$MODS_PATH" \
    --start-server "$SAVE_PATH" --server-settings "$SERVER_SETTINGS" \
    --bind "0.0.0.0:$(game_port "$1")" --rcon-bind "127.0.0.1:$(rcon_port "$1")" \
    --rcon-password "$(<"$SECRET_PATH")" --console-log "$DATA_ROOT/logs/factorio-console.log" \
    >"$DATA_ROOT/logs/factorio-stdout.log" 2>"$DATA_ROOT/logs/factorio-stderr.log" < "$STDIN_PATH" &
  echo $! > "$PID_PATH"
  for _ in $(seq 1 40); do
    grep -q "Starting RCON interface" "$DATA_ROOT/factorio-current.log" 2>/dev/null && break
    if ! worker_running; then
      stop_stdin_keeper
      die "worker exited; inspect $DATA_ROOT/logs/factorio-stderr.log"
    fi
    sleep 0.5
  done
  if ! grep -q "Starting RCON interface" "$DATA_ROOT/factorio-current.log"; then
    stop_stdin_keeper
    die "worker did not open RCON within 20 seconds"
  fi
  echo "started native Linux training worker $(basename "$(dirname "$WORKER_ROOT")") PID $(<"$PID_PATH") game=0.0.0.0:$(game_port "$1") rcon=127.0.0.1:$(rcon_port "$1")"
}

stop_worker() {
  configure_paths "$1"
  if ! worker_running; then rm -f "$PID_PATH"; echo "worker $(basename "$(dirname "$WORKER_ROOT")") is already stopped"; return; fi
  local pid="$(<"$PID_PATH")"
  kill -INT "$pid"
  for _ in $(seq 1 60); do
    kill -0 "$pid" 2>/dev/null || {
      rm -f "$PID_PATH"
      stop_stdin_keeper
      echo "stopped native Linux training worker $(basename "$(dirname "$WORKER_ROOT")")"
      return
    }
    sleep 0.5
  done
  die "worker PID $pid did not stop after SIGINT"
}

status_worker() {
  configure_paths "$1"
  if worker_running; then
    echo "running native Linux training worker $(basename "$(dirname "$WORKER_ROOT")") PID $(<"$PID_PATH") game=0.0.0.0:$(game_port "$1") rcon=127.0.0.1:$(rcon_port "$1")"
  else
    stop_stdin_keeper
    echo "native Linux training worker $(basename "$(dirname "$WORKER_ROOT")") is stopped"
  fi
}

case "$ACTION" in
  configure) write_worker_config ;;
  bootstrap)
    for ((index = 1; index <= WORKER_COUNT; index++)); do
      bootstrap_worker "$index"
      if (( index < WORKER_COUNT )); then sleep "$STAGGER_SECONDS"; fi
    done
    write_worker_config
    ;;
  deploy)
    for ((index = 1; index <= WORKER_COUNT; index++)); do
      configure_paths "$index"
      [[ -d "$DATA_ROOT" ]] || die "worker $(worker_suffix "$index") is not bootstrapped"
      sync_mods
      if (( index < WORKER_COUNT )); then sleep "$STAGGER_SECONDS"; fi
    done
    ;;
  start)
    for ((index = 1; index <= WORKER_COUNT; index++)); do
      start_worker "$index"
      if (( index < WORKER_COUNT )); then sleep "$STAGGER_SECONDS"; fi
    done
    ;;
  stop) for ((index = 1; index <= WORKER_COUNT; index++)); do stop_worker "$index"; done ;;
  status) for ((index = 1; index <= WORKER_COUNT; index++)); do status_worker "$index"; done ;;
esac
