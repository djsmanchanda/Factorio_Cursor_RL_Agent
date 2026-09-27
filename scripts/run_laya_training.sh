#!/usr/bin/env bash
# Path: scripts/run_laya_training.sh
# Purpose: Start the local Laya 4060 fine-tune detached with validation and durable logs.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Durable defaults (never /tmp): the content-addressed dataset written by
# tools/build_laya_dataset.py and one checkpoint directory shared with
# scripts/run_laya_training_resumable.sh.
DEFAULT_CASES_PATH="$REPO_ROOT/data/laya-datasets/latest.jsonl"
DEFAULT_CHECKPOINT_DIR="$REPO_ROOT/data/laya-checkpoints/laya-4060"
CASES_PATH="${LAYA_CASES_PATH:-$DEFAULT_CASES_PATH}"
MODEL_DIR="${LAYA_MODEL_DIR:-$REPO_ROOT/data/laya-base}"
CHECKPOINT_DIR="${LAYA_CHECKPOINT_DIR:-$DEFAULT_CHECKPOINT_DIR}"
LOG_DIR="${LAYA_LOG_DIR:-$REPO_ROOT/logs}"
LOG_PATH="$LOG_DIR/laya-4060-training.log"
STATUS_PATH="$CHECKPOINT_DIR/training-status.json"

# Keep the selected profile visible to both preflight and the detached worker.
# The queue script overrides these for the measured compile/flex profile.
# LAYA_MAX_LEN=2048 selects the long-context profile (micro-batch 1 with
# encoder and decision-head gradient checkpointing).
export LAYA_MAX_LEN="${LAYA_MAX_LEN:-1024}"
export LAYA_HEAD_MAX_LEN="${LAYA_HEAD_MAX_LEN:-256}"
export LAYA_MICRO_BATCH="${LAYA_MICRO_BATCH:-1}"
export LAYA_GRADIENT_ACCUMULATION="${LAYA_GRADIENT_ACCUMULATION:-32}"
export LAYA_GRADIENT_CHECKPOINTING="${LAYA_GRADIENT_CHECKPOINTING:-1}"
export LAYA_TORCH_COMPILE="${LAYA_TORCH_COMPILE:-0}"
export LAYA_ATTN_IMPLEMENTATION="${LAYA_ATTN_IMPLEMENTATION:-sdpa}"
export LAYA_FIXED_OPTION_WIDTH="${LAYA_FIXED_OPTION_WIDTH:-0}"
export LAYA_FUSED_ADAMW="${LAYA_FUSED_ADAMW:-0}"
export LAYA_LABEL_SMOOTHING="${LAYA_LABEL_SMOOTHING:-0.05}"
export LAYA_EARLY_STOP_PATIENCE="${LAYA_EARLY_STOP_PATIENCE:-1}"
export LAYA_CHECKPOINT_INTERVAL="${LAYA_CHECKPOINT_INTERVAL:-25}"
export LAYA_CHECKPOINT_KEEP="${LAYA_CHECKPOINT_KEEP:-3}"
export LAYA_RESUME_CHECKPOINT="${LAYA_RESUME_CHECKPOINT:-}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

cd "$REPO_ROOT"
test -s "$CASES_PATH" || { echo "dataset is missing or empty: $CASES_PATH (build one with tools/build_laya_dataset.py)" >&2; exit 2; }
# Pin the concrete content-addressed file so a moving latest.jsonl cannot
# change the dataset under a running or resumed job.
CASES_PATH="$(readlink -f "$CASES_PATH")"
test -f "$MODEL_DIR/rl_agent_config.json" || { echo "base Laya model is missing rl_agent_config.json: $MODEL_DIR" >&2; exit 2; }
test -f "$MODEL_DIR/model.safetensors" || { echo "base Laya weights are missing: $MODEL_DIR/model.safetensors" >&2; exit 2; }

if pgrep -f "tools/train_laya_4060.py" >/dev/null 2>&1; then
  echo "a Laya training process is already running" >&2
  exit 3
fi

"$REPO_ROOT/.venv/bin/python" tools/laya_preflight.py
mkdir -p "$LOG_DIR" "$CHECKPOINT_DIR"
export LAYA_PROGRESS_PATH="${LAYA_PROGRESS_PATH:-$CHECKPOINT_DIR/progress.json}"
# Written only after the duplicate-run guard so a refused start cannot
# overwrite the status of the job that is already running.
printf '{"status":"starting","cases":"%s","updated_at":"%s"}\n' "$CASES_PATH" "$(date --iso-8601=seconds)" >"$STATUS_PATH"

# Run the trainer as a child and forward TERM/INT to it, so stopping this
# wrapper lets Python checkpoint at its next optimizer step (exit 130/143).
run_forwarding_signals() {
  "$@" &
  local child=$!
  trap 'kill -TERM "$child" 2>/dev/null || true' TERM INT
  local code=0
  wait "$child" || code=$?
  while kill -0 "$child" 2>/dev/null; do
    code=0
    wait "$child" || code=$?
  done
  trap - TERM INT
  return "$code"
}

if [ "${LAYA_FOREGROUND:-0}" = "1" ]; then
  printf '{"status":"training","updated_at":"%s"}\n' "$(date --iso-8601=seconds)" >"$STATUS_PATH"
  on_exit_foreground() {
    code=$?
    if [ "$code" -eq 130 ] || [ "$code" -eq 143 ]; then
      printf '{"status":"interrupted","exit_code":%s,"updated_at":"%s"}\n' "$code" "$(date --iso-8601=seconds)" >"$STATUS_PATH"
    elif [ "$code" -ne 0 ]; then
      printf '{"status":"failed","exit_code":%s,"updated_at":"%s"}\n' "$code" "$(date --iso-8601=seconds)" >"$STATUS_PATH"
    fi
  }
  trap on_exit_foreground EXIT
  run_forwarding_signals "$REPO_ROOT/.venv/bin/python" -u tools/train_laya_4060.py \
    --cases "$CASES_PATH" \
    --model-dir "$MODEL_DIR" \
    --output-dir "$CHECKPOINT_DIR" >>"$LOG_PATH" 2>&1
  printf '{"status":"trained","updated_at":"%s"}\n' "$(date --iso-8601=seconds)" >"$STATUS_PATH"
  "$REPO_ROOT/.venv/bin/python" -u tools/evaluate_laya.py \
    --cases "$CASES_PATH" \
    --checkpoint "$CHECKPOINT_DIR" \
    --base-model "$MODEL_DIR" \
    --split held_out \
    --output "$CHECKPOINT_DIR/evaluation-held-out.json" >>"$LOG_PATH" 2>&1
  printf '{"status":"held_out_evaluated","updated_at":"%s"}\n' "$(date --iso-8601=seconds)" >"$STATUS_PATH"
  "$REPO_ROOT/.venv/bin/python" -u tools/evaluate_laya.py \
    --cases "$CASES_PATH" \
    --checkpoint "$CHECKPOINT_DIR" \
    --base-model "$MODEL_DIR" \
    --split test \
    --output "$CHECKPOINT_DIR/evaluation-test.json" >>"$LOG_PATH" 2>&1
  printf '{"status":"complete","updated_at":"%s"}\n' "$(date --iso-8601=seconds)" >"$STATUS_PATH"
  trap - EXIT
  exit 0
fi

nohup env \
  LAYA_CASES_PATH="$CASES_PATH" \
  LAYA_MODEL_DIR="$MODEL_DIR" \
  LAYA_CHECKPOINT_DIR="$CHECKPOINT_DIR" \
  LAYA_PROGRESS_PATH="$LAYA_PROGRESS_PATH" \
  LAYA_MAX_LEN="$LAYA_MAX_LEN" \
  LAYA_HEAD_MAX_LEN="$LAYA_HEAD_MAX_LEN" \
  LAYA_MICRO_BATCH="$LAYA_MICRO_BATCH" \
  LAYA_GRADIENT_ACCUMULATION="$LAYA_GRADIENT_ACCUMULATION" \
  LAYA_GRADIENT_CHECKPOINTING="$LAYA_GRADIENT_CHECKPOINTING" \
  LAYA_TORCH_COMPILE="$LAYA_TORCH_COMPILE" \
  LAYA_ATTN_IMPLEMENTATION="$LAYA_ATTN_IMPLEMENTATION" \
  LAYA_FIXED_OPTION_WIDTH="$LAYA_FIXED_OPTION_WIDTH" \
  LAYA_FUSED_ADAMW="$LAYA_FUSED_ADAMW" \
  LAYA_LABEL_SMOOTHING="$LAYA_LABEL_SMOOTHING" \
  LAYA_EARLY_STOP_PATIENCE="$LAYA_EARLY_STOP_PATIENCE" \
  LAYA_CHECKPOINT_INTERVAL="$LAYA_CHECKPOINT_INTERVAL" \
  LAYA_CHECKPOINT_KEEP="$LAYA_CHECKPOINT_KEEP" \
  LAYA_RESUME_CHECKPOINT="$LAYA_RESUME_CHECKPOINT" \
  PYTORCH_CUDA_ALLOC_CONF="$PYTORCH_CUDA_ALLOC_CONF" \
  bash -c '
    set -euo pipefail
    python_bin="$1"
    cases_path="$2"
    model_dir="$3"
    checkpoint_dir="$4"
    status_path="$checkpoint_dir/training-status.json"
    write_status() {
      printf "{\"status\":\"%s\",\"updated_at\":\"%s\"}\n" "$1" "$(date --iso-8601=seconds)" >"$status_path"
    }
    write_status "training"
    on_exit() {
      code=$?
      if [ "$code" -eq 130 ] || [ "$code" -eq 143 ]; then write_status "interrupted";
      elif [ "$code" -ne 0 ]; then write_status "failed"; fi
    }
    trap on_exit EXIT
    "$python_bin" -u tools/train_laya_4060.py \
      --cases "$cases_path" \
      --model-dir "$model_dir" \
      --output-dir "$checkpoint_dir" &
    child=$!
    trap "kill -TERM $child 2>/dev/null || true" TERM INT
    code=0
    wait "$child" || code=$?
    while kill -0 "$child" 2>/dev/null; do code=0; wait "$child" || code=$?; done
    trap - TERM INT
    if [ "$code" -ne 0 ]; then exit "$code"; fi
    write_status "trained"
    "$python_bin" -u tools/evaluate_laya.py \
      --cases "$cases_path" \
      --checkpoint "$checkpoint_dir" \
      --base-model "$model_dir" \
      --split held_out \
      --output "$checkpoint_dir/evaluation-held-out.json"
    write_status "held_out_evaluated"
    "$python_bin" -u tools/evaluate_laya.py \
      --cases "$cases_path" \
      --checkpoint "$checkpoint_dir" \
      --base-model "$model_dir" \
      --split test \
      --output "$checkpoint_dir/evaluation-test.json"
    write_status "complete"
  ' _ "$REPO_ROOT/.venv/bin/python" "$CASES_PATH" "$MODEL_DIR" "$CHECKPOINT_DIR" \
  >"$LOG_PATH" 2>&1 < /dev/null &

PID=$!
echo "started Laya training pid=$PID"
echo "log=$LOG_PATH"
echo "checkpoint=$CHECKPOINT_DIR"
