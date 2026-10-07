#!/usr/bin/env bash
# Path: scripts/laya_ops_history_campaign.sh
# Purpose: Window-filling A/B of Laya's decision-history rules over the deterministic operations, toward C2.

# Runs from a frozen code copy (FROZEN). Rounds of SEEDS seeds x every arm repeat until less than
# ROUND_SECONDS remain before DEADLINE (epoch seconds), so the window is used even when runs stall early.
# All rounds share one experiment id; seeds never repeat, so every run lands in one report.

set -uo pipefail

FROZEN="${FROZEN:?frozen code copy}"
DEADLINE="${DEADLINE:?epoch seconds}"
PY="${PY:-/home/djsm/Github_Desktop/Factorio_Cursor_RL_Agent/.venv/bin/python}"
EXPERIMENT="${EXPERIMENT:-ops-history-c2-20261007}"
PARALLEL="${PARALLEL:-12}"
SEEDS="${SEEDS:-4}"
ROUND_SECONDS="${ROUND_SECONDS:-4500}"  # a round's worst case: two waves under the per-run timeout
VARIANTS=(docs/rl/laya_variants/ops-mall-first-30.json docs/rl/laya_variants/ops-mf30-no-repeat.json
          docs/rl/laya_variants/ops-mf30-follow.json docs/rl/laya_variants/ops-mf30-unstick.json
          docs/rl/laya_variants/ops-mf30-history.json)

cd "$FROZEN"
log() { echo "$(date --iso-8601=seconds) $*"; }

seed=201
while (( $(date +%s) + ROUND_SECONDS < DEADLINE )); do
  log "round seeds $seed..$((seed + SEEDS - 1))"
  "$PY" tools/laya_experiment.py run --experiment "$EXPERIMENT" --actions deterministic \
    --variants "${VARIANTS[@]}" --seeds "$SEEDS" --seed-base "$seed" --until-phase C2 --max-game-ticks 216000 \
    --speed 2 --parallel "$PARALLEL" --timeout 2100 | tail -9
  seed=$((seed + SEEDS))
done
"$PY" tools/laya_experiment.py report --experiment "$EXPERIMENT"
log "campaign done"
