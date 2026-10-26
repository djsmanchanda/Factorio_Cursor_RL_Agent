<!-- Path: docs/rl/laya_helper_runs/model-launch-20261026/laya-kernel-result.md | Purpose: Record measured Laya kernel selection and the additional authorized 4060 fit. -->

# English Laya kernel qualification — 2026-10-27

The additional English Laya fit uses native PyTorch SDPA flash/efficient attention,
BF16 autocast with fp32 parameter storage, non-reentrant encoder/head gradient
checkpointing and fused AdamW on Starship's unlocked RTX 4060 Laptop (8 GiB).
The hardware unlock was left unchanged. This selection is supported by actual
forward/backward profiler events, finite optimizer updates and a fresh-process
save/reload check. It is not a claim of the globally fastest possible profile.

Both candidates used the same pinned source/data, released initialization, seed917
and three optimizer updates with accumulation8. Each processed the longest,
largest-head and largest-menu training examples eight times: 24 throwaway records,
159,456 tokens, no rejected/truncated inputs. Held-out probes were forward-only.

| Profile | Three step times (s) | Total (s) | Peak allocated bytes | Fresh reload max logit error |
|---|---|---:|---:|---:|
| Native SDPA | 26.040 / 24.226 / 22.181 | 72.447 | 3,494,769,664 | 0.0 |
| Compiled FlexAttention | 27.664 / 16.532 / 27.258 | 71.454 | 3,443,321,856 | 0.0 |

Timings include profiling/compilation and different shapes; they do not establish
a general warm speed advantage. Flex was faster on the second shape and slower
on the third. SDPA was selected for comparable aggregate time and varying-length
execution without Flex compilation. Both actually executed accelerated kernels;
no extra `flash_attn` package is required by the selected path.

The first pre-clipping gradients contain 27,624,449 elements. Cosine similarity
is 0.952895 and relative L2 difference is 0.317321 (max absolute0.101440).
First losses are 3.341428 and3.349533. These measurements do **not** establish
numerical equivalence; kernel-dependent dropout/numerics have not been separated.
Each candidate's own saved model reloads exactly, including longest calibration
and dev forward checks. Earlier guarded matching errors are archived as failed
preflight attempts; they did not produce a full fit.

Released English base `convaiinnovations/laya` is pinned at
`7b928d828b7b0e022f929d9bd2e44165aa270148`; six assets match Hub Git/LFS
identities. The unchanged weak-v3 data contains1729train/376calibration/401dev
rows. All2506 lossless inputs fit: maximum6820/5926/6057tokens respectively,
maximum25 options. The isolated serializer retains the full state/menu and
excludes targets from tokens. Nine CPU tests protect packing, integrity and
evaluation source/token joins. Reviews' checkpoint identity, reload memory,
preflight coverage and cooperative lock findings have been addressed.

The one-epoch fit launched2026-10-27 00:55:07IST as bounded user unit
`laya-english-warmstart-20261027.service`, invocation
`a6943ccb4ea349bdbb866cb585bed599`, RuntimeMaxSec28800. Recipe:
attention-only LoRA rank8/alpha16/dropout.05 plus decision head/type embedding/
scorer, frozen act head, direct soft CE, microbatch1/accumulation8, seed917,
217 updates; LR2e-5 LoRA/1e-4 head. The fit completed all217 updates and1729 records in1207.19seconds (20.12minutes), with zero truncations/rejections and3,496,233,984peak allocated CUDA bytes. Checkpoint `checkpoint-A` contains10files/336,304,724bytes and is backed up locally; its fresh-process reload gives exact0.0logit error.
Intermediate snapshots are evaluation artifacts, not tested resumable states.

Remote evidence lives in
`/home/djsm/Github_Desktop/Factorio_Cursor_RL_Agent/data/laya-warmstart/20261027`;
local copied receipts are under
`data/laya-orchestration/laya-warmstart-20261027/evidence/`.
Kernel comparison, exact argv, root/recipe freezes, native profiler receipts and
launch identity are retained separately. This is local-receipt weak imitation,
not RL, optimality, Factorio improvement or promotion. The original four released
base-model cohort arms and completed Kev checkpoint remain unchanged.

Lifecycle: the fit is complete, frozen, backed up and reload-verified. Complete the sequential777-row released/trained evaluations and fit calibration on376 rows only; never overwrite checkpoint metadata or promote automatically.
No root, reboot, unlock, production, installed-mod or push action was performed.
