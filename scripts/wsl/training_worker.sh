#!/usr/bin/env bash
# Path: scripts/wsl/training_worker.sh
# Purpose: Provision and manage one indexed, unprivileged WSL-native Factorio training worker.

set -euo pipefail

configure_worker() {
  local index="$1"
  [[ "$index" =~ ^[0-9]{2}$ ]] || die "worker index must be a two-digit number"
  local numeric=$((10#$index))
  (( numeric >= 1 && numeric <= 20 )) || die "worker index must be between 01 and 20"
  WORKER_INDEX="$index"
  WORKER_ROOT="$HOME/factorio-training-$WORKER_INDEX"
  RUNTIME_ROOT="$WORKER_ROOT/runtime/factorio"
  DATA_ROOT="$WORKER_ROOT/worker"
  SECRET_PATH="$WORKER_ROOT/rcon-password"
  PID_PATH="$WORKER_ROOT/factorio.pid"
  GAME_PORT=$((35000 + numeric))
  RCON_PORT=$((28000 + numeric))
}

die() {
  echo "training worker: $*" >&2
  exit 1
}

require_file() {
  [[ -f "$1" ]] || die "required file is missing: $1"
}

worker_running() {
  [[ -f "$PID_PATH" ]] && kill -0 "$(cat "$PID_PATH")" 2>/dev/null
}

assert_stopped() {
  worker_running && die "worker is already running (PID $(cat "$PID_PATH"))"
  rm -f "$PID_PATH"
}

write_worker_config() {
  cat > "$DATA_ROOT/config.ini" <<EOF
[path]
read-data=$RUNTIME_ROOT/data
write-data=$DATA_ROOT
EOF
  cat > "$DATA_ROOT/server-settings.json" <<EOF
{
  "name": "Factorio RL WSL Training Worker $WORKER_INDEX",
  "description": "Private isolated Factorio RL training worker",
  "visibility": {"public": false, "lan": false},
  "game_password": "",
  "require_user_verification": false,
  "auto_pause": false,
  "auto_pause_when_players_connect": false
}
EOF
  cat > "$DATA_ROOT/mods/mod-list.json" <<'EOF'
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
  local repo_root="$1"
  assert_stopped
  require_file "$repo_root/factorio_training_lab/control.lua"
  require_file "$repo_root/factorio_mod/control.lua"
  rm -rf "$DATA_ROOT/mods/factorio_training_lab"
  rm -rf "$DATA_ROOT/mods/factorio_cursor_rl_agent"
  cp -a "$repo_root/factorio_training_lab" "$DATA_ROOT/mods/"
  cp -a "$repo_root/factorio_mod" "$DATA_ROOT/mods/factorio_cursor_rl_agent"
  write_worker_config
}

configure_bridge() {
  local bridge_root="$1"
  mkdir -p "$bridge_root/script-output"
  if [[ -e "$DATA_ROOT/script-output" && ! -L "$DATA_ROOT/script-output" ]]; then
    die "script-output exists but is not the managed bridge: $DATA_ROOT/script-output"
  fi
  ln -sfn "$bridge_root/script-output" "$DATA_ROOT/script-output"
}

ensure_secret() {
  local seed_secret="$HOME/factorio-training-01/rcon-password"
  if [[ "$WORKER_INDEX" != "01" && -s "$seed_secret" ]]; then
    cp "$seed_secret" "$SECRET_PATH"
  elif [[ ! -s "$SECRET_PATH" ]]; then
    umask 077
    od -An -N32 -tx1 /dev/urandom | tr -d ' \n' > "$SECRET_PATH"
  fi
  chmod 600 "$SECRET_PATH"
}

bootstrap() {
  local archive="$1" source_save="$2" repo_root="$3" bridge_root="$4"
  require_file "$source_save"
  mkdir -p "$WORKER_ROOT" "$DATA_ROOT/mods" "$DATA_ROOT/saves" "$DATA_ROOT/logs"
  if [[ ! -x "$RUNTIME_ROOT/bin/x64/factorio" ]]; then
    local seed_runtime="$HOME/factorio-training-01/runtime/factorio"
    mkdir -p "$(dirname "$RUNTIME_ROOT")"
    if [[ -x "$seed_runtime/bin/x64/factorio" ]]; then
      cp -a "$seed_runtime" "$RUNTIME_ROOT"
    else
      require_file "$archive"
      local staging
      staging="$(mktemp -d)"
      trap 'rm -rf "$staging"' RETURN
      tar -xJf "$archive" -C "$staging"
      require_file "$staging/factorio/bin/x64/factorio"
      rm -rf "$RUNTIME_ROOT"
      mv "$staging/factorio" "$RUNTIME_ROOT"
      trap - RETURN
    fi
  fi
  if [[ ! -f "$DATA_ROOT/saves/training-01.zip" ]]; then cp "$source_save" "$DATA_ROOT/saves/training-01.zip"; fi
  configure_bridge "$bridge_root"
  ensure_secret
  sync_mods "$repo_root"
  echo "bootstrapped WSL training worker $WORKER_INDEX at $WORKER_ROOT"
}

start_worker() {
  assert_stopped
  require_file "$RUNTIME_ROOT/bin/x64/factorio"
  require_file "$DATA_ROOT/saves/training-01.zip"
  require_file "$SECRET_PATH"
  mkdir -p "$DATA_ROOT/logs"
  setsid "$RUNTIME_ROOT/bin/x64/factorio" \
    --config "$DATA_ROOT/config.ini" \
    --mod-directory "$DATA_ROOT/mods" \
    --start-server "$DATA_ROOT/saves/training-01.zip" \
    --server-settings "$DATA_ROOT/server-settings.json" \
    --bind "0.0.0.0:$GAME_PORT" \
    --rcon-bind "127.0.0.1:$RCON_PORT" \
    --rcon-password "$(cat "$SECRET_PATH")" \
    --console-log "$DATA_ROOT/logs/factorio-console.log" \
    >"$DATA_ROOT/logs/factorio-stdout.log" 2>"$DATA_ROOT/logs/factorio-stderr.log" < /dev/null &
  echo $! > "$PID_PATH"
  for _ in $(seq 1 40); do
    grep -q "Starting RCON interface" "$DATA_ROOT/factorio-current.log" 2>/dev/null && break
    worker_running || die "worker exited; inspect $DATA_ROOT/logs/factorio-stderr.log"
    sleep 0.5
  done
  grep -q "Starting RCON interface" "$DATA_ROOT/factorio-current.log" || die "worker did not open RCON within 20 seconds"
  echo "started WSL training worker $WORKER_INDEX PID $(cat "$PID_PATH") game=$(hostname -I | awk '{print $1}'):$GAME_PORT rcon=127.0.0.1:$RCON_PORT"
}

stop_worker() {
  if ! worker_running; then rm -f "$PID_PATH"; echo "worker $WORKER_INDEX is already stopped"; return; fi
  local pid
  pid="$(cat "$PID_PATH")"
  kill -INT "$pid"
  for _ in $(seq 1 60); do
    kill -0 "$pid" 2>/dev/null || { rm -f "$PID_PATH"; echo "stopped WSL training worker $WORKER_INDEX"; return; }
    sleep 0.5
  done
  die "worker PID $pid did not stop after SIGINT"
}

status_worker() {
  if worker_running; then
    echo "running worker $WORKER_INDEX PID $(cat "$PID_PATH") game=$(hostname -I | awk '{print $1}'):$GAME_PORT rcon=127.0.0.1:$RCON_PORT"
  else
    echo "worker $WORKER_INDEX is stopped"
  fi
}

action="${1:-}"
index="${2:-}"
shift 2 || true
configure_worker "$index"
case "$action" in
  bootstrap) bootstrap "${1:?archive required}" "${2:?save required}" "${3:?repo required}" "${4:?bridge required}" ;;
  deploy) sync_mods "${1:?repo required}" ;;
  start) start_worker ;;
  stop) stop_worker ;;
  status) status_worker ;;
  *) die "usage: $0 {bootstrap|deploy|start|stop|status} <worker-index> [arguments]" ;;
esac
