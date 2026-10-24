<!-- Path: docs/rl/Laya_Starship_Transfer_Decision_2026-10-24.md | Purpose: Record verified Starship log transfer, experimental signals and the calibrated Miniship C28 follow-up. -->

# Starship evidence transfer and Miniship C28

Decision recorded October 24, 2026 at 04:54 IST. The user requested copying
Starship logs and using the experimental results to choose further Miniship
tests. **Queue steel30 as the primary candidate, feedstock16 as an exploratory
candidate, and two identical baseline controls. No setting is adopted.**

## Verified local evidence

The final transfer was acknowledged October 24 at **04:41:36 IST**. Local root:
`/home/miniship/Github_Desktop/Factorio_Cursor_RL_Agent/data/laya-orchestration/starship-frontier-import-20261024`.
All **5,246 selected files / 3,354,952,365 bytes** matched remote SHA-256 values.
Final inventory manifest SHA-256:
`7902fa1a8ef51f9c0d2e33552c1e2156fce9b6755294834ce9496bcd7bcef99f`.

The copy includes raw logs, complete decision/evaluation evidence, initial
states and journals, manifests, operator/cleanup receipts and frozen source
archives. Binary world saves, duplicate mods, secrets and approximately 50 GB
of transient script-output caches were excluded. This is the verified selected
logs/evidence archive, not a copy of every world directory.

All **219 unique planned trials completed**: sampling60, capital60,
bottleneck60, research12, smoke15 and freshness12. Original cancellation records
remain intact; the 72 previously omitted jobs finished in a separate completion
window. Capital carries 48 original completions plus12 new ones; they are not
120 independent capital trials. Preserve the different execution/load epochs.

Reproducible local evidence:

- `final-transfer.json`, `final-inventory.json`, `transfer-state.json`:
  final copy verification and acknowledgement.
- `analysis/arm-comparisons.json`: all planned statuses, own-control matched
  contrasts, known endpoint denominators and bug events/affected jobs.
- `analysis/intervention-exposure.json`: per-job rule exposure and Miniship
  distribution replay.
- `analysis/feedstock-mechanism-and-bugs.json`: raw path/line citations for
  feedstock outcomes and its offered/selected choices.
- `original/`, `completion/`, `worlds/reference/`, `worlds/freshness/`:
  original and completion contracts, final cleanup and raw world evidence.

Imported completion state is terminal with cleanup verified, no owned world
processes and inactive host/capital/bottleneck units. The final-copy acknowledgement
released the additional copy inhibitor. The already approved one-shot automatic
suspend can proceed; actual kernel suspend was not independently observed.
Do not wake Starship or interpret later SSH unavailability as failed trials.

## Complete results that informed selection

Each row below has12 planned and12 known C6-v2 endpoints. C6-v2 means
`laya-c6-choice-output-v2`, separate from strict deterministic C6. Discordance
is candidate-only:control-only on matched seeds, against that cohort's own
control. C2/C3/C5 are strict fresh-final counts. Bug columns count typed bug
events and affected jobs separately; all these arms had zero caught exceptions.

Capital used **6,000 seconds** per trial:

| Arm | C6-v2 | Discordance | C2 / C3 / C5 final | Bug events / jobs |
| --- | ---: | ---: | ---: | ---: |
| control | 6/12 | — | 8 / 5 / 0 | 76 / 4 |
| mall-flat | 6/12 | 2:2 | 8 / 5 / 1 | 18 / 9 |
| foundation8 | 6/12 | 3:3 | 11 / 7 / 4 | 16 / 5 |
| batch-free | 7/12 | 3:2 | 9 / 5 / 1 | 4 / 3 |
| steel30 | 8/12 | 4:2 | 10 / 9 / 2 | 9 / 8 |

Steel30's changed rule was active at125 offered choice points across11/12 jobs,
with66 selections. All four candidate-only successes had exposure. This makes
it the better-supported mechanism to retest. Strict C6 ever/final remained2/1
for both steel and control. Fewer repeated bug events do not mean fewer affected
jobs: steel affected8/12 versus4/12. The adoption safety gate is unresolved.

Bottleneck used **7,200 seconds** per trial:

| Arm | C6-v2 | Discordance | C2 / C3 / C5 final | Bug events / jobs |
| --- | ---: | ---: | ---: | ---: |
| control | 2/12 | — | 5 / 3 / 2 | 0 / 0 |
| deepest16 | 2/12 | 1:1 | 8 / 5 / 3 | 5 / 4 |
| parallel-goals | 4/12 | 3:1 | 9 / 3 / 0 | 10 / 5 |
| retry-free | 5/12 | 4:1 | 9 / 7 / 0 | 11 / 4 |
| feedstock16 | 8/12 | 7:1 | 9 / 5 / 0 | 6 / 5 |

Feedstock16 has the largest headline difference, but its changed rule was active
in only3/12 jobs: **four choice points and two selections**. Five candidate-only
wins (`s80802`, `s80804`, `s80806`, `s80808`, `s80810`) had no grow offer or
exposure. Those trajectories do not establish a benefit from the changed prior.
Duplicate controls are needed to calibrate ordinary live-world/path variation.
Strict C6 ever/final was5/4 versus control1/1, while final C5 was0 versus2.
Its six bugs were three `starter_deconstruction_failed`, two
`producer_bootstrap_seed_shortage` and one `untyped_stuck`, affecting five jobs.
Retest the signal with exposure and service guards; do not promote it.

Other choices remain deferred:

- Sampling: control7/12 C6-v2, epsilon45/temperature3 each4/12,
  families/novelty4 each5/12; no primary completion gain.
- Foundation8: useful earlier-service hypothesis, with final C2/C3/C5 gains,
  but no C6-v2 gain. Retain for a targeted capacity question.
- Batch-free: small C6-v2 gain; wait for the already queued C26b loan question.
- Retry-free: weaker completion signal than feedstock, with bug and final-C5
  guards unresolved. Do not combine failure-suppression changes into C28.
- Research ramp125/ramp4: each3/4 C6-v2 versus1/4, but no modified demand-rung
  exposure. This does not demonstrate a research-growth effect.
- Freshness repair: observation mechanism exercised, but C6-v2 repair4/6 versus
  control5/6 and no earned research; control research remains unknown.

These are small exploratory comparisons on one canonical map. Policy seeds
do not establish unseen-terrain generalization. Research-stale observations
remain unknown; unlike windows, cohorts and predicate versions are not pooled.

## Frozen Miniship C28 contract

**60 jobs =15 new matched policy seeds82400–82414 ×4 arms**:

| Arm | Single changed factor | Role |
| --- | --- | --- |
| control_a | none | incumbent baseline |
| control_b | none; identical variant and effective catalog | A/A variation calibration |
| steel30 | `mission.ops-steel`, steel-family prior5→30 | primary candidate |
| feedstock16 | `mission.ops-grow-feedstock`, grow-family prior4→16 | exploratory candidate |

Controls share effective catalog SHA-256
`940630fb1c557a1f82620fe40c434dcfca166d86dc3d6c9083e0d3c7b317e431`.
All other preference entries, policy/runtime settings and15% seeded exploration
are unchanged. Compare each candidate separately to **both controls**; do not
pool them as30 independent seeds or invent an arbitrary A/A passing threshold.
Measure offered/active/selected intervention points, including unexposed wins.
Report C6-v2, strict ever/fresh-final C2/C3/C5/C6, unknown denominators, caught
exceptions, typed bug events and affected jobs. Follow the existing
[adoption rule](laya_variants/README.md); this portability test grants no adoption.

Cohort root:
`data/laya-experiments/laya-c28-2123-miniship-20261024` in the main Miniship checkout.
Harness and all four clean source copies are frozen at
`2d9b4b5be37632d3ac49cf2074417515ab659469`, behavior-equivalent to admitted
`d8a4825` with opt-in research/reconciliation/pacing off. No Starship experimental
source or unadopted C24/C25 changes were transplanted. The recorded baseline
gate expects `d8a4825372a944c6f01450ba97e882788ad4de2f`; a later gate change
does not authorize silently rebasing this freeze.

This tests portability of individual priors on the incumbent library. The
objective/operation menu and the capital wall budget differ from Starship, so
it is not an exact replication or a pooled promotion comparison.

Factorio2.1.23 build87750, canonical save SHA-256
`347eba65065014f82261330d84d5ca2fa3e3427cad3ab18dcfdf496dd9c442b0`,
wall7,200 seconds and game cap540,000 ticks apply to every arm. Distinct
game/RCON ports38700–38729 /31700–31729 and previously unused seed IDs were
checked. Shared host budget remains unchanged; concurrency30 is a ceiling
subject to normal host admission.

The supervisor depends on **C24, C25, C26b and C27b**, preserving their queue
and ownership. It launched under user unit
`laya-c28-2123-miniship-20261024.service` at04:46 IST. At04:53 IST it was active,
all60 jobs queued, zero admissions/worlds, waiting for C26b, and freeze checks
were clean. A waiting supervisor is not evidence of completed trials.

Cohort end **October27 18:00 IST**, latest full-window admission16:00;
independent systemd cap and30-second stop bound cleanup before18:05 IST.
`KillMode=control-group` scopes cleanup to C28. Never edit or restart an active
freeze; the forthcoming documentation commit is linked by a separate receipt.

## Validation and monitoring

Native variant parsing, quoted guidelines and effective catalog comparison
passed for all four arms: only the intended rule changes. Native Batch preflight
retained all60 unique planned IDs and correctly blocked dependency admission.
Bounded replay of12 Miniship C25 controls found16 grow-offer points among3,945
decisions; grow16 changed all16 distributions. This establishes intervention
wiring, not a completion benefit. Fast gate: **1,322 passed,1 skipped, exit0**;
log `/tmp/laya-c28-fast-20261024.log`, receipt `validation.json`.

This thread's two existing completion schedules now own C28:55-minute checks
(next October24 **05:10:55 IST**) and terminal accounting October27 **18:07 IST**.
Both remain bound to thread `94dcfaf2-9531-4234-bde5-b6ae616de9b1`; exact IDs,
prompts and readbacks live in
`data/laya-orchestration/starship-frontier-followup-20261024/state.json` and
`schedule-*-readback.json`. Run its local `checkin.py` for bounded freeze/status
and both-control comparisons. Finalize exposure/service/failure analysis after
all60 statuses are terminal, verify scoped cleanup and disable only these timers.

Lifecycle: C28 is already queued and starts when predecessors clear; no mod,
Factorio, scheduler or existing-world restart is required. Starship needs no
further action. No Opus notification was sent and its shared observer was not
changed. The managed parent account is preserved; systemd bounds work
independently of model authentication.

## C28 final accounting (2026-10-24 15:20 IST)

**Keep the incumbent; neither prior is adopted. C28 is complete and cleaned.** This terminal record supersedes the queued/running lifecycle and future-wake notes above. No combined variant or additional cohort is authorized.

All **60/60 planned jobs completed**, with one started and one terminal attempt each (120 ledger events; no omitted, retried, interrupted or unstarted jobs). First admission was October 24 at 10:22:27 IST; final owned cleanup was **14:29:37 IST**. The unchanged admission reason is `ready`. The frozen baseline, all harness/arm/mod/variant hashes, canonical save, 7,200-second/540,000-tick windows, dependency contract and baseline gate verified. No gate or predecessor winner was transplanted.

### Target and duplicate-control calibration

C6-v2 is `laya-c6-choice-output-v2`, separate from strict deterministic C6. All rows retain 15 planned jobs and 15 known target endpoints. Discordance is row-only:reference-only on matched policy seeds.

| Contrast | C6-v2 counts | Discordance | Difference |
| --- | --- | ---: | ---: |
| control_b vs control_a | 6/15 vs 4/15 | 4:2 | +13.3 percentage points |
| steel30 vs control_a | 6/15 vs 4/15 | 4:2 | +13.3 percentage points |
| steel30 vs control_b | 6/15 vs 6/15 | 3:3 | +0.0 percentage points |
| feedstock16 vs control_a | 7/15 vs 4/15 | 4:1 | +20.0 percentage points |
| feedstock16 vs control_b | 7/15 vs 6/15 | 3:2 | +6.7 percentage points |

Control B differs from the identical control A by two target successes. Steel has the same net difference against A and ties B; this does not establish a target gain beyond the observed A/A variation. Grow exceeds B by one target success, with limited intervention exposure and unresolved earlier-service evidence. No arbitrary A/A acceptance threshold is applied; the controls are never pooled as 30 independent seeds.

### Strict endpoints, service and failures

Values are **passed / known**, followed by unknown count where nonzero; **15 planned jobs per arm** always remain the denominator for accounting. Ever is latched strict acceptance; final is the last fresh predicate.

| Arm | C2 ever / final | C3 ever / final | C5 ever | C5 fresh-final | C6 ever / final | Bugs events / affected jobs |
| --- | --- | --- | --- | --- | --- | --- |
| control_a | 9/15 / 9/15 | 6/15 / 5/15 | 4/4; 11 unknown | 1/4; 11 unknown | 1/15 / 0/15 | 23 / 7 |
| control_b | 11/15 / 11/15 | 4/15 / 4/15 | 7/7; 8 unknown | 1/7; 8 unknown | 1/15 / 1/15 | 10 / 9 |
| steel30 | 12/15 / 12/15 | 9/15 / 7/15 | 8/8; 7 unknown | 2/8; 7 unknown | 1/15 / 1/15 | 7 / 6 |
| feedstock16 | 12/15 / 12/15 | 5/15 / 5/15 | 7/7; 8 unknown | 0/7; 8 unknown | 1/15 / 1/15 | 9 / 7 |

The local `checkin.py` and frozen batch report infer strict observations from accepted lists, which lose unknown predicates. This final accounting rebuilds **both ever and fresh-final** statuses from each clean journal’s `acceptance`/`at=end` receipt. In particular, C5 has 11/8/7/8 unknown jobs for A/B/steel/grow; these are not failures. The helper and frozen run were left unchanged.

All 60 jobs have **zero caught exceptions**, zero malformed decision lines, and complete decision logs. Typed bug events and affected jobs remain separate. The per-arm counts below are not deduplicated across jobs.

| Arm | Decisions | Typed bug codes (events) | Runner stops | Final plastic service |
| --- | ---: | --- | --- | --- |
| control_a | 3,300 | `untyped_stuck` 3, `power_route_unavailable` 5, `mall_loan_gate_mismatch` 15 | stalled 15 | no_output_in_window 11, stocked_plateau 3, producing 1 |
| control_b | 4,017 | `untyped_stuck` 5, `power_route_unavailable` 5 | stalled 14, window_timeout 1 | no_output_in_window 8, stocked_plateau 6, producing 1 |
| steel30 | 4,368 | `untyped_stuck` 4, `power_route_unavailable` 3 | stalled 15 | stocked_plateau 6, no_output_in_window 7, producing 2 |
| feedstock16 | 4,057 | `power_route_unavailable` 5, `untyped_stuck` 2, `starter_deconstruction_failed` 1, `stage_power_connection_failed` 1 | stalled 13, window_timeout 2 | stocked_plateau 7, no_output_in_window 8 |

There were 57 `stalled` and three `window_timeout` runner outcomes (completed trials, not 60 successes). The latter are recorded game-window terminations. Stocked plateaus do not establish sustained C5 service. Research was off; research acceptance remains unknown for all 60 jobs.

Strict matched discordance below uses **known pairs only**, always out of 15 planned pairs. Each entry is row-only:reference-only **[known pairs]**. C5 comparisons especially retain unobserved pairs.

| Contrast | C2 ever | C3 ever | C5 ever | C6 ever |
| --- | ---: | ---: | ---: | ---: |
| control_b vs control_a | 4:2 [15] | 3:5 [15] | 0:0 [3] | 1:1 [15] |
| steel30 vs control_a | 5:2 [15] | 4:1 [15] | 0:0 [4] | 1:1 [15] |
| steel30 vs control_b | 4:3 [15] | 6:1 [15] | 0:0 [4] | 1:1 [15] |
| feedstock16 vs control_a | 4:1 [15] | 1:2 [15] | 0:0 [3] | 1:1 [15] |
| feedstock16 vs control_b | 3:2 [15] | 3:2 [15] | 0:0 [5] | 1:1 [15] |

| Contrast | C2 fresh-final | C3 fresh-final | C5 fresh-final | C6 fresh-final |
| --- | ---: | ---: | ---: | ---: |
| control_b vs control_a | 4:2 [15] | 3:4 [15] | 1:0 [3] | 1:0 [15] |
| steel30 vs control_a | 5:2 [15] | 3:1 [15] | 1:0 [4] | 1:0 [15] |
| steel30 vs control_b | 4:3 [15] | 5:2 [15] | 0:0 [4] | 1:1 [15] |
| feedstock16 vs control_a | 4:1 [15] | 1:1 [15] | 0:0 [3] | 1:0 [15] |
| feedstock16 vs control_b | 3:2 [15] | 3:2 [15] | 0:1 [5] | 1:1 [15] |

Steel improves C2/C3 service counts against both controls and has fewer bug-affected jobs, but the long-window target gain is absent against B and C5 observations are incomplete. Grow has zero fresh-final C5 passes versus one in each control; unknowns prevent a complete paired service conclusion. Grow affected seven jobs versus seven in A and nine in B; its nine events must not be confused with affected-job counts. None of this establishes the full adoption gate.

### Offered, active and selected intervention exposure

Each cell lists **offered points / active-rule points / selections; exposed jobs out of 15**. Points are repeated decisions, not independent trials.

| Arm | Steel family | Grow family |
| --- | --- | --- |
| control_a | 411 / 411 / 130; 13/15 jobs | 22 / 22 / 1; 6/15 jobs |
| control_b | 423 / 423 / 134; 13/15 jobs | 11 / 11 / 2; 5/15 jobs |
| steel30 | 591 / 591 / 288; 13/15 jobs | 8 / 8 / 1; 4/15 jobs |
| feedstock16 | 298 / 298 / 105; 13/15 jobs | 12 / 12 / 3; 5/15 jobs |

All steel-only target wins had changed-rule exposure: against A, seeds 82400/82405/82411/82412 had offered=active counts 4/47/155/3 and selections 2/21/72/2; against B, the wins are 82400/82405/82411 with the same receipts. Exposure proves the intervention was available, not that it caused each win.

Grow-only wins against A are 82400/82410/82412/**82413**; the first three have zero grow offers, active points or selections. Against B, 82400/82404/**82413** win; the first two have zero exposure. Only **82413** was exposed: two offered/active points and one selection. Thus 3/4 and 2/3 grow-only wins respectively remain unexposed. Across the candidate arm, grow was active in just 5/15 jobs at 12 decision points with three selections.

### Final evidence, cleanup and lifecycle

Main-checkout evidence root: `data/laya-orchestration/starship-frontier-followup-20261024/`. Immutable collection directory: **`terminal-20261024T094928Z/`**. It preserves all 60 status records, the full 120-event attempt ledger, final batch report, freeze/config/selection, per-job clean journals/latest statuses/manifests, exposure, cleanup, timer and verification receipts. `final-accounting.json` contains all nine endpoint comparisons for the five required contrasts, every per-job strict status and 253 source-file hashes. Raw decision trajectories remain in the original frozen worlds and are hash-cited.

At 2026-10-24 15:20 IST, verified the **exact** `laya-c28-2123-miniship-20261024.service` inactive/dead, main PID 0, collected (`LoadState=not-found`), with no remaining owned cgroup, world process, reservation or missing cleanup receipt. A second process check matched executable, working directory, argv and cgroup; no owned process remains. All 253 collected source hashes and every planned ID, attempt, raw acceptance receipt, decision/bug count and known/unknown denominator reconciled. This is matched-cohort/live cleanup evidence. No runtime code changed and no test suite rerun was necessary; the recorded original fast gate remains 1,322 passed/1 skipped.

After cleanup and accounting, disabled **only** the two IDs in `state.schedules` (`frontier-completion-starship-monitor-20261024-v1` and `frontier-completion-starship-terminal-20261024-v1`). Readback confirms both disabled with no next run, and every other scheduled task unchanged. No further C28 check is scheduled.

Lifecycle: **nothing remains to restart, redeploy or stop**. No SSH or Starship wake, sudo, new world/cohort, runtime/mod/save/frozen-source mutation, shared-observer edit, controller message or subagent was used. The requested managed Personal_2 parent is preserved. All comparisons remain exploratory on one canonical map; matched policy seeds do not establish terrain generalization.
