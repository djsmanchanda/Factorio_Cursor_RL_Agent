#!/usr/bin/env bash
# Path: scripts/run_laya_jumpstart.sh | Purpose: Jumpstart experiment: run Laya on one prepared world, then restart a fresh controller on the same world and code after each stall or failure.
#
# Usage: scripts/run_laya_jumpstart.sh <restarts> <tools/run_laya_clean.py run arguments...>
# Segment 0 is a normal run. Each later segment adds --jumpstart, which carries only the earlier
# segments' lifecycle receipts (starters, foundations, retirements, steel, mall slots, growth); the
# world, code and seed stay the same. Stops early on mission_complete, a window stop, or SIGTERM.
set -u

restarts=${1:?restarts}
shift
python=${PYTHON:-python}
last=$(mktemp)
trap 'rm -f "$last"' EXIT

for segment in $(seq 0 "$restarts"); do
  flags=()
  [ "$segment" -gt 0 ] && flags=(--jumpstart)
  echo "JUMPSTART SEGMENT $segment/$restarts starting $(date -Iseconds)"
  "$python" tools/run_laya_clean.py run "$@" "${flags[@]}" | tee "$last"
  stop=$(tail -1 "$last" | "$python" -c 'import json, sys; print(json.loads(sys.stdin.read()).get("stop", ""))' 2>/dev/null)
  echo "JUMPSTART SEGMENT $segment/$restarts ended: stop=${stop:-crashed}"
  case "$stop" in
    stalled|safe_stop_after_repeated_failures|runner_error|"") ;;  # stalled or failed: restart
    *) break ;;
  esac
done
