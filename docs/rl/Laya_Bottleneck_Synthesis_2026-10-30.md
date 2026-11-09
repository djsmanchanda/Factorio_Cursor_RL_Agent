<!-- Path: docs/rl/Laya_Bottleneck_Synthesis_2026-10-30.md | Purpose: Combine five ds41f lens reviews of C30-C46c into bottlenecks and next-cohort implications. -->
# Laya bottleneck synthesis, 2026-10-30

Evidence level: replay of recorded cohort data (C30-C46c) plus spot re-checks by the main agent. No new experiment, no adoption claim.
Lens notes (helper output, evidence not authority): `docs/rl/laya_helper_runs/2026-10-30-ds41f-{funnel,controls,faults,capacity,frontier}.md`.

## Verified by the main agent
- `data/laya-host/history.jsonl` (5 d): active slots max 23, busy median 15, last-24h max 20. The batch ceiling of 30 was never reached.
- C45 and C46c: `stalled` 117/120 and `window_timeout` 3/120 in each. Nothing ended on the wall budget.
- C46c strict C6 ever: reference 9/60, consumer 19/60. Discordant pairs 17:7, exact two-sided p = 0.064.
- Game data (`factorio-2.1.21/data/space-age/base-data-updates.lua:757-771`): `mining-productivity-4` is deleted; `mining-productivity-3` becomes infinite at `1000*(L-2)` units of automation, logistic, chemical and production science (60 s each). "Mining-productivity-4" therefore means level 4 of that technology: 2,000 units.
- `tools/run_laya_clean.py --research-target` exits with an error: `training/laya_clean_runner.py` `Settings` has no `research_target`, `research_progressive` or `research_sustain_ticks` fields. Research ops (`research_select`, `research_labs`, `scale_production`) exist only on the cohort reference `9d9b279`.
- Correction: the frontier note says proxy S1 was reached in 0/240 runs. That is the job-summary phase-list omission. Final world status shows S1 in 24/60 reference and 33/60 consumer C46c runs.

## Bottlenecks, ranked
1. **Experiment power, not run length.** Control C6-ever sits at 13-23% (about 18.3 +/- 3.9 pp) with pure binomial dispersion across C34-C46c. Detecting +8 pp needs roughly 330-500 pairs; confirming the C46c effect needs about 120. Many past wins (C36 clearance, C35 LHI, C45 steel, C43 accell) did not replicate.
2. **C5 to C6 strands 70% of funnel loss.** 58 runs end at C6 with advanced circuits output-blocked (41) or unbuilt (13). The proxy C6 passes without a measured consumer draw, so strict C6 fails. The existing `op.raise_stock_gate` was offered in 5 of 128 sampled tails.
3. **C3 to C4 (first plastic) ends the median run.** 140k ticks median; stalled runs stop at about 345k ticks. Blockers: chemical-ladder gate (77 runs), mall-tier component with no producer (72), refinery-extension site collision (56), mall slot cap (50), steel seed wait (32), rotating-mall handoff (29).
4. **Missing facts are the common cause.** The chemical gate's `{target, rung}` never reaches the decision state; producer existence and refusal reasons per missing mall item are absent; there is no per-cell status fact at C6.
5. **Research is unmeasurable today** (see above). Best final state: 284 automation and 72 logistic packs, zero chemical or production, no labs recorded, `research_acceptance` null 240/240. Level 4 needs 2,000 of each pack and about 31-34 labs in the 210-230k ticks left after a median C6.
6. **Not binding:** wall time (max 5,175 s of 7,200), tick cap (19/410, 18 in S1), thermal (87-90 C of 96), the 30-slot ceiling. The host idled 20.9 of 120 h (17%, longest gap 5.3 h).

## Implications for the next cohorts
- Fix order per the repo rule: facts, then operations, then a quoted weight tested as a variant. The first three facts are the chemical rung, the producer-existence/refusal reason and the C6 cell status.
- Test the C5-to-C6 treatment from C5-origin checkpoints (runs are far shorter than from C0), which is what the validated checkpoint bank is for. Pair every comparison.
- Mining-productivity-4 needs a C6-origin starting point with the full tick budget, the research facts and operations ported from `9d9b279`, and a research receipt as the endpoint. Report it separately from proxy S1 and from strict C0-C6.
- Concurrency is not the lever; backing the queue to avoid idle gaps recovers more.
