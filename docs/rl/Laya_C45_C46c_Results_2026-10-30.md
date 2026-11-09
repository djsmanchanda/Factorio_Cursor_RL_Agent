<!-- Path: docs/rl/Laya_C45_C46c_Results_2026-10-30.md | Purpose: Record matched 2.1.24 Laya outcomes and research-measurement limits. -->
# Laya C45 and C46c results — 30 October 2026

Both intended experiments completed 120/120 planned identities, 60 matched
policy-seed pairs per cohort, on one copied source save with Factorio 2.1.24
build 87808. Each arm had a 540,000 game-tick and 7,200-second wall budget.
The frozen reference was `9d9b279`; C45 compared steel `33a7c73` and C46c
compared consumer `4390a18`. C46 `9462d4c` and C46b `113d21a` were aborted
wiring failures, not completed matched experiments. The superseded 2.1.23
C45/C46 freezes were never started and remain on admission drain.

The table uses each cohort's final accounted job record. Values are counts of
60 in each arm; E is strict ever and F is strict held at the end. C5 values
are descriptive because those reports did not record predicate-version tags.

| Cohort / arm | C2 E | C3 E | C4 E | C5 E/F | C6 E/F | Untyped / power-route events |
|---|---:|---:|---:|---:|---:|---:|
| C45 reference | 47 | 42 | 33 | 33 / 12 | 14 / 11 | 25 / 23 |
| C45 steel | 51 | 44 | 41 | 41 / 12 | 14 / 11 | 23 / 31 |
| C46c reference | 49 | 45 | 35 | 35 / 9 | 9 / 5 | 23 / 23 |
| C46c consumer | 49 | 46 | 35 | 35 / 4 | 19 / 4 | 138 / 129 |

For C45 C6-ever, the final records give 12 steel-only versus 12
reference-only pairs. A recovery defect affects one identity: `steel-s80612`
had already completed and cleaned its first attempt without C6, but was
marked interrupted and rerun after restart; the second attempt reached C6.
Counting the first terminal attempt yields steel C6-ever 13/60 and C6-final
10/60. Both attempts are preserved in `batch-events.jsonl`; neither should be
silently discarded. Each cohort had 138 attempts for 120 planned identities.

For C46c C6-ever, 17 pairs favored consumer and 7 favored reference
(two-sided exact p about 0.064). This is a useful mechanism signal, with an
important fault and durability cost: C6 was held at the end in 4 versus 5
runs, and consumer generated 115 untyped beacon/tank events that the builder
could not construct. Power-route refusals were concentrated in two consumer
runs. The consumer arm fails the no-increased-tool-fault adoption guard; the
steel arm has no demonstrated C6 gain. Neither is adopted by this review.
The v2 and v3 Laya C6 receipts describe different evidence and cannot be
compared as one endpoint.

In the accounted C46c jobs, `bootstrap_mall_slot_cap` appeared 319 times in
8 reference runs and 396 times in 9 consumer runs. The two consumer runs
`s80678` and `s80692` account for 49 and 52 power-route refusals respectively.
These repeated outcomes identify specific places to inspect; event counts
are not independent failures or proof that one change will improve the
cohort. Temporary mall batching was common in both arms (3,671 and 3,996
events), but some batches are productive progress. A separate journal review
found that fixed iron-extension sites frequently collide and that options
for unavailable producers can repeat. Those findings warrant bounded
reproduction and separate tested factors rather than stacked policy changes.

Past reference cohorts provide context, not matched comparisons: C43 on
2.1.23 reported strict C2 56/60 and C6-ever 12/60; C45 reference on 2.1.24
reported 47/60 and 14/60, and C46c reference reported 49/60 and 9/60. Seeds
and engine differ, so these numbers do not establish an engine effect or
independent terrain generalization. All policy seeds use one copied save.

Neither recent experiment measured completion of mining-productivity-4:
each report has `research.observed_pairs = 0`. The job-list phase summary
omits S1, but individual C46c terminal status files show S1 as the current
proxy phase in 24/60 reference and 33/60 consumer runs; their research
acceptance is not established by that phase. An initial snapshot lists 253 researched entries and no
`mining-productivity-4` key, but this is not evidence of unavailability:
repeatable technology uses a ranged prototype key. A read-only query on the
disposable source03 world at tick 259223 found `mining-productivity-3` at
level 4, enabled and not researched, so level 4 is currently an open target
in that run. It is not a start-of-run receipt for C45 or C46c. Future research
experiments need explicit target-level and completion receipts before they
can establish progress toward the requested objective. Do not infer it from
a C6 or proxy-phase result.

The next user-directed step is mixed-start C47 A/A calibration: 48 C0, 10
each C1–C4 and 16 each C5–C6, with paired seeds and generations. Its missing
checkpoint bank and live reloads remain prerequisites. Candidate follow-up
factors include the existing consumer recipe-capability filter `580eb11` and
an independently tested power-route remedy; choose after C47 readiness and
the remaining bottleneck review. The reference remains the acceptance anchor.

Evidence: `data/laya-experiments/laya-c45-2124-miniship-20261028/` and
`data/laya-experiments/laya-c46c-2124-miniship-20261029/`, including each
`freeze.json`, `job-status.json`, `batch-events.jsonl` and `report.json`;
`data/laya-orchestration/laya-loop/log.md`; and
`data/laya-orchestration/mixed-checkpoint-after-c46-20261028/state.json`.
