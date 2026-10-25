<!-- Path: docs/rl/Laya_C10_Result_2026-10-20.md | Purpose: Cohort 10 result: full stack on new seeds; copper arms invalidated by the second-collector link crash. -->
# Cohort 10 result (2026-10-20)

Campaign `data/laya-experiments/laya-c10-20261020`, unit `laya-c10-miniship-20261020`, miniship only,
Factorio 2.1.22, common save and mods, 540,000 ticks / 6,000 s per trial, seeds 79400-79447 (not used
in C9). **144/144 planned jobs completed.** Evidence level: matched experiment cohort (guided policy,
one copied save; not unseen terrain).

Arms: full 62d2c47 (C9 batch code + a test-only sync); copper 7a498ba (+05952ad second collector);
norung eb4467a (copper with rung progress reverted).

| Arm | Stops | C2 | C3 ever/final | C4 | C5 ever/final | C6 | tool_error events | safety stops |
|---|---|---|---|---|---|---|---|---|
| full | 47 stalled, 1 window_timeout | 17 | 12/12 | 14 | 14/11 | 0 | 0 | 0 |
| copper | 34 stalled, 14 safe stop | 13 | 6/6 | 9 | 6/6 | 0 | 91 | 14 |
| norung | 37 stalled, 11 safe stop | 14 | 8/6 | 7 | 3/3 | 0 | 61 | 11 |

**The copper arms are invalid as a test of the second collector.** Every tool error is
`TelemetryError: pending-plan claim requires an exact planner identity`: the link planner claimed its
reserved tiles without the identity the live occupancy survey requires (unit tests stubbed the survey).
Fixed in 030f8de with a test on the real survey; in C11's first 19 live runs, 47 second-collector
surveys raised no error. Copper vs full (C5 +4/-12, p=0.08) measures the crash, not the design.
norung vs copper (C2 +8/-7, C5 +2/-5): no visible effect of rung progress inside the crashing stack.

**Full on new seeds:** C5 14/48 ever (29%), 11/48 final, against 15/48 (31%) and 11/48 in C9 on
other seeds: the stack's C5 rate replicates on held-out policy seeds (same copied terrain). C6 zero
(accepted; see the C6 counter caveat, docs/rl/laya_helper_runs/c1-c9-audit-20261020/c6-counter-review.md).

Decision: no adoption. C11 (copper 8bd4149, ready 2ffefee, reconcile 071c0aa, all with 030f8de) runs
next on the same seeds; it has no original-full arm, so corrected copper vs the original stack needs a
later matched campaign.
