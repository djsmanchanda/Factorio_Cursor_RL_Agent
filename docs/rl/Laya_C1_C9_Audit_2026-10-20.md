<!-- Path: docs/rl/Laya_C1_C9_Audit_2026-10-20.md | Purpose: Cohort audit, verified reusable fixes, and next matched experiment contract. -->
# C1–C9 audit — October 20, 2026

The tooling has made real mechanical progress, but the cohorts do not show a
consistent overall speedup or an adoptable complete policy. Gate writes and
live pipe credit were repaired; later arms reach more chemical milestones.
Earlier milestones, collisions, fresh service and measurement remain concerns.
Two reusable behavior fixes are verified below. Neither has a matched-cohort
result yet. Keep active C9 and frozen queued C10 unchanged; test these in C11.

## Coverage and accounting

Three delegated raw-log audits examined 105 distinct runs: 15 each from C1,
C2, C3, C5, C6, C7 and C9. Samples cover arms, failures and outcomes and are
purposive, not unbiased rate estimates. Rates below use full available cohort
accounting. C4's 96 planned Starship jobs have no local raw logs; no remote
access was performed. C8's 1000 planned jobs were superseded before launch.
Neither missing cohort is a treatment result or a set of fifteen reviewed logs.

Detailed manifests, numbered raw-log windows, scripts and reports are local
under `docs/rl/laya_helper_runs/c1-c9-audit-20261020/` in the repository's main
checkout: `cohorts1-3.md`, `cohorts4-6.md`, `cohorts7-9.md`, and
`c6-counter-review.md`. These are evidence, not operating authority.

| Cohort | Accounting and comparison | Main result |
| --- | --- | --- |
| C1 | 128 planned; 103 complete, 8 interrupted, 17 unstarted | 186 rejected gate selections despite generic failures=0. No strict C4–C6. Later signal-type fix repairs wiring. |
| C2 | 108 planned; 72 complete, 36 unstarted; 24 complete matched seeds | merged→ladder strict C2 6→11, C3 2→4, C5 0→1. Pipe reopenings 39→25. Bundled intervention; no C6. |
| C3 | Primary 96 planned; 79 complete, 8 interrupted, 9 unstarted; 39 pairs | ladder→review strict C2 18→17, C3 7→3. No C5/C6. Superseded attempt had another 8 interrupted/88 unstarted slots, counted separately. |
| C4 | 96 planned; local terminal outcome unknown | No raw-log conclusion. Release-tag repair has source/other-cohort evidence only here. |
| C5 | 360 attempt slots across restarts: 193 complete, 8 interrupted, 2 runner errors, 157 other/unstarted | 95 same-part pairs: review→next C2 41→41, C3 12→12, C5 1→0. Pipe-credit API unreadable 104 times. Cross-part pair/duplicate completion kept separate. |
| C6 | 96/96 complete; 48 pairs | prev→facts unreadable credit queries 48→0, pipe reopenings 41→24; strict C2 17→21, C3 4→2. No adoption. |
| C7 | 396/396 complete; 198 pairs | base→netstall strict C2 80→78, C3 20→37, C5 1→4. Collision events 13→26 (12→20 affected runs); one robot survey exception, subsequently fixed. |
| C8 | 0 launched / 1000 planned | Superseded, no outcome inference. |
| C9 | October 20 12:19:14 IST snapshot: 218 complete, 20 running, 2 queued /240 | 41 complete five-arm seed blocks: C5 ever ref/plastic/rung/extend/batch 7/11/13/8/13; fresh 6/11/12/8/9. Interim, no adoption. |

C9 snapshot completed counts by arm were 44/44/44/43/43. On the 41 complete
blocks, strict C2 was 20/18/13/17/18 and C3 8/4/10/7/15. Report historical
achievement and fresh service separately; old final latches cannot substitute
for current acceptance. C6 accepted zero is not proof of no real consumer
crafting because its observer has the recipe-counter defect below.

## Speed and direction

C1–C6 used Factorio 2.1.21, 360k tick/4500s limits; C7/C9 used 2.1.22,
540k tick/6000s limits. Seeds, save budgets and policy bundles also differ.
Cross-cohort milestone rates therefore cannot establish causal speed gains.
Within matched arms, successful-reacher-only timing is conditional and can
improve while the probability of reaching the milestone falls.

Conditional paired wall-time medians illustrate the mixed result: C2 ladder
was 107.5s slower at C2 (8 both-reachers); C5 next was 29.5s slower at C2 and
75s at C3; C6 facts was 61.5s slower at C2 and 182s at C3; C7 netstall was
24.5s slower at C2 and 52s faster at C3. C9 batch versus extend was 8s slower
at C2 and 280s slower at C3 in the interim comparison. These are conditional
comparisons, not overall cohort speed estimates. Measure elapsed wall time and
ticks from the prepared run baseline, not absolute save age.

Correct direction: reliable gates, readable embodied inventory, fewer pipe
reopenings, more C3/C5 reach, actual finite advanced-circuit/chest batches.
Unresolved: mixed earlier-service outcomes, collisions, repeated output-full
false failures, missing reconciliation options, and inaccurate acceptance
counters. Do not compensate with extra layout rules or arbitrary weight/timer
changes before repairing these facts and operations.

## Verified changes

1. **Stocked refinery readiness**, commit `a12e7e5`. The shared deterministic
   bring-up accepts all planned machines reporting full_output only when each
   has completed a craft and its own provider holds product on a logistic
   network. Construction, coverage and generated power checks still precede
   this proof. It creates no production delta or milestone credit.
   In C9 batch-s79338, five stone retirement failures repeatedly diagnosed six
   full-output furnaces despite ample stock. A paused disposable clone verified
   4800 stone-brick in the actual provider on network 2, 10000kW generation,
   and completed crafts on all furnaces. Old diagnosis: six stuck after 6.060s;
   new proof: none stuck after 0.03268s, same tick 660487. The cloned server was
   stopped. This measures diagnostic overhead, not whole-factory speedup.
2. **Deferred starter reconciliation**, companion commit to this report. If a
   prior starter operation failed/deferred but bots later satisfy its plate
   rate, the same deterministic operation remains selectable to reconcile its
   owned state. Later stages still require its successful receipt. No starter
   is inferred from network stock. Actual C6 facts-s77632 decision 22 replay:
   recorded menu only wait.300/wait.1800; fixed menu includes copper and stone
   starter reconciliation, with copper-cable still locked. Copper stock was
   148 and only the iron starter had a successful receipt.

Validation: 137 focused stocked-output checks and 64 focused runner/economic
checks pass. New behavior regressions fail on their respective parent source.
The fast gate has 1166 passes, one skip and the existing
`training/laya_ops.py -> orchestrator.autonomous_builder` import-firewall
failure. No claim of a clean full gate. Replay artifacts are in
`data/laya-orchestration/c1-c9-audit-20261020/validation/` and the local helper
folder (`starter-fixed-menu-replay.json`). No production base or installed mod
was changed. Evidence level: unit tests, offline decision replay, and shared
readiness diagnostic on a paused disposable save clone; no matched treatment
cohort or adoption.

## Measurement repair before adoption

`live_base.find_line` sums entity lifetime products_finished only for machines
whose current recipe matches. Borrowing an assembler can import unrelated
historical crafts; restoring it removes real recipe output from observation.
C9 batch-s79309 logged fulfilled/restored circuit and two-provider batches;
batch-s79338 logged three providers, while acceptance missed consumers. These
receipts establish mechanics, not retrospective strict C6 success.

Use recipe-attributed completion evidence with scope/baseline provenance,
persisted across recipe changes and fresh acceptance adapter instances. Item
production statistics can corroborate item flow but alone do not prove recipe
attribution or ownership at completion. Counter resets or scope changes must
be unknown, not fabricated zero. A finite-consumer ownership contract requires
an explicit predicate version; do not pretend a restored loan is a currently
owned consumer. Detailed design and negative cases are in the counter review.

C5 also combines increasing craft count with nondecreasing provider inventory;
legitimate withdrawals can fail fresh C5 despite production. Version any
predicate repair and compare it separately. Preserve current v1 evidence.
Finally, count untyped_stuck bug outcomes as well as caught tool exceptions;
a summary failures=0 does not mean no bugs. No measurement relaxation is
included in the two behavior fixes.

## Owner scheduling contract

After C10 finishes, schedule C11 from a fresh frozen copy, with matched seeds,
save/mod/engine hashes, wall/tick windows, deadlines and complete planned-job
accounting. Suggested cumulative arms: C10 copper baseline (`7a498ba`, shared
behavior based on `05952ad`), baseline + stocked readiness, baseline + stocked
readiness + starter reconciliation. Adjacent contrasts change one factor;
full stack versus baseline is exploratory. Resolve cherry-pick integration
against the actual C10 arm rather than substituting source provenance.

Report fixed-window intention-to-treat reach and fresh acceptance, typed bugs,
interruption/crash accounting, deferred-starter reconciliation and diagnostic
wait volume. Label both-reacher timing conditional. Keep C6 v1 and raw finite
consumer evidence distinct until the observer is repaired/versioned. Preserve
seeded exploration and current weights. Follow the variant adoption rule; no
winner is recommended from these data. Lifecycle: fresh frozen Python runners
only, queued behind C10; no Factorio mod deployment or real-save operation.
