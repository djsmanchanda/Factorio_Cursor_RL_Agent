<!-- Path: docs/rl/Laya_Background_Run_2026-10-03.md | Purpose: Record the authorized eight-hour lab training/evaluation window and shutdown boundary. -->

# Eight-hour Laya generalization run

The user authorized work for the next eight hours on October 3. The wall-clock
window is anchored at **03:58:02 IST**, with a hard stop by **11:58:02 IST today**
(06:28:02 UTC), plus up to two minutes for shutdown/checkpoint cleanup.
`laya-eight-hours-20261003.service` launched at about **04:09 IST** after setup;
it uses only the remaining part of that original window. It has no automatic
restart or successor schedule.

Run evidence: `data/laya-pi/background-20261003-generalization/`.
Code is frozen at `1b8e8a7` under its `code/` directory, alongside a hashed Git
archive and the explicit native worker roster. Subsequent worktree edits do not
change its learning/evaluation code. Runtime/mod identity and source-save hashes
are recorded in `experiment.json` and `worker-identities.json`.

## Work and limits

- Twelve disposable workers: **01–04 and 06–13**, one stepped slot each,
  game speed 32, three labellers. Workers 05/14, the real deterministic runtime
  and GUI profile are outside the allocation.
- Startup evaluates the scripted rollout and preserved r21-final student on
  all three connected-workflow levels, with 32 matched development seeds
  **62001–62032**. Runtime/safety/sampler failures stop the supervisor; measured
  policy non-completions remain diagnostic failures.
- Fresh collection rotates levels 2/1/3, starting at **L1 17001, L2 18001,
  L3 19001**, 24 seeds/round. Round numbering continues after 21, but these
  are new scenario seed banks, not a relabelling of old worlds. The planned
  ranges did not overlap recorded collection or batch cohorts at setup.
- Features candidates require paired all-level admission before continuation
  reuse, plus the existing new-seed preflight. Scripted rollout is the initial
  data-collection continuation. These are counterfactual teachers, not fallback
  production policies.
- Only `episode-relative-v1` evidence enters training. Import corrected
  `rc01 rc02 dq01` history plus new `bg03` cases; student training uses a rolling
  **12-round** window, wider than the prior eight-round student window.
- No intermediate fine-tunes. Collection reserves **150 minutes** for a final
  two-epoch challenger, calibration, offline held-out check and development
  evaluation. Context 2048/head 256, micro-batch 1, accumulation 16, activation
  checkpointing, SDPA, recovery checkpoints every 25 optimizer updates, maximum
  fine-tune timeout 110 minutes. Preserved checkpoints remain unchanged.
- The outer supervisor reserves **45 additional minutes** for independent
  matched lab checks on **65001–65032**, all three levels: scripted rollout,
  preserved r21-final reference, and a newly completed offline-ready challenger.
  This cohort is not used for training or teacher admission. Missing/incomplete
  results stay explicit; comparisons never promote a policy automatically.
- The host service bounds all stages by the original wall deadline. An explicit
  stop marker prevents later independent stages from restarting workers.

The separate supplied-input mall has a unit-tested accounting seam but no live
fixture or compatible data yet. The saved-world diagnostic has one source
lineage and no independent calibration coverage. Neither enters this lab
training run, and these results do not establish learned quad/mall gameplay or
a win against the full deterministic opening.

## Launch checks

GPU preflight passed on the RTX 4060 Laptop GPU with about 7.4 GiB free VRAM and
no competing CUDA process. All twelve installed lab trees match the repository,
and their complete installed mod trees match one another. The installed shared
trash fix `f4c2178ded058478` is preserved. No mod deployment or GUI change was
performed; stopped workers reload the already installed verified mods.

Fifteen driver/queue/teacher checks passed, including final-reserve handling,
all-level admission, frozen outer execution and scoped cleanup. Dataset preflight
retains 4,455 train / 676 calibration / 481 held-out / 196 test cases, all
`workflow-health-v1`. The real tokenizer retains every train/calibration question
at the 2048/256 limits with zero overflow. Startup results are written to
`startup-verification.json`; subsequent state is in `status.json`,
`summary.log`, trainer progress and the paired-comparison logs.

All startup cohorts finished with zero runtime/safety errors and the corrected
sampler. Both rollout and r21-final completed **L1 32/32, L2 28/32, L3 32/32**.
The preserved student's mean costs remain higher (+0.4%, +8.1%, +7.7% by level),
so no quality improvement or promotion is claimed. The wider-data hypothesis
must be judged from the new challenger and independent final cohort.

At launch verification, round 22 was actively labelling L2 seeds 18001–18024
with three processes. New replay journals were present, and the console detected
the correct run name, round and all twelve workers. The service's hard timeout,
ownership group and sleep inhibitor were verified on the host. No new student
checkpoint had been trained at that milestone.

## Monitor and stop

The separate Operations Console remains at http://127.0.0.1:9138/ and detects
the named learning driver once startup checks finish. The supervisor's final
independent phase is recorded in `status.json` after that driver exits.

Stop the complete window, including its worker ownership group:

```bash
systemctl --user stop laya-eight-hours-20261003.service
```

`ExecStopPost` stops only allocated workers still belonging to this service;
other runtime owners are preserved. `RuntimeMaxSec`, `Restart=no`, a two-minute
stop grace, and a task-scoped sleep inhibitor were configured. The inhibitor
ends with the job. The console and worker 14 remain independently managed.

Lifecycle: new frozen Python consumers and the allocated disposable workers are
active. No real-base/GUI restart or model promotion is performed. Inspect final
artifacts after completion; do not infer success from service uptime.
