#!/usr/bin/env bash
# Path: scripts/sync_linux_gui_mods.sh
# Purpose: Build and synchronize the two named runtime mods plus the training
# lab into one Linux GUI profile without replacing unrelated mods.

set -euo pipefail

usage() {
  cat <<'EOF'
usage: sync_linux_gui_mods.sh [--mods-dir PATH] [--build-dir PATH]

Builds factorio_cursor_rl_agent (deterministic) and factorio_laya_agent (Laya)
from the shared factorio_mod source, then copies both plus factorio_training_lab
to the Linux Factorio GUI mods directory (default: ~/.factorio/mods) and enables
all three while preserving every other mod-list entry.

Enabling both runtime mods everywhere keeps the client's enabled mod set
identical to every server, so switching servers never prompts "sync mods".
EOF
}

die() {
  echo "Linux GUI mod sync: $*" >&2
  exit 1
}

GUI_MODS_PATH="$HOME/.factorio/mods"
BUILD_DIR=""
while (($#)); do
  case "$1" in
    --mods-dir) GUI_MODS_PATH="${2:?missing --mods-dir value}"; shift 2 ;;
    --build-dir) BUILD_DIR="${2:?missing --build-dir value}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ "$GUI_MODS_PATH" != "/" ]] || die "refusing to deploy a mod directly under /"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${BUILD_DIR:-$REPO_ROOT/build/runtime_mods}"

PYTHON="${PYTHON:-python3}"
"$PYTHON" "$REPO_ROOT/tools/build_runtime_mods.py" \
  --source "$REPO_ROOT/factorio_mod" --output "$BUILD_DIR" >/dev/null

MOD_NAMES=(factorio_cursor_rl_agent factorio_laya_agent factorio_training_lab)
MOD_SOURCES=(
  "$BUILD_DIR/factorio_cursor_rl_agent"
  "$BUILD_DIR/factorio_laya_agent"
  "$REPO_ROOT/factorio_training_lab"
)

for source_dir in "${MOD_SOURCES[@]}"; do
  [[ -f "$source_dir/info.json" && -f "$source_dir/control.lua" ]] \
    || die "mod source is incomplete: $source_dir"
done

mkdir -p "$GUI_MODS_PATH"
for index in "${!MOD_NAMES[@]}"; do
  target="$GUI_MODS_PATH/${MOD_NAMES[$index]}"
  rm -rf "$target"
  cp -a "${MOD_SOURCES[$index]}" "$target"
done

python3 - "$GUI_MODS_PATH/mod-list.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if path.exists():
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Linux GUI mod sync: invalid mod list: {path}: {exc}")
else:
    payload = {"mods": []}
if not isinstance(payload, dict) or not isinstance(payload.get("mods"), list):
    raise SystemExit(f"Linux GUI mod sync: invalid mod-list shape: {path}")
mods = payload["mods"]
enabled = {"factorio_cursor_rl_agent", "factorio_laya_agent", "factorio_training_lab"}
seen = set()
for mod in mods:
    if not isinstance(mod, dict) or not isinstance(mod.get("name"), str):
        raise SystemExit(f"Linux GUI mod sync: invalid mod-list entry: {path}")
    if mod["name"] in enabled:
        mod["enabled"] = True
        seen.add(mod["name"])
for name in sorted(enabled - seen):
    mods.append({"name": name, "enabled": True})
temporary = path.with_name(path.name + ".tmp")
temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
temporary.replace(path)
PY

printf 'synchronized %s, %s and %s to %s\n' \
  "${MOD_NAMES[0]}" "${MOD_NAMES[1]}" "${MOD_NAMES[2]}" "$GUI_MODS_PATH"
