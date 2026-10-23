<!-- Path: docs/rl/Laya_Mall_Utilization_2026-10-23.md | Purpose: Record guarded loan reconciliation, linear wait pacing and next-cohort comparison contracts. -->

# Mall utilization and decision pacing candidates

The user identified low utilization of already built mall assemblers and selected
a linear timing ramp of 3% per game minute for consideration in the next main
cohort(s). Both candidates are opt-in deterministic-action variants. Defaults,
policy weights, construction geometry, acceptance and the 18,000-tick stall
horizon remain unchanged. This is source/test evidence; no live benefit or
adoption is established.

## Observed mechanism

Starship capital run `steel30-s80702`, decision 251, tick 559819, held a
chemical-plant loan on electronic circuits at 114 completed crafts against 10
required, and a big-pole loan on iron sticks at 303 against 200. Both were
`disabled_by_control_behavior`. The first remained on its prerequisite at
decision 270, tick 586932; loans had cleared by decision 300. These observations
show delayed reconciliation, not a loan that can never advance.

The existing shared loan transaction already credits a completed prerequisite
and replans its next step. Previously the menu did not expose that reconciliation
independently of requesting the target item again. `ops-ready-loans` adds that
choice without inventing another loan planner or priority rule.

Earlier claims that assemblers worked "10% of the time" came from status samples
over 40 decision exports. They establish sampled status frequency, not measured
working time. Gated stock alone is not a failure: useful demand and missing
bottleneck output distinguish avoidable idleness from a satisfied producer.

## Selected loan transaction

`orchestrator/mall_reconciliation.py` records a live identity consisting of the
requester group, position, side, current recipe and target item. A choice is
offered for a measured completed prerequisite or an already pending release.
Unknown counters, regressed counters and a final-product step do not establish
a completed prerequisite. Machine status is a fact, not an extra readiness gate.

Selection re-observes identity and readiness before calling the shared
`_submit_bootstrap_loan`. Existing funding, anchor protection, chemical handoffs,
WIP handling and executor validation remain authoritative. The selected operation
skips unrelated post-operation mall upkeep. Readback distinguishes attempted,
changed, pending and unverified outcomes; `loan_reconciled` proves a changed
configuration identity, never produced or delivered items. Shared typed funding
signals remain intact. This does not broaden which standing cells may be lent.

## Linear game-time pacing

`ops-wait-linear3` uses elapsed game ticks from the run's first retained sample:

```text
minutes = elapsed_game_ticks / 3600
multiplier = min(4, 1 + 0.03 * minutes)
effective_wait_ticks = max(60, ceil(nominal_wait_ticks / multiplier))
```

| Elapsed game minutes | Multiplier | Nominal 300-tick wait | Nominal 1800-tick wait |
| --- | ---: | ---: | ---: |
| 0 | 1.00 | 300 | 1800 |
| 30 | 1.90 | 158 | 948 |
| 60 | 2.80 | 108 | 643 |
| 100+ | 4.00 | 75 | 450 |

The floor is configurable to 1..300 ticks; effective waits never exceed their
nominal length. Duplicate waits at a floor collapse to one option. Exact ops
recovery retains settings, RNG, original game-time origin and cumulative budgets.
The game clock makes this independent of server speed and queued wall downtime.

There is no fixed loop sleep between non-wait decisions. This intervention
shortens selected waits; it cannot accelerate synchronous operations or promise
the same multiplier in actual decisions per second. A bounded 40-decision
sample had only two waits, with a mean 19.23 game seconds between decisions,
so timing exposure may be small. Journal records retain nominal/effective waits;
status and summary record cumulative actual decisions per game/wall second.

The earlier ideas remain explicit alternatives: `ops-wait-growth5` uses
`min(4, 1.05 ** minutes)` and `ops-wait-sqrt` uses
`min(4, sqrt(1 + minutes / 20))`. They are not part of the primary linear contrast.
All timing/reconciliation controls are rejected for geometry actions.

## Observation contract

`observation_digest.mall_economics.utilization`, latest status and summary carry
`assembler-utilization-sample-v1`: survey tick, surveyed assembler count, known
status count, status histogram, working share of known statuses and ready-loan
count. Its scope is all assemblers in the saved-world survey, not a proven mall
entity registry. Unknown statuses and missing surveys remain explicit; furnaces
are excluded. The summary holds the latest pre-action sample, not an episode
average or measured working-time utilization. Decision records retain every sample.

Pacing changes when decisions sample the world. Do not compare pooled per-decision
status counts as if observation frequency were equal. For a diagnostic comparison,
use one first observation per elapsed-game-minute bin and disclose actual tick,
missing bins and sample counts. Treat that as sampled working share. Delivered
bottleneck output and shared acceptance remain the progress endpoints.

## Next main cohorts

These are prepared contrasts, not launched or frozen cohorts. The main Miniship
owner chooses unused seeds, exact admitted baseline, source/mod/save hashes,
ports, units and deadlines after C24/C25; no active queue is reordered here.
Use new frozen code with the same telemetry on every arm. Overlay only the named
setting onto the owner's common baseline, preserving its catalog, weights,
objective, engine, save, budgets and acceptance windows.

1. Loan availability: common control versus `mall_loan_reconciliation=true`;
   all waits off. A 30-seed pair is 60 planned jobs.
2. Timing: common control versus `decision_wait_schedule=linear` and
   `decision_wait_growth_per_minute=.03`; reconciliation off. Another 30-seed
   pair is 60 planned jobs. These two contrasts total 120 planned jobs.

Count every planned job, complete pair, interrupted attempt, crash and unstarted
job. Report strict ever/fresh-final C0-C6 separately from Laya rate proxies and
C6 choice/output v2. Earned research and fresh research availability remain
separate endpoints when that objective is enabled.

For reconciliation, record eligible observations, offered/selected choices,
configuration changes, pending/unknown receipts and subsequent target output.
For pacing, record nominal/effective wait selections, actual decisions per game
and wall second, game-window completion, host load and tool failures. For both,
report sampled assembler status by game-time bin, built cells, target output,
time to checkpoints and earlier-service regressions. Include time to strict and
proxy C1-C6 and Laya C6 from the first decision, window-censored restricted mean
time and matched-seed faster/slower counts. Strict C1 is especially relevant
because its working-assembler predicate directly exercises useful mall capacity;
disclose the acceptance sampling interval. Use the owner's common reporting
implementation on both arms. No exposure means an
unexercised contrast, not a negative performance result. A later combined arm
needs its own declared interaction comparison and the normal adoption rule.

## Verification

The final source passed 246 focused regression cases and the curated fast gate:
1,401 passed, three skipped, exit zero on 2026-10-23 at 21:31 IST. The
original terminal session also confirms exit zero before the provider token
expired at 21:17 IST; its quiet progress markers omitted collection skips.
Coverage includes measured readiness and stale identity refusal,
the real shared loan planner, selected-operation scope, exported receipts,
bounded pacing, geometry rejection before runtime preparation and coherent
recovery with all four schedules. This is offline
behavior evidence. No candidate cohort has been frozen or launched here.

## Integration and lifecycle

The feature commit is based on experimental Starship lineage `7a88861`.
Cherry-pick only this feature commit into the Miniship research-orchestrator
line and resolve source/doc context there; do not adopt this branch's ancestors.
It reuses the existing shared loan and observation APIs. Report freshness is a
separate source repair and must be pinned consistently across a comparison.
A three-way patch dry run of source, tests and area docs onto `9ac7937` is clean;
the appended status entry must be reconciled separately. This checks patch
compatibility, not runtime behavior on the owner's eventual admitted baseline.

Fresh frozen Python runners are required for use. This change adds no Lua or
executor capability requirement, so it adds no mod redeployment or Factorio
restart requirement beyond the chosen baseline's existing requirements. No
active campaign, world, server, installed mod or observer cadence was changed.
