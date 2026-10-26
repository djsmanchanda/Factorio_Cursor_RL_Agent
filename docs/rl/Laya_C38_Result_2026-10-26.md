<!-- Path: docs/rl/Laya_C38_Result_2026-10-26.md | Purpose: Close C38 with complete accounting and distinguish its stop failure from the unexercised clearance comparison. -->
# C38: comparison never exercised

Decision recorded 2026-10-26, 15:56 IST. C38 supplies evidence about its runner
stop contract; it supplies no clearance adoption result.

`laya-c38-2123-miniship-20261026` planned 120 jobs: **119 completed and one
preparation failure**, `robots-s80213`. All 120 attempts have terminal ledger
events. There are 59 complete seed pairs. Both arms retain immutable base
`9764fb6`, seeds 80160–80219, Factorio 2.1.23, 540,000 ticks and 7,200 seconds.
Robot exclusion is common; connected refinery-pole clearance is the treatment.

All 119 runners stopped `stalled`. Of these, 117 ended at 12 consecutive choices
without a recognized progress signal, with only 2,244–14,039 idle ticks
(median 4,476) consumed from the 18,000-tick window. These runs reached proxy
C0/C1 in counts 94/23. The other two, seed 80201 in both arms, reached the
idle-time threshold at C0. Thus all completed runs reached proxy C0, 23 reached
proxy C1, and **none reached C2**. Strict C1 ever passed in 38; strict C1 final
passed in one. These strict counts are separate from proxy phase reach.

No clearance action occurred. Tool errors, untyped faults and safety stops were
zero. The preparation failure's engine log records `EADDRINUSE` on game port
39912; the historical holder is unknown. It remains a failed planned job.

Frozen source ends ops on either 18,000 idle ticks or geometry's shared
`stall_decisions=12`. C37's frozen ops runner had no choice-count cutoff.
The stop is after a tool returns, not a mid-call cancellation. In representative
`robots-s80160`, the final streak contained repeated preflight refusals while
starter stock continued growing. This demonstrates premature termination, not
that another twelve choices would have solved the underlying refusal.

The same trace exposes a separate fact defect: seven physical provider chests,
one project reservation and six dependency-budget holds leave only one
allocatable chest. The option reports six available and no missing items.
Repairing the stop does not repair that false-feasibility observation. A repaired
baseline must pass a C2 smoke before another full policy cohort is admitted.

Repair `c71d826` removes the ops choice-count stop while retaining blocking-call
accounting, the idle-time guard and independent total/phase caps. Receipts use
`decision-idle-v3`. Two regressions fail on the old runner; 116 focused checks
and the fast gate (1,625 passed, one skipped) pass after repair. This is unit
evidence, not a repaired matched-cohort outcome.

C38's C1-v2/C5-v1 and future main's C1-v2/C5-v2 are separate series. Never
rescore C38, pool it with C37, or compare cross-cohort arms as causal evidence.

Sources: cohort `freeze.json`, `planned-jobs.json`, `job-status.json`,
`batch-events.jsonl`, journals, engine logs; independent
[terminal audit](laya_helper_runs/c38-diagnosis-20261026/terminal-audit.md) and
[outcome review](laya_helper_runs/c38-diagnosis-20261026/c37-outcomes-review.md).
Lifecycle: historical freezes unchanged. Future Python runners require a new
committed freeze; the repair alone requires no installed-mod deployment.
