#!/usr/bin/env bash
# Path: scripts/laya_ops_ab_campaign.sh
# Purpose: Bounded A/B campaign for Laya over the deterministic operations: a short C1 window, then a long C2 window, then reports.

# Runs from a frozen copy of the code (FROZEN, made by the launcher) so worktree edits during the campaign
# do not leak into it. Each stage is tools/laya_experiment.py: the baseline plus every variant on the same seeds,
# disposable copies of saves/mod_playground.zip, ports 34240+ / 27140+.

set -uo pipefail

FROZEN="${FROZEN:?frozen code copy}"
PY="${PY:-/home/djsm/Github_Desktop/Factorio_Cursor_RL_Agent/.venv/bin/python}"
STAMP="${STAMP:-20261007}"
PARALLEL="${PARALLEL:-12}"
VARIANTS=(docs/rl/laya_variants/ops-mall-first-10.json docs/rl/laya_variants/ops-mall-first-30.json)

cd "$FROZEN"
log() { echo "$(date --iso-8601=seconds) $*"; }

# Stage A: does the mall-first ordering get past C0 -> C1 more often? 16 seeds x 3 arms, speed 4.
log "stage A start"
"$PY" tools/laya_experiment.py run --experiment "ops-ab-c1-$STAMP" --actions deterministic \
  --variants "${VARIANTS[@]}" --seeds 16 --seed-base 1 --until-phase C1 --max-game-ticks 108000 \
  --speed 4 --parallel "$PARALLEL" --timeout 2700
log "stage A end rc=$?"

# Stage B: does it hold up over a long window toward C2? 12 seeds x 3 arms, speed 2.
log "stage B start"
"$PY" tools/laya_experiment.py run --experiment "ops-ab-c2-$STAMP" --actions deterministic \
  --variants "${VARIANTS[@]}" --seeds 12 --seed-base 101 --until-phase C2 --max-game-ticks 432000 \
  --speed 2 --parallel "$PARALLEL" --timeout 6600
log "stage B end rc=$?"

"$PY" tools/laya_experiment.py report --experiment "ops-ab-c1-$STAMP"
"$PY" tools/laya_experiment.py report --experiment "ops-ab-c2-$STAMP"
log "campaign done"
