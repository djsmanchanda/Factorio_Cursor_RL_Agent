#!/usr/bin/env bash
# Path: scripts/laya_overnight.sh
# Purpose: Unattended stepped Laya training for a fixed number of hours, then a clean shutdown.
#
# Rotates connected_workflow levels 2, 1, 3 on 12 one-slot
# instances, retrains the features policy on all stepped cases each round, fine-tunes Laya every
# second round on the same cases, evaluates everything on held-out seeds 4001-4032, and appends
# results to data/laya-pi/<run>/summary.log. Code runs from a snapshot of HEAD so worktree
# edits during the night cannot break it.
#
# Usage: nohup setsid scripts/laya_overnight.sh [HOURS] > data/laya-pi/overnight/driver.log 2>&1 &
# Resume a finished run: LAYA_RESUME_ROUND=N (last completed round), LAYA_START_POLICY=<features json>,
# LAYA_L1_SEED / LAYA_L2_SEED / LAYA_L3_SEED (next unused training seeds), LAYA_MIN_CASE_ROUND=K (Laya trains only on
# rounds >= K, e.g. 3 to drop the scripted-continuation rounds), LAYA_FINETUNE_FIRST=1,
# LAYA_FINETUNE_TIMEOUT (default 150m; raise it when the GPU is power-capped).
# Bounded experiments: LAYA_MAX_ROUNDS caps new rounds (0 = time budget only),
# LAYA_FINETUNE_EVERY controls cadence (0 = disabled), LAYA_FINETUNE_FINAL=1
# trains after the rounds, and LAYA_REFERENCE_CHECKPOINT evaluates a frozen
# reference adapter before training. LAYA_EPOCHS sets the fine-tune epoch count.
# LAYA_CASE_WINDOW_ROUNDS limits Laya to recent rounds (0 = fixed minimum only).
# LAYA_FINAL_RESERVE_SECONDS keeps collection from consuming the final training/evaluation window.
# LAYA_EVAL_START / LAYA_EVAL_COUNT choose the matched development cohort on every enabled level.
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
REPO=${LAYA_REPO:-$(cd "$(dirname "$0")/.." && pwd)}
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
LEVELS=(${LAYA_LEVELS:-2 1 3})
MAX_ROUNDS=${LAYA_MAX_ROUNDS:-0}; FINETUNE_EVERY=${LAYA_FINETUNE_EVERY:-2}
CASE_WINDOW=${LAYA_CASE_WINDOW_ROUNDS:-0}
FINAL_RESERVE=${LAYA_FINAL_RESERVE_SECONDS:-0}
EVAL_START=${LAYA_EVAL_START:-4001}; EVAL_COUNT=${LAYA_EVAL_COUNT:-32}
for value in "$MAX_ROUNDS" "$FINETUNE_EVERY" "$CASE_WINDOW" "$FINAL_RESERVE" "$EVAL_START"; do
  [[ "$value" =~ ^[0-9]+$ ]] || { echo "round controls must be nonnegative integers"; exit 2; }
done
[[ "$EVAL_COUNT" =~ ^[0-9]+$ ]] && (( EVAL_COUNT > 0 )) || { echo "evaluation count must be positive"; exit 2; }
export LAYA_EVAL_START="$EVAL_START" LAYA_EVAL_COUNT="$EVAL_COUNT"
(( ${#LEVELS[@]} > 0 )) || { echo "LAYA_LEVELS must not be empty"; exit 2; }
for level in "${LEVELS[@]}"; do
  case "$level" in 1|2|3) ;; *) echo "unsupported workflow level: $level"; exit 2 ;; esac
done
DEADLINE=$(( $(date +%s) + $(awk "BEGIN { print int($HOURS * 3600) }") ))
mkdir -p "$RUN"
rm -f "$STOP_FILE"

log() { echo "[$(date '+%F %T')] $*" | tee -a "$SUMMARY"; }
remaining() { echo $(( DEADLINE - $(date +%s) )); }

# Freeze the code for the whole night.
export LAYA_CODE="$RUN/code"
if [[ "${LAYA_DRIVER_FROZEN:-0}" != 1 ]]; then
  rm -rf "$LAYA_CODE"; mkdir -p "$LAYA_CODE"
  git -C "$REPO" archive HEAD | tar -x -C "$LAYA_CODE" || exit 2
  cp "$REPO/training-workers-linux.json" "$LAYA_CODE/training-workers-linux.json" || exit 2
  export LAYA_REPO="$REPO" LAYA_DRIVER_FROZEN=1
  # Bash reads a script incrementally. Freeze the outer driver too, before
  # labelling pauses it and worktree edits could move its unread file offsets.
  exec bash "$LAYA_CODE/scripts/laya_overnight.sh" "$@"
fi
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
    --game-speed "$LAYA_SPEED" --level "$level" --seed-start "$EVAL_START" --episodes "$EVAL_COUNT" --max-len 2048 --max-decisions 60 \
    --evidence-dir "$REPO/data/laya-exploration" --run-prefix "$name" "$@") > "$RUN/$name.log" 2>&1
  compare "$level" "$BATCHES/$name.jsonl"
}

compare() {
  local level=$1; shift
  local base="$BATCHES/$PREFIX-eval-rollout-v4-L$level.jsonl"
  [[ -f "$base" && -f "$1" ]] || { log "missing batch for comparison: $1"; return 1; }
  "$PY" "$LAYA_CODE/tools/compare_laya_batches.py" "$base" "$@" --seed-start "$EVAL_START" --episodes "$EVAL_COUNT" \
    2>&1 | tee -a "$SUMMARY"
}

all_cases() {
  local file corrected source_prefix
  for source_prefix in ${LAYA_CASE_PREFIXES:-$PREFIX}; do
    [[ "$source_prefix" =~ ^[A-Za-z0-9_.-]+$ ]] || { log "invalid case prefix" >&2; return 2; }
    for file in "$OUT"/"$source_prefix"-r*-cases.jsonl; do
      [[ -f "$file" ]] || continue
      corrected="${LAYA_RELABELED_CASE_DIR:-$OUT/relabel-v4}/${file##*/}"
      if [[ -f "$corrected" ]]; then printf '%s\n' "$corrected"; else printf '%s\n' "$file"; fi
    done
  done
}
# Laya's dataset may skip early rounds whose one-step labels teach wait loops.
laya_cases() {
  local file n minimum=${LAYA_MIN_CASE_ROUND:-1}
  local current_round=${round:-${LAYA_RESUME_ROUND:-0}}
  if (( CASE_WINDOW > 0 && current_round - CASE_WINDOW + 1 > minimum )); then
    minimum=$((current_round - CASE_WINDOW + 1))
  fi
  for file in $(all_cases); do
    n=${file##*-r}; n=${n%%-*}
    (( n >= minimum )) && echo "$file"
  done
}

laya_finetune() {
  local tag=$1 dataset="$RUN/dataset-$1.jsonl" ckpt="$REPO/data/laya-checkpoints/laya-${LAYA_RUN_NAME:-overnight}-$1"
  local cases=(); mapfile -t cases < <(laya_cases)
  local inputs=(); for c in "${cases[@]}"; do inputs+=(--cases-jsonl "$c"); done
  log "laya $tag: dataset from ${#cases[@]} case files"
  "$PY" "$LAYA_CODE/tools/build_laya_dataset.py" "${inputs[@]}" --require-sampling-version episode-relative-v1 \
    --output "$dataset" > "$RUN/dataset-$tag.log" 2>&1 \
    || { log "laya $tag: dataset build failed"; return; }
  # Gentle GPU profile: micro-batch 1 with checkpointing, low CPU priority.
  (cd "$LAYA_CODE" && LAYA_MAX_LEN=2048 LAYA_HEAD_MAX_LEN=256 LAYA_MICRO_BATCH=1 LAYA_GRADIENT_ACCUMULATION=16 \
    LAYA_GRADIENT_CHECKPOINTING=1 LAYA_CHECKPOINT_INTERVAL=25 timeout "${LAYA_FINETUNE_TIMEOUT:-150m}" nice -n 10 "$PY" tools/train_laya_4060.py \
    --cases "$dataset" --model-dir "$REPO/data/laya-base/typed-decisions" --output-dir "$ckpt") > "$RUN/train-$tag.log" 2>&1 \
    || { log "laya $tag: training failed (see $RUN/train-$tag.log)"; return; }
  log "laya $tag: trained -> $ckpt"
  (cd "$LAYA_CODE" && timeout 30m "$PY" tools/evaluate_laya.py --cases "$dataset" \
    --checkpoint "$ckpt" --base-model "$REPO/data/laya-base/typed-decisions" --split held_out \
    --output "$ckpt/evaluation-held-out.json") > "$RUN/offline-$tag.log" 2>&1 \
    || { log "laya $tag: offline evaluation failed; live evaluation skipped"; return; }
  local level
  for level in "${LEVELS[@]}"; do
    evaluate "$PREFIX-eval-laya-$tag-L$level" "$level" --policy laya --epsilon 0 --checkpoint "$ckpt"
  done
}

# 0. Scripted-rollout baselines on every enabled level.
heal_workers
for level in "${LEVELS[@]}"; do
  [[ -f "$BATCHES/$PREFIX-eval-rollout-v4-L$level.jsonl" ]] || evaluate "$PREFIX-eval-rollout-v4-L$level" "$level" --policy rollout
done
latest=${LAYA_START_POLICY:-rollout}
if [[ -n "${LAYA_REFERENCE_CHECKPOINT:-}" ]]; then
  for level in "${LEVELS[@]}"; do
    evaluate "$PREFIX-eval-laya-${LAYA_REFERENCE_TAG:-reference}-L$level" "$level" --policy laya --epsilon 0 --checkpoint "$LAYA_REFERENCE_CHECKPOINT"
  done
fi
[[ -n "${LAYA_FINETUNE_FIRST:-}" ]] && laya_finetune "r${LAYA_RESUME_ROUND:-0}-min${LAYA_MIN_CASE_ROUND:-1}"

# 1..N. Rounds until the deadline leaves too little time for another one, or a graceful stop is requested.
round=${LAYA_RESUME_ROUND:-0}; l1_seed=${LAYA_L1_SEED:-5101}; l2_seed=${LAYA_L2_SEED:-6001}
l3_seed=${LAYA_L3_SEED:-8101}; round_seconds=3600
rounds_started=0
while (( $(remaining) > round_seconds + 600 + FINAL_RESERVE )); do
  if (( MAX_ROUNDS > 0 && rounds_started >= MAX_ROUNDS )); then
    log "round limit reached: $MAX_ROUNDS new rounds"
    break
  fi
  if [[ -f "$STOP_FILE" ]]; then
    log "stop requested; finishing after round $round"
    break
  fi
  round=$((round + 1))
  rounds_started=$((rounds_started + 1))
  level=${LEVELS[$(( (round - 1) % ${#LEVELS[@]} ))]}
  case "$level" in
    1) seed=$l1_seed; l1_seed=$((l1_seed + SEEDS_PER_ROUND)) ;;
    2) seed=$l2_seed; l2_seed=$((l2_seed + SEEDS_PER_ROUND)) ;;
    3) seed=$l3_seed; l3_seed=$((l3_seed + SEEDS_PER_ROUND)) ;;
  esac
  # Rounds 1-2 label each level under the scripted rollout; later rounds iterate on the learned policy.
  cont=$latest; (( round <= 2 )) && cont=rollout
  # A teacher trained only on earlier levels cannot label the new decision
  # menu reliably. Bootstrap L3 explicitly until its first accepted round.
  if (( level == 3 )) && [[ ! -f "$RUN/level3-ready" ]]; then cont=rollout; fi
  name="$PREFIX-r$round"
  heal_workers
  mapfile -t previous < <(all_cases)
  started=$(date +%s)
  log "$name: level $level seeds $seed+$SEEDS_PER_ROUND continuation $(basename "$cont")"
  LAYA_REPO="$REPO" "$LAYA_CODE/scripts/laya_policy_iteration_round.sh" "$name" "$level" "$seed" "$SEEDS_PER_ROUND" "$cont" "${previous[@]}" \
    > "$RUN/$name.log" 2>&1
  if [[ -f "$OUT/features-$name.json" ]]; then
    # One continuation serves all levels. A good current-level score cannot
    # grant it authority to label a previously solved level that it regresses.
    admission_pairs=()
    for admission_level in "${LEVELS[@]}"; do
      if [[ "$admission_level" == "$level" ]]; then
        admission_batch="$BATCHES/eval-$name-L$level.jsonl"
        compare "$level" "$admission_batch"
      else
        admission_name="$PREFIX-admit-r$round-L$admission_level"
        evaluate "$admission_name" "$admission_level" --policy features --features-model "$OUT/features-$name.json"
        admission_batch="$BATCHES/$admission_name.jsonl"
      fi
      admission_pairs+=(--level-pair "$admission_level" "$BATCHES/$PREFIX-eval-rollout-v4-L$admission_level.jsonl" "$admission_batch")
    done
    if "$PY" "$LAYA_CODE/tools/laya_teacher_gate.py" "${admission_pairs[@]}" \
        --seed-start "$EVAL_START" --episodes "$EVAL_COUNT" > "$RUN/$name-admission.json"; then
      latest="$OUT/features-$name.json"
      (( level == 3 )) && touch "$RUN/level3-ready"
      "$PY" - "$OUT/continuation-$PREFIX.json" "$latest" "$round" <<'PY'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
temporary = path.with_suffix('.tmp')
temporary.write_text(json.dumps({'version': '1.0.0', 'policy': sys.argv[2], 'round': int(sys.argv[3]),
                                 'admission_scope': 'all_enabled_levels'}) + '\n')
temporary.replace(path)
PY
    else
      log "$name: comparison blocked; retaining continuation $(basename "$latest")"
    fi
  else
    log "$name: no features model produced (see $RUN/$name.log)"
  fi
  # Measured round time sets the stopping margin.
  round_seconds=$(( $(date +%s) - started ))
  log "$name: done in $((round_seconds / 60)) min, $(( $(remaining) / 60 )) min left"
  if (( FINETUNE_EVERY > 0 && round % FINETUNE_EVERY == 0 && $(remaining) > 5400 )); then laya_finetune "r$round"; fi
  heal_workers
done

if [[ "${LAYA_FINETUNE_FINAL:-0}" == 1 && ! -f "$STOP_FILE" ]] && (( rounds_started > 0 && $(remaining) > 600 )); then
  laya_finetune "r$round-final"
fi

# Final: the latest features policy on every enabled level, then shut down.
if [[ "$latest" != rollout ]]; then
  for level in "${LEVELS[@]}"; do
    # Run ids must be unique across runs: the runner refuses to reuse a journal.
    evaluate "$PREFIX-eval-final-r$round-L$level" "$level" --policy features --features-model "$latest"
  done
fi
log "final features policy: $latest"
for n in "${WORKERS[@]}"; do "$MANAGE" stop --worker-count 20 --only "$((10#$n))" >> "$RUN/workers.log" 2>&1; done
log "done; training workers stopped"
