<!-- Path: docs/rl/laya_helper_runs/model-launch-20261026/laya-training-result.md | Purpose: Record the completed optimized English Laya weak-v3 checkpoint, evaluation and limits. -->

# English Laya weak-v3 result — 2026-10-27

The additional user-requested fine-tune of **`convaiinnovations/laya` (English)**
is complete on Starship's unlocked RTX4060 Laptop. It uses native BF16 PyTorch
Flash/SDPA attention, fused AdamW and non-reentrant encoder/head checkpointing.
Both real accumulation8 kernel probes passed; SDPA was selected before the fit.
See the [kernel comparison](laya-kernel-result.md) for the mixed Flex timings and
measured gradient disagreement. No global-fastest or numerical-equivalence claim.

The single epoch completed217 optimizer updates over all1729 train records:
4,261,929 tokens, **1207.19seconds (20.12minutes)** in the training loop,
0.698seconds/record. Zero records were rejected/truncated; all217 gradient norms
were finite. CUDA peak allocated3,496,233,984bytes (3.26GiB), reserved
5,888,802,816bytes (5.48GiB); these differ from total driver/device usage.
The bounded user unit exited successfully and Starship's GPU is now free.
The final checkpoint reloads in a fresh process with **0.0 maximum logit error**.

Base revision `7b928d828b7b0e022f929d9bd2e44165aa270148` and six assets match
pinned Hub Git/LFS identities; base weight SHA256:
`891102d372688fc2a094dac56a384bc537b87c63f21f9f3dac0be2b7cbc8d86c`.
Attention LoRA rank8/alpha16/dropout.05 plus the decision head/type embedding/
scorer were trained; act head stayed frozen. Seed917, microbatch1/accumulation8,
LR2e-5 LoRA/1e-4 head, direct soft CE, no RLCD/smoothing/augmentation. The
unchanged weak-v3 split is1729train/376calibration/401dev. All2506 lossless
inputs fit the8192-token architecture: maxima6820/5926/6057, maximum25 options.
Targets never enter input tokens; held-out probes were forward-only.

Both released and trained models completed all777 held-out predictions with
zero errors. Every1554 prediction/source/input-token/menu/label/target join was
checked; the same new lossless serializer is used on both sides. Temperature
fits use only376 calibration rows. The401 dev rows were not used for training,
calibration fitting, checkpoint selection or another full fit.

| Dev metric (401 rows) | Released English Laya | This checkpoint |
|---|---:|---:|
| Weak-target top1 | 39/401 (9.73%) | 212/401 (52.87%) |
| Raw NLL | 2.437494 | 1.475205 |
| Calibration-fitted NLL | 1.656523 | 1.474701 |
| Calibration-fitted Brier (sum over options) | 0.791212 | 0.695224 |
| Calibration-fitted ECE (15 equal-width bins) | 0.126961 | 0.229601 |
| Fitted temperature | 100.0 (upper bound) | 0.972739 |

Paired dev counts:39 both correct,173 trained-only,0 released-only,189 neither.
Calibration top1 is40/376 versus190/376. The released optimum hits the declared
temperature bound; it is nearly flattened and does not repair ranking. Fitted
ECE is worse for the trained model even though NLL/Brier and weak agreement
improve; do not claim improved confidence calibration across every metric.
Released Laya's original option-bucket temperatures are not replaced by a
misleading single carried temperature. Only raw and independently fitted metrics
are compared here.

These are **local-receipt weak-imitation results**, not RL, optimal actions,
Factorio completion gains, terrain generalization or adoption. They measure
adaptation to `factorio-systemone-laya-exact-v1`, which differs from the existing
Laya runtime loader. The checkpoint is not a qualified Factorio serving bundle.
Kev's earlier higher weak-imitation result remains separate; these runs do not
isolate backbone capability or compare matched hyperparameter search budgets.

Local checkpoint:
`data/laya-models/checkpoints/laya-english-weak-v3-20261027`.
All10 files/336,304,724bytes were verified against the remote frozen hashes;
checkpoint manifest SHA256:
`e325e5bb2028ff744b8ee2ace3ec27aa242ccee0bc18b808dbc93fdc6da65f72`.
The unchanged six-file English base is also retained locally at
`data/laya-models/laya-english-base-7b928d8` for reconstruction. Intermediate
snapshots/trainer state are not tested resumable checkpoints.

Remote task:
`/home/djsm/Github_Desktop/Factorio_Cursor_RL_Agent/data/laya-warmstart/20261027`.
Local receipts, predictions, metrics and separate calibrators:
`data/laya-orchestration/laya-warmstart-20261027/evidence/`.
`offline-comparison.json` retains complete identities/metrics/paired counts.
Independent `offline-calibration-bundle.json` SHA256:
`3126d91fdc7194ec5c9e30e28d436bb91c39b360c8a374f4f213a383d1dea1bd`.
All original checkpoint hashes are unchanged after both evaluations/calibration.
Nine CPU tests pass, including lossless25-option packing, overflow/target
rejection, no label leakage, checkpoint identity and source/token joins; real
CUDA forward/backward/save/reload and full held-out evaluation provide the next
evidence level. No transitive runtime certification is implied.

Lifecycle: no more Starship fit, benchmark or evaluation is required for this
checkpoint. A later fine-tuned-model cohort needs an exact serializer adapter
and CPU/runtime/full-menu qualification before admission. The original120-job
released-model cohort is already active and unchanged by this checkpoint. Keep
its existing30-minute continuation and terminal/deadline accounting. No automatic
promotion, repeated full fit, root/reboot/unlock/production/mod change or push.
