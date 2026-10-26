<!-- Path: docs/rl/laya_helper_runs/c38-diagnosis-20261026/c37-outcomes-review.md | Purpose: Read-only audit of C37 robot-occupancy outcomes and C38 early-failure diagnosis for the parent's merge/converge/set-aside decision. -->

Parent verification, 2026-10-26 15:59 IST: the section 3.2 claim that control-s80125 passed C6 without C5 is incorrect. Raw accepted_ever is C0/C2/C4/C5/C6; C3 is missing. Direct marginal groups are C5-not-C6 25/29 and C6 11/7, with overlapping groups. The tracked C37 result is corrected; all 120 primary outcomes remain unchanged.

# C37 outcome audit and C38 early-failure diagnosis

Audit date: 2026-10-26 IST. Read-only. No subdelegation, no code/commit/lifecycle action, no edits outside this file.

## Decision card

| Question | Finding | Recommendation |
| --- | --- | --- |
| C37 default adoption | **No.** Preregistered primary strict C6 ever: robots 7 vs control 11 (5:9 discordant, exact two-sided p=0.424); final 6 vs 8 (4:6, p=0.754); Laya C6 v2 26 vs 29 (16:19, p=0.736). C2 also leans against (50 vs 53; 3:6, p=0.508). | Keep default base `5a5ed28`; do not adopt the robot exclusion as a stand-alone policy. |
| Functional robot-occupancy correction | **Verified.** 0 robot-named refusals in 60 robots runs vs 6 robot-only + 36 mixed control runs. Exactly two occupancy functions changed. In all six control robot-only seeds the robots arm lost the robot/mixed refusal; four still named the real pole, two had no refusal. | Treat as a correctness fix, not a performance gain. |
| Disposition | **Retain experimental; do not merge to default; do not discard.** | Keep `32c1ef5` on the experimental line as the common condition for a clearance cohort. Converge only after the `c71d826` C38 harness repair is verified. |
| C38 | **Void as a clearance test.** 120 planned = 119 completed + 1 `prepare_failed`; 117/119 completed runs stopped at the new 12-decision idle cutoff (median 4,476 idle ticks, 24.9% of the 18,000-tick window); 2 hit 18,000 ticks at 11 decisions; 0 C2; 0 clearance actions. | Do not read C38 as evidence for or against clearance. Refreeze and rerun only after the committed ops-stall repair (`c71d826`) is smoke-tested. |
| Cross-cohort causality | **Not admissible.** C37 is `d9402da` with C1-v1/C5-v1 and the 18,000-tick-only stop; C38 is `9764fb6` with C1-v2/C5-v1 and a 12-decision cutoff. | Use only within-cohort paired contrasts. |

## 1. Bounded context and sources

- C37: `data/laya-experiments/laya-c37-2123-miniship-20261026`, arms control `d9402da` / robots `32c1ef5`, seeds 80100–80159, 60 per arm, Factorio 2.1.23 build 87750, source save sha256 `347eba65065014f82261330d84d5ca2fa3e3427cad3ab18dcfdf496dd9c442b0` (same file as C36), 540,000 ticks / 7,200 s, concurrency 30. Ran 04:23–12:00 IST; shared the host with C38 from 05:17.
- C38: `data/laya-experiments/laya-c38-2123-miniship-20261026`, arms robots / clearance_robots, revision `9764fb6`, seeds 80160–80219, 60 per arm, same source-save hash, 540,000 ticks / 7,200 s, ports 39900/32900. Ran 05:17–12:48 IST.
- Canonical main worktree: `data/laya-orchestration/convergence-20261026/publish-code`, now `HEAD c71d826` (C38 repair on top of `2be329e`). At audit start it was at `2be329e` with the `decision-idle-v3` repair uncommitted; the parent committed it as `c71d826` at 15:47 IST while this read-only audit was running. I preserved all work, did not modify it, and did not run its tests.
- C37 result text: canonical `publish-code/docs/rl/Laya_C37_Result_2026-10-26.md` (corrected; C5 final 12 vs 8; 59/60 caveat) and takeover worktree `data/laya-orchestration/takeover-20261021/wt/docs/rl/Laya_C37_Result_2026-10-26.md` at `8e5280e` (older text; says C5 final “12 vs 6” and “no measured harm”). The canonical version is authoritative; `8e5280e` contains the typo and the stronger claim.
- Evidence level: completed matched cohort on one copied save, plus a failed early-exposure cohort. Not unseen-terrain or deployed-runtime evidence. Proxy phases are not deterministic milestones.

## 2. C37 planned-job accounting and stop reasons

Raw: `job-status.json`, `planned-jobs.json`, `batch-events.jsonl`, `report.json`.

- 120 planned IDs = 120 unique job IDs = 120 `attempt_started` = 120 `attempt_terminal` = 120 `completed`. 60 per arm, 60 seeds, every seed paired. No cuts, reruns, duplicates or omitted jobs. Every runner return code 0. `failures=0`, `evidence_errors=0` for all 120.
- Stop reasons: 111 `stalled`, 9 `window_timeout` (control 53/7, robots 58/2). The stalled runs end on the frozen 18,000-tick rule (example: `control-s80100`, runner log `STALLED: 18000 ticks without a built operation, work in progress or goal progress`). Window timeouts are capped at 540,000 ticks, not wall time; median elapsed 2,950 s, max 4,880 s.
- Provenance caveat: both freezes still carry a pre-run state string (`queued_dependency_gated` / `queued_host_gated`) although the cohorts are terminal. That is bookkeeping, not missing evidence; the job ledger and report are complete. Do not edit the freezes.

## 3. C37 endpoint counts and exact paired tests

`accepted_ever` = any acceptance sample passed during the run. `accepted_final` = last acceptance sample. Strict endpoints are `c0-v1`…`c6-v1`; Laya C6 is the separate `laya-c6-choice-output-v2` choice/output endpoint.

| Endpoint | control | robots | robots-only | control-only | exact sign p |
| --- | ---: | ---: | ---: | ---: | ---: |
| strict C1 ever | 2 | 1 | 1 | 2 | 1.000 |
| strict C1 final | 0 | 0 | 0 | 0 | 1.000 |
| strict C2 ever = final | 53 | 50 | 3 | 6 | 0.508 |
| strict C3 ever | 36 | 37 | 10 | 9 | 1.000 |
| strict C3 final | 36 | 36 | 9 | 9 | 1.000 |
| strict C4 ever = final | 36 | 36 | 15 | 15 | 1.000 |
| strict C5 ever | 36 | 36 | 15 | 15 | 1.000 |
| strict C5 final | 8 | 12 | 10 | 6 | 0.455 |
| **strict C6 ever (primary)** | **11** | **7** | **5** | **9** | **0.424** |
| strict C6 final | 8 | 6 | 4 | 6 | 0.754 |
| Laya C6 v2 (separate) | 29 | 26 | 16 | 19 | 0.736 |

- The harness `report.json` independently reproduces the same discordant counts for C2 (3:6), C5 ever (15:15), C6 ever (5:9), C5 final (10:6), C6 final (4:6) and Laya C6 (16:19). C3 was re-derived from `job-status.json`.
- Paired bootstrap (exploratory, 20,000 paired resamples): strict C6 ever difference robots−control = −6.7 pp, 95% interval −18.3 to +5.0 pp; C2 = −5.0 pp, −15.0 to +5.0 pp. The cohort is too small to establish non-inferiority; “no measured harm” in the older text is too strong. The defensible statement is “no established harm and no established gain.”
- A/A noise benchmark used by the result doc: C12 `seed` vs C13 `ref`, byte-identical code, 33 common seeds, C2 6 vs 13 with discordant 5:12, net 7. Every C37 net imbalance is ≤4 (C6 4, C5 final 4, C2 3, C3 1), so the C37 contrasts are inside the A/A **net** spread. This is a historical noise benchmark, not a formal equivalence test on C37.
- Speed secondary (RMST, game minutes, capped 150; lower is faster): strict C2 49 control / 54 robots; C3 97/94; C4 110/109; C5 112/111; C6 143/146; Laya C6 120/123. Strict C6 paired 7 robots-faster : 9 control-faster, median +18 min. Robots is slightly slower to the primary and C2, slightly faster to C3. These are secondary and within noise.

### 3.1 Endpoint-version caveats

- C37 uses C1-v1, C2-v1, C3-v1, C4-v1, C5-v1, C6-v1 and Laya C6 v2 for all 120 jobs (frozen code `arms/control/code/orchestrator/checkpoint_milestones.py`, `arms/robots/code/...`).
- **C5 is c5-v1, not the user's plastic-startup c5-v2.** The result doc says this; C37 cannot test the c5-v2 definition. The c5-v1 final predicate is also confounded: all 28 control and 24 robots runs that ever passed C5 but not at final end at `stocked_plateau` with 4,800–4,834 plastic in the network (one full passive-provider chest). C5 final measures late plastic **consumption/backpressure**, not lost capability. The 8 vs 12 difference is therefore not a capability signal in either direction.
- C1-v1 strict acceptance is 2 vs 1; C1 final is 0/0, so C1-v1 final is not a usable endpoint here. The “59/60” number is **not** a strict C1 denominator: proxy C1 is 59/60 control because `control-s80112` never entered the C1 proxy phase (only C0); proxy C1 is 60/60 robots. Proxy phase reach is not acceptance.
- C38 uses C1-v2 (20/59 vs 18/60) but **C5-v1**, not c5-v2, despite main now exposing c5-v2. C1 and C5 are therefore not comparable across C37/C38. C38 never reached C5, so its C5 version has no outcome effect, but it matters for any future freeze.

### 3.2 The 59/60 stop-table caveat

The result doc's last-25-decision table reports control groups 7 / 17 / 5 / 21 / 10 = 60, but its C6 row is 10 while marginal strict C6 ever is 11. The concrete anomaly is `control-s80125` (`control-s80125-s80125-1792978951`, `window_timeout`): it accepted C6 without C5, so the “first missing milestone” hierarchy places it in the C3-no-C5 group. That explains the C6 row, but it does not by itself produce 59.

A raw re-derivation does **not** reproduce 59 as a group count: the groups sum to 60/60. Three control runs have no non-ok outcome code in their last 25 decisions (`control-s80140`, `control-s80152`, `control-s80153`), and five control runs reached C5 without C3 (`control-s80102`, `control-s80117`, `control-s80125`, `control-s80141`, `control-s80152`), so the table's labels are descriptive rather than a strict funnel. Treat “59/60” as a labeling/denominator caveat for the stop table, not as a change to the primary acceptance denominator. All 120 jobs remain in the primary table; the marginal C6 count (11) is the correct C6 count.

The stop table is also not a linear funnel: 5 control and 6 robots runs reached C5 without C3, so “C3, no C5” is better read as “no C3, possibly C5/C6.” Use the table only for terminal-outcome description, never as an attainment denominator.

## 4. C37 exposure, mechanism and fault classes

`blockers.py` over refinery-extension refusal lines:

| Measure | control | robots |
| --- | ---: | ---: |
| refusals naming only robots (events / runs) | 6 / 6 | 0 / 0 |
| refusals naming robots and real infrastructure (events / runs) | 168 / 36 | 0 / 0 |
| refusals naming only real infrastructure (events / runs) | 527 / 48 | 570 / 46 |
| named `big-electric-pole` mentions | 638 | 568 |

Decisive raw example (one line per arm, not a log dump):

- control: `... iron-plate refinery extension intersects real infrastructure at [(-41, -15), (-41, -14), (-40, -15)] (big-electric-pole@(-40.0, -14.0), logistic-robot@(-37.828, -19.078)); refusing to expand its mine ...`
- robots: the same footprint, `(big-electric-pole@(-40.0, -14.0)); refusing to expand its mine ...` — robot gone, pole remains.

Within-seed mechanism check: the six control robot-only seeds are 80103, 80110, 80117, 80123, 80135, 80138. In all six, the robots arm has **zero** robot/mixed refusals. Four of the six still have real-only refusals (80103, 80110, 80117, 80138); two have no refusal at all (80123, 80135). Only 80138 reaches strict C6 in control, and robots does not. This is the cleanest evidence that the fix removed the robot obstruction but did not create a milestone gain on this base.

Why the null performance effect: the real big electric pole at (−40,−14) remains the binding blocker in 46/60 robots runs. Only 6 control runs had a robot-only refusal; 36 more had mixed robot + real refusals. Removing the robot component changes the refusal message, not the extension outcome.

Fault classes:

| Failure class (events / runs) | control | robots |
| --- | ---: | ---: |
| tool_error / safety stop | 0 / 0 | 0 / 0 |
| untyped_stuck | 72 / 17 | 29 / 15 |
| infrastructure collision | 2 / 2 | 0 / 0 |
| power route refusal | 15 / 10 | 16 / 9 |
| loan-release issues (journal + runner) | 2 worlds (80104, 80158) | 3 worlds (80118, 80124, 80152); 3 `LOAN RELEASE FAILED` runner lines |

Untyped stuck is dominated by control seeds 80154 (25 events), 80116 (12) and 80125 (12), and robots seed 80152 (11). By message: stone-brick refinery `full_output` 57 control / 24 robots; `No recipe knowledge for 'stone'` 11 / 0; copper-plate `full_output` 4 / 0; iron-plate `full_output` 0 / 5. These are planner/backpressure classes, not robot-occupancy faults. The robots reduction in untyped stuck is a secondary operational lean, not a preregistered target.

## 5. Preregistered adoption rule vs actual evidence

`docs/rl/laya_variants/README.md`: default adoption requires **more long-window target completions on matched conditions**, **no increase in safety/tool failures**, and **no earlier-service regression**. The C37 freeze names strict C6 ever as primary and C2/C3 as earlier-service guards (`freeze.json.primary`).

| Criterion | C37 evidence | Verdict |
| --- | --- | --- |
| More long-window target completions | strict C6 ever 7 robots vs 11 control (5:9, p=0.424); final 6 vs 8 (4:6, p=0.754); Laya C6 26 vs 29 (16:19, p=0.736). Only C5 final is higher for robots (12 vs 8, p=0.455) but it is secondary and plateau-confounded. | **Fail.** Point estimates are fewer, not more. |
| No increase in safety/tool failures | 0 tool/safety, 0 caught failures, 0 evidence errors; untyped stuck 29 vs 72; collision 0 vs 2. | **Pass.** |
| No earlier-service regression | C2 50 vs 53 (3:6, p=0.508; RMST 49 vs 54); C3 37 vs 36 (10:9, p=1.0; RMST 97 vs 94). C2 is a nominal regression inside the A/A net spread; C3 is a nominal improvement. | **Not clean.** The C2 point estimate is against; the rule does not grant a noise exemption. |
| Full planned denominator | 120/120, no cuts or reruns. | **Pass.** |
| No exposure means unexercised, not negative | The robot exclusion is exercised in 60 robots runs and removes the named robot refusals; the real pole remains. | **Pass for mechanism, not performance.** |

The first criterion already fails, so the guard debate cannot produce default adoption. The correct disposition is **retain experimental**, not adopt and not discard.

## 6. Functional correction vs matched performance gain

**What is proven (functional):**
- The code diff is exactly two occupancy edits in `orchestrator/live_base.py` (`occupied_tiles`, `occupied_tile_owners`) that exclude `logistic-robot` and `construction-robot`, plus one test. Control's only difference from `5a5ed28` is the blocker-naming telemetry, so the behavioral factor is isolated.
- Zero robot/mixed refinery refusals in 60 robots runs; 6 robot-only and 36 mixed in control; the six robot-only control seeds lose the robot refusal in the robots arm. The fix behaves as designed.

**What is not proven (performance):**
- No target completion improves. The primary and C2 point estimates lean against; C3 is level; Laya C6 is level/against; C5 final is a confounded secondary.
- “No harm” is not established either; the strict C6 confidence interval spans −18 to +5 pp. The older `8e5280e` claim “no measured harm” should not be used. The canonical main text (2be329e, unchanged in c71d826) correctly says the mechanism check “does not prove milestone non-regression or adoption.”

**Disposition options:**

| Option | Verdict | Reason and condition |
| --- | --- | --- |
| Merge to default main (`5a5ed28`) | **No** | No long-window gain; nominal C2/C6 leans against. Default base stays. |
| Converge into the experimental clearance line | **Yes, conditional** | Keep `32c1ef5` as the common robot-occupancy condition; it is the correct precondition for a clearance test. Only after the ops-stall repair is committed/verified, a new freeze is made, and a smoke run reaches C2/C3 with a clearance action. |
| Set aside the stand-alone policy claim | **Yes** | Set aside the claim, not the code. Archive the branch as a documented correctness fix if no clearance cohort is funded. |
| Discard the code/branch | **No** | Mechanism is correct, no safety/tool increase, and discarding would lose the conditional test condition. No evidence of harm outside A/A noise. |

**Evidence that would change the decision:**
- A valid clearance cohort (120/120, robots common to both arms, clearance the only between-arm factor) with strict C6 ever/final at least equal to control, paired discordant favoring clearance, no C2/C3 regression, and zero tool/safety faults. At least one `SMELTER POLE CLEARANCE`/`POLE RELOCATION` action per treatment run is required to prove exposure.
- A larger robot-only cohort with strict C6 ever at least equal to control and a confidence interval excluding the observed −18 pp, plus no C2/C3 regression. If the goal is a correctness-only merge, predeclare a limited adoption (as the mf30 exception did) with the null/negative performance evidence and a required recheck.
- Discard only if a valid cohort shows harm outside the A/A net spread, a safety/tool increase, or false placements/collisions. None is present in C37.

## 7. C38 diagnosis: invalid clearance cohort

Raw: `data/laya-experiments/laya-c38-2123-miniship-20261026/{job-status.json,freeze.json,report.json,logs}`, arm runner logs.

| Item | Value |
| --- | ---: |
| Planned jobs | 120 |
| Completed | 119 |
| `prepare_failed` | 1 (`robots-s80213`, `RuntimeError: prepare exit 1`) |
| Arm completion | robots 59/60, clearance_robots 60/60 |
| Complete matched pairs | 59 |
| Stop reasons | 119 `stalled`; 0 `window_timeout`; 1 prepare failure |
| Runs stopping at 12 decisions | **117 / 119** |
| Runs stopping at 18,000 ticks | 2 / 119 (`robots-s80201` and `clearance_robots-s80201`, both at 11 decisions) |
| Idle ticks at stop | 117 at 12 decisions: median 4,476; all 119: median 4,482; min 2,244; max 19,750 (18,000 = intended window) |
| Strict C2 | **0 / 0** |
| Strict C3–C6 | 0 / 0 |
| Strict C1 (c1-v2) | robots 20/59 ever, 1 final; clearance 18/60 ever, 0 final; discordant 2:4, p=0.688 |
| Laya C6 v2 | 0 / 0 |
| tool_error / untyped_stuck / safety | 0 / 0 / 0 |
| Clearance actions | **0** (`SMELTER POLE CLEARANCE` / `POLE RELOCATION` absent from all 60 treatment logs; “clearance” matches only the arm name in `RUN START`) |

### 7.1 Root cause

The converged base `9764fb6` couples ops stalls to the **geometry** setting:

`training/laya_ops_runner.py` (frozen C38): `if stall["idle_ticks"] >= STALL_TICKS or stall["since"] >= settings.stall_decisions:` with `Settings.stall_decisions = 12`.

C37's frozen ops runner has only `after["tick"] - stall["tick"] >= STALL_TICKS` (18,000 ticks). The README explicitly says: “Ops has a separate 18,000-tick stall constant; that setting does not tune it.” The converged base therefore ran with a 12-choice cutoff that fires at a median of 4,476 idle ticks for the 117 decision-cutoff runs, about 25% of the declared window, before the base reaches C2. This is a harness regression, not a clearance or robot effect.

Exact runner lines:

- `robots-s80160` (`robots-s80160-s80160-1792972320`): `STALLED: 2421 idle game ticks / 12 decisions without a funded submission, work in progress or goal progress`
- `robots-s80201` (`robots-s80201-s80201-1792996782`): `STALLED: 19654 idle game ticks / 11 decisions ...` (the intended 18,000-tick rule)
- `clearance_robots-s80201` (`clearance_robots-s80201-s80201-1792996580`): same 18,000-tick stop at 11 decisions.

### 7.2 Why C38 cannot validate clearance conditional on robots

- No run reached C2, so the refinery-extension path that calls `_plate_expansion_foundation` was never entered.
- Zero treatment logs contain the clearance action; the only between-arm factor was never administered. This is **unexposed**, not a negative result, exactly as the C37 freeze's “no exposure = unexercised, not negative” rule warns.
- The cohort is also unbalanced at 59 pairs because of the robots `prepare_failed` job. The freeze says all 120 planned jobs count; the report correctly shows 59/60 and 60/60, but any arm contrast is descriptive and underpowered even before the exposure problem.
- C38's freeze says “No automatic adoption. All120 planned jobs count … more target completions, no earlier-service regression and no increase in typed tool/safety faults.” With 0 C2, the primary endpoint is untested, not failed.
- Do **not** compare C37 robots to C38 clearance_robots (or C37 control to either C38 arm) as causal evidence: different base (`d9402da` vs `9764fb6`), different stop rule, different C1/C5 predicates, different seeds, and shared host time. Only within-C38 pairing was intended, and it is unexposed.

### 7.3 Repair committed during the audit

The repair is now commit `c71d826` (`fix(RL Laya): honor the ops idle game window before stalling`) on top of `2be329e`; it was uncommitted when I began reading and was committed at 15:47 IST while this audit was running. It removes the ops `settings.stall_decisions` cutoff, changes the stall summary to `decision-idle-v3` with `decision_limit: None`, and adds a regression test for delayed progress after twelve quick choices, citing C38's 117 runs in its comment. This is the right direction. I did not run its tests; the parent owns verification and freezing. Do not edit C38's freeze or reuse it for a new cohort.

## 8. Next cohort recommendation

1. **Verify the committed ops stall repair first.** `c71d826` restores the 18,000-tick-only ops rule. Run the narrow ops-runner tests and the fast gate, and confirm no `settings.stall_decisions` affects ops. Do this in the parent thread; do not mutate C38's frozen arms.
2. **Smoke before spending 120 jobs.** Run 2–4 seeds on the repaired revision for wiring; require at least one C2/C3 reach. If no `SMELTER POLE CLEARANCE`/`POLE RELOCATION` action appears in the treatment arm, extend to an 8–12-seed exposure check before the full cohort. If the base cannot reach C2 in the smoke window, do not launch a 120-job clearance cohort.
3. **New freeze, not a C38 rerun.** Freeze `c71d826` or a later test-verified revision with a new cohort ID, 120 jobs, 60 seeds per arm, robots common to both arms, clearance only in the treatment. Predeclare primary strict C6 ever, C2/C3 guards, C5 (state c5-v1 or c5-v2 explicitly), Laya C6 v2, full 120 denominator. Keep 540,000 ticks / 7,200 s; C37's observed median was 2,950 s and max 4,880 s, so 7,200 s is likely adequate, but confirm in smoke because clearance adds work.
4. **Predeclare the series break.** C37/C38 are C1-v1/C5-v1 (C38 is C1-v2 but C5-v1); main now exposes c5-v2 and c1-v2. A new cohort using either is not poolable with C29–C38. State the comparison boundary in the freeze and result.
5. **If no clearance cohort is funded:** close C38 as void (not negative), keep `32c1ef5` archived on the experimental branch as a documented correctness fix, and work the C37 open items: never-C2 chemical handoff, steel-seed site wait, late mall shortages, and `No recipe knowledge for 'stone'`.

## 9. Decisive paths, run IDs and lifecycle

**Paths**
- C37 root: `data/laya-experiments/laya-c37-2123-miniship-20261026/` (`job-status.json`, `planned-jobs.json`, `batch-events.jsonl`, `freeze.json`, `report.md`, `report.json`, `logs/`, arm journals).
- C38 root: `data/laya-experiments/laya-c38-2123-miniship-20261026/` (`job-status.json`, `freeze.json`, `report.md`, `report.json`, `logs/`).
- C37 result: `data/laya-orchestration/convergence-20261026/publish-code/docs/rl/Laya_C37_Result_2026-10-26.md`; older `8e5280e` copy at `data/laya-orchestration/takeover-20261021/wt/docs/rl/Laya_C37_Result_2026-10-26.md`.
- Audit scripts: `data/laya-orchestration/takeover-20261021/{blockers.py,stops.py,time_to.py}`.

**Run IDs (examples; full set in `job-status.json`)**
- C37 `control-s80100-s80100-1792968841`; `control-s80125-s80125-1792978951` (non-monotone C6); `robots-s80152-s80152-1792989798` (untyped-stuck seed).
- C38 `robots-s80160-s80160-1792972320` (12-decision stop); `clearance_robots-s80160-s80160-1792972623`; `robots-s80201-s80201-1792996782` and `clearance_robots-s80201-s80201-1792996580` (18,000-tick stops); `robots-s80213` (prepare failure, no run ID).

**Lifecycle**
- No services, freezes, source, schedules, Git refs or worlds were changed. Only `laya-host-scheduler.service` is active; no C37/C38 campaign unit is active.
- The robot exclusion and the C38 clearance patch are Python source changes; no mod deployment is implied. A new cohort needs a new frozen code copy and its own isolated servers. Parent owns any commit, merge, freeze, restart or deployment.

## 10. Limitations

- One copied save, one engine build; no terrain generalization. Strict milestones are proxy acceptance predicates, not sustained production; `accepted_final` is a point-in-time sample.
- The A/A benchmark is a different cohort (33 seeds, C12/C13); it bounds noise informally, not by a formal equivalence test.
- C5 is c5-v1 and plateau-confounded; the user's c5-v2 plastic-startup definition is untested in both cohorts.
- C38 is invalid for the clearance question; its 59-pair arm contrast is descriptive only. C37 host sharing with C38 may affect absolute timings but not the within-seed arm pairing.
- The canonical worktree repair is committed as `c71d826` but was not test-verified by this audit. No tests were run; the parent should verify before freezing.
