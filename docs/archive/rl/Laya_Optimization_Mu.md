<!-- Path: docs/archive/rl/Laya_Optimization_Mu.md | Purpose: Hardware-effectiveness handoff for Laya training on RTX 4060 Laptop + i9-13900H: measured baseline, VRAM budgets, profile ladder, CPU/thermal plan. -->

# Laya optimization — RTX 4060 Laptop + i9-13900H effectiveness (Mu track)

Companion: [kernels/overhead/training-dynamics handoff](Laya_Optimization_DS.md). That doc owns
the kernel analysis, the ranked kernel list, the pure-CUDA verdict, and training-quality bugs.
This doc owns the hardware-effective configuration: what this laptop can sustain, the exact
profile ladder with VRAM math, the CPU-side plan, and thermals.

## 0. Scope and constraints honored

Same audit, same rules as the DS track: the optimized run had completed (`complete` at
09:09:51 IST) before any probe; no Laya/Factorio/Nauvis/save/checkpoint/dataset state was
touched; no repo file modified; GPU probes were guard-aborted twice rather than compete with
parallel-agent probes. Scratch: `/tmp/laya_*` (see DS doc §8). Evidence: code inspection +
CPU measurements + partial GPU pilot; unmeasured rows are labeled.

## 1. Hardware profile (what this laptop actually is)

- **GPU: RTX 4060 Laptop, sm_89, 8 GiB / 128-bit / ~256 GB/s.** 4th-gen Tensor Cores with
  native BF16 (the run already uses BF16 autocast + TF32 — correct). Ada FP8 exists in
  silicon but the training path (Transformer Engine + per-tensor scaling for LoRA+RLCD) is
  immature here and the workload is memory/overhead-bound, not FLOPs-bound: **stay BF16.**
- **CPU: i9-13900H, 6 P-cores + 8 E-cores / 20 threads.** No NUMA. Training's GPU phase
  needs ~1–2 threads (collate + loop); tokenization startup is embarrassingly parallel.
- **PCIe Gen4 x8** (typical for this chassis): H2D is ~100 KB/microstep — the bus is
  irrelevant; ignore all pinning/non-blocking advice for throughput purposes.
- **Stack:** torch 2.14.0+cu130, transformers 5.17, PEFT 0.21, triton 3.8 (flex-ready),
  flash-attn absent (and not needed — flex covers the gap, DS doc §4.4).
- OS: Arch Linux, Hyprland; `nvidia-smi` works on host (sandboxed shells cannot see it).

## 2. Measured baseline on this hardware

Production optimized run (micro-2 / accum-16 / ckpt-ON / sdpa):

- 437 ms/microstep wall, **4.58 ex/s, 4,692 tok/s**, peak **2.52 GiB** (33% of VRAM).
- Flat throughput over 5.4 h ⇒ sustained clocks hold; observed 55 °C idle, 79 °C under a
  foreign 100%-GPU probe — no throttle signature in the training trace.
- ~15 TFLOPS inked vs ~100 TFLOPS BF16 peak ⇒ **~15% MFU**. "90% utilization" measures
  occupancy; the card is busy waiting on memory traffic, recompute, and small-batch
  under-saturation. Optimize tokens/s and time-to-target-metric, never util%.

## 3. VRAM budget (8 GiB card, 90% ceiling ≈ 7.4 GiB)

Measured (M) or extrapolated (E) peak per profile, FP32 base unless noted:

| Profile | Micro/Accum/Ckpt/Attn | Peak | Verdict |
|---|---:|---:|---|
| P0 production | 2/16/ON/sdpa | 2.52 GiB (M) | Reference |
| P6 matrix row 1 | 4/8/ON/sdpa | ~3.5–4 GiB (E) | Try first |
| P2b matrix + flex | 4/8/ON/flex | ~3.5–4 GiB (E) | Try with P6 |
| P1 ckpt OFF | 2/16/OFF/sdpa | ~4.5–5 GiB (E) | Highest-value unmeasured |
| P3 (killed) | 4/8/OFF/flex FP32 | 6.88 GiB own (M) | **OOM — rejected as stated** |
| P5 (unverified) | 4/8/OFF/flex BF16-frozen | ~6.0 GiB (E) | Too close to ceiling; verify only |

Budget anatomy: frozen FP32 encoder 1.58 GiB (→ ~0.79 GiB after BF16 cast), head+grads+AdamW
~0.4 GiB, LoRA negligible, activations the remainder (ckpt-ON ≈ 0.9 GiB @m2; ckpt-OFF ≈
4–5 GiB @m4 — rotary FP32 temps, gate+up MLP pairs, flex saved Q/K/V/O all resident).

## 4. Recommended profile ladder (exact settings)

Keep seed, dataset, model hash, effective batch 32, group 4, loss, splits, eval protocol.
`LAYA_GRADIENT_CHECKPOINTING=1/0`, `LAYA_MICRO_BATCH`, `LAYA_GRADIENT_ACCUMULATION`;
flex + BF16-frozen need the final agent's small patch (DS doc §4.3–4.4), then:

1. **P6: `MICRO_BATCH=4 ACCUM=8 CHECKPOINTING=1` + flex + BF16-frozen.** Predicted
   +25–50% throughput at ~3–3.5 GiB. Safest big step; run first.
2. **P1: `MICRO_BATCH=2 ACCUM=16 CHECKPOINTING=0` + flex + BF16-frozen.** Predicted
   +35–55% at ~4–4.5 GiB. Biggest single structural win (removes recompute); measure
   against P6 and take the faster.
3. **P2b** is step 1's flex component measured separately — keep it only if it beats P6
   cleanly (expected ~10%).
4. Stop conditions per row: OOM, peak > 7.4 GiB, thermal throttle, logit diff > ~2e-2,
   held-out tiebreak fail (DS doc §7.5). Never overwrite `/tmp/laya-4060-checkpoint{,-optimized}`.

Predicted end state: **~6.5–7.5 ex/s (≈6,700–7,700 tok/s), ~3–4.5 GiB peak**, full run in
~3–3.5 h instead of 5.4 h. The remaining time is GEMM/memory traffic at micro-2/4 — the
honest floor without changing batch math or context.

## 5. CPU-side plan (i9-13900H)

- **Parallelize startup tokenization.** `build_training_items` is single-threaded
  (22,143 items in ~103 s + calibration ~20 s, 215 items/s). Shard cases over a
  `multiprocessing` pool pinned to P-cores → ~25 s. One-time per run; do it, but it is
  not a throughput lever.
- **Threading:** leave OMP/MKL defaults during the GPU phase (CPU work is trivial);
  do not set high `OMP_NUM_THREADS` "for speed" — it only steals time from Factorio and
  the observer. Cap tokenize workers to P-cores (6), keep E-cores for Factorio training
  workers, the observer (port 8766), and the desktop.
- **Eval speed (iteration latency, not training):** `evaluate_laya.py` scores cases
  sequentially at ~67 ms/case (~3.5 min per held-out pass). Batching eval across cases
  (static L=1024 shapes!) plus eval-mode compile is the cheapest iteration win available.
  Same logits contract — pure engineering.

## 6. Thermal and power

- Observed: no throughput decay over 5.4 h sustained; 79 °C under foreign full load is
  normal laptop behavior. Current power is sustainable — the optimization goal is *less
  time at load*, not lower load.
- Monitor during pilots: `nvidia-smi dmon -s pucvmt -d 5` (power, util, clocks, VRAM,
  temp) plus clock-throttle reasons (`nvidia-smi -q -d CLOCK,THERMAL`).
- Laptop realities: VBIOS usually locks power limits (`-pl` may fail — try, don't depend
  on it); no Linux undervolt on this stack; a stand/cooler helps sustain boost but the
  trace shows it is not currently throttling. Persistence mode: harmless, optional.
- Never “optimize” by capping clocks to lower temperature while making the run longer —
  energy-to-solution is what matters, and shorter runs win it here.

## 7. What NOT to do on this hardware

- Build flash-attn from source (30–60+ min laptop build for a gap flex already closes).
- FP8 training (stack immaturity + objective risk + workload isn't FLOPs-bound).
- micro-8/ckptOFF or any profile projecting > 7 GiB (OOM-verified direction).
- Run GPU pilots while another compute app holds the card (this audit stood down twice;
  the final agent must do the same — contention corrupts both sides' timings).
- Chase utilization % or run concurrent GPU workloads (surrogate, local LLM, second
  pilot) alongside training.

## 8. Implementation checklist for the final agent

1. Quiet-GPU preconditions (DS doc §8.1).
2. `/tmp/laya_gpu_pilot2.py P0 P1 P6 P2 P2b PF` — confirm P0 ≈ 437 ms / 4.6 ex/s /
   2.7 GiB, then fill P1/P6/P2/P2b + profiler top-ops.
3. Patch (small, reviewable): BF16-frozen cast, `LAYA_ATTENTION` env → flex flip,
   BlockMask cache, logging-sync hygiene, P-core tokenize pool. No sequence-builder,
   loss, or schedule changes.
4. 100-optimizer-step pilot per §4 ladder in a fresh `/tmp` dir; gates from DS doc §8.5.
5. Full run + held-out/test evals + promotion evidence; report tok/s, step ms, peak GiB,
   held-out tiebreak numbers, and `nvidia-smi dmon` excerpt.
6. Separately (training track, not speed): act-head zero-gradient, all-choice data,
   advantage-noise, and state-truncation findings (DS doc §7) — each needs its own
   gated experiment, never bundled into a speed change.

## 9. Open questions (hardware side)

- Clean P1/P6 confirmation (blocked by shared GPU in this audit).
- Whether laptop VBIOS permits a power-limit efficiency point that beats stock in
  energy-to-solution (measure, don't assume; stock is fine).
- Eval batching speedup factor (predicted 3–6× on the eval passes; unimplemented).
