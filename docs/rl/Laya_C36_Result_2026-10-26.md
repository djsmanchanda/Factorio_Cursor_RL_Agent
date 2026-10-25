<!-- Path: docs/rl/Laya_C36_Result_2026-10-26.md | Purpose: Complete C36 accounting and the convergence adoption decision. -->
# Cohort 36: Refinery pole clearance with named blockers

Decision recorded **2026-10-26 IST** by the convergence owner. **Not adopted as the default yet: strict C6 improves, but reported faults increase and the final C5 guard remains unresolved.**

`data/laya-experiments/laya-c36-2123-miniship-20261025`: **120 planned = 120 completed**, 60 matched seeds per arm, no omitted crashes, cuts or reruns. Both arms use Factorio 2.1.23 build 87750, the same copied save, 540,000 ticks and a 7,200-second wall allowance. Strict predicates and the separate Laya choice/output C6 endpoint are reported independently.

| Endpoint | control / 60 | clearance / 60 | Treatment-only : control-only |
|---|---:|---:|---:|
| Strict C1 ever | 0 | 1 | 1:0 |
| Strict C2 ever | 47 | 48 | 8:7 |
| Strict C3 ever | 31 | 34 | 13:10 |
| Strict C4 ever | 31 | 38 | 16:9 |
| Strict C5 ever | 31 | 38 | 16:9 |
| Strict C6 ever | 5 | 15 | 12:2 |
| Strict C3 final | 28 | 34 | 15:9 |
| Strict C5 final | 17 | 7 | 7:17 |
| Strict C6 final | 2 | 13 | 12:1 |
| Laya C6-v2 | 20 | 32 | 22:10 |

C1 is historical `c1-v1`, C5 is `c5-v1`, and strict C6 is `c6-v1`. Main's c1-v2 and the requested plastic-startup C5-v2 are not results of this cohort. Ever and final use sampled acceptance; a final pass does not establish sustained service.

Control `d9402da` is the C29 release plus blocker-name telemetry; treatment `cdb16a2` adds pole clearance (`e5471f8` + `ba31bec`). Neither arm includes the structural census or the later robot exclusion.

Strict C6 improves **5→15 / 60**, 12:2 discordant pairs (exact two-sided p=0.0129), and final strict C6 improves 2→13, 12:1 (p=0.0034). Laya C6-v2 improves 20→32. C33's earlier C3 penalty does not recur: C3 is 31→34 (13:10). The target gain is substantial enough to prioritize a follow-up.

The mechanism removes the actual pole obstruction: named `big-electric-pole` blockers fall 383→0; 26 treatment worlds move an iron-line pole. Remaining blockers are predominantly flying robots; some are building ghosts. C37 tests robot exclusion on the non-clearance line, so it cannot validate the combination.

The default adoption gates are still unmet. `untyped_stuck` rises **12→36** (11→21 affected runs): stone-line health 10→20, iron-line health 0→6, and a bare refinery-extension refusal 0→10; copper-line health falls 2→0. Caught exceptions remain zero, which must not hide typed fault counts. No infrastructure collisions were observed. These are newly exposed existing paths, but that explanation does not erase the recorded guard.

Final C5 falls **17→7**, 7:17 discordant pairs (p=0.064). The raw service projection classifies many treatment endings as a stocked plastic plateau (31 versus 13), with little measured consumption. This is evidence of a measurement/consumption question, not proof of unchanged service; no new predicate is substituted after seeing the result. Paired earlier-milestone times are roughly 3–4 game minutes slower; completed-run medians are 2,928→3,591 wall seconds. Report those costs alongside the C6 gain.

Decision: retain clearance as a promising experimental factor, keep C29 as the adopted policy reference, and compare clearance with robot occupancy corrected in both arms on fresh matched seeds. This estimates a conditional clearance effect, not a factorial interaction. Neither the combination nor a changed C5 interpretation is adopted by scheduling that experiment.

Evidence: the cohort's `batch.json`, `freeze.json`, `job-status.json`, `report.json`, per-run acceptance journals and runner logs; independent raw aggregation of all 120 terminal worlds. Local helper evidence is in `docs/rl/laya_helper_runs/convergence-20261026/{c35-c36-review.md,c35-c36-derived.json,recent-cohorts-review.md}`. These helpers are evidence; the decision above belongs to this tracked result. [Adoption rule](laya_variants/README.md).

Evidence level: completed matched experiment on one copied starting save. No terrain generalization, deployed-runtime result or real-base promotion. Lifecycle: no restart or deployment for this result; existing C37 stays untouched.
