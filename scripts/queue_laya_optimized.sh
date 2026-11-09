#!/usr/bin/env bash
# Path: scripts/queue_laya_optimized.sh
# Purpose: Wait for the current disposable Laya run, then launch a faster safe-profile retry.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CASES_PATH="${LAYA_CASES_PATH:-/tmp/factorio-laya-cases-all.jsonl}"
MODEL_DIR="${LAYA_MODEL_DIR:-$REPO_ROOT/data/laya-base}"
CHECKPOINT_DIR="${LAYA_FAST_CHECKPOINT_DIR:-/tmp/laya-4060-checkpoint-kernel-v2}"
LOG_DIR="${LAYA_LOG_DIR:-$REPO_ROOT/logs}"
QUEUE_LOG="$LOG_DIR/laya-optimized-queue.log"

cd "$REPO_ROOT"
test -s "$CASES_PATH" || { echo "dataset is missing or empty: $CASES_PATH" >&2; exit 2; }
test -f "$MODEL_DIR/rl_agent_config.json" || { echo "base model config is missing: $MODEL_DIR" >&2; exit 2; }
test -f "$MODEL_DIR/model.safetensors" || { echo "base model weights are missing: $MODEL_DIR" >&2; exit 2; }
mkdir -p "$LOG_DIR"

echo "$(date --iso-8601=seconds) queued optimized Laya run; waiting for active train/evaluate process" >>"$QUEUE_LOG"
while ps -eo args= | grep -E '[t]ools/(train_laya_4060|evaluate_laya)\.py' >/dev/null 2>&1; do
  sleep 30
done

echo "$(date --iso-8601=seconds) active run finished; starting optimized profile" >>"$QUEUE_LOG"
export LAYA_CASES_PATH="$CASES_PATH"
export LAYA_MODEL_DIR="$MODEL_DIR"
export LAYA_CHECKPOINT_DIR="$CHECKPOINT_DIR"
export LAYA_MICRO_BATCH="${LAYA_MICRO_BATCH:-2}"
export LAYA_GRADIENT_ACCUMULATION="${LAYA_GRADIENT_ACCUMULATION:-16}"
export LAYA_GRADIENT_CHECKPOINTING="${LAYA_GRADIENT_CHECKPOINTING:-0}"
export LAYA_TORCH_COMPILE="${LAYA_TORCH_COMPILE:-1}"
export LAYA_ATTN_IMPLEMENTATION="${LAYA_ATTN_IMPLEMENTATION:-flex_attention}"
export LAYA_FIXED_OPTION_WIDTH="${LAYA_FIXED_OPTION_WIDTH:-6}"
export LAYA_FUSED_ADAMW="${LAYA_FUSED_ADAMW:-0}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"
exec bash "$REPO_ROOT/scripts/run_laya_training.sh" >>"$QUEUE_LOG" 2>&1
