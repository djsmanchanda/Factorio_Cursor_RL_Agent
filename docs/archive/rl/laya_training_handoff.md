<!-- Path: docs/archive/rl/laya_training_handoff.md | Purpose: Handoff for read-only optimization audits while a local Laya run remains active. -->

# Laya training optimization handoff

This handoff is for agents auditing speed, memory, and efficiency. The active
training process must not be stopped, modified, duplicated, or competed with.
Agents must not touch Factorio, Nauvis, deterministic runtime authority, saves,
or the active checkpoint directory.

## Live run snapshot

Evidence captured **2026-09-20 08:41 IST**:

- Active queue-launched process: PID `2329531` (host PID; do not terminate).
- Queue started the optimized run at `2026-09-20 03:36:20 IST` after the first
  training/evaluation pipeline completed.
- Dataset: 22,143 train cases; calibration/held-out/test remain independent.
- Profile: RTX 4060 Laptop GPU, BF16, `max_len=1024`, `head_max_len=256`.
- Current optimized profile: `micro_batch=2`, `gradient_accumulation=16`,
  `group_size=4`, gradient checkpointing enabled, 4 epochs.
- Progress: epoch 4/4, 2,607 / 2,768 optimizer steps, 41,712 microsteps.
- Throughput: 2.289 examples/s, 4,689 tokens/s.
- Peak allocated VRAM: 2,706,465,792 bytes (~2.52 GiB).
- Current ETA from the trainer: ~1,125 seconds (~19 minutes), followed by
  held-out and test evaluation.
- Current loss: 0.631; mean loss: 0.901; entropy: 0.635; gradient norm:
  156.995; reward mean: -0.032; reward std: 0.838.

The live progress source is:

```text
/tmp/laya-4060-checkpoint-optimized/progress.json
/tmp/laya-4060-checkpoint-optimized/progress_history.jsonl
```

The queue log is:

```text
logs/laya-optimized-queue.log
```

Note: the active process was launched before the telemetry label correction;
its `2.289` field is micro-batches/s. With micro-batch 2, the actual example
throughput is approximately **4.579 examples/s**. Future runs report that
correctly as `examples_per_second` and retain `microbatches_per_second`
separately.

The first completed run is preserved at `/tmp/laya-4060-checkpoint` and
reported:

- held-out accuracy `0.7111`, NLL `0.8361`, Brier `0.3630`, ECE `0.0910`;
- test accuracy `0.6040`, NLL `0.9446`, Brier `0.4220`, ECE `0.0783`;
- zero inference failures and disposable-shadow eligibility;
- real-Nauvis eligibility remains false.

## Optimization questions

Audit these without changing the active process:

1. Can micro-batch 4 / accumulation 8 preserve the effective batch of 32 and
   increase optimizer steps/s without exceeding the memory/thermal envelope?
2. Can gradient checkpointing be disabled at micro-batch 4 or 8 without an
   out-of-memory event and with a material throughput gain?
3. Is the input pipeline CPU-bound, or is GPU kernel execution the bottleneck?
   Measure host-to-device time, forward/backward time, optimizer time, and
   dataloader/tokenization time separately.
4. Would pinned tensors, non-blocking transfers, fused AdamW, or a carefully
   scoped `torch.compile` improve throughput without changing logits or the
   RLCD objective?
5. Does longer context actually occur in the cases? If not, can a measured
   context profile be selected without omitting required state fields?

## Safe experiment matrix for the next run

Keep the same seed, dataset, model hash, effective batch, RLCD group size,
loss, calibration split, and evaluation protocol. Run a 100-optimizer-step
pilot before any full run:

| Profile | Micro-batch | Accumulation | Checkpointing | Decision |
|---|---:|---:|---|---|
| Current baseline | 2 | 16 | on | Reference: 2.29 examples/s |
| Memory-safe fast | 4 | 8 | on | Try first |
| Throughput fast | 4 | 8 | off | Try only after pilot memory check |
| Aggressive | 8 | 4 | off | Only if profile remains below the VRAM ceiling |

Reject a profile if it OOMs, exceeds roughly 90% of total VRAM, materially
raises thermal throttling, changes the loss/label contract, or produces a
held-out regression. Store each pilot in a distinct checkpoint directory;
never overwrite the completed baseline.

## Relevant implementation points

- `training/laya_finetune.py`: sequence build, LoRA/head training loop,
  gradient accumulation, BF16 autocast, progress metrics, calibration.
- `training/laya_runtime.py`: local profile and preflight.
- `scripts/queue_laya_optimized.sh`: waits for train/evaluate completion and
  starts the separate optimized checkpoint.
- `scripts/run_laya_training.sh`: detached train + held-out/test evaluation.
- `tools/evaluate_laya.py`: exact accuracy, NLL, Brier, ECE, latency, and
  promotion evidence.
- `training/observation.py` and `tools/laya_observer.*`: dashboard telemetry.

## Hard constraints

- Do not stop or signal PID `2329531`.
- Do not launch any competing CUDA process while it is active.
- Do not stop Factorio or alter its GPU workload automatically.
- Do not grant Laya real-Nauvis authority.
- Do not replace uncertainty with deterministic fallback.
- Do not edit the active checkpoint or dataset while the run is writing.
- Optimization agents are read-only until the run and evaluations are done.
