<!-- Path: docs/archive/rl/Laya_Optimization_DS.md | Purpose: Kernel/overhead/training-dynamics audit of Laya fine-tuning with a ranked improvement list for the final implementation agent. -->

# Laya optimization — kernels, overhead & training dynamics (DS track)

Companion: [hardware-effectiveness handoff](Laya_Optimization_Mu.md) (RTX 4060 Laptop + i9-13900H
profiles, VRAM budgets, CPU/thermal plan). This doc covers kernel-level findings, overhead
measurements, the pure-CUDA question, and training-quality findings. It does not duplicate the
Mu hardware ladder except where a verdict needs its number.

## 0. Scope and constraints honored

- The optimized run finished before any GPU probe: `training-status.json = complete`
  (`2026-09-20T09:09:51+05:30`), both evaluations written. No Laya train/evaluate process was
  ever touched; every GPU command carried an in-command guard that aborted on any Laya process
  or non-observer compute app (it aborted twice on parallel-agent probes; those probes were
  never disturbed).
- Untouched: Factorio, Nauvis, saves, both checkpoint dirs, the dataset (read-only opens),
  the protected observer process (PID 1207278). No repo file was modified; all scratch lives
  in `/tmp` (`/tmp/laya_len_profile.py`, `/tmp/laya_model_facts.py`,
  `/tmp/laya_state_overflow.py`, `/tmp/laya_gpu_pilot.py`, `/tmp/laya_gpu_pilot2.py`,
  `/tmp/laya_pilot_results.json`).
- Evidence levels reached: code inspection + CPU measurements + partial GPU pilot. Clean
  GPU A/B re-timing was blocked by a shared GPU (short-lived parallel probes kept appearing);
  every GPU number below is labeled measured-vs-predicted.

## 1. Measured baseline (production run, 5.4 h, rock solid)

From `/tmp/laya-4060-checkpoint-optimized/progress_history.jsonl` (2,768 optimizer-step records):

- Wall: 19,383 s for 44,288 microsteps → **437 ms/microstep** at micro-batch 2, L=1024.
- True throughput: **4.58 examples/s** (history's `examples_per_second` field is mislabeled
  micro-batches/s in this run; code now computes it correctly), **4,692 tokens/s** (median).
- Peak VRAM: **2,706,465,792 B (~2.52 GiB)** — only 33% of the 8 GiB card.
- Throughput is flat across all 5.4 h (2.28–2.29): **no thermal decay** at sustained load.
- Grad norm median 38.3, max **2,283** (clipped at 1.0 every optimizer step — clipping works).
- Loss 3.88 → noisy 0.3–2.0 (expected for RLCD sampling loss, not a bug).
- MFU estimate: ~421 M params × ~3.3 GFLOP/token (fwd+bwd+ckpt-recompute) × 4,692 tok/s ≈
  **15 TFLOPS**, i.e. roughly **15% of the 4060 Laptop's BF16 peak**. The reported "90%
  utilization" is occupancy, not efficiency — the headroom is real.

## 2. Bottleneck anatomy (handoff Q3: answered by measurement)

Phase split per microstep (CUDA events, flex/ckptON/m2 pilot; timing caveat: parallel GPU
probe may have shared SMs, but proportions are corroborated by the production wall average):

| Phase | Median | Share |
|---|---:|---:|
| H2D copies (5 tensors) | 0.09 ms | ~0% |
| Forward (28-layer encoder + head) | 123 ms | ~31% |
| RLCD sample/reward/loss | 0.9 ms | ~0% |
| Per-step `.cpu()` logging syncs | 0.15 ms | ~0% |
| Backward (incl. checkpoint recompute) | 269 ms | ~69% |

Conclusions:

1. **The input pipeline is not CPU-bound.** All tokenization is upfront
   ([laya_finetune.py](/home/djsm/Github_Desktop/Factorio_Cursor_RL_Agent/training/laya_finetune.py:232),
   215 items/s single-threaded); per-step `_collate` handles 2–8 items (~µs–ms). No DataLoader,
   no workers, nothing to prefetch.
2. **H2D, RLCD math, and logging syncs are jointly < 2 ms of ~400 ms.** Pinned memory,
   non-blocking transfers, and fused RLCD kernels cannot move the needle — do them last or never.
3. **Backward dominates (69%)** because gradient checkpointing recomputes the 28-layer encoder
   on every backward while peak VRAM sits at 33%. This is the single biggest structural
   inefficiency: paying +~50% FLOPs to save memory the card does not need at micro-batch 2.
4. Attention backend is **not** the bottleneck (a theory this audit killed by measurement):
   10 full-attention layers already hit PyTorch flash (zero-padding ⇒ mask `None`); the 18
   sliding-window layers take a dense 4D-mask SDPA fallback. Switching all layers to Triton
   `flex_attention` (verified **logit-identical**, max abs diff 0.0156 on identical weights)
   measured only ~10% faster. Real, but secondary.

## 3. VRAM anatomy (why the matrix rows behave as they do)

Model facts (meta-device count + pilot accounting): 421.3 M params total = 394.8 M frozen
encoder + 26.5 M trainable head (`head` 25.2 M, `scorer` 1.1 M, `act_head` 0.3 M) + ~1.4 M LoRA.

- Frozen encoder is **FP32** (`model.to("cuda")` never downcasts; autocast re-casts per use):
  ~1.58 GiB dead weight plus per-use cast traffic. Casting frozen params once to BF16 is
  bit-safe for training (cast is deterministic; LoRA/head untouched) and frees ~0.8 GiB.
- Measured: flex/ckptOFF/micro-4 allocates **6.88 GiB own** → OOM on 8 GiB with FP32 base
  (rotary FP32 temps, gate+up MLP pairs, flex saved Q/K/V/O, full activation residency).
  So handoff row "Throughput fast" (m4/ckptOFF, FP32 base) is **rejected as stated**; it may
  fit with BF16-frozen (~6.0 GiB, still too close to the 90% ceiling — verify, don't assume).
- ckptOFF at micro-2 was never cleanly timed (blocked); footprint extrapolates to ~4.5–5 GiB
  (fits) with backward cut ~40%. **This is the highest-value unmeasured profile (P1).**

## 4. Ranked improvement list (test-backed where stated)

Order = expected gain × confidence ÷ risk. Predicted gains are estimates; each row names its
verifier (`/tmp/laya_gpu_pilot2.py PROFILE`, quiet GPU required).

1. **P1: ckpt OFF at micro-batch 2 / accum 16.** Predicted **+30–45%** (removes encoder
   recompute from the 69% backward share). Fits (~5 GiB). Risk: low (same math, more
   residency). Verify: `P1`, then 100-optimizer-step pilot + held-out gate.
2. **P6: micro-batch 4 / accum 8 / ckpt ON** (handoff "Memory-safe fast"). Predicted
   **+15–40%** (same FLOPs, better GEMM saturation, halved per-step Python overhead).
   Fits (~3.5–4 GiB). Verify: `P6`. Combines with (4).
3. **Cast frozen weights to BF16 once after load.** Saves ~0.8 GiB + ~5–10% cast traffic;
   de-risks (1)/(2) and any ckptOFF attempt. Verify: `P5`-family + logit-equivalence check
   (expect diffs ~1e-2, BF16 noise floor). One-line-class change in the final agent's patch.
4. **Sliding-window attention → `flex_attention`.** Verified equivalent (0.0156 max diff);
   ~10% gain; one-line flip of `model.encoder.config._attn_implementation` (dispatch + mask
   creation both read it at forward time; transformers compiles flex internally once, 23.5 s
   one-time cost). attention_dropout is 0.0 so flex training is allowed. Verify clean `P2`.
5. **Cache the two BlockMasks.** Shapes are perfectly static (100% of sequences are exactly
   1024 tokens, §6) yet masks are rebuilt every forward. Minor gain, nearly free.
6. **Scoped `torch.compile`.** Static shapes make this viable, but size the prize first with
   `PF` (profiler capture — never ran). If Python/launch overhead is material, compile the
   decision head + scorer + RLCD block (`reduce-overhead`/CUDA-graphs friendly); full-encoder
   `max-autotune` is a time sink on a laptop — try last. Predicted +10–25% *if* overhead
   share confirms. Must re-verify logits (flex uses `dynamic=False` already).
7. **Logging hygiene.** Move the per-microstep entropy/loss `.cpu()` conversions
   ([laya_finetune.py](/home/djsm/Github_Desktop/Factorio_Cursor_RL_Agent/training/laya_finetune.py:293)
   and `:307`) to optimizer-step boundaries. ~0.5 ms/step today — hygiene, not a win.
8. **Do not bother (measured negligible):** fused/foreach AdamW (optimizer runs 1/16 steps),
   pinned + non-blocking H2D (~100 KB/step), fused RLCD kernel (0.9 ms block), DataLoader
   workers (nothing to load), sequence packing/sorting (zero padding waste — every sequence
   is exactly 1024).
9. **Do not do:** flash-attn source build (flex already covers the gap; build pain on a
   laptop with zero evidence of further gain), FP8 training (immature stack for LoRA+RLCD,
   risks the logits contract, and the workload is memory/overhead-bound, not FLOPs-bound),
   micro-8/ckptOFF (OOM-verified direction), effective-batch change without LR scaling and a
   held-out gate.

## 5. Pure CUDA / C++ verdict: don't rewrite; the gap is already covered

A custom CUDA/C++ stack (fused ModernBERT block, hand-written attention, C++ data path) would
be a large, fragile effort that duplicates what PyTorch + Triton already provide here:

- The only slow custom-path candidate (sliding-window attention) is fixed by flex/Triton
  with 3 lines of config — measured, equivalent, no build system.
- The dominant cost is structural (checkpoint recompute + FP32 traffic + micro-batch-2
  under-saturation), not kernel quality; no C++ rewrite removes recompute.
- The RLCD block is 0.9 ms; fusing it in Triton/CUDA buys nothing end-to-end.
- The genuine C++-adjacent wins (BF16 weights, graphs, foreach optimizer) are PyTorch
  one-liners, not a rewrite.

Narrow exception: if `PF` shows the two `nn.TransformerEncoderLayer` head layers
(`laya/common.py:DecisionModel.forward`, `src_key_padding_mask` path) to be pathologically
launch-bound, a single fused Triton head block would be the only custom kernel worth scoping.
Size it first; don't pre-build it.

## 6. Context-length findings (handoff Q5: answered by measurement)

- **100% of train (22,143) and calibration (2,901) items tokenize to exactly 1024.**
  Zero padding waste; batch shapes perfectly static; no packing/sorting gain exists.
- Longer context genuinely occurs and is being cut: raw state needs p50 1,765 / p95 3,619 /
  max 3,673 tokens against ~800–940 tokens of room; **mean 59%, median 47% of state tokens
  are truncated** (right-truncate keeps the state's beginning). Head+options are tiny
  (p50 83, max 225).
- So: cutting `max_len` below 1024 buys throughput by amputating state (a training-quality
  cost, not a free win); raising to 2048/4096 covers p50/p95 at ~2–4× attention+MLP cost
  (a quality experiment for the training track, gated on held-out). State-JSON compaction
  and truncation-side (keep head vs tail) are training-track questions, flagged not solved.

## 7. Training-performance findings (not benchmaxing)

Real gaps found in the objective/wiring (all in
[/home/djsm/Github_Desktop/Factorio_Cursor_RL_Agent/training/laya_finetune.py](/home/djsm/Github_Desktop/Factorio_Cursor_RL_Agent/training/laya_finetune.py)):

1. **The escalation (`act`) head receives exactly zero gradient.**
   `objective_loss + 0.0 * act.sum()` keeps it in the graph with a 0 multiplier, so every
   `act_probability` in every evaluation report comes from an untrained head. If escalation
   gating matters downstream, this is the top training-quality bug. Fixing it changes the
   objective → separate gated experiment, not part of a speed pilot.
2. **Dataset is 100% `choice` questions** (1 question/case; options ∈ {2, 6}). Consequences:
   score/noul head capacity, `type_emb` rows 1–2, and temperatures[1,2] (default 1.0, confirmed
   in the run log) are all untrained dead weight; the RPS reward branch never fires. Either
   diversify the data or narrow the head — a training-track decision.
3. **Advantage normalization is noisy**: `(reward − mean) / std` over group×batch = 8 samples
   ([laya_finetune.py](/home/djsm/Github_Desktop/Factorio_Cursor_RL_Agent/training/laya_finetune.py:299)).
   A per-question baseline would stabilize credit assignment (objective change → gate it).
4. **Gradient spikes to 2,283** (median 38) — clipping at 1.0 is load-bearing; keep it, and
   treat any LR/optimizer change as suspect until grad-norm traces stay bounded.
5. **Optimized run vs first baseline is mixed, not a clean win**: held-out acc 0.689 vs 0.711
   (regression), test acc 0.630 vs 0.604 (gain), NLL improved on both, ECE worse on both.
   Same seed but different micro-batching ⇒ different trajectory. The final agent needs a
   tiebreak protocol (held-out acc primary, NLL/Brier/ECE secondary, seed/batch-order
   discipline) before declaring any profile "preserved".

## 8. Verification protocol for the final implementation agent

1. Quiet-GPU preconditions: no `train_laya_4060`/`evaluate_laya` process, no foreign compute
   app, `nvidia-smi` free > 7 GiB. (Parallel probes were the blocker in this audit.)
2. Run `/tmp/laya_gpu_pilot2.py P0 P1 P6 P2 P2b PF` (per-profile processes, OOM-isolated,
   ~2 min each, writes only `/tmp/laya_pilot2_results.json`). `P0` must reproduce ~437 ms /
   ~4.6 ex/s / ~2.7 GiB or the harness is untrusted.
3. Apply: BF16-frozen + winning (P1 or P6) + flex + mask cache. Re-run `P2b`-equivalent.
4. 100-optimizer-step pilot in a **distinct** `/tmp` checkpoint dir (never the baselines),
   same seed/dataset/model-hash/effective-batch/group/loss/splits/eval.
5. Gates: no OOM, peak < 90% VRAM (~7.4 GiB), no thermal throttle (`nvidia-smi dmon`),
   sdpa-vs-flex logit diff ≤ ~2e-2, held-out tiebreak protocol from §7.5, loss/label contract
   byte-identical (sequence builder untouched).
6. Only then: full run + held-out/test evaluation + promotion evidence via
   [evaluate_laya.py](/home/djsm/Github_Desktop/Factorio_Cursor_RL_Agent/tools/evaluate_laya.py).

## 9. What this audit could not verify (do not treat as done)

- Clean `P0/P1/P6/P2/P2b` timings and the `PF` profiler capture (shared-GPU contention).
- Exact SDPA sliding-window fallback backend (cuDNN vs mem-efficient vs math).
- Whether full-encoder `torch.compile` graphs cleanly (scoped compile recommended instead).
- Any held-out effect of speed changes (requires the 100-step pilots in §8).

## 10. Raw pointers

- Baseline history: `/tmp/laya-4060-checkpoint-optimized/progress_history.jsonl`
- Evals: `/tmp/laya-4060-checkpoint-optimized/evaluation-{held-out,test}.json`,
  `/tmp/laya-4060-checkpoint/evaluation-{held-out,test}.json`
- Training loop:
  [training/laya_finetune.py](/home/djsm/Github_Desktop/Factorio_Cursor_RL_Agent/training/laya_finetune.py);
  profile/preflight:
  [training/laya_runtime.py](/home/djsm/Github_Desktop/Factorio_Cursor_RL_Agent/training/laya_runtime.py);
  model: `.venv/.../laya/common.py` (`DecisionModel`, `build_model`, `proper_reward`);
  attention dispatch: `.venv/.../transformers/models/modernbert/modeling_modernbert.py`,
  `transformers/{masking_utils.py,integrations/{sdpa_attention,flex_attention}.py}`
