#!/usr/bin/env bash
# Path: scripts/sync_linux_gui_mods.sh
# Purpose: Synchronize the project's two Factorio mods into one Linux GUI profile without replacing unrelated mods.

set -euo pipefail

usage() {
  cat <<'EOF'
usage: sync_linux_gui_mods.sh [--mods-dir PATH]

Copies factorio_cursor_rl_agent and factorio_training_lab from this repository
to the specified Linux Factorio GUI mods directory (default: ~/.factorio/mods)
and enables both entries while preserving every other mod-list entry.
EOF
}

die() {
  echo "Linux GUI mod sync: $*" >&2
  exit 1
}

GUI_MODS_PATH="$HOME/.factorio/mods"
while (($#)); do
  case "$1" in
    --mods-dir) GUI_MODS_PATH="${2:?missing --mods-dir value}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ "$GUI_MODS_PATH" != "/" ]] || die "refusing to deploy a mod directly under /"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MOD_NAMES=(factorio_cursor_rl_agent factorio_training_lab)
MOD_SOURCES=(factorio_mod factorio_training_lab)

for index in "${!MOD_NAMES[@]}"; do
  source_dir="$REPO_ROOT/${MOD_SOURCES[$index]}"
  [[ -f "$source_dir/info.json" && -f "$source_dir/control.lua" ]] \
    || die "mod source is incomplete: $source_dir"
done

mkdir -p "$GUI_MODS_PATH"
for index in "${!MOD_NAMES[@]}"; do
  target="$GUI_MODS_PATH/${MOD_NAMES[$index]}"
  rm -rf "$target"
  cp -a "$REPO_ROOT/${MOD_SOURCES[$index]}" "$target"
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
enabled = {"factorio_cursor_rl_agent", "factorio_training_lab"}
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

printf 'synchronized %s and %s to %s\n' "${MOD_NAMES[0]}" "${MOD_NAMES[1]}" "$GUI_MODS_PATH"
