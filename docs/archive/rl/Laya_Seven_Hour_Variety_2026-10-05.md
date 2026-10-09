<!-- Path: docs/archive/rl/Laya_Seven_Hour_Variety_2026-10-05.md | Purpose: Record prior terminal results and the isolated seven-hour tests and neural learning-rate comparison. -->

# Seven-hour tests and local model training

Authorized continuation of session `01a0fe0a-d649-7e70-9f6d-591c71aaed38`.
Window: **2026-10-05T03:03:39.146411+05:30 through 2026-10-05T10:03:39.146411+05:30**.

Prior campaigns are terminal. Mechanics matrix: 554 trials, 0/141 C2 completions;
262/354 component completions, 37 execution errors. Follow-up: 144/450 queued trials
recorded by cutoff; 15/17 components complete; C2 0/127, comprising 112 measured
misses, three execution errors, and twelve interrupted observations. Final fixed
C2 rechecks: **6/9 fixed versus 0/9 controls**, all 18 without execution errors;
earned checkpoints scored 3/3, 1/3, 2/3. These are guided exploration results.

The new actor uses frozen `b22ebfa`, twelve private workers on loopback game
34700–34711 and RCON 27600–27611, copied immutable baseline/mod inputs, and three
hash-verified earned C1 checkpoints. No shared save, installed mod, existing
worker, default policy, or production authority is changed by this campaign.

Queue: **441 trials**, comprising 135 fixed-compiler C2 screens and 306 component
tests over 51 fixture conditions. C2 comparisons use fresh policy seeds 71000–71002,
patience24/48/72, gate scales .85/1/1.15, buildable horizons2/4/5, dependency/combined
context, exploration .05/.15/.30, rolling measurement windows60/120/240 game
seconds, and 120/180-game-minute caps. Fixture seeds72000–72002 compare baseline
and combined context across producer capacity, transport distance, robot supply,
request buffering, input/sink interruptions, and machine/pole/belt repair.
Physical terrain is held fixed; new policy seeds do not establish unseen-terrain
generalization. Component fixtures are funded and reported separately from earned
world continuations. Changing measurement windows is an acceptance-condition
experiment, not a like-for-like production promotion comparison.

Dynamic scheduling keeps at most twelve workers active. If the queue finishes,
replication uses seed offsets10000 under the same deadline. Saved checkpoints are
due at two, four and six hours, with a final report at shutdown. Interrupted jobs
are incomplete observations. Each raw result additionally preserves its last
action's engine-counter measurement in `evidence/campaign-metrics.json`; that
measurement is distinct from the rolling phase acceptance window.

The read-only observer uses `opencode/space-bunny-free`, xhigh, with a120-second
pause after each review and a600-second review bound. Previous-session explicit
authorization for this observer was verified after the initial approval review
rejected the combined launch. Local tests launched separately; the reviewer then
approved the observer on that evidence. Reports are advisory and cannot change
the frozen matrix.

Local neural training is a separate experiment. Frozen training code is
`8d6db2f`. Its copied/revalidated completion-first-v4 dataset has **4324 train,
480 calibration,432 held-out,523 test** examples, episode-relative sampling,
zero exclusions and no scenario-seed overlap across splits. The old398-example
latest pointer was not used. Both candidates start from a private base-model copy
and train two epochs with2048-token context, micro-batch1, accumulation16,
checkpointing on and eager SDPA. One uses encoder/head learning rates
2.5e-5/1e-4; the other1.25e-5/5e-5. Separate subprocesses evaluate each candidate
on held-out and test splits after training. This workflow-health dataset does not
claim to train a C2 saved-world policy. Checkpoints remain experimental.
The GPU is exclusive to this comparison; metric/VRAM guards and the common
deadline bound it. No automatic policy promotion occurs.

Validation before/at launch:53 focused experiment checks passed; training checks
38 passed,1 skipped. GPU preflight reports ready with roughly7.4GB free VRAM.
All new service units have bounded systemd runtime and owned subprocess cleanup.

Units: `laya-variety-20261005.service`,
`laya-variety-observer-20261005.service`,
`laya-variety-training-20261005.service`.
Evidence root: `data/laya-experiments/variety-20261005/`.
Status: `progress.json`; training: `training/status.json`; observer:
`helper/state.json` and `helper/findings.md`; terminal reports:
`report.json`, `training/status.json` and per-candidate evaluation JSON.

Lifecycle: newly started private runners only. No user restart is required.
The predecessor's guarded shared-mod deployment previously completed without a
server restart; its recorded GUI reload requirement remains separate, and physical
returned-item delivery remains unverified.

Launch verification: all three owned units active; twelve private test workers, six component trials completed without execution errors. The first neural candidate recorded optimizer updates, zero context overflow across4324 training/480 calibration examples, and approximately2.6GB peak allocation. The second candidate and independent evaluations remain queued.
