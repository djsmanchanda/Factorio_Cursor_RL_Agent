<!-- Path: docs/rl/Laya_Session_Handoff_2026-10-09.md | Purpose: Resume the completed Laya experiment review and isolated lifecycle repair with accurate runtime state and next work. -->

# Laya session handoff — October 9, 2026

The 320-trial cohort is complete. Keep the baseline weights. The strongest
reusable defect found in its review—unfinished starter retirement disappearing
from the menu—is fixed and verified on a separate branch. Fresh-start policy
improvement has **not** been measured yet.

This handoff is left uncommitted in the main checkout, like the earlier local
handoffs. It records this work; it does not replace other agents' handoffs or
authorize restarting their runtimes.

## Code and isolation

| Item | Identity / state |
| --- | --- |
| Lifecycle fix | `codex/laya-lifecycle-obligations`, commit `a68d52d` |
| Fix base | `370de88` |
| Isolated checkout | `data/laya-experiments/lifecycle-20261009/code` |
| Main checkout when checked | `codex/deterministic-checkpoint-fleet`, `76268c3`, with unrelated pending work |
| Integration | Fix is not merged into the main checkout; no production promotion |
| Runtime | Verification used Factorio 2.1.21 build 87673 and frozen cohort mods |

Do not switch/reset the main checkout or copy the isolated tree over it. Review
the scoped commit against the current main code when integrating; preserve
newer documentation, archived links and other agents' changes. The branch's
dated verification report is [available in its checkout](../../data/laya-experiments/lifecycle-20261009/code/docs/rl/Laya_Lifecycle_Verification_2026-10-09.md).

## What I worked on

Earlier work audited the last 200 dated status updates, Laya documentation and
the deterministic workflow. The documentation cleanup clarified hard boundaries
versus soft strategy, proxy milestones versus real acceptance, and standing
mall cells versus temporary loans. It reduced the three active guides by 31%
and preserved guided distributions on 32 recorded decisions. The broader
deterministic audit identified fragmented dependency handling, stale evidence,
non-causal progress/budget renewal and starting-layout assumptions. That audit
is a diagnosis, not a completed rebuild of the deterministic runner.

The isolated Laya evaluation work added per-condition acceptance packets,
preserved structured operation results, introduced real-checkpoint stopping
windows and supported up to 40 disposable worlds with finish-first admission.
Lowering the cap holds replacement starts while existing worlds finish. These
changes were exercised in the completed cohort; their frozen source and inputs
remain separate from the dirty main checkout.

The latest implementation is the lifecycle repair:

- Export scoped bootstrap-district submission and lifecycle facts into the
  brief, option features and recorded decision state.
- Keep submitted replacement reconciliation available across satisfied rates,
  later proxy phases and shortage returns. Receipt history alone is insufficient
  because bots can finish previously submitted work after an unsuccessful call.
- Remove completed obligations only on verified district release. New capacity
  still follows demand, and Laya still chooses among the offered alternatives.
- Distinguish `released` from `retirement_pending` receipts; incomplete normal
  returns have `ok=false`, rather than appearing to complete retirement.
- Sample unchanged checkpoint predicates before and after retirement attempts.

The deterministic builder, ownership/funding/output/bot-removal guards, Lua mods
and numeric policy weights were not changed by this repair. Pending retirement
is now explicitly a goal-serving option, so its observed features and resulting
selection probabilities can change. Extra acceptance surveys also take live
game time; evaluation must account for that.

## Completed experiment results

The `next-20261008` cohort contains **320/320** unique planned trials, with no
missing or duplicate jobs. These results supersede older incomplete campaign
snapshots for this cohort; the power-interrupted loan/honest campaigns remain
separate historical evidence.

| Arm | Trials | Real C1 observed | Real C2 observed | Rate-proxy C2 |
| --- | ---: | ---: | ---: | ---: |
| Baseline: core mall ×180, other slots ×6 | 160 | 25 | 12 | 48 |
| Only other slots ×1 | 160 | 16 | 9 | 58 |

The candidate's proxy numbers looked better while its real-checkpoint counts
were lower. Paired exploratory p-values were .163 for C1 and .664 for C2;
there is no established improvement or equivalence. Retain baseline weights.
All 21 recorded C2 passes were checked against unchanged `c2-v1` evidence.
Only two trials recorded C1 before C2; independent predicates and periodic
sampling leave unsampled C1 states possible. Do not describe every C2 pass as
a verified ordered C0 → C1 → C2 trajectory.

There were zero process crashes, five wall timeouts, fifteen game-window
timeouts and 279 stall stops. Separately, journals contained five `tool_error`
and 93 `untyped_stuck` outcomes. Crash-free processes do not mean fault-free
tooling. Full review: [RESULTS_REVIEW.md](../../data/laya-experiments/next-20261008/RESULTS_REVIEW.md),
with counts and packet references in [final-result.json](../../data/laya-experiments/next-20261008/final-result.json).

## What is verified now

- **54 focused tests pass** for ops choices/receipts, acceptance exports and
  district persistence/ownership/output guards, including existing startup order.
- **101/101** affected failed trials regain all needed metal-retirement options
  when their terminal menus are recompiled with scoped ledger facts. Receipts
  alone recover every needed choice in only **36/101**. This is hypothetical
  next-menu coverage, not 101 successful reruns.
- **Two copied failure worlds reached real C2** through scripted mechanics on
  2.1.21. In `ops-other-slots-1-s50507`, both starters were recovered with bots.
  In `baseline-s50616`, the tool initially retained iron because the provider
  lacked output; its choice stayed pending. A later attempt after output appeared
  released iron and passed C2. The final runner recorded the transition in four
  acceptance packets: start, before retirement, after retirement and end.

Both original saves and source ledger hashes stayed unchanged; frozen mod
copies matched. Copies retained episode identity and explicit continuation
provenance. These were scripted mechanics checks, **not policy-selected wins or
full fresh-start runs**. Exclude them from policy-performance and training
datasets. They establish recovery and guard behavior, not a measured performance
gain or map generalization.

Artifacts: [menu-replay.json](../../data/laya-experiments/lifecycle-20261009/menu-replay.json),
[live-replay.json](../../data/laya-experiments/lifecycle-20261009/live-replay.json),
[final-runner replay](../../data/laya-experiments/lifecycle-20261009/pending-receipt-live.json).
Worlds, manifests, packets and logs are under the same experiment directory.

## Current runtime and remaining gaps

Process checks during this handoff found the separate deterministic server
running as PID `1502810`, using the 2.1.21 executable; its log confirms build
87673. No other Factorio process was present in that host snapshot. The Laya
cohort and both private verification worlds are stopped. The cohort admission
file still says **40**, which is a future-start cap, not a running-world count.
No successor campaign or helper was launched by this repair or handoff.

The main server's factory progress was not remeasured here. Its process being
alive does not establish a milestone or validate another agent's changes.
This work did not deploy mods, restart that server or change its save.

C1 remains a major unresolved issue: among 255 terminal samples from trials
with neither C1 nor C2 observed, the four-working-mall-assembler check failed
in 234, and the stone-starter check failed in 97. Missing capacity, input
starvation, stock gating and cell loans are different mechanisms. Fixing
retirement alone does not fix them.

Ops still uses the guided sampler with an exploration floor. This work fitted
no learned model to ops decisions. Full prospective bills, requester draws,
displaced lender production, later-goal actions and research completion remain
incomplete. C2 is an intermediate factory checkpoint, not the complete mission.
General ops restart recovery and robustness across unseen maps/starting kits
are also not established by these tests.

## Planned next work, in order

1. **Run a small matched fresh-save comparison.** Proposed first gate: 16
   preselected paired seeds, baseline versus the lifecycle fix, on the frozen
   2.1.21 save/mods, speed 2, unchanged `c2-v1`, and the prior 360,000 game-tick /
   4,800 wall-second trial caps. Up to 32 worlds fits the existing 40-world
   capacity, but first verify fresh private ports, explicit admission scopes
   and run identity. Do not restart completed campaigns in place.
2. **Keep measurement comparable.** Mirror retirement-boundary sampling in
   both arms through a documented evaluation-only overlay, or explicitly label
   the comparison as behavior plus sampling. Record real C1/C2, ordered observed
   transitions, time/resources to acceptance, retirement retry cost, failures,
   unknown telemetry and stall causes. Preserve the fixed cohort and raw packets;
   decide adoption from those results, not proxy rates or scripted recovery.
3. **Diagnose the C1 split.** Trace machine existence → inputs → control state
   → craft/output counters → ownership/loan. Improve observations and available
   choices before changing weights or weakening the checkpoint. Repair one
   reusable cause at a time, with a failing example and matched verification.
4. **Make stagnation diagnosis causal.** Expose each unmet postcondition,
   owned transaction, blocking dependency, changed facts and available recovery.
   Flag a missing recovery choice explicitly. Use shared preview → choose →
   execute → reconcile contracts; keep hard legality separate from soft design.
   Avoid another pile of recipe/coordinate exceptions or longer timers without
   measured useful progress. Broader evidence is in the
   [deterministic workflow audit](../deterministic/Deterministic_Workflow_Audit_2026-10-08.md)
   and [Laya audit](Laya_System_Audit_2026-10-08.md).
5. **Then train and test generalization.** Keep proxy, local progress, real
   acceptance and scripted replay labels separate. Fit a compatible policy only
   after observations/actions/outcomes are adequate, then evaluate held-out
   maps, starting conditions and the deterministic baseline with rollback.
   Complete later production/research actions before claiming mission completion.

The comparison above is a proposed next experiment, not an already-running
service. A future helper should be read-only, attached to the exact new run
IDs and log roots, and record its actual model identity. No Muse availability
or helper session was verified for a successor run.

Adopting the Python lifecycle fix requires a new/restarted runner using that
source. **No mod deployment or Factorio restart is required.** Documentation
changes in this handoff require no lifecycle action.
