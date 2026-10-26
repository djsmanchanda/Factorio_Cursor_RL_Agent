<!-- Path: docs/rl/Laya_C37_C38_Decision_2026-10-26.md | Purpose: Record the executive integration decision and evidence-gated next experiment after independent C37/C38 reviews. -->
# C37/C38 executive decision

Recorded 2026-10-26, 15:56 IST after three independent DeepSeek 4.1 Flash
reviews. The reviews are evidence; the decisions below belong to this record.

Keep C29's `5a5ed28` as the adopted policy reference. Keep the audited proper
merge on main and integrate the verified ops stall repair `c71d826`. Main is an
integration baseline; its source contents do not establish a performance
promotion over the reference. Robot occupancy exclusion is already in main's
shared mechanics. Preserve that functional correction, but make no C37
performance-adoption claim. Every follow-up arm must declare it as common.

Do not adopt C37 as a stand-alone performance change. All 120 jobs completed;
strict C6 ever is control 11/60 versus robots 7/60, with 5 treatment-only and
9 control-only pairs (exact two-sided p=0.424). Final C6 is 8/60 versus 6/60;
the separate Laya production endpoint is 29/60 versus 26/60. C5-v1 final is
8/60 versus 12/60, correcting the older result copy. Neither a null test nor
the historical A/A spread establishes equivalence.

Retain connected pole clearance experimentally and defer its next full
confirmation. C36's target gain remains promising, but its fault/service guards
are unresolved. C37 removes robot co-blockers while 568 of 570 remaining
robots-arm refusal events still name a big pole. That explains an extension
constraint; it does not by itself identify the missing C6 service. C38 had no
clearance exposure and cannot settle this question.

Prioritize **advanced-circuit consumer service** for the next policy cohort.
Within C37, all 54 C5-ever/C6-never runs lack a passive/requester-chest cell at
their final observation; 50 have provider chests in stock, 27 have a circuit
producer, and 33 previously started a temporary chest batch. By contrast,
17 of 18 C6-ever runs retain a chest cell at the end. These are associations
plus representative lifecycle traces, not proof that forced retention works.
The current validator accepts owned chest-consumer output; avoid optimizing a
validator-only chest loop and report useful output and displaced supply.

The smallest implementation should expose consumer ownership, configured/running
recipe and output separately from item stock and producer ownership. Put these
read-only facts in both arms. The treatment then offers an explicit, bill-backed
consumer-service operation using the existing standing-cell/loan machinery and
geometry. Let Laya choose it using existing goal-related preferences. Do not
silently retain every loan, mandate a chest layout, alter the C6 predicate, or
combine new weights with the operation.

The intended comparison is 60 fresh matched seed pairs, 120 planned jobs, with
the same committed repaired integration base, Factorio 2.1.23, canonical copied
save, 540,000 ticks and 7,200 seconds. Only the consumer-service capability
differs. Primary: strict C6 ever; key secondary: strict C6 final. Report C2/C3/C4
guards, C5-v2 startup and separate current plastic service, Laya C6-v2, typed
fault events and affected jobs, consumer ownership/output, operation exposure,
displaced producers, and all planned terminal/unknown statuses. Reserve fresh
seeds and ports at freeze time. The shared admission deadline remains
2026-10-27 18:00 IST; never extend it or shrink the denominator silently.

Falsify the proposed mechanism if ownership does not increase, if ownership
increases without useful consumer output/C6, or if earlier service or tool/safety
faults regress. Keep the adoption rule unchanged. A positive result would be
conditional on this integrated base, not an unconditional comparison to C29.

Before that full cohort, the repaired base must reach strict C2 with no tool or
safety fault. A two-job smoke, `laya-c39-preflight-2123-miniship-20261026`, is
running from immutable `c71d826`, seeds 80220/80221, ports 40000/33000, capped at
180,000 ticks/1,800 seconds per run and a 2,100-second systemd unit limit. It has
no consumer or clearance treatment and cannot establish adoption. If it fails,
fix the observed allocatable-stock facts before further policy comparisons.
The consumer operation still needs implementation, regressions and an exposure
smoke before freezing 120 jobs. No full C39 cohort is queued by this decision.

An isolated C5-v2 live fixture verified episode arming against historical
production, five engine-clock samples and four 10-item/s intervals. It injected
counter flow in a disposable diagnostic world: this proves sampler mechanics
only. The fixture server is stopped. Evidence is
`data/laya-orchestration/c39-20261026/startup-live-probe.json`.

The clearance patch was also reconciled in a separate local candidate checkout:
64 focused checks and its fast gate pass. It remains unlaunched and outside
main. No installed mod, real base, source save or historical freeze was changed.

Evidence: [C37 result](Laya_C37_Result_2026-10-26.md),
[C38 result](Laya_C38_Result_2026-10-26.md), and the
[outcomes](laya_helper_runs/c38-diagnosis-20261026/c37-outcomes-review.md),
[mechanisms](laya_helper_runs/c38-diagnosis-20261026/c37-mechanisms-review.md), and
[terminal](laya_helper_runs/c38-diagnosis-20261026/terminal-audit.md) reviews.
Operational continuation is `data/laya-orchestration/c39-20261026/state.json`.
