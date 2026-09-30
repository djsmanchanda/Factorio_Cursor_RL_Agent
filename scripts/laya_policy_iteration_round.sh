#!/usr/bin/env bash
# Path: scripts/laya_policy_iteration_round.sh
# Purpose: One stepped policy-iteration round: label -> cases -> features policy -> held-out evaluation.
#
# Usage: scripts/laya_policy_iteration_round.sh ROUND LEVEL SEED_START SEEDS CONTINUATION [PREVIOUS_CASES...]
#   CONTINUATION is "rollout" or a features-model JSON. Environment:
#   LAYA_INSTANCES  worker numbers (default "01 02 03 04 06 07 08 09 10 11 12 13")
#   LAYA_PROCS      labeller processes; one Python process cannot drive many instances (default 3)
#   LAYA_SPEED      game.speed for stepped tick bursts (default 32)
#   LAYA_CODE       code checkout to run (default: this repository); data always lands in this repository
#   LAYA_REPO       repository that holds .venv and data/ (default: the one containing this script)
# A failed labeller process loses only its own seeds; the round continues with what was labelled.
set -uo pipefail

ROUND=${1:?round name}; LEVEL=${2:?level}; SEED_START=${3:?seed start}; SEEDS=${4:?seed count}; CONT=${5:?continuation}
shift 5
PREVIOUS_CASES=("$@")
REPO=${LAYA_REPO:-$(cd "$(dirname "$0")/.." && pwd)}
CODE=${LAYA_CODE:-$REPO}
PY="$REPO/.venv/bin/python"
INSTANCES=(${LAYA_INSTANCES:-01 02 03 04 06 07 08 09 10 11 12 13})
PROCS=${LAYA_PROCS:-3}; SPEED=${LAYA_SPEED:-32}
EVAL_START=${LAYA_EVAL_START:-4001}; EVAL_COUNT=${LAYA_EVAL_COUNT:-32}
OUT="$REPO/data/laya-pi"; LABELS="$REPO/data/laya-counterfactual"
mkdir -p "$OUT"
cd "$CODE"

# Probe the exact new seed block before spending hours replaying a collapsed
# teacher. This only selects lab continuation; live policy authority is unchanged.
if [[ "$CONT" != rollout ]]; then
  gate_args=()
  for n in "${INSTANCES[@]}"; do gate_args+=(--instance "factorio-training-linux-$n"); done
  for policy in rollout features; do
    policy_args=(--policy "$policy")
    [[ "$policy" == rollout ]] || policy_args+=(--features-model "$CONT")
    timeout 1h "$PY" tools/run_laya_workflow_batch.py "${gate_args[@]}" --slots 1 --stepped --game-speed "$SPEED" \
      --level "$LEVEL" "${policy_args[@]}" --seed-start "$SEED_START" --episodes "$SEEDS" \
      --max-len 2048 --max-decisions 60 --max-no-progress 60 \
      --evidence-dir "$REPO/data/laya-exploration" --run-prefix "gate-$ROUND-$policy-L$LEVEL" \
      > "$OUT/gate-$ROUND-$policy.log" 2>&1 || { echo "teacher probe failed"; exit 2; }
  done
  "$PY" tools/laya_teacher_gate.py "$REPO/data/laya-exploration/batches/gate-$ROUND-rollout-L$LEVEL.jsonl" \
    "$REPO/data/laya-exploration/batches/gate-$ROUND-features-L$LEVEL.jsonl" \
    --seed-start "$SEED_START" --episodes "$SEEDS" > "$OUT/gate-$ROUND.json"
  gate_status=$?
  cat "$OUT/gate-$ROUND.json"
  case "$gate_status" in
    0) ;;
    3) echo "teacher regressed on the new cohort; labelling under verified rollout"; CONT=rollout ;;
    *) echo "teacher gate blocked; no labels collected"; exit 2 ;;
  esac
fi

cont_args=(--continuation rollout)
[[ "$CONT" == rollout ]] || cont_args=(--continuation features --features-model "$CONT")

per_proc=$(( ${#INSTANCES[@]} / PROCS )); seeds_per=$(( (SEEDS + PROCS - 1) / PROCS ))
pids=()
for ((p = 0; p < PROCS; p++)); do
  args=()
  for n in "${INSTANCES[@]:p*per_proc:per_proc}"; do args+=(--instance "factorio-training-linux-$n"); done
  start=$(( SEED_START + p * seeds_per )); left=$(( SEEDS - p * seeds_per ))
  count=$(( left < seeds_per ? left : seeds_per ))
  (( count > 0 )) || continue
  timeout 4h "$PY" tools/laya_counterfactual_labels.py "${args[@]}" --slots 1 --stepped --game-speed "$SPEED" \
    --level "$LEVEL" --seed-start "$start" --seeds "$count" --depth 30 --base-repeats 2 "${cont_args[@]}" \
    --evidence-dir "$LABELS" --run-prefix "$ROUND-p$p" > "$OUT/$ROUND-p$p.log" 2>&1 &
  pids+=($!)
done
for pid in "${pids[@]}"; do wait "$pid" || echo "labeller $pid exited with $?"; done
tail -qn1 "$OUT/$ROUND"-p*.log

shopt -s nullglob
labels=("$LABELS/labels/$ROUND"-p*.jsonl)
(( ${#labels[@]} )) || { echo "no labels for $ROUND"; exit 1; }
# Stepped labels are near noise-free, so targets can be much sharper than the labeller's default.
"$PY" tools/laya_labels_to_cases.py "${labels[@]}" --temperature-ticks "${LAYA_TEMPERATURE:-100}" \
  --output "$OUT/$ROUND-cases.jsonl" || exit 1
"$PY" tools/train_laya_features.py "$OUT/$ROUND-cases.jsonl" "${PREVIOUS_CASES[@]}" \
  --output "$OUT/features-$ROUND.json" || exit 1

all=()
for n in "${INSTANCES[@]}"; do all+=(--instance "factorio-training-linux-$n"); done
timeout 1h "$PY" tools/run_laya_workflow_batch.py "${all[@]}" --slots 1 --stepped --game-speed "$SPEED" \
  --level "$LEVEL" --policy features --features-model "$OUT/features-$ROUND.json" \
  --seed-start "$EVAL_START" --episodes "$EVAL_COUNT" --max-len 2048 --max-decisions 60 \
  --evidence-dir "$REPO/data/laya-exploration" --run-prefix "eval-$ROUND-L$LEVEL" > "$OUT/eval-$ROUND.log" 2>&1
tail -n1 "$OUT/eval-$ROUND.log"
