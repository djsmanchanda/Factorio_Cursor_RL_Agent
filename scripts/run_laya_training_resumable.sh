#!/usr/bin/env bash
# Path: scripts/run_laya_training_resumable.sh
# Purpose: Run Laya in the foreground and resume from the newest atomic checkpoint after failure.

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Same durable defaults as scripts/run_laya_training.sh, so retries, progress,
# and the checkpoint probe below all look at one directory.
export LAYA_CHECKPOINT_DIR="${LAYA_CHECKPOINT_DIR:-$REPO_ROOT/data/laya-checkpoints/laya-4060}"
export LAYA_PROGRESS_PATH="${LAYA_PROGRESS_PATH:-$LAYA_CHECKPOINT_DIR/progress.json}"
DATASET_DIR="$REPO_ROOT/data/laya-datasets"
CASES_PATH="${LAYA_CASES_PATH:-$DATASET_DIR/latest.jsonl}"
MAX_RETRIES="${LAYA_MAX_RETRIES:-3}"
RETRY_DELAY="${LAYA_RETRY_DELAY_SECONDS:-5}"

# Auto-build is explicit and reproducible: only the databases/case files
# named in LAYA_DATASET_DATABASES / LAYA_DATASET_CASES_JSONL (colon-separated,
# in the given order) are used, always at step level with seed-derived splits.
if [ ! -s "$CASES_PATH" ] && [ "${LAYA_AUTOBUILD_CASES:-1}" != "0" ]; then
  build_args=()
  IFS=':' read -r -a databases <<<"${LAYA_DATASET_DATABASES:-}"
  for database in "${databases[@]}"; do
    [ -n "$database" ] && build_args+=(--database "$database")
  done
  IFS=':' read -r -a case_files <<<"${LAYA_DATASET_CASES_JSONL:-}"
  for case_file in "${case_files[@]}"; do
    [ -n "$case_file" ] && build_args+=(--cases-jsonl "$case_file")
  done
  if [ "${#build_args[@]}" -eq 0 ]; then
    echo "dataset is missing: $CASES_PATH; set LAYA_DATASET_DATABASES and/or LAYA_DATASET_CASES_JSONL to rebuild it" >&2
    exit 2
  fi
  if [ -n "${LAYA_CASES_PATH:-}" ]; then
    build_args+=(--output "$CASES_PATH")
  fi
  echo "$(date --iso-8601=seconds) rebuilding Laya dataset: ${build_args[*]}" >&2
  "$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/tools/build_laya_dataset.py" \
    "${build_args[@]}" --step-level --derive-seed-splits || exit 2
fi
test -s "$CASES_PATH" || { echo "dataset is missing or empty: $CASES_PATH" >&2; exit 2; }
# Every retry must train on the same bytes even if latest.jsonl moves.
export LAYA_CASES_PATH="$(readlink -f "$CASES_PATH")"

resume_target="${LAYA_RESUME_CHECKPOINT:-latest}"
attempt=0

while :; do
  attempt=$((attempt + 1))
  echo "$(date --iso-8601=seconds) starting resumable Laya attempt ${attempt}/${MAX_RETRIES}" >&2
  LAYA_FOREGROUND=1 \
  LAYA_RESUME_CHECKPOINT="$resume_target" \
  bash "$REPO_ROOT/scripts/run_laya_training.sh" &
  child=$!
  # Forward stop signals so the trainer checkpoints before exiting.
  trap 'kill -TERM "$child" 2>/dev/null || true' TERM INT
  wait "$child"
  status=$?
  while kill -0 "$child" 2>/dev/null; do
    wait "$child"
    status=$?
  done
  trap - TERM INT
  if [ "$status" -eq 0 ]; then
    exit 0
  fi
  if [ "$status" -eq 130 ] || [ "$status" -eq 143 ]; then
    echo "Laya training was stopped by a signal after checkpointing; not retrying" >&2
    exit "$status"
  fi
  if [ "$status" -eq 3 ]; then
    echo "another Laya training process is running; not retrying" >&2
    exit "$status"
  fi
  if [ "$attempt" -ge "$MAX_RETRIES" ]; then
    echo "Laya training failed after ${attempt} attempts" >&2
    exit "$status"
  fi
  if ! find "$LAYA_CHECKPOINT_DIR/checkpoints" \
      -mindepth 1 -maxdepth 1 -type d -name 'step-*' -print -quit 2>/dev/null | grep -q .; then
    echo "no periodic checkpoint exists yet; retrying from the initial checkpoint" >&2
  else
    echo "training failed; retrying from the newest periodic checkpoint" >&2
  fi
  resume_target=latest
  sleep "$RETRY_DELAY"
done
