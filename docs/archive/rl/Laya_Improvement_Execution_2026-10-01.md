<!-- Path: docs/archive/rl/Laya_Improvement_Execution_2026-10-01.md | Purpose: Record bounded training and verified bottleneck observations without claiming model promotion. -->

# Laya improvement implementation — October 1

## Bounded corrected-pipeline experiment

Run `bounded-20261001`, supervised by user service
`laya-bounded-20261001.service`, started at 02:16 IST. Its code is frozen at
`2db0ee6`; subsequent observation edits do not change this experiment.

- Workers: 01–04, 06–07, one paused-clock slot per worker, speed 32,
  three labellers; worker 14 remains the separate demo.
- At most three new rounds: r34/L2 seeds 6613–6618, r35/L1 seeds 5677–5682,
  r36/L3 seeds 8101–8106. L3 bootstraps with rollout.
- Starting features continuation: `features-ov-r33.json`.
- One final two-epoch LoRA fine-tune; Laya case window starts at round 29.
- Fresh scripted baselines and r26-min19 reference evaluation on all levels;
  final features and new Laya evaluation on the same 32 development seeds.
- Four-hour soft budget, six-hour host limit, process-group termination and
  scoped worker cleanup. No production authority or unattended successor run.

These seeds have been evaluated repeatedly. They are development evidence;
they cannot establish untouched promotion results. Initial reference results:

| Policy | Level | Completed | Mean cost | p95 cost |
|---|---:|---:|---:|---:|
| Scripted | 1 | 32/32 | 6,866.5 | 6,875 |
| Scripted | 2 | 28/32 | 12,448.4 | 43,125 |
| Scripted | 3 | 32/32 | 11,986.4 | 13,602 |
| r26-min19 | 1 | 32/32 | 6,268.0 | 6,828 |
| r26-min19 | 2 | 28/32 | 11,746.0 | 42,500 |
| r26-min19 | 3 | 31/32 | 11,580.1 | 13,582 |

The reference loses one scripted completion at L3; a smaller mean is not a
promotion win. Final training results are pending at this milestone.
Configuration, logs and comparisons live under
`data/laya-pi/bounded-20261001/`; journals remain in their original stores.

## Bottleneck facts implemented and tested live

`workflow-health-v1` records bounded phase-owned inventories and held items,
separate source stocks, current machine status counts, power connectivity and
empty-energy counts, sampled working fraction, remaining patch resources, and
remaining entity allowances. Production and consumption use independent engine
counters, with reset/gap handling and window-end provenance.

The adapter rejects foreign scope, stale health, nonfinite values, oversized
payloads and inconsistent counts. Cell WIP and source stock never become
accessible construction materials. Missing inventory reads remain unknown.
Sampled working fraction is a 60-tick status sample, not exact utilization;
nameplate or sustainable capacity is not invented. Entity allowances are not
a finite construction economy. Belt contents, fluids and ground-item stock are
outside this inventory scope and must not be inferred as zero factory stock.

The same branch digest renders training labels and live requests. New features
policies use set 5 with telemetry presence/unknown masks; older model vectors
retain their widths. New workflow batches record the observation profile.

Worker 05 was stopped, received a scoped mod/code overlay, then restarted.
The already deployed shared trash-requester implementation was retained.
A temporary GUI-mod target kept the running demo and GUI files unchanged.
The initial probe failed before any decision because the strict bridge report
schema had not admitted the new fields. That failed evidence remains intact;
the schema and local reference registry were corrected before fresh runs.

Verification:

- 4/4 scripted L3 completions on seeds 7001–7004 after contract correction;
- 120 decision requests fit the actual tokenizer: maximum 1,932/2,048 tokens,
  zero truncation/overflow;
- 96 observations carried health and available consumption counters;
- one fresh seed, 8107, two counterfactual prefixes, both faithful, exported
  two cases carrying `workflow-health-v1` through the normal ETL;
- 91 focused Python/Lua/bridge/workflow checks passed.

Artifacts: `data/laya-health-validation/`, including original failed journals,
`context-fit.json`, `label-fit.json`, successful journals, labels and cases.
Each probe recycled its disposable surface; worker 05 is stopped afterward.
This proves telemetry/mechanics, not health-aware learned performance.

## Restoration action verified

The old compiler suppressed a branch after historical plan execution, even
when surviving construction later disappeared. `training/laya_repair.py` now
compiles unranked restoration choices only for known executed layouts with
current phase-owned evidence of losses. It prices the missing entities, retains
survivors through idempotent placement, verifies fresh missing quantities in
its bound signature, and delegates exact collisions/budgets to the lab.
Unknown ownership, unexpected survivors, an exhausted allowance or inactive
runtime cannot authorize a restoration. Features set 6 adds repair/context
terms without changing set-5 checkpoint dimensions.

A controlled worker-05 L3 probe (seed 7001) removed one owned belt-output
inserter before running the factory. After 6,000 ticks, belt delivery remained
zero. Selecting the compiled restoration placed exactly one entity and retained
all four surviving entities in that phase. Joint service completed at 7,800
ticks; entity/bounds/budget violations were zero and cleanup completed.
The repair option disappeared after the cell was whole again.

This was an explicitly selected mechanics intervention, not a trained model's
repair decision, a training label or a production-base repair. The first probe
helpers exposed configuration/scope mistakes and an overly long tick burst
that passed terminal-world cleanup; those artifacts are retained. The final
probe observes in bounded steps and stops at completion.

The cleanup failure exposed a bridge bug: its recycle-report filter waited for
`completed` even when the matching request had already returned `ok=false`.
It now surfaces the terminal error immediately. A second recycle of the already
recycled probe returned its actual error in 0.030 seconds instead of the old
120-second timeout. Focused restoration/toolkit/features/runner/health/bridge
checks passed. Evidence: `data/laya-health-validation/repair-probe.json`,
`recycle-failure.json`, and the corrected `repair_probe.py`. Worker 05 is
stopped again; no new Lua deployment was needed for this Python action seam.

## Remaining sequence and lifecycle

The bounded experiment must finish before changing its fleet's Lua. Deploy the
health mod to stopped workers and matching GUI, restart the affected Factorio
runtimes and Python consumers, and use fresh observation-matched batches for
the next training cohort. Retraining is needed for Laya to learn the new facts.

Next implementation is seeded fault curriculum and authoritative active-layout
provenance for upgrade choices, followed by further expansion/transport edits. It is followed by finite construction
materials, depletion, power/transport limits and endurance objectives. The
matched deterministic opening and an independent promotion suite remain
required before any real-base authority. None of those stages is claimed
complete by the bounded run or the observation smoke.

## Wind-down and continuation safeguards

The user requested a quick wind-down. Workers 01–13 are stopped; demo worker
14 and the operations console remain running. No successor run is scheduled
and no new Laya adapter was trained. The first experiment finished r34/L2
and r35/L1, then stopped before its final evaluations/fine-tune. Original
configuration and all journals remain preserved.

Measured r35 features L1: 32/32, mean cost 6,088, p95 6,104, cheaper on all
32 development pairs versus scripted mean 6,866.5. This is a features-policy
result on repeatedly used seeds, not a Laya or promotion result. On training
seeds 6613/6615/6618, the r33 continuation waited without building.

The outer driver failed with exit 2 while reading the working Bash file after
an edit changed unread offsets. Inner tools were frozen, but the outer driver
was not. New launches re-exec the outer script from the committed frozen
checkout. The integration harness overwrites the original during a round and
verifies that the frozen driver still completes its rotation and cleanup.

New policy-iteration rounds probe the proposed features teacher and scripted
reference on the exact new seed block before labelling. The teacher gate
rejects completion/mean-cost regressions; tool/safety/evidence errors or an
all-failed teacher pair block the round rather than silently selecting a
policy. Named experiments may import prior case prefixes explicitly.

The last teacher probe exposed a failure-packet nesting overflow from the new
health telemetry. Failure packets now reference its content hash and journal
while preserving full facts in the original observations. Explicitly safe lab
horizon failures are reported as policy timeouts, with absent safety facts
remaining environment failures. Forty focused checks passed, including the
new teacher guard, timeout/health failure-packet path and mutable-driver
regression, plus shell syntax. The teacher guard has unit evidence; its live
probe remained blocked, and no follow-up live run was started during wind-down.

Required next lifecycle: deploy the health mod to the stopped fleet and matching
GUI, then launch new frozen consumers. Worker 05 already has the verified health
mod; other workers retain the earlier joint-sustain mod. First rerun the teacher
guard and failure path, then resume bounded three-level training. Seeded fault
training and upgrades, finite economy, endurance and the matched deterministic
promotion benchmark remain outstanding.

## Eight-hour overnight run started

At the user's subsequent request, `overnight-20261001-health` started at
03:51 IST under `laya-overnight-20261001-health.service`, frozen at `67d84a4`.
Its host limit is eight hours, deadline **11:51 IST on October 1**, with scoped
worker cleanup on completion, error or timeout. A six-hour collection/iteration
budget reserves up to two hours for remaining training/evaluation. A slow
in-flight stage can still be interrupted at the host deadline; its journals
and periodic training checkpoints are retained, without promotion credit.

Configuration:

- Twelve workers: 01–04, 06–13, one stepped slot each, speed 32, three labellers.
- Twenty-four seeds per round; resume round 35, so the next levels are L3/L2/L1.
- Next banks: L1 5683, L2 6619, L3 8109. Prefix `nh01` imports `ov` history.
- r35 features is the initial continuation; L3 bootstraps with rollout until
  its first accepted features cohort. Every later features teacher is checked
  on its new seed block before labelling.
- Laya trains on the latest eight rounds, minimum round 29, for two epochs;
  fine-tuning every third round and a final pass when time permits. Training
  timeout is 120 minutes with periodic checkpoints.
- Fresh scripted and r26-min19 diagnostic cohorts on all three levels. Each
  completed new adapter receives offline held-out case evaluation before
  disposable live evaluation on development seeds 4001–4032.

Preflight verified the formerly failing teacher/failure path: rollout and r35
features both completed 2/4 on seeds 6613–6616, with two correctly attributed
policy timeouts each, zero tool/safety errors; features mean 23,397 versus
25,658.5, admitted by the teacher gate. Two high-demand seeds remain unsolved
by this fixture/reference; they are not converted into positive failed waits.
Dataset preflight retained 928 cases, including 708 train, 71 calibration and
88 held-out, and excluded 1,646 obsolete costs-only cases.

Deployed both mods to workers 01–14 and GUI, retaining the installed shared
trash-requester hash `f4c2178ded05`. All 14 copies match the deployment snapshot.
The unoccupied demo worker was reloaded and returned fresh health/joint-output
evidence; it remains separate and running. The training workers reloaded at
launch and their baseline journals expose the health observation profile.
Fresh baselines completed L1 32/32, L2 28/32, L3 32/32, zero tool/safety errors.
The active outer driver path was verified under the immutable run checkout.

Evidence/configuration: `data/laya-overnight-preflight/` and
`data/laya-pi/overnight-20261001-health/experiment.json`, logs, datasets and
admission records. Console: http://127.0.0.1:9138/. Reopen an old Factorio GUI
session before joining the updated workers. No chained successor run or
real-base promotion is scheduled; the repeated development seeds cannot
establish untouched promotion evidence.
