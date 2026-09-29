#!/usr/bin/env bash
# Path: scripts/laya_overnight.sh
# Purpose: Unattended stepped Laya training for a fixed number of hours, then a clean shutdown.
#
# Alternates connected_workflow level 1 and level 2 policy-iteration rounds on 12 one-slot
# instances, retrains the features policy on all stepped cases each round, fine-tunes Laya every
# second round on the same cases, evaluates everything on held-out seeds 4001-4032, and appends
# results to data/laya-pi/<run>/summary.log. Code runs from a snapshot of HEAD so worktree
# edits during the night cannot break it.
#
# Usage: nohup setsid scripts/laya_overnight.sh [HOURS] > data/laya-pi/overnight/driver.log 2>&1 &
# Resume a finished run: LAYA_RESUME_ROUND=N (last completed round), LAYA_START_POLICY=<features json>,
# LAYA_L1_SEED / LAYA_L2_SEED (next unused training seeds), LAYA_MIN_CASE_ROUND=K (Laya trains only on
# rounds >= K, e.g. 3 to drop the scripted-continuation rounds), LAYA_FINETUNE_FIRST=1,
# LAYA_FINETUNE_TIMEOUT (default 150m; raise it when the GPU is power-capped).
#
# Concurrent/named runs: LAYA_RUN_NAME (default "overnight") selects the run directory
# data/laya-pi/$LAYA_RUN_NAME and its summary.log; LAYA_PREFIX (default "ov") namespaces every
# round/eval/checkpoint name so a second run never collides with another run's files in the
# shared data/laya-pi and data/laya-exploration/batches directories. Both default to today's
# unnamed behaviour, so existing invocations are unaffected.
#
# Graceful stop: create "$RUN/stop-requested" (any content). The driver checks for it at the top
# of every round-loop iteration, finishes the round in flight, then runs its normal final
# evaluation and worker shutdown -- it never leaves an in-progress round half-written.
set -uo pipefail

HOURS=${1:-7.5}
REPO=$(cd "$(dirname "$0")/.." && pwd)
PY="$REPO/.venv/bin/python"
MANAGE="$REPO/scripts/manage_linux_training_worker.sh"
OUT="$REPO/data/laya-pi"; RUN="$OUT/${LAYA_RUN_NAME:-overnight}"; SUMMARY="$RUN/summary.log"
BATCHES="$REPO/data/laya-exploration/batches"
PREFIX=${LAYA_PREFIX:-ov}
STOP_FILE="$RUN/stop-requested"
export LAYA_INSTANCES=${LAYA_INSTANCES:-"01 02 03 04 06 07 08 09 10 11 12 13"}
export LAYA_PROCS=${LAYA_PROCS:-3} LAYA_SPEED=${LAYA_SPEED:-32}
WORKERS=($LAYA_INSTANCES)
SEEDS_PER_ROUND=${LAYA_SEEDS_PER_ROUND:-36}
DEADLINE=$(( $(date +%s) + $(awk "BEGIN { print int($HOURS * 3600) }") ))
mkdir -p "$RUN"
rm -f "$STOP_FILE"

log() { echo "[$(date '+%F %T')] $*" | tee -a "$SUMMARY"; }
remaining() { echo $(( DEADLINE - $(date +%s) )); }

# Freeze the code for the whole night.
export LAYA_CODE="$RUN/code"
rm -rf "$LAYA_CODE"; mkdir -p "$LAYA_CODE"
git -C "$REPO" archive HEAD | tar -x -C "$LAYA_CODE"
cp "$REPO/training-workers-linux.json" "$LAYA_CODE/training-workers-linux.json"
log "start: $HOURS h, code $(git -C "$REPO" rev-parse --short HEAD), instances ${WORKERS[*]}, procs $LAYA_PROCS, speed $LAYA_SPEED"

heal_workers() {
  local n
  for n in "${WORKERS[@]}"; do
    if "$MANAGE" status --worker-count 20 --only "$((10#$n))" 2>&1 | grep -q "is stopped"; then
      log "worker $n was stopped; starting it"
      "$MANAGE" start --worker-count 20 --only "$((10#$n))" >> "$RUN/workers.log" 2>&1 || log "worker $n failed to start"
    fi
  done
}

instance_args() { local n; for n in "${WORKERS[@]}"; do printf -- "--instance factorio-training-linux-%s " "$n"; done; }

# evaluate NAME LEVEL POLICY_ARGS... : stepped held-out batch, then compare with that level's rollout baseline.
evaluate() {
  local name=$1 level=$2; shift 2
  heal_workers
  (cd "$LAYA_CODE" && timeout 90m "$PY" tools/run_laya_workflow_batch.py $(instance_args) --slots 1 --stepped \
    --game-speed "$LAYA_SPEED" --level "$level" --seed-start 4001 --episodes 32 --max-len 2048 --max-decisions 60 \
    --evidence-dir "$REPO/data/laya-exploration" --run-prefix "$name" "$@") > "$RUN/$name.log" 2>&1
  compare "$level" "$BATCHES/$name.jsonl"
}

compare() {
  local level=$1; shift
  local base="$BATCHES/$PREFIX-eval-rollout-L$level.jsonl"
  [[ -f "$base" && -f "$1" ]] || { log "missing batch for comparison: $1"; return; }
  "$PY" "$REPO/tools/compare_laya_batches.py" "$base" "$@" 2>&1 | tail -n +2 | tee -a "$SUMMARY"
}

all_cases() { ls "$OUT"/"$PREFIX"-r*-cases.jsonl 2>/dev/null; }
# Laya's dataset may skip early rounds whose one-step labels teach wait loops.
laya_cases() {
  local file n
  for file in $(all_cases); do
    n=${file##*/"$PREFIX"-r}; n=${n%%-*}
    (( n >= ${LAYA_MIN_CASE_ROUND:-1} )) && echo "$file"
  done
}

laya_finetune() {
  local tag=$1 dataset="$RUN/dataset-$1.jsonl" ckpt="$REPO/data/laya-checkpoints/laya-${LAYA_RUN_NAME:-overnight}-$1"
  local cases=(); mapfile -t cases < <(laya_cases)
  local inputs=(); for c in "${cases[@]}"; do inputs+=(--cases-jsonl "$c"); done
  log "laya $tag: dataset from ${#cases[@]} case files"
  "$PY" "$REPO/tools/build_laya_dataset.py" "${inputs[@]}" --output "$dataset" > "$RUN/dataset-$tag.log" 2>&1 \
    || { log "laya $tag: dataset build failed"; return; }
  # Gentle GPU profile: micro-batch 1 with checkpointing, low CPU priority.
  (cd "$REPO" && LAYA_MAX_LEN=2048 LAYA_HEAD_MAX_LEN=256 LAYA_MICRO_BATCH=1 LAYA_GRADIENT_ACCUMULATION=16 \
    LAYA_GRADIENT_CHECKPOINTING=1 LAYA_CHECKPOINT_INTERVAL=25 timeout "${LAYA_FINETUNE_TIMEOUT:-150m}" nice -n 10 "$PY" tools/train_laya_4060.py \
    --cases "$dataset" --model-dir "$REPO/data/laya-base/typed-decisions" --output-dir "$ckpt") > "$RUN/train-$tag.log" 2>&1 \
    || { log "laya $tag: training failed (see $RUN/train-$tag.log)"; return; }
  log "laya $tag: trained -> $ckpt"
  local level
  for level in 1 2; do
    evaluate "$PREFIX-eval-laya-$tag-L$level" "$level" --policy laya --epsilon 0 --checkpoint "$ckpt"
  done
}

# 0. Scripted-rollout baselines on both levels (every later evaluation compares against these).
heal_workers
for level in 1 2; do
  [[ -f "$BATCHES/$PREFIX-eval-rollout-L$level.jsonl" ]] || evaluate "$PREFIX-eval-rollout-L$level" "$level" --policy rollout
done
latest=${LAYA_START_POLICY:-rollout}
[[ -n "${LAYA_FINETUNE_FIRST:-}" ]] && laya_finetune "r${LAYA_RESUME_ROUND:-0}-min${LAYA_MIN_CASE_ROUND:-1}"

# 1..N. Rounds until the deadline leaves too little time for another one, or a graceful stop is requested.
round=${LAYA_RESUME_ROUND:-0}; l1_seed=${LAYA_L1_SEED:-5101}; l2_seed=${LAYA_L2_SEED:-6001}; round_seconds=3600
while (( $(remaining) > round_seconds + 600 )); do
  if [[ -f "$STOP_FILE" ]]; then
    log "stop requested; finishing after round $round"
    break
  fi
  round=$((round + 1))
  level=$(( round % 2 == 1 ? 2 : 1 ))
  if (( level == 1 )); then seed=$l1_seed; l1_seed=$((l1_seed + SEEDS_PER_ROUND)); else seed=$l2_seed; l2_seed=$((l2_seed + SEEDS_PER_ROUND)); fi
  # Rounds 1-2 label each level under the scripted rollout; later rounds iterate on the learned policy.
  cont=$latest; (( round <= 2 )) && cont=rollout
  name="$PREFIX-r$round"
  heal_workers
  mapfile -t previous < <(all_cases)
  started=$(date +%s)
  log "$name: level $level seeds $seed+$SEEDS_PER_ROUND continuation $(basename "$cont")"
  LAYA_REPO="$REPO" "$LAYA_CODE/scripts/laya_policy_iteration_round.sh" "$name" "$level" "$seed" "$SEEDS_PER_ROUND" "$cont" "${previous[@]}" \
    > "$RUN/$name.log" 2>&1
  if [[ -f "$OUT/features-$name.json" ]]; then
    latest="$OUT/features-$name.json"
    compare "$level" "$BATCHES/eval-$name-L$level.jsonl"
  else
    log "$name: no features model produced (see $RUN/$name.log)"
  fi
  # Measured round time sets the stopping margin.
  round_seconds=$(( $(date +%s) - started ))
  log "$name: done in $((round_seconds / 60)) min, $(( $(remaining) / 60 )) min left"
  if (( round % 2 == 0 && $(remaining) > 5400 )); then laya_finetune "r$round"; fi
  heal_workers
done

# Final: the latest features policy on both levels, then shut the fleet down.
if [[ "$latest" != rollout ]]; then
  for level in 1 2; do
    # Run ids must be unique across runs: the runner refuses to reuse a journal.
    evaluate "$PREFIX-eval-final-r$round-L$level" "$level" --policy features --features-model "$latest"
  done
fi
log "final features policy: $latest"
for n in "${WORKERS[@]}"; do "$MANAGE" stop --worker-count 20 --only "$((10#$n))" >> "$RUN/workers.log" 2>&1; done
log "done; training workers stopped"
