#!/usr/bin/env bash
# Path: scripts/manage_linux_deterministic_server.sh
# Purpose: Manage an isolated native-Linux deterministic Factorio server from a copied real-base save.

set -euo pipefail

usage() {
  cat <<'EOF'
usage: manage_linux_deterministic_server.sh {bootstrap|deploy|deploy-if-required|reset|start|stop|status} [options]

Options:
  --root PATH           Dedicated server state (default: ~/.local/share/factorio-rl/deterministic).
  --runtime-root PATH   Private Factorio runtime (default: ~/.local/share/factorio-rl/runtime/factorio-2.1.21).
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
  --update-threads N    Factorio update-runner thread count per world
                        (engine default when omitted; 1 = single update thread).
  --cpu-affinity LIST   Pin Factorio to a taskset CPU list (for example 0-3,8).
  --nice N              Lower this server's CPU priority by N (0..19).
  --fleet-mode          Headless lane mode; never sync GUI mods or snapshot
                        a lane save into the repository saves directory.

bootstrap copies --source-save only when the dedicated save does not already
exist. It never modifies the source save or the normal ~/.factorio profile.
reset backs up the isolated save before replacing it from --source-save.
deploy replaces both project mods on the isolated server and matching Linux GUI
profile; it requires a stopped server. It never restarts the GUI client. start
requires a completed bootstrap and snapshots the selected save into
<repository>/saves/mod_playground.zip for reproducible handoff.
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
RUNTIME_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/factorio-rl/runtime/factorio-2.1.21"
FACTORIO_BIN=""
READ_DATA=""
SOURCE_SAVE=""
EPISODE_ID=""
TECHNOLOGY=""
BOOTSTRAP_PROFILE="reduced-v1"
GUI_MODS_PATH="$HOME/.factorio/mods"
GAME_PORT=34199
RCON_PORT=27017
UPDATE_THREADS=""
CPU_AFFINITY=""
NICE_LEVEL=0
FLEET_MODE=0

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
    --update-threads) UPDATE_THREADS="${2:?missing --update-threads value}"; shift 2 ;;
    --cpu-affinity) CPU_AFFINITY="${2:?missing --cpu-affinity value}"; shift 2 ;;
    --nice) NICE_LEVEL="${2:?missing --nice value}"; shift 2 ;;
    --fleet-mode) FLEET_MODE=1; shift ;;
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
if [[ -n "$UPDATE_THREADS" ]]; then
  [[ "$UPDATE_THREADS" =~ ^[0-9]+$ ]] && (( 10#$UPDATE_THREADS >= 1 && 10#$UPDATE_THREADS <= 64 )) \
    || die "update threads must be between 1 and 64"
fi
if [[ -n "$CPU_AFFINITY" ]]; then
  command -v taskset >/dev/null 2>&1 || die "taskset is required for --cpu-affinity"
  taskset -c "$CPU_AFFINITY" true >/dev/null 2>&1 \
    || die "invalid or unavailable CPU affinity list: $CPU_AFFINITY"
fi
[[ "$NICE_LEVEL" =~ ^[0-9]+$ ]] && (( 10#$NICE_LEVEL <= 19 )) \
  || die "nice must be an integer between 0 and 19"

FACTORIO_BIN="${FACTORIO_BIN:-$RUNTIME_ROOT/bin/x64/factorio}"
READ_DATA="${READ_DATA:-$RUNTIME_ROOT/data}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_ROOT="$STATE_ROOT"
MODS_PATH="$DATA_ROOT/mods"
SAVE_PATH="$DATA_ROOT/saves/mod_playground.zip"
REPO_SAVE_PATH="$REPO_ROOT/saves/mod_playground.zip"
SECRET_PATH="$DATA_ROOT/rcon-password"
PID_PATH="$DATA_ROOT/factorio.pid"
# Fleet lanes add a creation-token sidecar beside the legacy PID file.  The
# factorio.pid format stays a bare integer for every existing caller.
START_TOKEN_PATH="$DATA_ROOT/factorio.pid.start-token.json"
BOOT_ID_PATH="/proc/sys/kernel/random/boot_id"
FLEET_START_TOKEN=""
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

process_start_token() {
  local pid="$1" stat rest
  local -a fields
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
  stat="$(<"/proc/$pid/stat")" 2>/dev/null || return 1
  # Field 2 (comm) may itself contain spaces and parentheses; strip through the
  # last ") " so the array begins at field 3 (state).  Field 22 is then index 19.
  rest="${stat##*) }"
  read -r -a fields <<< "$rest"
  ((${#fields[@]} >= 20)) || return 1
  [[ "${fields[19]}" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "${fields[19]}"
}

process_state_code() {
  local pid="$1" stat rest
  local -a fields
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
  stat="$(<"/proc/$pid/stat")" 2>/dev/null || return 1
  rest="${stat##*) }"
  read -r -a fields <<< "$rest"
  ((${#fields[@]} >= 1)) || return 1
  printf '%s\n' "${fields[0]}"
}

current_boot_id() {
  local boot
  IFS= read -r boot < "$BOOT_ID_PATH" 2>/dev/null || return 1
  [[ "$boot" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]] \
    || return 1
  printf '%s\n' "$boot"
}

# kill -0 fails for both "no such process" and "operation not permitted".  The
# latter still means the PID exists, so classify it as present and let the
# caller fail closed rather than deleting another user's live server record.
process_presence() {
  local pid="$1" error
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || { printf 'absent\n'; return 0; }
  if [[ -e "/proc/$pid/stat" ]]; then
    printf 'present\n'
    return 0
  fi
  if kill -0 "$pid" 2>/dev/null; then
    printf 'present\n'
    return 0
  fi
  error="$(LC_ALL=C kill -0 "$pid" 2>&1 || true)"
  case "$error" in
    *"not permitted"*) printf 'present\n'; return 0 ;;
    *"No such process"*) printf 'absent\n'; return 0 ;;
  esac
  if [[ -e "/proc/$pid" ]]; then
    printf 'present\n'
    return 0
  fi
  printf 'unknown\n'
}

# Strict full-record match: exactly one pid, creation_token and boot_id, with
# no duplicate keys, extra fields or trailing bytes.  Anything else is corrupt
# evidence and must never drive a signal or a cleanup.
read_start_token_record() {
  local record boot
  [[ -f "$START_TOKEN_PATH" ]] || return 1
  record="$(<"$START_TOKEN_PATH")" 2>/dev/null || return 1
  [[ "$record" =~ ^\{[[:space:]]*\"pid\"[[:space:]]*:[[:space:]]*([1-9][0-9]*)[[:space:]]*,[[:space:]]*\"creation_token\"[[:space:]]*:[[:space:]]*([0-9]+)[[:space:]]*,[[:space:]]*\"boot_id\"[[:space:]]*:[[:space:]]*\"([0-9a-fA-F-]+)\"[[:space:]]*\}$ ]] \
    || return 1
  RECORDED_PID="${BASH_REMATCH[1]}"
  RECORDED_TOKEN="${BASH_REMATCH[2]}"
  boot="${BASH_REMATCH[3]}"
  [[ "$boot" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]] \
    || return 1
  RECORDED_BOOT_ID="$boot"
  return 0
}

write_fleet_start_token() {
  local pid="$1" token boot temporary
  FLEET_START_TOKEN=""
  token="$(process_start_token "$pid")" || return 1
  # Publish the token to the caller so a later write failure can still prove
  # ownership before any signal is sent.
  FLEET_START_TOKEN="$token"
  boot="$(current_boot_id)" || return 1
  temporary="$START_TOKEN_PATH.tmp.$$"
  ( umask 077; printf '{"pid":%s,"creation_token":%s,"boot_id":"%s"}\n' "$pid" "$token" "$boot" > "$temporary" ) \
    || { rm -f "$temporary"; return 1; }
  mv -f "$temporary" "$START_TOKEN_PATH" || { rm -f "$temporary"; return 1; }
}

remove_fleet_records() {
  rm -f "$PID_PATH" 2>/dev/null || true
  rm -f "$START_TOKEN_PATH" 2>/dev/null || true
}

# Classify a recorded PID against the creation token captured at start.
#   owned   the live PID still carries the recorded start token
#   gone    the PID is definitely absent or now belongs to another process
#   unknown /proc could not be read, so ownership is unproven
# Always returns 0; callers branch on FLEET_PID_STATE so `set -e` cannot turn
# an unknown classification into a silent success path.
fleet_pid_state() {
  local pid="$1" expected="$2" presence token
  presence="$(process_presence "$pid")"
  case "$presence" in
    absent)
      FLEET_PID_STATE="gone"
      return 0
      ;;
    unknown)
      FLEET_PID_STATE="unknown"
      return 0
      ;;
  esac
  if ! token="$(process_start_token "$pid" 2>/dev/null)"; then
    # A process may exit between the presence check and the stat read.  Only a
    # now-provably-absent PID becomes gone; anything else stays unknown.
    presence="$(process_presence "$pid")"
    if [[ "$presence" == absent ]]; then
      FLEET_PID_STATE="gone"
    else
      FLEET_PID_STATE="unknown"
    fi
    return 0
  fi
  if [[ "$token" != "$expected" ]]; then
    FLEET_PID_STATE="gone"
    return 0
  fi
  # A zombie has already exited.  Its token proves it was ours, and it cannot
  # serve the lane, so treat it as gone and allow our own records to be cleaned.
  if [[ "$(process_state_code "$pid" 2>/dev/null)" == "Z" ]]; then
    FLEET_PID_STATE="gone"
  else
    FLEET_PID_STATE="owned"
  fi
  return 0
}

# Returns 0 only for a live PID whose /proc start time and boot ID match the
# fleet sidecar.  Returns 1 only after proving the recorded server is absent or
# reused.  Unknown liveness or boot evidence dies instead of guessing:
# signaling a reused PID, deleting the only ownership record, or starting a
# second server over a live lane is worse than a stalled lane.
fleet_server_running() {
  local pid boot presence token
  RECORDED_PID=""
  RECORDED_TOKEN=""
  RECORDED_BOOT_ID=""

  if [[ ! -f "$PID_PATH" ]]; then
    if [[ ! -e "$START_TOKEN_PATH" ]]; then
      return 1
    fi
    # A sidecar without a PID file still proves a lane may be live.  Its own
    # PID must be classified before any evidence is removed.
    read_start_token_record \
      || die "fleet token record $START_TOKEN_PATH exists without $PID_PATH and is missing, corrupt, or has duplicate fields; refusing to infer lifecycle state"
    fleet_pid_state "$RECORDED_PID" "$RECORDED_TOKEN"
    case "$FLEET_PID_STATE" in
      gone)
        rm -f "$START_TOKEN_PATH" 2>/dev/null || true
        return 1
        ;;
      owned)
        die "fleet token record names live PID $RECORDED_PID but $PID_PATH is missing; refusing to delete the only ownership evidence or start another server. Inspect the lane, stop PID $RECORDED_PID if it is this server, then remove $START_TOKEN_PATH"
        ;;
      *)
        die "fleet token record names PID $RECORDED_PID but its liveness cannot be verified; refusing to infer lifecycle state or delete ownership evidence. Inspect /proc/$RECORDED_PID and $START_TOKEN_PATH"
        ;;
    esac
  fi
  pid="$(<"$PID_PATH")" 2>/dev/null \
    || die "fleet PID record is unreadable: $PID_PATH; refusing to infer lifecycle state"
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] \
    || die "fleet PID record is invalid: $PID_PATH; refusing to infer lifecycle state"
  presence="$(process_presence "$pid")"
  if [[ "$presence" == absent ]]; then
    # The PID named by the legacy record is definitely gone.  Before deleting
    # anything, require the sidecar to agree; a sidecar naming another live
    # process means the evidence is corrupt, not stale.
    if [[ -e "$START_TOKEN_PATH" ]]; then
      read_start_token_record \
        || die "fleet PID $pid is absent but $START_TOKEN_PATH is missing, corrupt, or has duplicate fields; refusing to delete ownership evidence"
      [[ "$RECORDED_PID" == "$pid" ]] \
        || die "fleet creation-token record names PID $RECORDED_PID but $PID_PATH names PID $pid; refusing mismatched cleanup"
      fleet_pid_state "$RECORDED_PID" "$RECORDED_TOKEN"
      if [[ "$FLEET_PID_STATE" != "gone" ]]; then
        die "fleet PID $pid is absent but $START_TOKEN_PATH still names live or unverifiable PID $RECORDED_PID; refusing to delete ownership evidence. Inspect the lane before starting another server"
      fi
    fi
    remove_fleet_records
    return 1
  fi
  [[ "$presence" == unknown ]] \
    && die "fleet PID $pid liveness cannot be verified (kill and /proc evidence disagree); refusing to infer lifecycle state"
  read_start_token_record \
    || die "fleet server PID $pid is alive but $START_TOKEN_PATH is missing, corrupt, or has duplicate fields; refusing to infer lifecycle state"
  [[ "$RECORDED_PID" == "$pid" ]] \
    || die "fleet creation-token record names PID $RECORDED_PID but $PID_PATH names PID $pid; refusing mismatched cleanup"
  boot="$(current_boot_id)" \
    || die "cannot read the current Linux boot ID; refusing to use an unbound fleet record"
  if [[ "$RECORDED_BOOT_ID" != "$boot" ]]; then
    # A record can only be written while its process is live on the current
    # boot, so a mismatch while that PID is live is corruption, not proof of a
    # reboot.  Deleting the sidecar would discard the only evidence of lane
    # ownership and could let a second server take over the lane.
    die "fleet server PID $pid is alive but $START_TOKEN_PATH records boot $RECORDED_BOOT_ID instead of the current boot $boot; a corrupt record is indistinguishable from a reboot, so refusing to signal it or delete ownership evidence. Inspect the lane and process identity before removing $PID_PATH and $START_TOKEN_PATH manually"
  fi
  fleet_pid_state "$pid" "$RECORDED_TOKEN"
  case "$FLEET_PID_STATE" in
    owned)
      return 0
      ;;
    gone)
      # The recorded token may still match if the process has exited but is
      # not reaped yet; that is a stopped server, not a reused PID.
      presence="$(process_presence "$pid")"
      if [[ "$presence" == "absent" ]]; then
        remove_fleet_records
        return 1
      fi
      [[ "$presence" == "unknown" ]] \
        && die "fleet server PID $pid liveness became unverifiable; refusing to infer lifecycle state"
      if token="$(process_start_token "$pid" 2>/dev/null)" && [[ "$token" == "$RECORDED_TOKEN" ]]; then
        remove_fleet_records
        return 1
      fi
      die "fleet server PID $pid was reused (start token no longer matches $RECORDED_TOKEN); refusing to signal it or delete ownership evidence. Inspect /proc/$pid and $START_TOKEN_PATH"
      ;;
    *)
      die "fleet server PID $pid is alive but its /proc start time is unavailable; refusing to use or delete an untrusted PID"
      ;;
  esac
}

# Token-write failure happens after we spawned a child.  Only signal it when
# ownership is proven; an unknown identity never signals and never deletes the
# records, so the lane stays visibly claimed for manual inspection.
cleanup_failed_fleet_start() {
  local pid="$1"
  if [[ -z "${FLEET_START_TOKEN:-}" ]]; then
    # The token was never captured, so no live process can be proven ours.
    case "$(process_presence "$pid")" in
      absent)
        remove_fleet_records
        stop_stdin_keeper
        die "fleet PID $pid exited before its creation token could be recorded; refused start"
        ;;
      *)
        die "could not establish the identity of fleet PID $pid after a token-write failure; refusing to signal it or delete unverified records. Inspect /proc/$pid and $START_TOKEN_PATH"
        ;;
    esac
  fi
  fleet_pid_state "$pid" "$FLEET_START_TOKEN"
  case "$FLEET_PID_STATE" in
    owned) ;;
    gone)
      # Our child exited, or the PID now names a different process.  Clean only
      # our own records; never signal the reused PID.
      remove_fleet_records
      stop_stdin_keeper
      die "fleet PID $pid exited or was reused before its creation token could be recorded; refused start"
      ;;
    *)
      die "could not verify the identity of fleet PID $pid after a token-write failure; refusing to signal it or delete unverified records. Inspect /proc/$pid and $START_TOKEN_PATH"
      ;;
  esac
  kill -TERM "$pid" 2>/dev/null || true
  for _ in $(seq 1 40); do
    fleet_pid_state "$pid" "$FLEET_START_TOKEN"
    case "$FLEET_PID_STATE" in
      owned) sleep 0.1 ;;
      gone) break ;;
      *)
        die "fleet PID $pid became unverifiable while stopping after a token-write failure; refusing to send further signals or delete ownership evidence. Inspect /proc/$pid and $START_TOKEN_PATH"
        ;;
    esac
  done
  fleet_pid_state "$pid" "$FLEET_START_TOKEN"
  case "$FLEET_PID_STATE" in
    owned)
      kill -KILL "$pid" 2>/dev/null || true
      for _ in $(seq 1 20); do
        fleet_pid_state "$pid" "$FLEET_START_TOKEN"
        case "$FLEET_PID_STATE" in
          owned) sleep 0.1 ;;
          gone) break ;;
          *)
            die "fleet PID $pid became unverifiable after SIGKILL; refusing to delete ownership evidence. Inspect /proc/$pid and $START_TOKEN_PATH"
            ;;
        esac
      done
      fleet_pid_state "$pid" "$FLEET_START_TOKEN"
      [[ "$FLEET_PID_STATE" == "gone" ]] \
        || die "fleet PID $pid still matches its recorded identity after SIGKILL; refusing to delete records for a live process"
      ;;
    gone) ;;
    *)
      die "fleet PID $pid became unverifiable while stopping after a token-write failure; refusing to delete ownership evidence. Inspect /proc/$pid and $START_TOKEN_PATH"
      ;;
  esac
  remove_fleet_records
  stop_stdin_keeper
  die "could not record a trustworthy creation token for fleet PID $pid; terminated the process we started and refused start"
}

stop_stdin_keeper() {
  if [[ -f "$STDIN_KEEPER_PID_PATH" ]] \
    && kill -0 "$(<"$STDIN_KEEPER_PID_PATH")" 2>/dev/null; then
    kill "$(<"$STDIN_KEEPER_PID_PATH")" 2>/dev/null || true
  fi
  rm -f "$STDIN_KEEPER_PID_PATH" "$STDIN_PATH"
}

assert_stopped() {
  if (( FLEET_MODE )); then
    fleet_server_running && die "server is already running (PID $(<"$PID_PATH"))"
    remove_fleet_records
  else
    server_running && die "server is already running (PID $(<"$PID_PATH"))"
    rm -f "$PID_PATH"
  fi
  stop_stdin_keeper
}

write_server_files() {
  mkdir -p "$MODS_PATH" "$DATA_ROOT/saves" "$DATA_ROOT/logs" "$DATA_ROOT/script-output"
  cat > "$CONFIG_PATH" <<EOF
[path]
read-data=$READ_DATA
write-data=$DATA_ROOT
EOF
  if [[ -n "$UPDATE_THREADS" ]]; then
    cat >> "$CONFIG_PATH" <<EOF

[other]
update-runner-threads-count=$UPDATE_THREADS
EOF
  fi
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
  local threads_json="null" affinity_json="null"
  [[ -n "$UPDATE_THREADS" ]] && threads_json="$UPDATE_THREADS"
  [[ -n "$CPU_AFFINITY" ]] && affinity_json="\"$CPU_AFFINITY\""
  cat > "$DATA_ROOT/runtime-settings.json" <<EOF
{
  "factorio_update_threads": $threads_json,
  "cpu_affinity": $affinity_json,
  "nice": $NICE_LEVEL
}
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
  if (( ! FLEET_MODE )); then
    "$REPO_ROOT/scripts/sync_linux_gui_mods.sh" --mods-dir "$GUI_MODS_PATH"
  fi
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

snapshot_save_to_repository() {
  local snapshot_source="$SOURCE_SAVE"
  [[ -n "$snapshot_source" ]] || snapshot_source="$SAVE_PATH"
  [[ -f "$snapshot_source" ]] || die "save snapshot source is missing: $snapshot_source"
  mkdir -p "$(dirname "$REPO_SAVE_PATH")"
  local temporary="$REPO_SAVE_PATH.start.$$"
  cp -p "$snapshot_source" "$temporary"
  cmp -s "$snapshot_source" "$temporary" || {
    rm -f "$temporary"
    die "repository save snapshot does not match source save"
  }
  mv "$temporary" "$REPO_SAVE_PATH"
  echo "snapshotted server save to repository at $REPO_SAVE_PATH"
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
  # Fleet lanes are materialized without the normal bootstrap path.  Refresh
  # only lane-local config; write_server_files never touches GUI or repository
  # state, and FLEET_MODE keeps mod synchronization local.
  if (( FLEET_MODE )) || [[ -n "$UPDATE_THREADS" || -n "$CPU_AFFINITY" ]] || (( 10#$NICE_LEVEL > 0 )); then
    write_server_files
  fi
  [[ -f "$CONFIG_PATH" && -f "$SERVER_SETTINGS" ]] || die "server configuration is missing; run bootstrap first"
  port_available "$GAME_PORT" || die "game port $GAME_PORT is already in use"
  port_available "$RCON_PORT" || die "RCON port $RCON_PORT is already in use"
  if (( ! FLEET_MODE )); then
    snapshot_save_to_repository
  fi
  mkdir -p "$DATA_ROOT/logs"
  : > "$DATA_ROOT/factorio-current.log"
  : > "$DATA_ROOT/logs/factorio-console.log"
  mkfifo "$STDIN_PATH"
  # Keep the FIFO open without inheriting dashboard subprocess stderr.  The
  # manager is invoked with captured pipes; an inherited descriptor would keep
  # callers blocked until the keeper is stopped, even after Factorio is ready.
  tail -f /dev/null > "$STDIN_PATH" 2>"$DATA_ROOT/logs/factorio-stdin-keeper.log" &
  echo $! > "$STDIN_KEEPER_PID_PATH"
  local launch=("$FACTORIO_BIN" --config "$CONFIG_PATH" --mod-directory "$MODS_PATH" \
    --start-server "$SAVE_PATH" --server-settings "$SERVER_SETTINGS" \
    --bind "127.0.0.1:$GAME_PORT" --rcon-bind "127.0.0.1:$RCON_PORT" \
    --rcon-password "$(<"$SECRET_PATH")" --console-log "$DATA_ROOT/logs/factorio-console.log")
  if (( 10#$NICE_LEVEL > 0 )); then
    launch=(nice -n "$NICE_LEVEL" "${launch[@]}")
  fi
  if [[ -n "$CPU_AFFINITY" ]]; then
    launch=(taskset -c "$CPU_AFFINITY" "${launch[@]}")
  fi
  setsid "${launch[@]}" \
    >"$DATA_ROOT/logs/factorio-stdout.log" 2>"$DATA_ROOT/logs/factorio-stderr.log" < "$STDIN_PATH" &
  echo $! > "$PID_PATH"
  if (( FLEET_MODE )); then
    local started_pid="$(<"$PID_PATH")"
    write_fleet_start_token "$started_pid" || cleanup_failed_fleet_start "$started_pid"
  fi
  for _ in $(seq 1 240); do
    grep -q "Starting RCON interface" "$DATA_ROOT/factorio-current.log" 2>/dev/null && break
    if (( FLEET_MODE )); then
      fleet_server_running || { stop_stdin_keeper; die "server exited; inspect $DATA_ROOT/logs/factorio-stderr.log"; }
    elif ! server_running; then
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
  local running=1 pid recorded_token
  if (( FLEET_MODE )); then
    fleet_server_running || running=0
  elif ! server_running; then
    running=0
  fi
  if (( ! running )); then
    if (( FLEET_MODE )); then
      remove_fleet_records
    else
      rm -f "$PID_PATH"
    fi
    stop_stdin_keeper
    echo "isolated deterministic server is already stopped"
    return
  fi
  pid="$(<"$PID_PATH")"
  if (( ! FLEET_MODE )); then
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
  fi

  recorded_token="$RECORDED_TOKEN"
  # Re-verify immediately before signaling; a reused PID must never receive our
  # SIGINT or SIGKILL, and an unreadable process must fail closed instead of
  # being mistaken for a clean exit.
  fleet_pid_state "$pid" "$recorded_token"
  case "$FLEET_PID_STATE" in
    owned) ;;
    gone)
      remove_fleet_records
      stop_stdin_keeper
      echo "isolated deterministic server is already stopped"
      return
      ;;
    *)
      die "fleet server PID $pid identity cannot be verified immediately before SIGINT; refusing to signal it or delete ownership evidence. Inspect /proc/$pid and $START_TOKEN_PATH"
      ;;
  esac
  kill -INT "$pid"
  for _ in $(seq 1 120); do
    fleet_pid_state "$pid" "$recorded_token"
    case "$FLEET_PID_STATE" in
      owned) sleep 0.5 ;;
      gone)
        remove_fleet_records
        stop_stdin_keeper
        echo "stopped isolated deterministic server"
        return
        ;;
      *)
        die "fleet server PID $pid liveness became unverifiable after SIGINT; refusing to send further signals or delete ownership evidence. Inspect /proc/$pid and $START_TOKEN_PATH"
        ;;
    esac
  done
  fleet_pid_state "$pid" "$recorded_token"
  case "$FLEET_PID_STATE" in
    owned) ;;
    gone)
      remove_fleet_records
      stop_stdin_keeper
      echo "stopped isolated deterministic server"
      return
      ;;
    *)
      die "fleet server PID $pid liveness became unverifiable after SIGINT; refusing to send further signals or delete ownership evidence. Inspect /proc/$pid and $START_TOKEN_PATH"
      ;;
  esac
  # SIGINT timed out but the same verified process still holds the identity.
  kill -KILL "$pid" 2>/dev/null || true
  for _ in $(seq 1 20); do
    fleet_pid_state "$pid" "$recorded_token"
    case "$FLEET_PID_STATE" in
      owned) sleep 0.5 ;;
      gone)
        remove_fleet_records
        stop_stdin_keeper
        echo "stopped isolated deterministic server after SIGKILL"
        return
        ;;
      *)
        die "fleet server PID $pid liveness became unverifiable after SIGKILL; refusing to delete ownership evidence. Inspect /proc/$pid and $START_TOKEN_PATH"
        ;;
    esac
  done
  die "fleet server PID $pid still matches its recorded identity after SIGKILL"
}

status_server() {
  local running=1
  if (( FLEET_MODE )); then
    fleet_server_running || running=0
  elif ! server_running; then
    running=0
  fi
  if (( running )); then
    echo "running isolated deterministic server PID $(<"$PID_PATH") game=127.0.0.1:$GAME_PORT rcon=127.0.0.1:$RCON_PORT root=$DATA_ROOT"
  else
    stop_stdin_keeper
    echo "isolated deterministic server is stopped root=$DATA_ROOT"
  fi
}

# Focused tests source this file with a valid action and then exercise the
# helpers directly; executed callers continue to the lifecycle dispatch.
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
  return 0
fi

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
