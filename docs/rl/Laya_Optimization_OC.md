<!-- Path: docs/rl/Laya_Optimization_OC.md | Purpose: Kernel-level optimization handoff for Laya training on the RTX 4060 Laptop / i9-13900H. -->
# Laya Training Optimization Handoff (RTX 4060 Laptop / i9-13900H)

Audience: the implementation agent that will change `training/laya_finetune.py`,
`training/laya_runtime.py`, and `scripts/*laya*`. This document reports
measurements only; **no production file was modified** while producing it.

## 0. Scope, constraints, and evidence level

- Read-only audit. The active run and its checkpoint were not touched. The run
  finished on its own (status `trained`, held-out/test evaluations written at
  09:09); benchmarks were started only after the GPU was confirmed idle and were
  guarded against concurrent CUDA processes (a parallel agent was also using the
  GPU; contaminated samples were discarded and re-run).
- No Factorio/Nauvis interaction, no real-base authority, no dataset or
  checkpoint mutation, no production-code modification. The only repository
  change is this document.
- Evidence level: **integration-level pilots**. The benchmark harness imports
  and executes the production model construction, `_collate`, autocast, RLCD
  objective, and AdamW step verbatim (copied from `train_local`), on the frozen
  dataset, at the exact configured profile. It does **not** yet include the
  full 4-epoch run plus `tools/evaluate_laya.py`; that remains the acceptance
  step for the implementation agent (Section 8).

Environment: i9-13900H (14C/20T), 38 GiB RAM, RTX 4060 Laptop 8 GiB (7.65 GiB
usable), ~90 W sustained package cap (94 W observed peak), Linux, torch
2.14.0+cu130 (CUDA 13.0, cuDNN 9.24), transformers 5.17.0, peft 0.21.0,
triton 3.8.0, no flash-attn, no xformers. Compute capability 8.9 (Ada).

Model: ModernBERT-large encoder (28 layers, hidden 1024, intermediate 2624,
16 heads, 18 sliding-window-128 layers + 10 global layers) + 2-layer decision
head + scorer + `act_head`; LoRA r=8/alpha=16 on `Wqkv`/`Wo`; decision modules
(`head`, `type_emb`, `scorer`, `act_head`) fully trainable.

Dataset: 22,143 train cases / 22,143 items (1 question per case in train),
2,901 calibration, 3,084 held-out, 1,581 test.

Frozen baseline measured from the live optimized run: 4 epochs, 2,768 optimizer
steps, 44,288 microsteps, 19,382 s, **4.58 examples/s**, 4,679 tokens/s,
peak 2.52 GiB, `micro_batch=2`, `accum=16`, checkpointing on, eager SDPA.

## 1. Executive summary

1. **The step is 99.9 % GPU compute; CPU/data work is noise.** CPU collate is
   0.4–1.5 ms, H2D 0.2–0.3 ms per 232–2,100 ms step. Do not spend effort on
   dataloaders, pinning, or prefetching. See Section 4.
2. **Gradient checkpointing is the largest single overhead.** At the same
   micro-batch (2), disabling it is mathematically exact and measured
   **4.42 → 6.12 examples/s (+38 %)** at 5.68 GiB peak (fits in 7.65 GiB).
3. **`torch.compile` is the largest kernel-level win.** With checkpointing off
   it reaches **7.58 examples/s (+71 %)**; adding `flex_attention` for the
   encoder reaches **8.61 examples/s (+95 %)** at the same 5.29 GiB.
4. **Larger micro-batches are slower, not faster, on this laptop.** mb4/mb8
   lose 7–16 % per example even with compiled kernels; the ~90 W power cap and
   memory bandwidth dominate. `micro_batch=4, accum=8` and `micro_batch=8,
   accum=4` should be rejected, and mb4/ckpt-off OOMs cleanly. See Section 3.
5. **Kernel substitutions are numerically neutral at the bf16 noise floor.**
   bf16-vs-fp32 drift (max |Δlogit| 0.031, mean |ΔCE| 0.010) exceeds the drift
   of compiled SDPA (0.031/0.0078) and compiled flex (0.027/0.0066). See
   Section 6.
6. **Two 1,600-microstep pilots (100 optimizer steps) were run end-to-end**:
   live baseline eager/ckpt-on at 4.565 ex/s (438 ms) vs recommended
   compile+flex/ckpt-off at 8.52 ex/s (235 ms). Per-step CE diverges pointwise
   (bf16 chaos), but windowed means track until ~step 900 and the fast profile
   ends *lower* on training CE (final 200 microsteps: 0.905 vs 1.097). Training
   CE is not the acceptance metric; held-out/test is. See Section 6.
7. **Every training sequence is exactly 1,024 tokens — 100 % at the cap — and**
   **the state JSON is truncated mid-`options` in every case.** This is a
   training-quality issue, not just a speed one; the median case loses 820 of
   its ~1,765 state tokens and the mean drop is 65 %. See Section 7.
8. Recommended target profile: **`micro_batch=2`, `accum=16`,
   `gradient_checkpointing=0`, `torch.compile`, `flex_attention`, fixed option
   width**, ~8.5 examples/s sustained (measured over 1,600 microsteps = 100
   optimizer steps), 5.30 GiB peak, ~2.9 h for 4 epochs instead of 5.4 h, and
   lower energy per example.
9. If 5.3 GiB is judged too tight on the 8 GiB card, the safe fallback is the
   same profile with checkpointing on: **6.40 examples/s at 2.48 GiB (+45 %)**.

## 2. Where the time goes

Per-microstep GPU attribution at the live baseline (`mb2`, `accum16`,
checkpointing on), 32 clean microsteps, CUDA events + `torch.profiler`:

| Region | ms/microstep | Share |
|---|---:|---:|
| Forward (incl. recompute forward) | 153.8 | 34 % |
| Backward | 290.6 | 64 % |
| RLCD loss/reward/entropy (GPU) | 4.4 | 1.0 % |
| Optimizer + grad-clip | 3.3 | 0.7 % |
| H2D | 0.2 | 0.05 % |
| CPU `_collate` (not on GPU) | 0.4 | 0.1 % |
| **Wall per microstep** | **452** | GPU sum = wall (fully saturated, no bubbles) |

Kernel profile (12 microsteps, self CUDA 5.11 s ≈ 426 ms/microstep):

| Kernel family | Share |
|---|---:|
| bf16 GEMMs (`aten::mm`/`matmul`, sm80 GEMM kernels) | ~41–45 % |
| Elementwise + device copies (`copy_`, `_to_copy`, `mul`, `add`) | ~22–25 % |
| SDPA **memory-efficient** attention forward+backward (cutlass `fmha`) | ~13 % |
| Dropout (LoRA 0.05 + head 0.1) | ~3 % |
| Everything else (softmax, norms, RoPE, gather, optimizer) | remainder |

Notes:
- SDPA already selects the memory-efficient backend, not math and not flash
  (verified kernel names `fmha_cutlassF/B_bf16_...`). There is no accidental
  eager-attention fallback.
- The 22–25 % elementwise/copy share is launch- and bandwidth-bound work that
  `torch.compile` fuses; this is where compile mostly wins (loss region
  2.8 → 0.8 ms, forward 148 → 97 ms).
- Metric syncs (`float(entropy...cpu())` per microstep, line 293, and
  `float(objective_loss...cpu())`, line 307) cost nothing measurable
  (326.8 ms sync-on vs 328.4 ms sync-off). Keep them for observability.

## 3. Measured profile matrix (effective batch 32 in all rows)

All rows are clean GPU runs; pyTorch CUDA allocator `expandable_segments:True`;
32 measured microsteps unless noted. `ex/s` = examples/second.

| Profile | ms/microstep | ex/s | tok/s | peak GiB | Verdict |
|---|---:|---:|---:|---:|---|
| eager, mb2/a16, ckpt on (live baseline) | 452.4 | 4.42 | 4,527 | 2.52 | reference |
| eager, mb2/a16, ckpt **off** | 326.8 | 6.12 | 6,267 | 5.68 | accept (memory-tight) |
| eager, mb4/a8, ckpt on | 968.1 | 4.13 | 4,231 | 2.95 | reject: slower |
| eager, mb8/a4, ckpt on | 2,099.7 | 3.81 | 3,902 | 3.82 | reject: slower |
| eager, mb4/a8, ckpt **off** | — | — | — | OOM | reject: clean OOM |
| compile, mb2/a16, ckpt off | 263.9 | 7.58 | 7,759 | 5.29 | accept |
| compile, mb2/a16, ckpt on | 345.0 | 5.80 | 5,937 | 2.49 | safe fallback |
| **compile+flex, mb2/a16, ckpt off** | **232.0** | **8.61** | **8,812** | **5.29** | **recommended** |
| compile+flex, mb2/a16, ckpt off, 100 opt steps | 234.8 | 8.52 | 8,724 | 5.30 | sustained, 1,600 microsteps |
| compile+flex, mb2/a16, ckpt on | 312.5 | 6.40 | 6,555 | 2.48 | safe fallback |
| compile+flex, mb4/a8, ckpt on (fixed width, warmed) | 599.7 | 6.67 | 6,830 | 2.96 | reject: slower than mb2 |

Observations:
- Removing checkpointing removes one full extra forward per step. Eager
  `fwd` is unchanged (154 → 148 ms) while `bwd` falls 291 → 173 ms because the
  recompute forward disappears; with compile+flex the step becomes
  `fwd` 97 ms + `bwd` 131 ms + loss 0.8 ms + opt 0.75 ms.
- Batch scaling is negative. mb2→mb4 roughly triples per-unit work with
  compiled kernels. This tracks the power cap: the 100-step pilot peaked at
  94 W / 88 °C; mb4 peaked at ~90 W. Energy per example (peak-power estimate):
  mb2/ckpt-off ≈ 9.3 J, mb4/ckpt-on ≈ 13.5 J.
- `torch.compile` one-off cost: ~30–60 s for model build + inductor compile
  (finite shape set, see Section 5.2); measured end-to-end 6 m 59 s for the
  1,600-microstep pilot including compile.

## 4. Answers to the five optimization questions

**Q1 — Can mb4/a8 preserve effective batch 32 and raise optimizer steps/s
without exceeding the envelope?**
No. mb4/a8 is 968 ms/microstep eager (4.13 ex/s) and 600 ms with
compile+flex+ckpt-on (6.67 ex/s), both below mb2 (8.52–8.61 ex/s). The
effective batch is preserved (`loss/accum`, same 692 optimizer steps/epoch),
but the laptop is power/bandwidth-limited, so larger GEMMs do not pay off.
mb4/ckpt-off also OOMs cleanly (confirmed with no foreign GPU process).
Keep `micro_batch=2, accumulation=16`.

**Q2 — Can checkpointing be disabled at mb4/mb8?**
No. At mb2/ckpt-off the measured peak is 5.68 GiB, and the activation delta is
≈ 2.8 GiB; mb4 would need ≈ 8.5 GiB > 7.65 GiB and OOMs (observed). mb8 is
out of reach. Disable checkpointing at **mb2 only**.

**Q3 — Is the input pipeline CPU-bound, or is GPU execution the bottleneck?**
GPU. Forward+backward+loss+optimizer account for 99.9 % of wall time; GPU event
sum equals wall (no bubbles). Measured per microstep: `_collate` 0.4–1.5 ms,
H2D 0.2–0.3 ms, forward 97–154 ms, backward 131–291 ms, optimizer 0.75–3.3 ms.
One-time item build/tokenization is 74 s of a 19,382 s run (0.4 %). Pinned
tensors, non-blocking transfers, prefetch workers, and dataloader processes
have nothing to recover. The 20 hardware threads are idle during training.

**Q4 — Would pinned tensors, fused AdamW, or `torch.compile` help?**
- Pinned/non-blocking: no (transfers are 0.05 % of the step).
- Fused AdamW: at most ~0.5 % (optimizer is 0.75–3.3 ms/step). Optional,
  zero-risk: `torch.optim.AdamW(..., fused=True)`.
- `torch.compile`: **yes, +71 %** with checkpointing off (4.42 → 7.58 ex/s).
- `flex_attention` for the 28-layer encoder on top: **+13 % more** (7.58 →
  8.61 ex/s), because 18 of 28 layers are sliding-window and flex skips fully
  masked blocks that memory-efficient SDPA still multiplies.
Neither changes the loss contract beyond bf16 reordering (Section 6).

**Q5 — Does longer context occur; can a measured context profile be selected?**
Longer context occurs in **100 %** of training cases: every sequence is exactly
1,024 tokens because the state text is longer than the budget in every case
(min 1,489 state tokens, p50 1,765, mean 2,546, max 3,673). No padding waste
exists, so no padding-based speedup is available. The cap is binding and cuts
the state mid-`options` (Section 7). Recommended direction: raise `max_len`
(e.g. 1,280–1,536) **with checkpointing on**, and/or reprioritize the
serialized state so candidate `options` and `recent_history` survive. Both
change the observation distribution and require held-out validation.

## 5. Recommended implementation plan

### 5.1 Target profile

```text
LAYA_MICRO_BATCH=2
LAYA_GRADIENT_ACCUMULATION=16          # effective batch 32, unchanged
LAYA_GRADIENT_CHECKPOINTING=0          # exact math, +38 % before kernels
PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
LAYA_TORCH_COMPILE=1                   # new switch
LAYA_ATTN_IMPLEMENTATION=flex_attention  # training only; predictor stays SDPA
LAYA_FIXED_OPTION_WIDTH=6              # one compiled shape (max_prefixes)
```

Expected: ~8.5 ex/s (≈1.86× the live 4.58 ex/s), 4 epochs ≈ 2.9 h, peak
5.30 GiB, and roughly 40 % less energy per example. A 4-epoch run at mb2/a16
has 692 optimizer steps/epoch (2,768 total), all evenly divisible, so no
partial-accumulation edge cases.

### 5.2 Code hooks (do not apply blind; validate first)

1. `training/laya_finetune.py:248-251` — checkpointing is already env-switched;
   pass `0` via `LAYA_GRADIENT_CHECKPOINTING` (no code change).
2. Set the encoder attention implementation after `get_peft_model` /
   checkpointing setup (around line 247), then compile after
   `model.to("cuda")` and `model.train()` (around line 256):
   ```python
   model.encoder.config._attn_implementation = os.environ.get(
       "LAYA_ATTN_IMPLEMENTATION", "sdpa"
   )
   # ... existing .to("cuda"), .train() ...
   if os.environ.get("LAYA_TORCH_COMPILE") == "1":
       model = torch.compile(model, mode="default", dynamic=False)
   ```
   `mode="default"` because `max-autotune` warns "Not enough SMs"; `dynamic=False`
   is correct because the option-width is fixed (below) and every sequence is
   exactly 1,024 tokens, so there is a single graph.
3. `training/laya_finetune.py:126-142` (`_collate`) — pad `marker_pos`,
   `marker_mask`, `target` to a fixed width 6 (`max_prefixes` in
   `data/laya-base/rl_agent_config.json`; max observed 6, p95 = 6, mean 3.75).
   This gives Inductor exactly one shape and removes all per-batch
   recompilation. Padding width costs nothing measurable (markers feed a
   gather + 2-layer scorer, not the encoder).
4. Warm all shapes once before the epoch loop (a single dummy
   forward+backward), then reset step counters/timers; otherwise the first
   optimizer-step report includes compile time.
5. Keep `torch.set_float32_matmul_precision("high")` and bf16 autocast.
6. `training/laya_runtime.py` / `LayaModelPredictor` — **leave SDPA and eager**
   for inference and evaluation. Flex/compile are training-only, so the
   deploy/eval path stays bit-comparable with the current checkpoints.
7. Persist the new fields (`torch_compile`, `attn_implementation`,
   `gradient_checkpointing`, `micro_batch`) into the checkpoint's
   `rl_agent_config.json` (already writes mb/accum/ckpt at lines 375-377) so a
   checkpoint hash is self-describing.
8. Optional: `fused=True` in the AdamW constructor (line 259).
9. `scripts/queue_laya_optimized.sh:29-31` — update the exported defaults to
   the target profile; add `PYTORCH_CUDA_ALLOC_CONF` in
   `scripts/run_laya_training.sh` near the `nohup env` block (line 29).

### 5.3 Memory handling on the 8 GiB card

- 5.30 GiB peak leaves ≈ 2.35 GiB headroom but the allocator must be told:
  `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`. Without it the same
  profile measured 5.71 GiB and is fragmentation-sensitive.
- Do not co-run CUDA workloads (the dashboard observer holds a 94 MiB context;
  another benchmarking agent intermittently holds 2–4 GiB). Any concurrent
  process can force an OOM at this profile.
- Compile cache: `TORCHINDUCTOR_CACHE_DIR` may be persisted under `logs/` to
  make the compile cost a one-time, first-run expense.

## 6. Numerical contract (why this does not change the objective)

Forward-equivalence test, 8 identical batches, identical weights, eval mode,
bf16 autocast; reference = eager SDPA. Values are max |Δ| over markers
(logits) and per-batch CE.

| Variant vs eager SDPA | max Δlogit | mean ΔCE | max ΔCE |
|---|---:|---:|---:|
| bf16 vs fp32 eager (noise floor) | 0.0313 | 0.0100 | 0.0181 |
| compiled SDPA | 0.0313 | 0.0078 | 0.0100 |
| compiled flex_attention | 0.0273 | 0.0066 | 0.0154 |
| eager flex_attention | 0.0332 | 0.0073 | 0.0150 |

Every kernel variant is inside the intrinsic bf16 reordering noise; no variant
changes the algorithm, the loss, or the RLCD sampling. Two caveats:

- `act_logits` diverges by up to 48 across all variants (the head amplifies
  microscopic hidden-state differences). It is multiplied by `0.0` in the
  objective (line 302), so it receives no gradient. Do not compare
  `rl_agent.act_probability` across backend changes.
- Micro-batch itself is **not** exactly semantics-neutral: `reward.std()` at
  line 299 normalizes over the whole `group_size × micro_batch` tensor, so
  changing mb alters advantages slightly. This is already visible in the live
  evidence: the first run (mb1/a32) held-out 0.7111 vs the optimized run
  (mb2/a16) 0.6890, with test moving the other way (0.6040 → 0.6300). The
  recommendation keeps mb2, so it does not introduce a new mb change.

### 6.1 100-optimizer-step pilot, baseline vs recommended

Both traces: same seed, same 1,600 microsteps (100 optimizer steps), same item
order, same RLCD settings. Baseline = eager, mb2/a16, checkpointing on (the
live profile, independently reproduced at 4.565 ex/s / 438 ms / 2.52 GiB).
Fast = compile + flex_attention, mb2/a16, checkpointing off (8.52 ex/s /
235 ms / 5.30 GiB / peak 94 W / 88 °C).

| Metric | Value |
|---|---|
| per-microstep abs ΔCE: mean / p50 / p95 / max | 0.098 / 0.037 / 0.501 / 1.897 |
| per-microstep abs Δobjective: mean / p50 / p95 / max | 0.388 / 0.294 / 1.089 / 2.504 |
| 100-microstep mean CE, first 8 blocks baseline | 1.390, 1.226, 1.271, 1.194, 1.168, 1.147, 1.171, 1.171 |
| 100-microstep mean CE, first 8 blocks fast | 1.378, 1.211, 1.265, 1.197, 1.169, 1.145, 1.165, 1.171 |
| final 200-microstep mean CE baseline / fast | 1.097 / 0.905 |

Interpretation: windowed CE tracks closely for the first ~900 microsteps, then
the two trajectories separate (bf16 rounding accumulates through the optimizer;
the objective itself is unchanged). The fast run ends with lower training CE,
but that is not evidence of generalization. Acceptance must be held-out/test
accuracy under the frozen evaluation protocol, not training loss. This also
explains why per-step loss comparisons between profiles are not a valid
equivalence test beyond short horizons.

## 7. Context profile finding (training quality)

Measured over all 22,143 train items:

| Quantity | min | p50 | mean | p95 | max |
|---|---:|---:|---:|---:|---:|
| Full state tokens | 1,489 | 1,765 | 2,546 | 3,619 | 3,673 |
| Room left for state at max_len=1024 | 799 | 941 | 891 | — | — |
| Dropped state tokens | 536 | 820 | 1,654 | 2,801 | 2,862 |

Dropped fraction is non-zero for **100 %** of cases: the median case loses 820
tokens (46 % of its state), the mean drop is 1,654 tokens (65 % of the mean
state), and the worst case loses 2,862. State key order in the dataset:
`acceptance_gap, active_failures, budget, current_transaction, goal_stack,
guidelines, objective, options, provenance, recent_history, run, state_digest,
version`. In a representative case the cumulative budget reaches 301 tokens
before `options`, which alone needs 885 tokens, so truncation lands inside
`options` and everything after (`provenance`, `recent_history`, `run`,
`state_digest`, `version`) is always lost. Candidate details are systematically
cut at the same point, which can bias learning toward earlier candidates.

Options, in ascending cost:
1. **Filter non-decision metadata** (`version`, `run`, `state_digest`,
   maybe `provenance`) from the rendered state in both
   `training/laya_finetune.py:105` and `training/laya_runtime.py:174`.
   No retraining contract change if applied to both, but the observation
   distribution changes, so re-validate.
2. **Serialize `options` first** (or split option descriptions into
   fixed per-option budgets) so no candidate is hidden.
3. **Raise `max_len` to 1,280–1,536** with checkpointing on; expect ~1.3–1.5×
   compute. Then re-measure `room` and dropped tokens.
4. Regenerate the dataset with the new state serialization if (1)/(2) are
   adopted, so train/inference stay identical.

Do not silently change the state order without re-running held-out; this is a
model-behavior change, not a kernel swap.

## 8. Acceptance protocol and rollback

1. **Pilot:** 100 optimizer steps (1,600 microsteps) at a **new**
   checkpoint dir (e.g. `/tmp/laya-4060-checkpoint-kernel-v2`), same seed,
   dataset, model hash, effective batch 32, group size 4, loss, calibration
   split. Verify the first ~100 microsteps of CE/objective match the baseline
   within bf16 noise (pointwise traces diverge after ~900 microsteps by design,
   Section 6.1, so do not use long-horizon loss equality as an acceptance
   test). Verify compile warmup does not leak into the first reported step,
   peak VRAM, and throughput.
2. **Full run:** 4 epochs, then `tools/evaluate_laya.py` on held-out and test.
3. **Reject if** any of: peak > 7.0 GiB (≈90 % of 7.65 GiB), sustained loss
   divergence beyond the bf16 trace envelope, or held-out regression versus the
   mb2/a16 reference (0.6890 accuracy, NLL 0.7256, Brier 0.3580, ECE 0.1038;
   test 0.6300 / 0.9089 / 0.4404 / 0.1356). Treat the older mb1/a32 numbers
   (held-out 0.7111) as a separate reference, not the acceptance baseline.
4. **Rollback:** keep `/tmp/laya-4060-checkpoint` and
   `/tmp/laya-4060-checkpoint-optimized` untouched; the new profile must write
   only to a fresh directory. Do not grant real-Nauvis authority.
5. Record in the new checkpoint: profile, compile/attention flags, measured
   throughput, peak VRAM, and the held-out/test report.

## 9. Rejected / not worth doing (with evidence)

| Idea | Verdict | Evidence |
|---|---|---|
| mb4/a8 or mb8/a4 (any ckpt) | reject | 4.13/3.81 ex/s eager; 6.67 ex/s compiled, all < mb2 |
| ckpt-off at mb4/mb8 | reject | clean OOM; projected ≈ 8.5 GiB > 7.65 GiB |
| Pinned tensors / non-blocking H2D | skip | H2D 0.2–0.3 ms/step (0.05 %) |
| Dataloader workers / prefetch | skip | collate 0.4–1.5 ms/step; GPU is 99.9 % busy |
| Remove per-step metric syncs | skip | 326.8 vs 328.4 ms (noise) |
| Fused AdamW | optional | ≤ 0.5 % (optimizer ≤ 3.3 ms of 232–2,100 ms) |
| flash-attn install | skip | flex_attention already covers sliding/global; no wheel needed |
| CUDA graphs (`mode="reduce-overhead"`) | untested follow-up | static shapes make it plausible; graph pools + 5.3 GiB may OOM |
| TF32/FP16 switch | skip | already bf16; no Ada throughput gain, fp16 adds scaling risk |
| Tokenization parallelism | skip | 74 s of 19,382 s (0.4 %); i9 threads idle |

## 10. Reproduction

Artifacts (temporary, outside the repo):
`/tmp/opencode/laya-bench/{prep_items.py,bench_step.py,profile_kernels.py,validate_flex.py,run_clean.sh,history_*.jsonl,stats.json}`.

```bash
# cached tokenized items + context stats (CPU)
.venv/bin/python /tmp/opencode/laya-bench/prep_items.py
# one profile (GPU guard + thermal/contamination monitor built in)
PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True .venv/bin/python \
  /tmp/opencode/laya-bench/bench_step.py --micro-batch 2 --accum 16 \
  --checkpointing off --microsteps 1600 --skip-microsteps 8 \
  --compile --attn-impl flex_attention \
  --loss-history /tmp/opencode/laya-bench/history.jsonl
# add --fixed-options 6 for the production single-graph variant
# (the 1,600-step pilot was measured without it, at the same 8.52 ex/s)
# kernel attribution
.venv/bin/python /tmp/opencode/laya-bench/profile_kernels.py \
  --micro-batch 2 --checkpointing on --microsteps 12
# equivalence check
.venv/bin/python /tmp/opencode/laya-bench/validate_flex.py
```

## 11. One-paragraph recommendation

Adopt `micro_batch=2 / accumulation=16 / gradient_checkpointing=0` together
with `torch.compile` and `flex_attention`, fixed option width 6, and
`PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`. This is measured at
8.52 examples/s over 100 optimizer steps (1.86× the live run), 5.30 GiB peak,
and energy per example down ~40 %, with kernel numerics inside the bf16 noise
floor and the RLCD objective untouched. Keep the checkpointing-on variant
(6.40 ex/s, 2.48 GiB) as the rollback if the 5.3 GiB peak is not acceptable.
Then spend the quality budget on Section 7 (state truncation), which currently
drops 65 % of the mean observation in every training case.
