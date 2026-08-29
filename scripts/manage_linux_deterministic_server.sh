#!/usr/bin/env bash
# Path: scripts/manage_linux_deterministic_server.sh
# Purpose: Manage an isolated native-Linux deterministic Factorio server from a copied real-base save.

set -euo pipefail

usage() {
  cat <<'EOF'
usage: manage_linux_deterministic_server.sh {bootstrap|deploy|deploy-if-required|reset|start|stop|status} [options]

Options:
  --root PATH           Dedicated server state (default: ~/.local/share/factorio-rl/deterministic).
  --runtime-root PATH   Private Factorio runtime (default: ~/.local/share/factorio-rl/runtime/factorio-2.1.17).
  --factorio-bin PATH   Factorio executable (defaults below --runtime-root).
  --read-data PATH      Factorio read-data directory (defaults below --runtime-root).
  --source-save PATH    Required for bootstrap; copied once into the dedicated root.
  --episode-id ID       Episode identity written by reset (default: timestamp).
  --technology NAME     Research target recorded in the episode manifest.
  --bootstrap-profile NAME
                        Supply contract: reduced-v1 (default) or supplied-v1.
  --gui-mods PATH       Linux GUI Factorio mods directory (default: ~/.factorio/mods).
  --game-port PORT      Game port (default: 34199).
  --rcon-port PORT      Loopback RCON port (default: 27017).

bootstrap copies --source-save only when the dedicated save does not already
exist. It never modifies the source save or the normal ~/.factorio profile.
reset backs up the isolated save before replacing it from --source-save.
deploy replaces both project mods on the isolated server and matching Linux GUI
profile; it requires a stopped server. It never restarts the GUI client. start
requires a completed bootstrap.
EOF
}

die() {
  echo "native Linux deterministic server: $*" >&2
  exit 1
}

require_port() {
  [[ "$2" =~ ^[0-9]+$ ]] && (( 10#$2 >= 1 && 10#$2 <= 65535 )) \
    || die "$1 must be an integer between 1 and 65535"
}

ACTION="${1:-}"
[[ -n "$ACTION" ]] || { usage >&2; exit 2; }
if [[ "$ACTION" == "-h" || "$ACTION" == "--help" ]]; then
  usage
  exit 0
fi
shift || true

STATE_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/factorio-rl/deterministic"
RUNTIME_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/factorio-rl/runtime/factorio-2.1.17"
FACTORIO_BIN=""
READ_DATA=""
SOURCE_SAVE=""
EPISODE_ID=""
TECHNOLOGY=""
BOOTSTRAP_PROFILE="reduced-v1"
GUI_MODS_PATH="$HOME/.factorio/mods"
GAME_PORT=34199
RCON_PORT=27017

while (($#)); do
  case "$1" in
    --root) STATE_ROOT="${2:?missing --root value}"; shift 2 ;;
    --runtime-root) RUNTIME_ROOT="${2:?missing --runtime-root value}"; shift 2 ;;
    --factorio-bin) FACTORIO_BIN="${2:?missing --factorio-bin value}"; shift 2 ;;
    --read-data) READ_DATA="${2:?missing --read-data value}"; shift 2 ;;
    --source-save) SOURCE_SAVE="${2:?missing --source-save value}"; shift 2 ;;
    --episode-id) EPISODE_ID="${2:?missing --episode-id value}"; shift 2 ;;
    --technology) TECHNOLOGY="${2:?missing --technology value}"; shift 2 ;;
    --bootstrap-profile) BOOTSTRAP_PROFILE="${2:?missing --bootstrap-profile value}"; shift 2 ;;
    --gui-mods) GUI_MODS_PATH="${2:?missing --gui-mods value}"; shift 2 ;;
    --game-port) GAME_PORT="${2:?missing --game-port value}"; shift 2 ;;
    --rcon-port) RCON_PORT="${2:?missing --rcon-port value}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

case "$ACTION" in
  bootstrap|deploy|deploy-if-required|reset|start|stop|status) ;;
  *) usage >&2; exit 2 ;;
esac

require_port "game port" "$GAME_PORT"
require_port "RCON port" "$RCON_PORT"
[[ "$GAME_PORT" != "$RCON_PORT" ]] || die "game port and RCON port must differ"
[[ "$BOOTSTRAP_PROFILE" == "reduced-v1" || "$BOOTSTRAP_PROFILE" == "supplied-v1" ]] \
  || die "bootstrap profile must be reduced-v1 or supplied-v1"

FACTORIO_BIN="${FACTORIO_BIN:-$RUNTIME_ROOT/bin/x64/factorio}"
READ_DATA="${READ_DATA:-$RUNTIME_ROOT/data}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_ROOT="$STATE_ROOT"
MODS_PATH="$DATA_ROOT/mods"
SAVE_PATH="$DATA_ROOT/saves/mod_playground.zip"
SECRET_PATH="$DATA_ROOT/rcon-password"
PID_PATH="$DATA_ROOT/factorio.pid"
STDIN_PATH="$DATA_ROOT/factorio.stdin"
STDIN_KEEPER_PID_PATH="$DATA_ROOT/factorio-stdin-keeper.pid"
CONFIG_PATH="$DATA_ROOT/config.ini"
SERVER_SETTINGS="$DATA_ROOT/server-settings.json"
EPISODE_DIR="$DATA_ROOT/episode"
EPISODE_MANIFEST="$EPISODE_DIR/current.json"

sha256_file() {
  sha256sum "$1" | awk '{print $1}'
}

repository_revision() {
  git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown
}

dirty_file_count() {
  git -C "$REPO_ROOT" status --porcelain --untracked-files=normal 2>/dev/null | wc -l | tr -d ' '
}

tree_hash() {
  local root="$1"
  (
    cd "$root"
    # LC_ALL=C pins byte-order sorting; tools/autonomous_run.py:_directory_hash
    # replicates this exact contract when re-verifying episode manifests.
    find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}'
  )
}

server_running() {
  [[ -f "$PID_PATH" ]] && kill -0 "$(<"$PID_PATH")" 2>/dev/null
}

stop_stdin_keeper() {
  if [[ -f "$STDIN_KEEPER_PID_PATH" ]] \
    && kill -0 "$(<"$STDIN_KEEPER_PID_PATH")" 2>/dev/null; then
    kill "$(<"$STDIN_KEEPER_PID_PATH")" 2>/dev/null || true
  fi
  rm -f "$STDIN_KEEPER_PID_PATH" "$STDIN_PATH"
}

assert_stopped() {
  server_running && die "server is already running (PID $(<"$PID_PATH"))"
  rm -f "$PID_PATH"
  stop_stdin_keeper
}

write_server_files() {
  mkdir -p "$MODS_PATH" "$DATA_ROOT/saves" "$DATA_ROOT/logs" "$DATA_ROOT/script-output"
  cat > "$CONFIG_PATH" <<EOF
[path]
read-data=$READ_DATA
write-data=$DATA_ROOT
EOF
  cat > "$SERVER_SETTINGS" <<'EOF'
{
  "name": "Factorio RL Linux Deterministic Server",
  "description": "Private isolated native Linux deterministic Factorio server",
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
  {"name":"recycler","enabled":true},
  {"name":"space-age","enabled":true},
  {"name":"factorio_cursor_rl_agent","enabled":true},
  {"name":"factorio_training_lab","enabled":true}
]}
EOF
}

sync_mod() {
  assert_stopped
  [[ -f "$REPO_ROOT/factorio_mod/control.lua" ]] || die "deterministic mod is missing"
  [[ -f "$REPO_ROOT/factorio_mod/info.json" ]] || die "deterministic mod metadata is missing"
  [[ -f "$REPO_ROOT/factorio_training_lab/control.lua" ]] || die "training mod is missing"
  [[ -f "$REPO_ROOT/factorio_training_lab/info.json" ]] || die "training mod metadata is missing"
  [[ "$(realpath -m "$GUI_MODS_PATH")" != "$(realpath -m "$MODS_PATH")" ]] \
    || die "GUI mods directory must not be the isolated server mods directory"
  sync_mod_copy "$MODS_PATH" factorio_cursor_rl_agent factorio_mod
  sync_mod_copy "$MODS_PATH" factorio_training_lab factorio_training_lab
  "$REPO_ROOT/scripts/sync_linux_gui_mods.sh" --mods-dir "$GUI_MODS_PATH"
  write_server_files
}

sync_mod_copy() {
  local target_root="$1" mod_name="$2" source_name="$3"
  [[ "$target_root" != "/" ]] || die "refusing to deploy a mod directly under /"
  mkdir -p "$target_root"
  rm -rf "$target_root/$mod_name"
  cp -a "$REPO_ROOT/$source_name" "$target_root/$mod_name"
}

deploy_if_required() {
  assert_stopped
  if [[ -d "$MODS_PATH/factorio_cursor_rl_agent" ]] \
    && diff -qr "$REPO_ROOT/factorio_mod" "$MODS_PATH/factorio_cursor_rl_agent" >/dev/null 2>&1 \
    && [[ -d "$MODS_PATH/factorio_training_lab" ]] \
    && diff -qr "$REPO_ROOT/factorio_training_lab" "$MODS_PATH/factorio_training_lab" >/dev/null 2>&1; then
    echo "server project mod copies already match"
    sync_mod_copy_check_gui
    return
  fi
  sync_mod
  echo "changed project mods deployed to $MODS_PATH and $GUI_MODS_PATH"
}

sync_mod_copy_check_gui() {
  if [[ (-e "$GUI_MODS_PATH/factorio_cursor_rl_agent" && ! -d "$GUI_MODS_PATH/factorio_cursor_rl_agent") \
    || (-e "$GUI_MODS_PATH/factorio_training_lab" && ! -d "$GUI_MODS_PATH/factorio_training_lab") ]] \
    || ! diff -qr "$REPO_ROOT/factorio_mod" "$GUI_MODS_PATH/factorio_cursor_rl_agent" >/dev/null 2>&1 \
    || ! diff -qr "$REPO_ROOT/factorio_training_lab" "$GUI_MODS_PATH/factorio_training_lab" >/dev/null 2>&1; then
    sync_mod
    echo "changed GUI project mod copies deployed to $GUI_MODS_PATH"
  fi
}

ensure_secret() {
  if [[ ! -s "$SECRET_PATH" ]]; then
    umask 077
    od -An -N32 -tx1 /dev/urandom | tr -d ' \n' > "$SECRET_PATH"
  fi
  chmod 600 "$SECRET_PATH"
}

bootstrap() {
  [[ -x "$FACTORIO_BIN" ]] || die "Factorio executable is missing: $FACTORIO_BIN"
  [[ -d "$READ_DATA/base" ]] || die "Factorio read-data root is invalid: $READ_DATA"
  [[ -n "$SOURCE_SAVE" ]] || die "bootstrap requires --source-save"
  [[ -f "$SOURCE_SAVE" ]] || die "source save is missing: $SOURCE_SAVE"
  assert_stopped
  mkdir -p "$DATA_ROOT/saves"
  if [[ ! -f "$SAVE_PATH" ]]; then
    cp -p "$SOURCE_SAVE" "$SAVE_PATH"
  fi
  sync_mod
  ensure_secret
  echo "bootstrapped isolated deterministic server at $DATA_ROOT using copied save $SAVE_PATH"
}

write_episode_manifest() {
  local source_hash copy_hash deterministic_hash training_hash revision dirty created temporary
  source_hash="$(sha256_file "$SOURCE_SAVE")"
  copy_hash="$(sha256_file "$SAVE_PATH")"
  deterministic_hash="$(tree_hash "$MODS_PATH/factorio_cursor_rl_agent")"
  training_hash="$(tree_hash "$MODS_PATH/factorio_training_lab")"
  revision="$(repository_revision)"
  dirty="$(dirty_file_count)"
  created="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  [[ -n "$EPISODE_ID" ]] || EPISODE_ID="episode-$(date -u +%Y%m%dT%H%M%SZ)-${RANDOM}"
  mkdir -p "$EPISODE_DIR"
  temporary="$EPISODE_MANIFEST.tmp.$$"
  cat > "$temporary" <<EOF
{
  "schema_version": "1.0.0",
  "episode_id": "$EPISODE_ID",
  "bootstrap_profile": "$BOOTSTRAP_PROFILE",
  "target_technology": "$TECHNOLOGY",
  "surface": "nauvis",
  "force": "player",
  "source_save": "$(realpath "$SOURCE_SAVE")",
  "source_save_sha256": "$source_hash",
  "isolated_save": "$SAVE_PATH",
  "isolated_save_sha256": "$copy_hash",
  "baseline_world_fingerprint": "sha256:$copy_hash",
  "deployed_factorio_mod_sha256": "$deterministic_hash",
  "deployed_factorio_training_lab_sha256": "$training_hash",
  "baseline_verified": false,
  "initial_game_tick": null,
  "repository_revision": "$revision",
  "dirty_worktree_file_count": $dirty,
  "created_at": "$created",
  "started_at": null,
  "ended_at": null,
  "termination_reason": null
}
EOF
  mv "$temporary" "$EPISODE_MANIFEST"
  rm -f \
    "$DATA_ROOT/logs/deterministic-power-state.json" \
    "$DATA_ROOT/logs/deterministic-plan-reservations.jsonl" \
    "$DATA_ROOT/logs/deterministic-plan-reservations.json" \
    "$DATA_ROOT/logs/autonomous-priorities.json" \
    "$DATA_ROOT/logs/deterministic-mission-state.json" \
    "$DATA_ROOT/logs/deterministic-blockers.jsonl"
}

reset_save() {
  [[ -n "$SOURCE_SAVE" ]] || die "reset requires --source-save"
  [[ -f "$SOURCE_SAVE" ]] || die "source save is missing: $SOURCE_SAVE"
  assert_stopped
  sync_mod
  mkdir -p "$DATA_ROOT/saves/backups"
  if [[ -f "$SAVE_PATH" ]]; then
    local backup="$DATA_ROOT/saves/backups/mod_playground-$(date +%Y%m%d-%H%M%S).zip"
    cp -p "$SAVE_PATH" "$backup"
    echo "backed up isolated deterministic save to $backup"
  fi
  local temporary="$SAVE_PATH.reset.$$"
  cp -p "$SOURCE_SAVE" "$temporary"
  cmp -s "$SOURCE_SAVE" "$temporary" || {
    rm -f "$temporary"
    die "reset copy does not match source save"
  }
  if [[ "$(sha256_file "$SOURCE_SAVE")" != "$(sha256_file "$temporary")" ]]; then
    rm -f "$temporary"
    die "reset SHA-256 does not match source save"
  fi
  mv "$temporary" "$SAVE_PATH"
  write_episode_manifest
  echo "reset isolated deterministic save from $SOURCE_SAVE"
}

port_available() {
  local port="$1"
  ! ss -ltnH "sport = :$port" | grep -q . && ! ss -lunH "sport = :$port" | grep -q .
}

start_server() {
  assert_stopped
  [[ -x "$FACTORIO_BIN" ]] || die "Factorio executable is missing: $FACTORIO_BIN"
  [[ -f "$SAVE_PATH" ]] || die "server is not bootstrapped; run bootstrap first"
  [[ -f "$SECRET_PATH" ]] || die "server RCON secret is missing; run bootstrap first"
  [[ -f "$CONFIG_PATH" && -f "$SERVER_SETTINGS" ]] || die "server configuration is missing; run bootstrap first"
  port_available "$GAME_PORT" || die "game port $GAME_PORT is already in use"
  port_available "$RCON_PORT" || die "RCON port $RCON_PORT is already in use"
  mkdir -p "$DATA_ROOT/logs"
  : > "$DATA_ROOT/factorio-current.log"
  : > "$DATA_ROOT/logs/factorio-console.log"
  mkfifo "$STDIN_PATH"
  # Keep the FIFO open without inheriting dashboard subprocess stderr.  The
  # manager is invoked with captured pipes; an inherited descriptor would keep
  # callers blocked until the keeper is stopped, even after Factorio is ready.
  tail -f /dev/null > "$STDIN_PATH" 2>"$DATA_ROOT/logs/factorio-stdin-keeper.log" &
  echo $! > "$STDIN_KEEPER_PID_PATH"
  setsid "$FACTORIO_BIN" --config "$CONFIG_PATH" --mod-directory "$MODS_PATH" \
    --start-server "$SAVE_PATH" --server-settings "$SERVER_SETTINGS" \
    --bind "127.0.0.1:$GAME_PORT" --rcon-bind "127.0.0.1:$RCON_PORT" \
    --rcon-password "$(<"$SECRET_PATH")" --console-log "$DATA_ROOT/logs/factorio-console.log" \
    >"$DATA_ROOT/logs/factorio-stdout.log" 2>"$DATA_ROOT/logs/factorio-stderr.log" < "$STDIN_PATH" &
  echo $! > "$PID_PATH"
  for _ in $(seq 1 240); do
    grep -q "Starting RCON interface" "$DATA_ROOT/factorio-current.log" 2>/dev/null && break
    if ! server_running; then
      stop_stdin_keeper
      die "server exited; inspect $DATA_ROOT/logs/factorio-stderr.log"
    fi
    sleep 0.5
  done
  if ! grep -q "Starting RCON interface" "$DATA_ROOT/factorio-current.log"; then
    stop_stdin_keeper
    die "server did not open RCON within 120 seconds"
  fi
  echo "started isolated deterministic server PID $(<"$PID_PATH") game=127.0.0.1:$GAME_PORT rcon=127.0.0.1:$RCON_PORT"
}

stop_server() {
  if ! server_running; then
    rm -f "$PID_PATH"
    stop_stdin_keeper
    echo "isolated deterministic server is already stopped"
    return
  fi
  local pid="$(<"$PID_PATH")"
  kill -INT "$pid"
  for _ in $(seq 1 120); do
    kill -0 "$pid" 2>/dev/null || {
      rm -f "$PID_PATH"
      stop_stdin_keeper
      echo "stopped isolated deterministic server"
      return
    }
    sleep 0.5
  done
  die "server PID $pid did not stop after SIGINT"
}

status_server() {
  if server_running; then
    echo "running isolated deterministic server PID $(<"$PID_PATH") game=127.0.0.1:$GAME_PORT rcon=127.0.0.1:$RCON_PORT root=$DATA_ROOT"
  else
    stop_stdin_keeper
    echo "isolated deterministic server is stopped root=$DATA_ROOT"
  fi
}

case "$ACTION" in
  bootstrap) bootstrap ;;
  deploy)
    [[ -d "$DATA_ROOT" ]] || die "server is not bootstrapped"
    sync_mod
    echo "deployed both project mods to $MODS_PATH and $GUI_MODS_PATH"
  ;;
  deploy-if-required) deploy_if_required ;;
  reset) reset_save ;;
  start) start_server ;;
  stop) stop_server ;;
  status) status_server ;;
esac
