<!-- Path: docs/rl/Laya_C39_Result_2026-10-27.md | Purpose: Record complete C39 accounting and the reference-base decision for the model cohort. -->

# C39 result and shared base — 2026-10-27

Retain reference `9d9b2796a3ecb5bc1faed213ea825a6614e8eaff` as the shared
mechanical base for the prepared model cohort. Do not adopt integrated
`f098f9f2cda9e3803d8ed3569a37c7643d37368f` from this experiment.
Parent thread `94dcfaf2-9531-4234-bde5-b6ae616de9b1` made this decision after
complete results and cleanup verification at 2026-10-27 00:47 IST.

All 120 planned C39 jobs completed: 60 matched seeds per arm, 80240–80299,
540,000-tick / 7,200-second caps, no missing or duplicate attempts, all runner
return codes zero. Raw acceptance journals agree with recorded ever/final
outcomes for every job. This compares whole frozen bases with different
collection and stop behavior; it does not isolate a single causal mechanism.

| Strict endpoint | Reference /60 | Integrated /60 |
| --- | ---: | ---: |
| C6 ever, primary | 13 | 0 |
| C6 final | 8 | 0 |
| C2 ever/final | 53 | 13 |
| C3 ever/final | 50 | 7 |
| C4 ever/final | 39 | 0 |

C6-ever discordances are 13 reference-only, zero integrated-only; exact
two-sided paired P=0.000244140625. C6-final P=0.0078125. Integrated fails the
required improvement in long-window completions and earlier-service guards
under the [adoption rule](laya_variants/README.md).

Active C5 predicates are incompatible across all 60 pairs (reference frozen
v1 without journal version tags; integrated v2). Descriptive reference C5
ever/final is 39/7; integrated confirms no active passes and ends unknown in
all 60. Integrated's separately collected v1 shadow also has zero passes.
These facts do not make the active C5 comparison valid. C1 and stop contracts
also differ. Integrated exposure is shorter; do not call this policy-only or
equal-exposure evidence.

No caught-exception/tool-error/safe-stop events were observed. Untyped fault
events are 37 reference versus 88 integrated, affecting 21 versus six runs;
73 integrated events are unsupported petroleum-gas requests and 15 are
full-output health faults. Other typed and maintenance faults remain in the
full reports. Integrated standing-provider exposure occurred in 14/60 runs,
with 150 selections and zero successful outcomes; consumer ownership or
retention improvement remains unproven. Set the integrated bundle aside for
mechanism repair and subsequent isolated experiments rather than merging it
into the model comparison.

Evidence:

- [Final paired outcomes and predicate audit](laya_helper_runs/model-launch-20261026/c39-final-outcomes.md), with per-job JSON companion.
- [Exposure and fault mechanisms](laya_helper_runs/model-launch-20261026/c39-final-mechanisms.md).
- `data/laya-orchestration/model-launch-20261026/c39-cleanup-proof.json`: unit inactive/success, MainPID 0, no scoped live processes, no active/reserved registry jobs. Retained world evidence and expired C14b/C15b queue metadata are preserved.
- `data/laya-orchestration/model-launch-20261026/prepared-cohort/base-selected.json`: ready reference-base decision with evidence and deciding parent.

This selects a disposable experimental base, without changing production or
adopting a learned model. The four-arm model cohort still needs concurrent
memory qualification and its separately counted four-job wiring smoke;
`baseline-decision.json` remains pending. No model jobs have been admitted at
this milestone. No runner restart, Factorio restart, mod deployment or push.
