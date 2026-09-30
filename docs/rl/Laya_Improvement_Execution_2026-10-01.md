<!-- Path: docs/rl/Laya_Improvement_Execution_2026-10-01.md | Purpose: Record bounded training and verified bottleneck observations without claiming model promotion. -->

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
