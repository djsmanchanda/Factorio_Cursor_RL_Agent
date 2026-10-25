<!-- Path: docs/rl/Laya_C9_Result_2026-10-20.md | Purpose: Cohort 9 result: integrated cumulative C5/C6 campaign, all planned jobs accounted. -->
# Cohort 9 result (2026-10-20)

Campaign `data/laya-experiments/laya-c9-20261020`, unit `laya-c9-miniship-20261020`, miniship only,
Factorio 2.1.22, common save and mods, 540,000 ticks / 6,000 s per trial. 48 seeds (79300-79347) ×
five cumulative arms. **240/240 planned jobs completed**; stops: 239 stalled, 1 window_timeout (batch).
Evidence level: matched experiment cohort (guided policy, one copied save; not unseen terrain).

Arms (each adds one factor to the previous): ref f17b28c (netstall + one plastic chest + robot fix +
fresh acceptance + whole-block harness); plastic +7c514a4; rung +rung progress only; extend +2ee4644;
batch cf03dbb (+2efb3ef, 6423837 finite advanced-circuit seed and funded consumers).

Strict milestones, ever / fresh final (48 runs each):

| Arm | C2 | C3 | C4 | C5 | C6 |
|---|---|---|---|---|---|
| ref | 22 | 10 / 7 | 8 | 8 / 7 | 0 |
| plastic | 19 | 5 / 4 | 12 | 12 / 12 | 0 |
| rung | 16 | 11 / 11 | 14 | 14 / 13 | 0 |
| extend | 20 | 9 / 8 | 10 | 10 / 10 | 0 |
| batch | 20 | 16 / 16 | 15 | 15 / 11 | 0 |

Paired discordances (newer-only / older-only; two-sided sign test):

| Contrast | C2 | C3 | C5 ever | C5 final |
|---|---|---|---|---|
| plastic vs ref | 9/12 | 4/9 | 9/5 (0.42) | 9/4 (0.27) |
| rung vs plastic | 7/10 | 8/2 (0.11) | 7/5 | 6/5 |
| extend vs rung | 14/10 | 5/7 | 7/11 (0.48) | 7/10 |
| batch vs extend | 9/9 | 13/6 (0.17) | 11/6 (0.33) | 9/8 |
| batch vs ref | 7/9 | 12/6 (0.24) | 10/3 (0.09) | 7/3 (0.34) |

No contrast is significant on the C5/C6 endpoints. C6 is zero in every arm (see the counter caveat
in `docs/rl/laya_helper_runs/c1-c9-audit-20261020/c6-counter-review.md`: zero accepted C6 is not proof
of no output). Interim readings at 37 pairs (batch vs ref C5 p=0.04) did not hold.

Failures, affected runs of 48 (decisions per arm 10.3k-12.0k): tool_error 0 in every arm.
infrastructure_collision ref 7, plastic 8, rung 4, extend 5, batch 11; power_route_unavailable 5/7/7/8/8;
untyped_stuck 0/2/1/4/5. The batch arm's collision and untyped_stuck counts are the highest; all are
refusals or typed waits, not damage, but they need a mechanism check before any adoption.

Mechanism (log reading): without the batch, C5 runs end in the advanced-circuit/passive-provider
handoff cycle. With it, the seed fires and that cycle mostly clears, but most runs that lose C5 by the
final sample end on copper `district_expansion_unbuildable`, several with coal short: copper growth is
the next wall (05952ad, tested in C10).

Decision: no adoption. C5 roughly 17-31% per arm against about 2% in cohort 7; the common reference
changes carry most of that. Next: C10 (full vs +copper second collector vs copper without rung
progress, seeds 79400-79447), then C11 (copper vs +stocked readiness a12e7e5 vs +starter reconciliation
374e68e). Analysis script: `data/laya-orchestration/next-20261018/c9-analysis/status.py`.
