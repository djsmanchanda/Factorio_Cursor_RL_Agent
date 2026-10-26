<!-- Path: docs/rl/laya_helper_runs/c38-diagnosis-20261026/c37-mechanisms-review.md | Purpose: Read-only C37 mechanism audit for the C6 conversion decision. -->
# C37 mechanism review — robots vs control, 2026-10-26

Read-only independent audit. No code, ref, service, save or world was changed. Evidence level:
matched experiment cohort (60 seed pairs, 120/120 completed) with raw decision journals.
This is not a deployed-runtime or live-result claim. C38 is referenced only for sequencing;
its zero-C2 result is a runner-stop artifact, not clearance evidence.

Sources read directly:
- C37 root: `data/laya-experiments/laya-c37-2123-miniship-20261026/`
- `report.md:7-8` (arm endpoints), `report.md:14` (60 matched pairs), `report.md:17-18` (arm revisions)
- `job-status.json` (`accepted_ever`, `accepted_final`, `laya_acceptance`)
- all 120 `evidence/decision-states.jsonl` and `evidence/clean-decisions.jsonl`
- arm source: `arms/control/code`, `arms/robots/code` (the only non-world diff is
  `orchestrator/live_base.py` plus `tests/test_occupancy_ignores_robots.py`)
- C36 comparison: `docs/rl/laya_helper_runs/convergence-20261026/c35-c36-review.md:128-130,272-282`
- C38 terminal status: `data/laya-experiments/laya-c38-2123-miniship-20261026/report.md:3,7-8`

Citation shorthand: `W = arms/<arm>/code/data/laya-saved-world/laya-c37-2123-miniship-20261026`.
Run IDs below are `control-s<seed>-s<seed>-<epoch>` or `robots-s<seed>-s<seed>-<epoch>`.

Definitions used below:
- **C5-ever** = `accepted_ever` contains C5 (36/60 in each arm).
- **C5-not-C6** = C5-ever and C6 never (25 control + 29 robots = 54 runs).
- **C6-ever** = `accepted_ever` contains C6 (11 control + 7 robots = 18 runs).
- **C6-final** = `accepted_final` contains C6 (8 control + 6 robots = 14 runs).
- **C5-final-not-C6** = the report's strict-final C5 set (8 + 12 = 20 runs). This is a
  different, disjoint end-window set; do not treat it as the progression denominator.

C5-final and C6-final are disjoint in this cohort: all 14 C6-final runs have C5-ever, but
none has C5-final. C5-final is a 120 s plastic-counter window that often stops advancing in
late runs. Use C5-ever/C6-ever for conversion, and report C6-final separately for
persistence.

## 0. Bottom line

1. **Robot occupancy exclusion is correct but orthogonal to C6.** It removes robot
   co-blockers, but 568/570 remaining refinery-extension refusals in the robots arm still
   name `big-electric-pole` (99.6%). C6-ever conversion among C5-ever runs is 11/36 control
   vs 7/36 robots (30.6% vs 19.4%; Fisher p=0.41), not a gain. C6-final is 8/60 vs 6/60
   (p=0.78).
2. **C6 is gated by a retained advanced-circuit-consuming cell, not by circuit
   production.** All 54 C5-not-C6 runs have no passive/requester-chest cell at the final
   observation; 50/54 have at least one passive-provider-chest in stock; 27/54 have an
   advanced-circuit producer cell. 33/54 started a passive-provider-chest temporary batch
   but ended with no owned chest cell.
3. **C36's 44/47 finding replicates inside C37 without pooling.** 54/54 C5-not-C6 runs
   have no consumer cell; 103/106 C6-final failure checks have `consumer_deltas {}` at the
   last check; 89/106 never show any consumer delta. The refinement is that the recipe is
   not missing: the consumer cell is transient or absent.
4. **C6-final is non-monotonic.** 18 runs reached C6-ever but only 14 kept C6-final; four
   lost it (three on the advanced-circuit production window, one on consumer release).
   Laya C6-v2 is production-only (29/60 control, 26/60 robots) and is not a consumer
   endpoint.
5. **Big-electric-pole is still binding for refinery extension, but not for C6.** Every
   C6-ever run has at least one extension refusal; zero C6 passes occur in the 23 runs with
   no refusal. The pole is a growth/geometry constraint, not the C5→C6 gate.
6. **Smallest fix: expose consumer-cell ownership and keep/stand one existing chest cell
   when C6 is unmet.** Do not add geometry or another generic production weight. The
   existing chest recipe/kit is usually present; the missing object is a retained cell.
7. **C38 retry:** not as a C6 cohort. A fixed C38 retry is a clearance/extension test
   (its between-arm factor is connected refinery-pole clearance), not a robot-exclusion or
   C6 test. Prioritize the consumer-cell cohort; retry clearance+robots second, with an
   extension/S1 primary endpoint and a C6 guardrail.

## 1. Cohort endpoints and arm contrast

| Arm | C2 | C3 | C4 | C5-ever | C5-not-C6 | C6-ever | C6-final | Laya C6-v2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| control (d9402da) | 53 | 36 | 36 | 36 | 25 | 11 | 8 | 29 |
| robots (32c1ef5) | 50 | 36 | 36 | 36 | 29 | 7 | 6 | 26 |

Source: `report.md:7-8`; progression counts from `job-status.json` `accepted_ever`/`accepted_final`.
Both arms reach C5-ever equally (36/36), so the robots arm did not improve reach. Among
C5-ever runs, C6-ever conversion is 11/36 vs 7/36 (p=0.41); the overall C6-final difference
is 8/60 vs 6/60 (p=0.78). Neither supports a C6 conversion claim for robot exclusion.

The robots arm did raise C5-final (8→12) and lower C2 (53→50), but C5-final and C6-final
are separate end-window states. The extra C5-final runs are not extra C6 conversions; they
are runs whose plastic window was still advancing while no consumer cell was owned.

## 2. Representative traces (matched seeds)

| Seed | control | robots | What it isolates |
|---:|---|---|---|
| 80125 | C6 pass | C5-not-C6 | Same seed; control keeps a chest cell, robots stalls on advanced-circuit kit |
| 80140 | C5-not-C6 | C6 pass | Reverse discordance; chest cell, not arm, tracks C6 |
| 80136 | C5-not-C6 | C5-not-C6 | Both produce circuits; both lack a consumer cell |
| 80137, 80149 | C6 pass | C6 pass | Success path: chest cell is owned despite pole refusals |

### 2.1 Seed 80125 — control C6 pass, robots C5-not-C6

`robots-s80125-s80125-1792978923`:
- First C6 decision at index 240; the brief says only
  `C6 advanced_circuits: advanced-circuit 0.00/0.25/s short` (`W/robots-s80125/evidence/decision-states.jsonl:241`).
- Index 241 selects `op.produce.advanced-circuit` and gets a temporary batch
  (`...:242`); index 245 gets `material_shortage`:
  `quad_mall_advanced-circuit_top needs material ... assembling-machine-1: need 2, short 2,
  long-handed-inserter: need 2, short 2` (`...:246`).
- Index 247 tries `op.produce.long-handed-inserter` but is deferred behind the borrowed
  copper-cable cell making assembling-machine-1 (`...:248`). The run stalls with
  `passive-provider-chest` stock 2 and no advanced-circuit or chest cell in the final
  mall-cell survey (`...:248` last observation;
  `W/robots-s80125/evidence/clean-decisions.jsonl:294` C5 passed, C6 failed,
  `consumer_deltas {}`).
- `op.produce.passive-provider-chest` was **never offered** in this run; the chest stock
  target was already met.

`control-s80125-s80125-1792978951`:
- Index 308 starts a passive-provider-chest temporary batch:
  `passive-provider-chest is being made as a temporary bootstrap mall batch`
  (`W/control-s80125/evidence/decision-states.jsonl:309`).
- It later keeps a passive-provider-chest cell (loaned, not standing) and passes C6 at
  decision 489 (`W/control-s80125/evidence/clean-decisions.jsonl:565`), even though the
  same run repeatedly refuses an iron refinery extension on a big-electric-pole
  (`.../decision-states.jsonl:485`).

Reading: the difference is whether the advanced-circuit/chest cell is stood and retained,
not whether robots were overhead during a placement survey. The robots arm's failure is a
kit/ordering failure upstream of the consumer cell; the control arm's pass is the chest
cell surviving long enough to be sampled.

### 2.2 Seed 80140 — control C5-not-C6, robots C6 pass (reverse)

`control-s80140-s80140-1792984826`:
- It owns an advanced-circuit cell at the end, has passive-provider-chest stock 1, but
  every one of its 7 passive-provider-chest selections is `chemical_capability_handoff`;
  no chest cell is ever owned.
- C6 at end fails both `advanced_circuit_consumer_output` and
  `advanced_circuits_newly_produced`; `consumer_deltas {}` and production delta 0
  (`W/control-s80140/evidence/clean-decisions.jsonl:410`). Big-electric-pole was offered
  0 times in this run, so a pole refusal is not necessary for C6 failure.

`robots-s80140-s80140-1792984995`:
- Index 485/487 shows the advanced-circuit cell making a requester chest:
  `borrowed advanced-circuit cell is producing temporary requester-chest for the
  requester-chest seed` (`W/robots-s80140/evidence/decision-states.jsonl:486-488`).
- It keeps a requester-chest cell (loaned) and passes C6 at decision 489
  (`W/robots-s80140/evidence/clean-decisions.jsonl:586`), despite a pole refusal at index
  489 (`.../decision-states.jsonl:490`).

Reading: same seed, opposite arm outcomes. Occupancy is not deterministic here; the chest
cell's presence is. This is a policy/path-dependent consumer-ownership race, not a
clearance effect.

### 2.3 Seed 80136 — both C5-not-C6; production is not the gate

- `control-s80136-s80136-1792983164`: C6 fails **only** `advanced_circuit_consumer_output`,
  with `advanced_circuits_newly_produced` true and production delta 68; `consumer_deltas {}`
  (`W/control-s80136/evidence/clean-decisions.jsonl:301`). Final observation has no chest
  cell (`.../decision-states.jsonl:258`).
- `robots-s80136-s80136-1792983837`: same shape, production delta 1751, consumer only
  failure, `consumer_deltas {}` (`W/robots-s80136/evidence/clean-decisions.jsonl:382`);
  final observation has an advanced-circuit cell and steel-chest but no passive/requester
  chest cell (`.../decision-states.jsonl:276`).

Reading: these runs prove circuits were produced; the missing condition is an owned
consumer, not the advanced-circuit recipe or output counter.

### 2.4 Seeds 80137 and 80149 — both C6 pass

Both arms pass C6 in these pairs and both have at least one chest cell at the final
observation. Examples: control-s80137 passes at decision 429
(`W/control-s80137/evidence/clean-decisions.jsonl:498`); robots-s80137 passes at decision
399 (`W/robots-s80137/evidence/clean-decisions.jsonl:463`); control-s80149 passes at 319
(`.../clean-decisions.jsonl:379`); robots-s80149 passes at 279
(`.../clean-decisions.jsonl:348`). All four still show refinery-extension pole refusals.
The success path is a retained chest cell, not absence of geometry conflict.

## 3. What actually gates C6

### 3.1 The predicate is current ownership, not cumulative consumption

`orchestrator/checkpoint_milestones.py:512-526` requires both:
- newly produced advanced circuits, and
- an **owned** recipe whose ingredients contain advanced-circuit, with a positive
  progression delta.

`orchestrator/checkpoint_observer.py:453-473` defines `owned` as `find_line(...) is not
None`; it surveys currently configured machines, not historical production. A temporary
cell that was released before the sample is not owned. This is why C6-final can regress
after C6-ever passed.

### 3.2 Cell census

| Set | n | chest cell (ppc/requester) | advanced-circuit cell | passive-provider-chest stock ≥1 |
|---|---:|---:|---:|---:|
| C5-not-C6 (C5-ever, C6-never) | 54 | **0** | 27 | 50 |
| C6-ever | 18 | 17 | — | — |
| C6-final | 14 | **14** | — | — |
| C5-final-not-C6 (report subset) | 20 | **0** | 13 | 20 |

Chest cell counts are from the last `decision-states.jsonl` `mall_economics.cells`; the
`C5-not-C6` set is `job-status.json` `accepted_ever` (C5 true, C6 false). The report's
C5-final subset gives the same zero-chest result, but the 54-run C5-ever set is the
mechanism denominator.

Among the 54 C5-not-C6 runs:
- 33 started a passive-provider-chest temporary batch/production but ended with **no**
  chest cell (transient ownership);
- 9 only ever received `chemical_capability_handoff` for passive-provider-chest (advanced
  circuit/plastic capability never completed);
- 12 never selected passive-provider-chest (option absent due to stock/policy; mostly
  robots-arm runs).

Only two advanced-circuit-consuming recipes ever appear in C6 checks across all 120 runs:
`passive-provider-chest` (55 sampled deltas) and `requester-chest` (11). No
`processing-unit` or `chemical-science-pack` consumer output is ever observed. C6 in this
cohort is therefore a logistic-chest milestone, not a science-consumer milestone. That is
a validator/product caveat, not a reason to loosen the predicate.

### 3.3 Ever vs final: C6 is non-monotonic

C6-ever is 11 control + 7 robots = 18; C6-final is 8 + 6 = 14. Four runs lost C6:
`control-s80107`, `control-s80138`, `control-s80154`, `robots-s80146`.
- The first three final-fail only `advanced_circuits_newly_produced` while still showing a
  consumer delta (269, 33, 2116 respectively); their consumer survived, but the
  advanced-circuit production window did not.
- `robots-s80146` final-fails `advanced_circuit_consumer_output` with `consumer_deltas {}`
  despite production delta 1961; its consumer cell was released.

This matters for endpoint choice: C6-ever measures achievement; C6-final measures a
current-window coincidence of producer and consumer. A consumer-cell retention fix should
improve both, but the next cohort must report both rather than only final.

### 3.4 Laya C6-v2 is not a consumer endpoint

`training/laya_phase_acceptance.py:110-132` passes Laya C6 when the advanced-circuit
production counter advances after an explicit choice. It does not require an owned
consumer. Accordingly Laya C6-v2 is 29/60 control and 26/60 robots while strict C6-final
is 8/60 and 6/60. The 20-21 run gap is production-only success, not consumer conversion.
Do not use Laya C6-v2 as evidence for or against a consumer fix.

## 4. Big-electric-pole and the robot exclusion

| Signal | control | robots |
|---|---:|---:|
| Refinery-extension refusal lines | 701 | 570 |
| Lines naming `logistic-robot` | 169 | **0** |
| Lines naming `big-electric-pole` | 638 (91.0%) | **568 (99.6%)** |
| Runs with ≥1 refusal | 51/60 | 46/60 |
| C6-ever runs with ≥1 refusal | 11/11 | 7/7 |
| C6 in runs with no refusal | 0 (23 runs overall) | 0 |

The refusal tile set is stable: `[(-41, -15), (-41, -14), (-40, -15)]` with
`big-electric-pole@(-40.0, -14.0)`. Control-s80100 line 174 names both the pole and a
logistic robot (`W/control-s80100/evidence/decision-states.jsonl:174`); robots-s80100 line
93 names only the pole at the same tiles (`W/robots-s80100/evidence/decision-states.jsonl:93`).
The robot exclusion removed the co-blocker, not the real obstruction.

The pole is **not** the C6 gate:
- control-s80140 had 0 big-electric-pole offers/selections and still failed C6
  (`W/control-s80140/evidence/clean-decisions.jsonl:410`).
- control-s80125 and robots-s80140 pass C6 while repeatedly refusing the same extension
  (`W/control-s80125/evidence/decision-states.jsonl:485`,
  `W/robots-s80140/evidence/decision-states.jsonl:490`).
- All 18 C6-ever runs have at least one refusal; the one C5-ever run per arm without a
  refusal has no C6.

So: yes, big-electric-pole is still binding for refinery extension/S1 growth; no, it does
not explain C5→C6 conversion. The robot change is a correctness fix with no C6 headroom.

## 5. C36 replication, without cross-cohort pooling

C36 reported 44/47 failing clearance runs with no owned advanced-circuit consumer at the
end sample (`c35-c36-review.md:272-282`). C37, read on its own:
- 54/54 C5-not-C6 runs have no passive/requester-chest cell at the final observation.
- 103/106 C6-final failure checks have `consumer_deltas {}` at the last C6 check.
- 89/106 never show any consumer delta at any C6 check; 17 show one at some earlier check
  but still fail final (mostly the production window).

This supports C36 rather than contradicting it, and strengthens the denominator: the
C5-ever-not-C6 set is 54/54 vs C36's 44/47 (93.6%). No C36 runs are pooled into these
counts.

The refinement is important: C36 emphasized the chest cell's circular kit. C37 shows the
dominant C37 mode is **transient ownership**: 33/54 C5-not-C6 runs already started a
passive-provider-chest batch, and 50/54 have chest stock, but 0/54 retain a chest cell.
The kit/recipe is usually present; the cell is released or never stood. C36's circular-kit
case still exists (e.g., robots-s80125's advanced-circuit cell is short 2 assembling
machines and 2 long-handed inserters), but it is a minority path, not the universal gate.

## 6. Diagnosis by category

| Category | Evidence | Verdict |
|---|---|---|
| Physics / tool limitation | Big pole really occupies `(-40,-14)` and blocks the extension tiles; 568/570 robots refusals. C6 observer only sees currently owned cells (`checkpoint_observer.py:470`). | Real for extension; C6 failure is not a physics bug. The observer's current-state contract explains final regression but not the absent cell. |
| Missing observation | The C6 brief says only `advanced-circuit 0.00/0.25/s short` (`W/robots-s80125/evidence/decision-states.jsonl:241`). It never says whether an advanced-circuit-consuming cell is owned. 50/54 C5-not-C6 runs have chest stock, so stock reads as capability. | **Primary actionable gap.** The policy is optimizing the stated production target, not the hidden consumer condition. |
| Missing operation | `op.mall_slot.passive-provider-chest` does not exist; `BASELINE_MACHINES` has only gear/cable/circuit/belt (`orchestrator/baseline_production.py:29-34`). `op.produce.passive-provider-chest` is offered only on a stock target and its successful form is a temporary batch (`training/laya_ops_runner.py:754-772`). 33/54 C5-not-C6 runs started that batch but retained no cell. | **Primary actionable gap.** There is no standing/persistent consumer-cell operation. |
| Supply ownership | 0/54 C5-not-C6 runs own a chest cell; 17/18 C6-ever runs own one at the end (the exception lost it after passing). Producer ownership is insufficient: 27/54 C5-not-C6 runs own an advanced-circuit cell. | **The dominant axis.** It is consumer-cell ownership/retention, not producer or item stock. |
| Kit / chemical capability | robots-s80125 is short 2 assembling-machine-1 and 2 long-handed-inserter; 9/54 C5-not-C6 runs only ever get `chemical_capability_handoff` for passive-provider-chest. | Real but minority. Kit is upstream of the consumer cell, not the universal gate. |
| Geometry / clearance | Pole refusals occur in all C6-ever runs; C6 passes despite them. control-s80140 has no pole offer and still fails C6. | Orthogonal to C6; relevant to extension/S1. |
| Policy preference | Laya repeatedly chooses advanced-circuit and other mall batches; it selects passive-provider-chest in 42/54 C5-not-C6 runs (33 batched, 9 handoff), but the cell is not retained. | Rational given the brief; not a robot-arm preference. The fact/operation gap explains the choice. |

Ranking for C6 conversion: (1) consumer-cell retention/ownership; (2) missing consumer fact
and standing option; (3) kit/chemical capability; (4) geometry/pole. The
"passive-provider-chest gate" in C37 is not a stock or recipe gate: 50/54 C5-not-C6 runs
already have chest stock. It is an ownership gate.

## 7. Recommendation

### 7.1 Smallest reusable fact

Add a consumer fact to the C6 brief and the advanced-circuit option features, separate from
item stock:
- `consumer_cell_owned` / `consumer_recipe_running`: whether any owned machine is
  configured to a recipe whose ingredients include advanced-circuit, and whether it is
  working.
- `consumer_item_stock`: existing passive/requester chest counts (do not use this as a
  proxy for a cell).
- `producer_cell_owned`: whether an advanced-circuit cell exists; this separates the
  27/54 producer-present failures from the 27/54 producer-absent ones.
- `consumer_cell_standing` / `loaned`: whether the owned consumer cell is a temporary loan.

This is a fact-only change and follows the repository's facts-before-operations order. It
would have told Laya in robots-s80125 and control-s80136 that chest stock was 2 but the C6
consumer was absent.

### 7.2 Smallest reusable operation

Reuse the existing chest-cell geometry/kit; do not add a new line or recipe:
- When C6 is unmet and `consumer_cell_owned` is false, offer the existing
  `op.produce.passive-provider-chest` with an incremental target even when normal chest
  stock is already sufficient, and **retain the resulting cell** (`persist_cell` /
  `stand_consumer`) instead of releasing the temporary loan.
- If the cell already exists as a loan, keep it configured until an advanced-circuit
  consumer is owned at a C6 sample; do not release it merely because the chest stock
  target was met.
- If the trace shows the cell still releases before the sample, the next factor is a
  dedicated `op.stand_mall_cell.passive-provider-chest`; that is a lifecycle variant of
  the same cell, not new geometry.

This targets the 33/54 transient cases directly. It does not fix the 9 chemical-handoff
cases or the 12 never-selected cases; those are smaller residuals and should not be mixed
into the first cohort.

### 7.3 Single-factor next cohort hypothesis

**Factor:** C6 consumer-cell ownership/retention — expose `consumer_cell_owned` and keep or
stand one existing passive-provider-chest cell while C6 is unmet.

**Hypothesis:** because 33/54 C5-not-C6 runs already started a chest batch but retained no
cell, retaining that cell will convert more C5-ever runs to C6; the effect should be
visible first as `consumer_cell_owned` at C6 checks, then as C6-ever/final.

**Design:** fresh 60 matched seed pairs, one factor, same source save and ports policy;
control vs consumer-retention arm. Nothing else changes (no clearance, no robot change, no
new recipe).

**Primary endpoint:** strict C6-ever (latching achievement). **Key secondary:** strict
C6-final (persistence). **Mechanism endpoint:** `consumer_cell_owned` at C6 checks and at
the final observation. Report Laya C6-v2 separately, never as the primary.

**Guardrails:** C5-ever not lower; C2/C3/C4-ever not lower; `untyped_stuck` not higher;
`material_shortage` and `temporary_mall_batch` not higher; full 120/120 denominator; no
increase in idle loans. A C6 gain that comes only from chest stock without an owned
advanced-circuit producer is validator gaming, not a pass.

**Falsification:** reject the factor if (a) `consumer_cell_owned` does not rise at C6
checks (operation was not offered/chosen), (b) C6-ever does not rise by the pre-registered
margin (C36's 5→15 was detectable with 60 pairs; a smaller expected effect needs more
pairs), or (c) C5/C2 falls or `untyped_stuck` rises. If cell ownership rises but C6 does
not, the production window or validator, not ownership, is binding.

### 7.4 Conditional clearance + robots after the C38 runner regression

C38 is terminal 119 completed + 1 prepare_failed, but both arms have no C2 (report: proxy
C1/C2/C3/C4 12/0/0/0 robots and 11/0/0/0 clearance_robots; 59/60 and 60/60 stalled; 117
twelve-decision stops per parent).
That is a runner-stop regression, not evidence against clearance. C38 is not usable as a
clearance result.

C37 already answers the robot half: robot exclusion alone removes robot co-blockers but
leaves 568/570 pole refusals and does not convert C6. C38's between-arm factor is connected
refinery-pole clearance, not robot exclusion; a fixed C38 retry would test the active
clearance ingredient that C36 found positive (C6-ever 5→15, final 2→13;
`c35-c36-review.md:128-130,217-219`).

**Answer:** yes for a clearance/extension confirmation after the runner fix; no for the
next C6 cohort.

Recommendation:
- **Do not run clearance+robots as the next C6 cohort.** The direct missing condition in
  C37 is consumer-cell retention; test that first.
- **If a second cohort is available after C38's runner fix**, retry clearance+robots with
  a pre-registered extension/S1 primary endpoint (refinery extension completes, S1
  throughput) and C6-ever/final as guardrails. Keep the robot exclusion in both arms; the
  between-arm factor is pole clearance, not robots.
- **Do not cite C38's zero C2 as a clearance failure.** It is an infrastructure stop.
- If the parent must choose one cohort before the deadline: consumer-cell retention. The
  clearance question is already positive in C36 and is a confirmation, not the missing
  mechanism.

## 8. Evidence index and missing evidence

Representative raw citations:
- C6 pass with chest cell: `W/control-s80125/evidence/clean-decisions.jsonl:565`,
  `W/robots-s80140/evidence/clean-decisions.jsonl:586`.
- C6 fail with no chest cell: `W/control-s80140/evidence/clean-decisions.jsonl:410`,
  `W/control-s80136/evidence/clean-decisions.jsonl:301`,
  `W/robots-s80136/evidence/clean-decisions.jsonl:382`.
- Transient chest batch: `W/control-s80125/evidence/decision-states.jsonl:309`;
  `W/robots-s80140/evidence/decision-states.jsonl:486-488`.
- Kit failure: `W/robots-s80125/evidence/decision-states.jsonl:246,248`.
- Robot removal / pole remains: `W/control-s80100/evidence/decision-states.jsonl:174`,
  `W/robots-s80100/evidence/decision-states.jsonl:93`.
- C6 predicate: `orchestrator/checkpoint_milestones.py:512-526`;
  observer ownership: `orchestrator/checkpoint_observer.py:453-473`;
  Laya production-only C6: `training/laya_phase_acceptance.py:110-132`;
  mall baseline: `orchestrator/baseline_production.py:29-34`;
  produce gate: `training/laya_ops_runner.py:754-772`.
- Robot diff: `arms/robots/code/orchestrator/live_base.py:1076-1077,1999-2001`;
  `report.md:18`.
- C38 status: `data/laya-experiments/laya-c38-2123-miniship-20261026/report.md:3,7-8`.

Missing / bounded evidence:
- C6 acceptance is sampled at decisions, not per tick. `consumer_deltas {}` can mean the
  cell was released between samples, not that it never existed. The 54/54 final-cell
  census and the ever/final split are the guard against overreading it.
- No per-tick cell-ownership lifecycle log exists; `temporary_mall_batch` is a receipt,
  not a full loan timeline. The 33/54 transient classification is from the decision
  outcomes plus the final cell list.
- No raw kit ledger for every run; kit shortage is inferred from the named material
  shortage and the final `mall_economics` cells. robots-s80125 is the clearest case.
- C38 has no C2 and cannot be used as clearance evidence; its 117 twelve-decision stops
  are a runner regression.
- C37 decision journals are uncompacted (~14 MB/run); all 120 were parsed for the counts,
  but only representative lines are quoted here. Counts are reproducible from
  `job-status.json` plus the per-run `decision-states.jsonl`/`clean-decisions.jsonl`.
