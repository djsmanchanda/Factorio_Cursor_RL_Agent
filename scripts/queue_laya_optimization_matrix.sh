#!/usr/bin/env bash
# Path: scripts/queue_laya_optimization_matrix.sh
# Purpose: Queue isolated Laya optimization pilots until the host GPU is idle.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CASES_PATH="${LAYA_CASES_PATH:-/tmp/factorio-laya-cases-all.jsonl}"
MODEL_DIR="${LAYA_MODEL_DIR:-$REPO_ROOT/data/laya-base}"
LOG_ROOT="${LAYA_MATRIX_LOG_DIR:-$REPO_ROOT/logs/laya-optimization-matrix}"
RESULTS_PATH="$LOG_ROOT/results.jsonl"
PILOT_STEPS="${LAYA_PILOT_MICROSTEPS:-96}"
PILOT_WARMUP="${LAYA_PILOT_WARMUP:-8}"
MAX_ITEMS="${LAYA_PILOT_MAX_ITEMS:-600}"
BUSY_MIB="${LAYA_GPU_BUSY_MIB:-300}"
MAX_TEMP="${LAYA_GPU_START_TEMP_C:-65}"
STABLE_CHECKS="${LAYA_GPU_STABLE_CHECKS:-1}"

cd "$REPO_ROOT"
test -s "$CASES_PATH" || { echo "dataset is missing or empty: $CASES_PATH" >&2; exit 2; }
test -f "$MODEL_DIR/rl_agent_config.json" || { echo "model config is missing: $MODEL_DIR" >&2; exit 2; }
test -f "$MODEL_DIR/model.safetensors" || { echo "model weights are missing: $MODEL_DIR/model.safetensors" >&2; exit 2; }
command -v nvidia-smi >/dev/null 2>&1 || { echo "nvidia-smi is required; queue will not run blind" >&2; exit 2; }

mkdir -p "$LOG_ROOT"
exec 9>"$LOG_ROOT/matrix.lock"
flock -n 9 || { echo "another Laya optimization matrix is already queued" >&2; exit 3; }

gpu_is_idle() {
  local busy temp
  busy="$(nvidia-smi --query-compute-apps=used_memory --format=csv,noheader,nounits 2>/dev/null | awk -v limit="$BUSY_MIB" 'BEGIN{s=0} $1+0 > limit {s += $1} END{print s+0}')"
  temp="$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits 2>/dev/null | head -n1 | tr -d ' ' || true)"
  [[ "$busy" =~ ^[0-9]+$ ]] && [[ "$temp" =~ ^[0-9]+$ ]] && (( busy == 0 )) && (( temp <= MAX_TEMP ))
}

wait_for_idle_gpu() {
  local stable=0
  while true; do
    if pgrep -af 'tools/(train_laya_4060|evaluate_laya)\.py' >/dev/null 2>&1; then
      stable=0
    elif gpu_is_idle; then
      stable=$((stable + 1))
      if (( stable >= STABLE_CHECKS )); then
        return 0
      fi
    else
      stable=0
    fi
    sleep "${LAYA_GPU_POLL_SECONDS:-5}"
  done
}

run_profile() {
  local name="$1"
  shift
  if [ -f "$RESULTS_PATH" ] && rg -F '"name": "'"$name"'"' "$RESULTS_PATH" >/dev/null 2>&1; then
    echo "$(date --iso-8601=seconds) skipping completed $name" | tee -a "$LOG_ROOT/matrix.log"
    return 0
  fi
  local log_path="$LOG_ROOT/${name}.log"
  echo "$(date --iso-8601=seconds) starting $name" | tee -a "$LOG_ROOT/matrix.log"
  wait_for_idle_gpu
  set +e
  PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}" \
    "$REPO_ROOT/.venv/bin/python" tools/laya_optimization_bench.py \
      --profile "$name" \
      --cases "$CASES_PATH" \
      --model-dir "$MODEL_DIR" \
      --max-items "$MAX_ITEMS" \
      --warmup "$PILOT_WARMUP" \
      --microsteps "$PILOT_STEPS" \
      "$@" >"$log_path" 2>&1
  local code=$?
  set -e
  local result
  result="$(tail -n1 "$log_path" 2>/dev/null || true)"
  if ! "$REPO_ROOT/.venv/bin/python" -c 'import json,sys; json.loads(sys.stdin.read())' <<<"$result" >/dev/null 2>&1; then
    result="$(printf '%s\n' '{"status":"failed","profile":{"name":"'"$name"'"},"error":"pilot produced no JSON result","exit_code":'"$code"'}')"
  fi
  printf '%s\n' "$result" >>"$RESULTS_PATH"
  echo "$(date --iso-8601=seconds) finished $name status=$(printf '%s' "$result" | "$REPO_ROOT/.venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin).get("status","unknown"))')" | tee -a "$LOG_ROOT/matrix.log"
  return 0
}

printf '%s\n' "$(date --iso-8601=seconds) queued Laya optimization matrix" >>"$LOG_ROOT/matrix.log"
touch "$RESULTS_PATH"

# Each profile runs in a fresh Python process. The matrix preserves the
# effective batch (32) and isolates speed/memory ideas from quality changes.
run_profile baseline_sdpa_ckpt_on_m2 --micro-batch 2 --accum 16 --checkpointing --attention sdpa --sync-metrics
run_profile metric_sync_off_m2 --micro-batch 2 --accum 16 --checkpointing --attention sdpa
run_profile sdpa_ckpt_off_m2 --micro-batch 2 --accum 16 --attention sdpa --sync-metrics
run_profile flex_ckpt_on_m2 --micro-batch 2 --accum 16 --checkpointing --attention flex_attention --sync-metrics
run_profile fused_adamw_sdpa_ckpt_on_m2 --micro-batch 2 --accum 16 --checkpointing --attention sdpa --fused-adamw --sync-metrics
run_profile flex_ckpt_on_m4 --micro-batch 4 --accum 8 --checkpointing --attention flex_attention --sync-metrics
run_profile flex_ckpt_off_m4 --micro-batch 4 --accum 8 --attention flex_attention --sync-metrics
run_profile bf16_frozen_flex_ckpt_off_m4 --micro-batch 4 --accum 8 --attention flex_attention --bf16-frozen --sync-metrics
run_profile compile_sdpa_ckpt_off_m2 --micro-batch 2 --accum 16 --compile --attention sdpa --fixed-options 6 --sync-metrics
run_profile compile_flex_ckpt_on_m2 --micro-batch 2 --accum 16 --compile --checkpointing --attention flex_attention --fixed-options 6 --sync-metrics
run_profile compile_flex_ckpt_off_m2 --micro-batch 2 --accum 16 --compile --attention flex_attention --fixed-options 6 --sync-metrics
run_profile compile_flex_ckpt_off_m2_nosync --micro-batch 2 --accum 16 --compile --attention flex_attention --fixed-options 6

echo "$(date --iso-8601=seconds) matrix complete results=$RESULTS_PATH" | tee -a "$LOG_ROOT/matrix.log"
