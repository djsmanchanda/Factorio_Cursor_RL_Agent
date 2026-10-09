# Path: docs/rl/Laya_Optimization_Decision.md
# Purpose: Decision record for the RTX 4060 Laya training optimization proposals.

# Laya optimization decision

Date: 2026-09-20

## Decision

Implement the OC measured speed profile as an opt-in, isolated training profile:

```text
micro_batch=2
gradient_accumulation=16       # effective batch remains 32
gradient_checkpointing=0
torch_compile=1
attention_implementation=flex_attention
fixed_option_width=6
PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
```

The existing checkpointing-on profile remains the rollback:

```text
micro_batch=2
gradient_accumulation=16
gradient_checkpointing=1
torch_compile=0
attention_implementation=sdpa
```

The fast profile writes to a new checkpoint directory and never overwrites the
previous checkpoints. It is still disposable training evidence; it does not
grant Nauvis authority.

## Comparison

| Proposal | Strong contribution | Evidence | Decision |
| --- | --- | --- | --- |
| AG | Finds the state truncation, gradient clipping, candidate, and unused escalation-head risks; recommends metric-sync cleanup and fused AdamW. | Mixed: some host measurements, some un-gated quality hypotheses. | Keep as separate quality work; do not bundle with the speed run. |
| DS | Conservative ranking, VRAM/thermal guardrails, no-CUDA rewrite, and explicit held-out gates. | Strong code inspection and partial pilots; some rows were still predictions. | Adopt its isolation, rollback, and acceptance gates. |
| Mu | Hardware-specific memory and thermal limits; rejects FP8, flash-attn builds, large batches, and CPU-side distractions. | Hardware analysis plus measured baseline; several candidate rows unmeasured. | Adopt its hardware constraints and fallback policy. |
| OC | Complete A/B integration pilots: checkpoint-off at micro-batch 2, compile, flex attention, and fixed shapes. | Strongest evidence: 8.52 examples/s over 100 optimizer steps at about 5.30 GiB versus 4.58 examples/s baseline at about 2.52 GiB. | Primary implementation. |

The reported speed is an engineering result, not a quality result. The earlier
checkpoints disagree on held-out accuracy (0.7111 versus 0.6890) while test
accuracy moves in the opposite direction (0.6040 versus 0.6300). Every full
run therefore needs held-out/test evaluation before promotion.

## Implemented controls

- Preflight now reports the selected batch, checkpointing, compile, attention,
  and fixed-option profile instead of silently reporting the default 1/32.
- Training reads the same profile from environment variables and persists it in
  `rl_agent_config.json`.
- Fixed option width removes marker-shape recompilation; a too-small width fails
  closed rather than silently dropping candidates.
- Compile/flex are training-only. Runtime prediction and evaluation stay eager
  SDPA for checkpoint compatibility.
- The queue defaults to a fresh `laya-4060-checkpoint-kernel-v2` directory and
  the measured fast profile. Set `LAYA_GRADIENT_CHECKPOINTING=1` and
  `LAYA_TORCH_COMPILE=0` for the rollback profile.

## Deliberately separate experiments

These are important, but changing them with the speed profile would make a
result uninterpretable:

1. Reorder/compact state serialization so `state_digest` and physical factory
   signals are not truncated by the redundant options JSON.
2. Preserve candidates beyond the first 20 and reject unlabeled/truncated gold
   actions explicitly.
3. Propagate case/question confidence and weights into the objective.
4. Correct partial gradient-accumulation normalization and report window-level
   pre/post-clip norms.
5. Decide how to train the currently zero-gradient escalation head and whether
   the dataset needs non-choice questions.
6. Validate any RLCD sigma/loss normalization change against a fresh held-out
   run.

## Acceptance gate

Run a new 100-optimizer-step pilot only on an idle GPU. Abort on CUDA OOM,
non-finite loss/entropy/gradient, peak allocation above 7.0 GiB, or thermal
throttling. Then run the full four-epoch profile and evaluate held-out and test
splits. Compare accuracy first, then NLL/Brier/ECE, while recording throughput,
peak allocated/reserved VRAM, and the selected profile. Keep the existing
checkpointing-on run as the immediate rollback.

## Queued hypothesis matrix

`scripts/queue_laya_optimization_matrix.sh` now runs the four audits as a
fresh-process matrix after the GPU is idle. It measures baseline and metric-sync
control, checkpointing on/off, SDPA/flex, fused AdamW, micro-batch 4, BF16-frozen
weights, and compile+flex with checkpointing on/off. Each row has its own log and
is appended to `logs/laya-optimization-matrix/results.jsonl`; an OOM marks only
that row and the queue continues.

The default idle confirmation is now one 5-second check to minimize downtime
between rows. Set `LAYA_GPU_STABLE_CHECKS=3` when a more conservative 15-second
confirmation is preferred.

Run it from the host terminal (where `nvidia-smi` can see the laptop GPU):

```bash
nohup bash scripts/queue_laya_optimization_matrix.sh \
  >logs/laya-optimization-matrix-launch.log 2>&1 </dev/null &
```

The matrix is deliberately a speed/memory pilot. It does not modify model
weights, write a promotion checkpoint, change state serialization, or run
Factorio. The full profile still requires the held-out/test gate above.

Pure CUDA/C++ rewriting, pinned-memory workers, FP8, flash-attn builds, and
larger micro-batches are not in scope: the audits either measured them as
irrelevant/slower or rejected them for memory and maintenance risk.
