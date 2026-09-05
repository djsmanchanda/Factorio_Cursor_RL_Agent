<!-- Path: docs/archive/handoffs/33_live_observation_2026-09-03.md -->
<!-- Purpose: Live observation journal for the 18:10 mining-productivity-4 run: log batches, server-side probes, bottlenecks, code correlations, plan. -->

# Live observation — 2026-09-03 18:10 run (mining-productivity-4)

Append-only. Newest entries at the bottom. Times are IST (+05:30) unless noted.
`+Ns` = runner-log elapsed time since RUN START.

## Run provenance (evidence header)

- Command: `research target=mining-productivity-4 surface=nauvis force=player bootstrap_profile=reduced-v1`
- Server: isolated deterministic root, game `127.0.0.1:34199` / RCON `127.0.0.1:27017`, PID 1288801
- Runner: PID 1289314, `RUN START ts=2026-09-03T18:10:10`, initial tick 230946
- Previous 17:28 run ended first: SIGTERM from `fresh_campaign` at 18:10:06
  (recorded as `bug/unhandled_exception: runner terminated by signal 15` —
  intentional lifecycle stop misclassified as a bug; see Obs 0).
- Code revision: `35169b9` + 9 dirty files (all today's Python-only fixes, no Lua):
  `orchestrator/autonomous_builder.py`, `orchestrator/build_diagnostics.py`,
  `orchestrator/game_bridge.py`, `docs/deterministic/planning.md`,
  `CURRENT_STATUS.md`, 4 test files. No mod redeploy needed; runner restart
  picked the worktree up. 31 commits ahead of origin/main (all
  `fix/feat(deterministic)` mall-bootstrap work).
- Mod set: unchanged since last deploy; episode manifest hash check passed
  (`EPISODE BASELINE VERIFIED`).
- Method: read-only. Log tails + RCON tick probes (`/sc rcon.print(game.tick)`)
  + occasional read-only stock surveys. No live mutation, no restarts, no
  redeploys from this journal.

## Baseline @ 18:11–18:12 (+0–2 min)

- Server healthy: game+RCON true, dashboard 9137 runner PID 1289314.
- Tick 236524 at wall 18:11:44 vs 230946 at 18:10:10 → **~55.8 effective UPS**
  over the opening (fresh save, few entities — below 60, see Obs 1).
- Opening sequence nominal and fast: 3 starters (iron/copper/stone) placed +
  powered by +53s; prep cells (gears x2, cable, circuits) done by +45s.
- Iron foundation survey `new_mine_site` took **13.1s wall** (+54–67s) —
  the single slowest synchronous step so far (Obs 1).

## Obs 0 — Fresh-campaign SIGTERM recorded as a bug blocker (2026-09-03, +1262s of prior run)

- The 17:28 run's tail shows `RuntimeError: runner terminated by signal 15`
  → blocker `classification:"bug", code:"unhandled_exception"`.
- That SIGTERM is the campaign manager stopping the old runner for the fresh
  episode — intended lifecycle, not a controller defect.
- Code: `tools/autonomous_run.py:765 _raise_termination_signal` raises on any
  signal; the blocker writer does not distinguish SIGTERM-from-manager vs crash.
- Effect: `deterministic-blockers.jsonl` accumulates a false `bug` entry per
  fresh campaign, polluting Helper Agent review evidence.
- Suggestion: catch the termination path and record `intended_difficulty`
  (or a dedicated `lifecycle_stop` code) instead of `bug/unhandled_exception`.

## Obs 1 — RCON survey latency dominates wall-clock (opening)

- `iron-plate new_mine_site elapsed=13.11s`, `copper ... 26.8s` in prior runs;
  `resource_patch`/`clear_refinery_sites` are sub-second.
- UPS 55.8 during a fresh-save opening suggests tick cost is not simulation
  but RCON round-trips + report polling (each survey = several blocking
  game round-trips; `new_mine_site` scans candidates one live query at a time
  per the 08-01 region-scan entry — but 13s is still the budget to beat).
- Watch: per-phase SURVEY START/END lines; correlate slow surveys with run
  wall-time share. Candidate fix (not yet): batch the mine-site candidate
  scan into fewer round-trips or cache across the 60s re-surveys
  (`SURVEY END new_mine_site elapsed=0.00s` on cache hits shows caching works).

## Observation log (running)

### +0–60s: starters + prep, no anomalies

- All three direct starters placed, powered, resolved in ≤2 remedy rounds each.
- Roboport chains + power bridges dominate the action count (every starter
  needs 1 roboport + ~6 power actions). No material waits yet — starter bills
  are tiny (9/5/7 ghost items).
- First live test of today's code coming up: iron belt demand (128 + spares).

### +68–280s: iron belts — spare math live, no restore churn (FIRST FIX VALIDATION)

- `PREP DEMAND: transport-belt=128` → `spare ceiling=154` (128×1.2=153.6→154 ✓).
- Belt loan produced **continuously on one cell, crafts 19→96, stock 0→124
  transferable, zero premature restores**. Old code would have restored at
  ~128 available and re-borrowed every pass.
- At +279s a waiting fast-inserter batch triggered a clean
  `LOAN PREEMPT ... releasing spare production through 154` → restore with
  reason → new loan. Preempt-as-time-slicing works as designed.
- Server: 59.9 UPS steady (tick 247900 @18:14:53). Opening 55.8 dip was
  survey load, not simulation.
- New-code markers all present: `bill 128 + spares`, `holds X/154
  transferable`, `spare ceiling=154`, `LAGGING BUILD` lines.

### +306–530s: splitter/fast-inserter handoffs clean; iron built end-to-end

- Serial handoffs all reasoned correctly: `PREEMPT ... releasing spare
  production through N for X` → restore with quota reason → next loan.
  No stuck handoffs this run.
- **T3 resolved**: `splitter required=3, spare ceiling=5` (+284s) = bill 3 +
  WIP-gap 2 (two splitters locked in requester WIP at the time); later
  recompute with WIP 0 fell back to the startup-cap path (spare ceiling=3).
  The WIP-aware spare adapts as designed — not a bug.
- +391s: belt demand shows `holds 122/154, 12 locked in requester/buffer
  WIP` — the WIP-aware LAGGING line firing live exactly as designed.
- +393s: **second belt producer funded** — `BOOTSTRAP MALL CAPACITY:
  assigning a demand-owned transport-belt slot` (cell 35,37) during a loan
  gap; provider at 185 by +411s (`MALL PRODUCING`). The user's parallel-
  capacity ask is validated live (via the loan-gap slot path; the new
  `minimum_machines=2` reserve path has NOT visibly fired — see C1).
- +416–494s: iron mine (19 ghosts earmarked) + 142-action refinery placed,
  powered, covered; `BOOTSTRAP SWAP: full iron-plate system is healthy`,
  pioneer released. Earmark-while-producing works when the mall follows
  through. Copper survey 26.8s again (Obs 1 confirmed, 2nd data point).

### +528–653s: RUN DEATH — `mall_loan_gate_mismatch` on drills (ROOT-CAUSED, FIXED)

- Drill loan: bill 6, spare ceiling 8. Step advanced 6 → 8 (spare phase) but
  the machine's logistic gate froze at the bill-phase configure (6): cell
  disabled at 6 drills, loan waited for 8, `STUCK ... disabled below its
  stock target 8` → RUN END at +653s. available_stock 6, target 8.
- Mechanism: `bootstrap_loan_plan` sets the gate from the live step target,
  but the bill→spare transition took the WAIT path (no reconfigure), so the
  gate never followed the step. First live failure of the spare system, and
  it is a configuration-drift bug, not a design flaw: gate, loan step, and
  demand-pop must all agree on bill+spare, and the gate was the odd one out.
- Fix applied (worktree, uncommitted): resubmit (gate refresh) whenever the
  persisted `step_target_count` drifts from the computed step target; plus
  one status re-read before the STUCK raise so a stale disabled sampling
  during bot drain becomes one more wait round instead of RUN END.
- Evidence it would have saved this run: at +647s the cell sat disabled at
  6/8 with copper mine ghosts already placed and drills flowing — one gate
  refresh (6→8) unblocks 2 more crafts, loan restores, demand pops at 8,
  copper mine builds. No save reset needed: restart resumes here.

## Thread resolutions + improvement plan

- T1: PARTIALLY ANSWERED — 2nd belt cell funded via loan-gap slot path
  (+393s), not via the new `minimum_machines=2` reserve path (no `DYNAMIC
  MALL CAPACITY: transport-belt` line seen). C1 stands: while a belt loan is
  continuously active, the reserve path never evaluates.
- T2: copper drills never reached diagnosis — run died at drill production
  (+653s). Material-first diagnosis still untested live; the rerun exercises
  it at the copper mine.
- T3: RESOLVED — spare 5 = bill 3 + WIP-gap 2 (see +306–530s section).

Plan:
- P0 DONE (worktree): loan gate follows step target; STUCK re-reads status.
  Restart runner on preserved world — copper resumes at 6/8 drills.
- P1 (watch): if restore/reborrow churn reappears in any form, split
  same-target second loans with divided targets instead of serial cycles.
- P2: evaluate `minimum_machines` reserve path before the loan early-return
  in `_ensure_mall_item`, so large backlogs fund parallel producers without
  waiting for a loan gap. Needs split-target care (two producers must not
  each aim at the full spare ceiling).
- P3: skip `_deliver_cell_ingredients` when the cell made craft progress
  since the last pass (progress ⇒ fed); highest-frequency live-query loop,
  mostly moves 0 items.
- P4: batch or cache `new_mine_site` surveys (13–27s wall each); UPS holds
  60 otherwise.
- P5: record manager-initiated SIGTERM stops as lifecycle/intended stops,
  not `bug/unhandled_exception` blockers (Obs 0).
- P6: next run, track belt-plan-to-foundation and drill-plan-to-mine
  latency as the two headline cycle times.

## Code correlations

### C3 — loan gate must track the loan step, not the bill (run-killer, fixed)

- `mall_bootstrap.bootstrap_loan_plan:411` writes
  `logistic_condition = stock_gate(step.recipe, step.target_count)` on every
  configure — but nothing reconfigured on the bill→spare step advance, so a
  `craft while network < 6` gate strangled a loan waiting for 8.
- Fix: `gate_refresh_needed` in `_submit_bootstrap_loan` forces the submit
  path on any step-target vs persisted-tag drift; the STUCK branch re-reads
  status once before dying. Tests: spare-phase refresh submits gate constant
  8; stale-disabled resumes.
- Residual risk accepted: under permanent drain the cell now holds to true
  completion; anchor displacement is bounded by preempt (other-batch needs)
  and the 10-slot pool.

### C1 — 2nd belt producer may be unreachable while its loan is active

- `_ensure_mall_item` returns early when `_rationed_mall_batch` reports a
  loan in progress — so `_bootstrap_reserve_machine_target` (the only path
  that returns `minimum_machines=2`) never evaluates for an item with an
  active loan. `transport-belt` joined `_PARALLEL_BOOTSTRAP_RESERVE_ITEMS`
  today, but same-target concurrent loans are deliberately single
  (`_start_bootstrap_loan` services the existing loan and returns).
- Net: belts still serialize on one AM1 whenever a belt loan is continuously
  active — which the churn fix just made *more* likely (loan holds cell to
  true completion). The demand-owned-slot path (`if not active:`) only runs
  in loan gaps. If T1 confirms no 2nd belt cell all run, the follow-up is a
  same-target second loan (with split targets so two loans don't each aim at
  154) or evaluating reserve-machine-target before the loan early-return.

### C2 — CELL DELIVERY chatter may dominate RCON load

- ~40 `CELL DELIVERY: moved 0–5 X` lines in this window, most moving 0.
  `_deliver_cell_ingredients` runs per pass per task (now up to 3 tasks/pass
  with multi-serve): requester lookup + network counts + provider search even
  when the cell is crafting fine. Cheap individually, but it is the highest-
  frequency live-query loop in the bootstrap. Consider skipping delivery when
  the cell made craft progress since the last pass (progress ⇒ fed).

## Standing direction + drain-aware pop (user, post-run)

- Docs are guidelines, not permanent: doc-aligned behavior that blocks
  progress gets asked about, not followed blindly; doc edits need prior
  approval (this journal excepted). Code fixes proceed; doc wording is
  proposed, not applied.
- Direction answers: drain-aware pop + watch one more run before P2.
- Implemented in code (no doc edits): `_serve_mall_task` retires a bill-met
  demand when its loan advanced this pass yet spendable stock did not grow
  since the last survey (history snapshotted in `_survey_pass`) and the item
  holds an active loan — proven produce-eat equilibrium. Climbing stock,
  idle loans, and missing history keep the full spare gate. The cell keeps
  producing spares in the background; preempt frees it on contention.
- Evidence: 4 new drain tests, 676-case fast gate, 559-case deterministic
  tier (3 pre-broken files excluded, failing identically clean), compile +
  diff clean. Python/controller only; restart runner on preserved world.
- PROPOSED doc wording (not applied, awaiting approval): planning.md spare
  paragraph += "A demand whose bill is met in transferable stock while its
  loan keeps advancing without accumulation retires as drained; the cell
  finishes spares in the background." CURRENT_STATUS.md += one entry for the
  gate fix + one for drain-aware pop.

## Run 3 — 18:41:42 fresh campaign (all fixes live, incl. gate + drain-aware)

Provenance: same command/profile/save; initial tick 230936; runner PID 1528869
(fresh_campaign 18:41:38). Worktree = 35169b9 + 9 dirty files incl. today's
gate-refresh, STUCK re-read, drain-aware pop, flow funding, belt parallel set.
Disclosure: the planning.md loan-gate sentence and this journal were written
before the user's no-doc-edits rule; CURRENT_STATUS has NO gate/drain entry
yet (proposed wording above stands).

### Comparison baseline (headline metrics)

| metric | 17:28 run | 18:10 run | 18:41 run |
|---|---|---|---|
| iron starter powered | +4s | +4s | +8s |
| copper starter powered | +14s | +14s | +46s (roboport chain detour) |
| prep cells (gear/cable/circuits) | ~+99s | ~+99s | ~+65s (in progress, faster) |
| iron belt demand → spare ceiling | 128 → 147 (churn) | 128 → 154 | TBD (watch DRAIN-AWARE POP) |
| belt completion style | restore/reborrow churn | continuous, preempt handoff | TBD |
| iron swap healthy | +605s | +494s | TBD |
| copper drills | 2/6, coverage loop | 6/8, gate death +653s | TBD (gate fix live) |
| terminal state | STUCK no_progress 860s | STUCK gate_mismatch 653s | TBD — must clear +653s |

### +0–65s: opening nominal, prep faster than both priors

- Starters identical positions/bills; copper needed an extra roboport hop
  (+37s vs +14s) — map-clutter/routing variance, not a regression.
- Prep cells tracking ~30s ahead of the 18:10 run at the same mark.

### Run 3 batch +68–451s: parallel belts live, earmark path fast

- +149s: iron `new_mine_site` took **45.9s wall** (vs 13.1s last run, same
  world). Survey cost varies wildly run to run — strengthens P4 (batch/cache
  mine-site scans). All other surveys sub-second; cached re-surveys 0.00s.
- +156s belt demand → demand-owned slot immediately (gears 78 + 3 plates
  covered the bill; no loan needed this run — trajectory variance from
  18:10's loan path on identical demand).
- +272s: **`DYNAMIC MALL CAPACITY: transport-belt has 54s of blocking
  backlog; funding 2 producers`** — the new parallel-belt rule fired live on
  first demand. 2nd cell placed +276s; provider at **185 belts by +274s**
  (~1.6/s). Parallel ask SATISFIED (via slot path; reserve path still
  unobserved — C1 partially stands).
- +306s PIPELINE READY → mine placed +311s, 142-action refinery +325s,
  reconciling +332s. Full foundation ghosts down ~25 min ahead of the 18:10
  run's pace at the same mark.
- Transferable-wait patience working as designed: belt ghosts short on
  network stock waited through `force stock 4-8 / transferable 0` rounds,
  then `MATERIAL DELIVERY moved 2 belts` into the stage provider (+411s,
  +442s) instead of dying or placing empty chests.
- Belt reserve floor held: splitter deferred at `only 8 remain` (+180s),
  protecting blueprint belts from recipe consumption.

### Comparison table (live)

| metric | 17:28 run | 18:10 run | 18:41 run |
|---|---|---|---|
| iron mine ghosts placed | ~+440s | ~+524s | +311s |
| iron refinery placed | — | — | +325s |
| belts to 120+ transferable | churn to 860s death | +250s (124, loan) | +274s (185, 2 cells) |
| 2nd belt producer | never | loan-gap slot | +276s demand slot + DYNAMIC line |
| mine-site survey wall | — | 13.1s iron | 45.9s iron |
| terminal | STUCK churn 860s | gate death 653s | alive at +451s |

### Run 3 batch +499–765s: spare pop + material-first diagnosis VALIDATED

- +655s: **`RATIONED MALL READY: transport-belt batch has reached 122 plus
  spares (147 transferable)`** — spare-gated pop firing exactly as designed.
  No restore churn anywhere in the belt lifecycle this run.
- +760s: **material-first diagnosis fired live on the exact incident
  coordinates**: `ghost electric-mining-drill at (87.5, -41.5) has no
  transferable electric-mining-drill in stock (4 pending ghost(s))` →
  `materials:electric-mining-drill:4` → mall demand queued. Last run this
  same ghost looped on `coverage_charge_wait`. A stocked belt ghost (+727s)
  still correctly gets the charge wait — the diversion is selective, not a
  blanket reorder.
- Iron swap +589s (middle of priors: 494s / 605s). Copper mine placed +682s
  with the same 2/6-drill backlog, refinery +691s; drill loan producing
  (prerequisite gears 40 crafted). The 2/6 pattern is now a handled transient
  instead of a death sentence.
- Micro-batches work: 1-belt foundation shortfall → required=1/spare=9 loan
  on a free cell → restored cleanly (+534–646s).
- Copper survey 26.83s ≈ 18:10's 26.84s (deterministic same-site cost);
  iron survey varies 13→46s run to run (candidate-order luck).

### Comparison table (live)

| metric | 17:28 run | 18:10 run | 18:41 run |
|---|---|---|---|
| iron swap healthy | +605s | +494s | +589s |
| belt final pop | never (STUCK 860s) | n/a (died 653s) | +655s @147 transferable |
| drill ghosts | coverage loop forever | gate death 6/8 | materials verdict +760s, producing |
| copper mine placed | — | +448s | +682s |
| terminal | STUCK churn | STUCK gate | alive at +765s |

## Run 3 close-out: iron + copper DONE (verified live, no restart needed)

Live world at tick 309931 after RUN END: **14 drills, 0 ghosts, 13 furnaces**
= 6+6 mine drills, 6+6 refinery furnaces, 2+1 stone-starter leftovers.
Providers hold **708 iron-plate + 1298 copper-plate**, 194 belts, 3 drills.
Both district ledgers show `replacement_output_measured` →
`pioneer_retirement_started` → `pioneer_absence_verified` → `released`
(copper measured_output_count 134). Built, producing, pioneers retired:
iron and copper full systems are DONE. Stone foundation never started (run
died first) — out of scope for this verdict.

### Follow-up fixes implemented (code only, no doc edits per standing rule)

- F-A standing reserves (user philosophy): pre-core squeeze now applies only
  while starters carry the base (`_scarce_metal_startup`); once metal
  transitions, items keep grown `mall_reserve` storage past the bill
  (`MALL STANDING RESERVE` line). Tight batch preserved pre-transition and
  in dry harnesses.
- F-B requester headroom: finite-batch caps and loan requester multipliers
  ask ceil(crafts × 1.2), still bounded by the throughput window (existing
  anti-hoard tests updated to the intended values).
- Crash fix: loan pad survey sanitizes machine names to assembler tiers, so
  a robot hovering the pad parses as tier-1 instead of emitting
  `configure_entity logistic-robot` → `configure_target_missing` → RUN END.
- Evidence: 254 focused, 680 fast, 564 deterministic (3 pre-broken excluded),
  compile + diff clean.
- PARKED (not done): binding-need priority boost (drills at 75), mine-swap
  drill-completeness gate, P2 reserve-path reorder, P3 delivery skip, P4
  survey batching, P5 SIGTERM classification.
- PROPOSED doc wording (not applied): planning.md standing-reserve paragraph
  += squeeze-release + requester-headroom sentences; CURRENT_STATUS.md +=
  gate-fix, drain-pop, standing-reserve, robot-guard entries.

## Run 4 — 19:15:49 fresh campaign (all fixes incl. standing reserves live)

Provenance: same command/profile/save; initial tick 230941; runner PID 1790152
(fresh_campaign 19:15:45). Worktree adds standing-reserve release, requester
+20% headroom, and the robot-name guard over Run 3's code. First live test of
all three; gate-refresh and drain-aware pop still awaiting their trigger
conditions (both need a spare-phase stall).

### Comparison baseline

| metric | 17:28 | 18:10 | 18:41 | 19:15 (watch) |
|---|---|---|---|---|
| iron starter powered | +4s | +4s | +8s | +4s |
| copper starter powered | +14s | +14s | +46s | TBD |
| iron mine placed | ~+440s | ~+524s | +311s | TBD |
| belt peak / how | churn | 124 loan | 185, 2 cells | TBD (requester ×1.2?) |
| iron swap | +605s | +494s | +589s | TBD |
| copper drills | 2/6 loop | 6/8 gate death | 6/6 via materials verdict | TBD |
| terminal | STUCK 860s | STUCK 653s | STUCK 988s (robot race) | TBD — must clear +988s |

### Run 4 batch +44–334s: loan targets spare natively; slots over loans

- Belt loan created with **bill minimum 154** (spare-inclusive target, via
  ensure_produced's temporary-target raise) while the RATIONED line still
  reports required=128: demand and loan now agree on 154, which is exactly
  what the completion gate waits for. (18:10 showed bill minimum 128 with a
  130 ceiling — the old mismatch shape.) Cosmetic gap: the "bill minimum"
  and "required=" figures disagree; harmless.
- +299s clean preempt belt(154 fulfilled)→fast-inserter; splitter and
  inserter took **demand-owned slots directly** (stock covered bills) instead
  of loans — the mall widens while affordable, per the standing-reserve
  direction. Splitter deferred at `only 8 remain` protects blueprint belts.
- Mine survey 13.08s again (13/46/13 across runs — candidate-order luck;
  copper stable 26.8s). P4 stands.
- Requester headroom (+20%) is in the plan but NOT log-visible: the message
  still prints step crafts ("asks for exactly 77") while the plan carries
  ceil(x*1.2). Follow-up: print the applied multiplier (one-line log fix).
- No crash, no STUCK through +334s. Robot guard still unproven (needs a
  robot-over-pad survey tick to matter).

### Run 4 note: loan message now prints applied headroom (next restart)

- `MALL BOOTSTRAP LOAN` line prints the requester multiplier actually
  planned (`ceil(step × 1.2)`) instead of "exactly N crafts". Code-only,
  message-only; live on next runner restart, not this run.

### Run 4 follow-up fixes (code only, from Run 3/4 evidence)

- Binding demands outrank reserves: foundation/ghost-driven shortages are
  recorded (`_BLOCKING_MALL_ITEMS`) and promoted to rating 100 at survey
  while queued (drills 75→100 while mine ghosts wait); entries retire with
  their demand. A binding loan below its bill is shielded from preempt by
  non-binding batches (drill 2/8 no longer yields to circuits-200); bill-met
  and binding-vs-binding preempts unchanged.
- Evidence: 4 new binding/preempt/rating tests, 258 focused, 684 fast, 568
  deterministic (3 pre-broken excluded), compile + diff clean.
- PROPOSED doc wording (not applied): planning.md += binding-priority and
  preempt-shield sentences; CURRENT_STATUS.md += gate, drain-pop, standing
  reserve, robot-guard, binding-priority entries.

### Run 4 batch +811–1417s: furthest run ever, both prior death points cleared

- Circuits-200 closed +1075s through prerequisite stepping (cables 582 →
  circuits 200) with craft progress every pass and NO crash: the robot-guard
  fix holds (no `configure_target_missing`), though a robot-over-pad tick
  may simply not have recurred — guard proven by test, not yet by fire.
- Splitter-50 closed +1331s via parallel loan with two prerequisite hops
  (circuits 237 → belts 188 → splitters 50), each fulfilled in seconds.
  Multi-level rotating loans compose correctly.
- Stone mine +1386s, 162-action refinery +1402s, now in coverage/power
  remediation (+1416s). Copper swap done earlier (post-metal reserve gated
  on it). Zero STUCKs, zero crashes through +1417s.
- Comparison: 17:28 died +860s (belt churn), 18:10 died +653s (drill gate),
  18:41 died +988s (robot race). 19:15 alive past all three marks and
  building stone — first run to reach a third foundation.

### Run 4 batch +1417–1676s: stone powered, core-mall promotion running

- Stone refinery power bridged +1462–1483s (3 rounds), district reconciling
  with pioneer kept pending first output — correct lifecycle patience.
- Core mall promoting: AM2 ✓, fast-inserter ✓, provider-chest gated on
  steel → chemical ladder (pipe loan running). **Four concurrent loans**
  (fast-inserter, belts, AM2, pipe) with clean handoffs — the parallel-loan
  design at full stretch, no churn deaths.
- Belt-prep loan shows required=1/spare=271: the standing-reserve path
  sizing to the grown reserve post-transition (my change live). FULFILLED by
  craft count at 92 crafts with honest restore.
- Run is now past stone construction into chemical/core-mall territory —
  further than any prior run by ~700s of mission progress.

### Stone starter still standing — CORRECT, not a retirement bug (probed live)

- User report: proper stone system complete+running, starter not deconstructed.
- Live truth: district `stone-brick` is `provisioning`, measured output 0 —
  3/6 refinery furnaces work, 3 sit recipe-less (`no_ingredients`).
- Feed geometry matches the approved template exactly (12/12 inserters built,
  same orientations as the 6/6-working iron/copper refineries) — no layout
  gap, no missing ghosts (0 globally), no ground ore, no competing sink, all
  6 mine drills live and `working`.
- The 55-tile haul carries only 1–4 ore/tile with downstream segments empty:
  inflow ≈ 3-furnace appetite. Ore accounting closes (starter + young mine
  output ≈ bricks produced), so this is a still-ramping mine (drills built
  progressively by bots since +1386s), not a leak. North rows should join as
  flow saturates; the swap gate (measured output + full readiness) is
  correctly holding the pioneer meanwhile.
- If north rows haven't joined within ~5 min of this note, the next probe is
  per-drill output rate (progress counters sampled twice) to test
  rate-mismatch vs ramp.

### Stone 2:1 fix (user diagnosis confirmed live, implemented in code)

- User: stone-brick takes 2 stone/craft (furnace 1.25 ore/s in, 0.625/s out)
  vs 1:1 for iron/copper — stone needs double the mines for equal production.
  Live probes agreed: 6 drills fed exactly the 3 south-row furnaces.
- Fix (no stone literal anywhere): new-mine rows scale with the recipe's own
  ore-per-product ratio — `opening_mine_row_drills`: iron/copper keep legacy
  3/row, stone opens 6/row (12-drill mine), clamped to [3,12], narrow patches
  still fall back via patch-fit. Furnace side was already rate-aware.
- Evidence: 2 new row-sizing tests, 64 extraction tests, 684 fast, 570
  deterministic (3 pre-broken files excluded, re-verified identical clean),
  compile + diff clean. Python/controller only; next restart grows stone via
  normal expansion (or a fresh episode opens 12 upfront).
- PROPOSED doc wording (not applied): planning.md mine/refinery pairing
  lines += ore-aware row sizing (12 drills feed 6 stone furnaces);
  CURRENT_STATUS.md += one entry.

### 20:37 run death (+221s): uniform-100 promotion from my own binding change

- PREP DEMAND queued belts+inserters+splitters together; my new survey loop
  promoted ALL of them to 100 ("blocking prerequisite", also persisted to
  autonomous-priorities.json). Alphabetical tie-break served splitter first
  every pass; splitter permanently deferred on the belt floor (0 belts); the
  stop-at-first-stuck multi-serve rule starved belts/fast-inserter behind it.
  Meanwhile the fast-inserter loan borrowed a gear half while gears were
  scarce, halving gear flow into a plate trickle shared four ways. 12
  identical passes → STUCK. My binding change caused this one — bulk prep
  queues are not ghost pressure.
- Fix (code only): binding marks come ONLY from submitted-replacement ghost
  demands now; PREP DEMAND bulk queues do not mark. PriorityList load resets
  base_rating/reason/status/retry to live defaults (identity, target,
  progress kept) so dead-episode promotions cannot steer the next run;
  pressure re-marks within a pass or two.
- Evidence: 3 new narrowness/hygiene tests, 234 focused, 687 fast, 573
  deterministic (3 pre-broken excluded), compile + diff clean.
- PROPOSED doc wording (not applied): planning.md += "binding marks cover
  only submitted-replacement ghost demands; bulk prep queues never promote,
  and persisted ratings reset on load." CURRENT_STATUS.md += one entry.

## Run 5 — 21:13:41 fresh campaign (binding-narrow + load-hygiene live)

Provenance: same command/profile/save; initial tick 230917; runner PID 2651715.
First run with narrow binding marks and rating-reset-on-load.

### Late failure found in Run 3's tail (19:15 episode ran to +2594s)

- The 19:15 world did NOT die at +988s: after the robot crash the runner was
  restarted on the preserved world and ran to +2594s (43 min) — iron swap,
  copper swap, circuits-200, splitter-50, stone started.
- Terminal: `automation-science-pack at None%`: "Nothing is producing
  transport-belt, which the mall has been drawing from stock." The bootstrap
  consumed its own belt producers (core promotion reclaims temporary slots)
  while science draws belts, and no permanent belt line exists. Next fault
  after copper: producers must not be reclaimed while downstream draws.
  Parked for a future turn — observe whether Run 5 reproduces it.

### Comparison baseline

| metric | 17:28 | 18:10 | 18:41 | 19:15 long tail | 21:13 (watch) |
|---|---|---|---|---|---|
| iron swap | +605s | +494s | +589s | +539s | TBD |
| copper swap | — | — | +776s | +776s | TBD |
| circuits-200 | — | — | stuck 6/8 | +1075s | TBD |
| terminal | STUCK 860s | STUCK 653s | STUCK 988s | STUCK 2594s (belts gone) | TBD — must clear +221s |

### Run 5 batch +129–553s: all newest fixes firing live

- Ratings discriminate again (uniform-100 GONE): belts 100 default,
  fast-inserter/splitter 45 + age. No stuck-head task; iron mine +369s,
  refinery +373s with a tiny residual bill (splitter=2, belts=6).
- First **DRAIN-AWARE POP** live (+1361s): fast-inserter retired at bill 1
  with loan advancing — no stuck, no churn.
- Requester headroom visible in every loan line ("step 77 + headroom" →
  93 crafts, "step 4 + headroom" → 5, etc.).
- WIP-aware spare adapting: belt spare ceiling 160 = bill 128 + 32 locked
  WIP, recomputed live (+1510s).
- Ghost materials remedy → foundation demand → mall queue working
  (+1545s splitter=1 for refinery ghosts); splitter demand-owned cell
  repaired in place (+1356s) instead of duplicated.
- Belt loan fulfilled at spare (154) with clean preempt to fast-inserter
  (+1289s). No restore churn, no STUCK through +553s.

### Comparison table (live)

| metric | 17:28 | 18:10 | 18:41 | 19:15 tail | 21:13 |
|---|---|---|---|---|---|
| belt loan style | churn→death | continuous | continuous | continuous | continuous, spare 160 w/ WIP |
| discriminator | — | — | uniform-100 bug | uniform-100 bug | 100/45 split restored |
| DRAIN-AWARE POP | — | — | — | — | +1361s first live fire |
| iron mine/refinery | ~+440s | ~+524s | +311s/+325s | +442s/+459s | +369s/+373s |
| terminal | STUCK 860s | STUCK 653s | STUCK 988s | STUCK 2594s | alive +553s |

### Stone 2:1 root cause + adequacy fix (user diagnosis confirmed)

- User: stone-brick is 2 stone/craft (1.25 ore/s per furnace) vs 1:1 metals —
  double the mines. Live probes agreed: 6 drills fed exactly the 3 south-row
  furnaces; feed geometry matches the approved template (all 12 inserters),
  no missing ghosts, no ground ore, no competing sink, ore accounting closes.
- First attempt (12-drill opening) was WRONG and reverted: the patch fits
  3-wide rows beside the squatting starter (proven by 19:15's 6-drill fit)
  but not a 12-contiguous site, so 12-upfront converted a fit into a
  permanent siting deferral. Fit-first instead.
- Real fix: ore-adequacy expansion. `mine_short_of_furnace_appetite` (pure
  ore-rate math, multi-ore recipes only, 1:1 behavior untouched) fires the
  existing ORE STARVATION expansion when a full module is fed below its ore
  appetite — the 50%-fed case no rule covered. Stone converges 6 then +6 via
  parallel bands; iron/copper silent at full feed.
- Evidence: 3 new adequacy tests, 235 focused, 687 fast, 574 deterministic
  (3 pre-broken excluded), compile + diff clean. Python/controller only.
- PROPOSED doc wording (not applied): planning.md pairing lines +=
  ore-adequacy expansion rule; CURRENT_STATUS.md += one entry.

### 02:29 run: loan-ingredient deadlock (inserter/fast-inserter 20-min loop)

- Symptom: gear cell alternated inserter-batch/restore for 20+ min; provider
  capped correctly at 10 stacks; inserters never completed.
- Live truth: logistics healthy (1 valid 8-port network, 400+400 bots);
  circuits 170 exist but ALL in requester WIP, zero in providers, zero
  circuit producers, 4 free pool slots. The loan waited on an ingredient no
  cell makes, while ingredient production needs a demand nobody queued:
  `bootstrap_external_shortages` treats assembler-made intermediates as
  transparently craftable, so they never surface.
- Fix (code only): `_serve_mall_task` now queues a starved loan's missing
  assembler-made ingredient as a promoted mall demand (transferable 0, no
  producer, no covering loan, not already queued; one per pass; mined/
  external inputs keep existing paths). The queue dedupes; production
  resumes through normal serving.
- The 10-stack provider bar is exonerated: it correctly holds surplus while
  the true missing piece (circuit demand) never existed.
- Evidence: 2 new ingredient-demand tests, 256 focused, 689 fast, 576
  deterministic (3 pre-broken excluded), compile + diff clean.
- PROPOSED doc wording (not applied): planning.md += loan-ingredient demand
  sentence; CURRENT_STATUS.md += one entry.

### Network-truth inventory + trash-unrequested verdict (user asks, probed live)

- Mall logistics are ONE network (8 networks map-wide are outpost islands,
  benign). 400+400 bots on a valid network: no delivery failure.
- Mode-filtered scan == network truth here: transferable already excludes
  requester/buffer WIP, providers reconcile (gears 1700 counted), and ZERO
  buffer chests exist, so adding buffers changes nothing live. No accounting
  rewrite: the mechanism is sound, the missing piece was demand visibility
  (fixed: starved loan ingredients now queue, needs runner restart to go live).
- `trash_unrequested` is NOT runtime-scriptable on 2.1.17 (probed 6 candidate
  keys + control behavior: all absent). The mod cannot flip that checkbox;
  emulation (restore-time sweeps via bot orders) is possible but heavy and
  unneeded once ingredient demands exist. Not implemented; revisit only if
  stale-WIP lockup recurs with demands flowing.
- The 10-stack provider bar is exonerated: it correctly holds surplus (gears
  1700 gated full); the loop starved on circuits nobody demanded, not on caps.

### Trash requesters via blueprint recipe (user-supplied blueprint, verified live)

- User blueprint decodes to `request_filters.trash_not_requested=true` on a
  blank-section requester. Runtime probes on 2.1.17: NO setter, NO section
  field, NO blueprint-stamping API — creation time is the only moment.
- Proven live on an isolated surface (created + deleted same call):
  create_entity with the flag accepted, and bots moved 5 unrequested copper
  out of the chest into storage within 25s.
- Implemented (mod change, NOT deployed): direct requester creation carries
  the flag; bot-revived requester ghosts are destroy+recreated with the flag
  at revive time (empty chest, no raise_built recursion, ghost fallback),
  scoped to bot builds on player/planner forces — hand placements untouched,
  training untouched. Group/request management unchanged ("like they were").
- Evidence: new lupa suite (4 passed under isolated lupa), luac clean on all
  mod files, 689 fast + 576 deterministic green.
- LIFECYCLE REQUIRED (needs approval): deploy mod to server + GUI copies,
  restart Factorio server, restart GUI client, restart runner. Python-only
  restart is NOT sufficient for this one.
- PROPOSED doc wording (not applied): factorio_mod/README + planning.md
  trash-behavior sentences; CURRENT_STATUS.md += one entry.

### 21:13 +2549s death: craft-proven work never retires + belts never restock

- Terminal: automation-science-pack at None%, "Nothing is producing
  transport-belt, which the mall has been drawing from stock." Belt loan
  restored ~+571s; belt demand popped and NOTHING re-queued while science
  drew stock to zero. Same signature as the 19:15 tail (+2594s).
- Fix 1 (code only): survey pops a demand when loan monotonic counters prove
  the bill (craft-proof), not just on transferable accumulation -- covers
  produce-eat equilibrium and WIP siphoning (circuits 195/200 case).
  Demand re-queues naturally on new need.
- Fix 2 (code only): evergreen stockout watchdog in survey -- transport-belt
  at/below 25 transferable with no live line and no active loan re-queues
  the 200 standing target with a visible reason. Producer lines, active
  loans, queued demands, and healthy stock each suppress it alone.
- PriorityList.complete gained an optional reason (backward compatible).
- Evidence: 4 new survey tests, 260 focused, 693 fast, 580 deterministic
  (3 pre-broken excluded), compile + diff clean. Python/controller only;
  restart runner to activate.
- PROPOSED doc wording (not applied): planning.md += craft-proof pop and
  evergreen-restock sentences; CURRENT_STATUS.md += one entry.

### Verification loop (user: repeat until green)

- Focused 260, fast 693, deterministic 580 (3 pre-broken files excluded,
  re-verified identical clean), 32 Lua suites under isolated lupa, luac clean
  on all mod files, py_compile + diff clean. Single pass, no fallout.

### Mall-first opening: 2 permanent + rotational to 8, no starter stacks (user)

- Opening is now six fixed mall cells: one permanent iron-gear-wheel anchor,
  one permanent copper-cable anchor (never borrowed or reconfigured), plus
  four rotational cells starting as second gear, second cable, one circuit,
  one belt. New needs below the eight-slot cap add a rotational cell; at the
  cap the six rotational slots borrow/restore and the two anchors are
  untouched. `BOOTSTRAP_MALL_SLOT_TARGET` 10 -> 8; circuit left the anchor
  set so the single circuit cell can rotate.
- Prep attempts the standing six ungated (readiness wait removed); plate and
  construction shortages route to the mall and plate foundations as before.
- Fresh plates skip the drill->furnace->chest starter entirely and go
  straight to the six-furnace direct foundation. The opening district itself
  is recorded as the lifecycle pioneer at provisioning, so science
  transition, pipe promotion, and pioneer release keep working with no
  starter entities. Starter machinery stays for legacy saves and as the
  cold-start circular-shortage fallback.
- Evidence: focused 316, fast 697, deterministic 584 (3 pre-broken files
  excluded), compile + diff clean. Python/controller only; restart runner.
- PROPOSED doc wording (not applied): planning.md opening + anchor + pool
  paragraphs rewritten for 2-perm/6-rot, mall-first, foundation-as-pioneer;
  CURRENT_STATUS.md += one entry.

### Mall-first opening died at +6s on a real circle (2026-09-04 05:13 run)

- Fresh stock holds zero belts. First iron foundation needs 123 belts + 3
  splitters + 1 fast inserter; belts need plates. Unhandled MaterialShortage
  escaped _rationed_mall_batch through _ensure_mall_item and killed the run.
- Fix 1 (crash): _ensure_mall_item now catches Deferred/Shortage around the
  rationed batch exactly like the steel path -- queue demands, never die.
- Fix 2 (circle): splitter tiers join _BOOTSTRAP_CIRCULAR_ENTITIES, so the
  +6s bill takes the beltless drill->furnace seed fallback instead of
  escaping. The seed is a self-retiring fallback now, not the opening: mall
  cells ghost first, seed builds only to break the belt/plate circle, then
  retires after the foundation validates. Zero-belt stock makes some beltless
  seed physically unavoidable; alternative is seeding belts in reduced-v1.
- Evidence: focused 319, fast 700, deterministic 587 (3 pre-broken files
  excluded), compile + diff clean. Python/controller only; restart runner.

### Seed restore: three beltless starters open again, mall cells already placed (user)

- The 05:19 run proved the skip too aggressive: mall cells ghosted, but the
  foundation step demanded the full 123-belt blueprint atomically with zero
  belt stock, the seed fallback never fired (drills in the bill), and the
  run sat 12 identical passes to STUCK at +73s. No crash (batch catch held).
- Fresh plates build their beltless direct seed first again -- iron, copper,
  stone in order -- while the six mall cells stay placed-and-ungated ahead
  of them and self-retire after foundations validate. Pioneer-from-foundation
  recording stays as a safety net for lifecycles without pioneers.
- Evidence: focused 319, fast 700, deterministic 587 (3 pre-broken files
  excluded), compile + diff clean. Python/controller only; restart runner.

### 1023s run: shared cells unborrowable + circuit request clamped (2026-09-04)

- Opening works: 6 cells placed ungated, 3 seeds built/retired, both metal
  foundations healthy, circuits stocked to 200. Death at 1023s:
  rationed_mall_no_borrower for splitter with 7 live cells, 0 loans.
- Cause 1 (live-probed): all four paired cells share one provider per pair,
  and the borrow guard skipped every shared half. Dedicated cells now rank
  first; shared cells rank as fallback instead of refusal. Anchor counts and
  loan-cell exclusion unchanged.
- Cause 2 (live-probed): circuit requester read copper-cable min=3 with
  section multiplier 6 (standing standard is 15) -- the pre-core-temporary
  finite-batch clamp rewrote the live base window, capping in-flight cable
  at 18 and crumb-feeding the 200 reserve for ~700s. The paired-request
  refresh now floors at the standard throughput window; the deliberate
  splitter/underground pre-transition clamp is exempt.
- Evidence: focused 352, fast 705, deterministic 592 (3 pre-broken files
  excluded), compile + diff clean. Python/controller only; restart runner.

### Uniform limits across same-item cells (user standard 2026-09-04)

- Every live cell making the same item now carries the same provider count:
  a per-item canonical limit (monotonic max, cleared per run) folds in each
  refresh/promote proposal, and twins converge on the max at their next
  refresh. Loan restores pass the canonical count instead of resetting the
  twin to 1. Stock gates were already per-item uniform; request windows were
  floored in the previous change.
- Evidence: focused 356, fast 707, deterministic 596 (3 pre-broken files
  excluded), compile + diff clean. Python/controller only; restart runner.

### Self-supply readiness for the 6-AM1 / 5-drill start (user 2026-09-04)

- 5 drills cover exactly the three seeds (2+1+2); 6 AM1s cover exactly the
  6 standing cells. Everything after must be mall-made -- audit found the
  chains already exist, no code change: drills and AM1s are rationed-batch
  items whose loan ladder bottoms out at seed plates/gears/circuits, seed
  drill shortfalls queue mall demands and retry (submit ghosts when the
  drill chain is scheduled, else MaterialShortage handoff), and seed
  retirements recycle the 5 drills into the first mines.
- New tests pin it: drill batch admitted on seed stock, circuits-before-
  drills ladder order, raw-plate external when cable runs dry, seventh-AM1
  batch admitted at zero stock, seed shortfall queues a mall drill demand.
- Watch items on the reduced run: background drill/AM1 reserves rebuild
  from zero after seeds/cells deploy; first mines are entirely mall-made
  drills while seeds still run.
- Evidence: focused 361, fast 708, deterministic 601 (3 pre-broken files
  excluded), compile + diff clean. No lifecycle change; restart not needed
  for this (tests only) but pending fixes still await one.

### Observation loop opens: 05:48 ValueError + 05:51 run past 1352s (2026-09-04)

- 05:23 run (baseline, pre-fix): 1023s, no_borrower at splitter-50.
- 05:48 run: died +132s on ValueError in available_items --
  '250|39.5|32.5|assembling-machine-1', a loan-record tail glued into a
  stock response. Transport verified clean (43KB single-packet RCON proven
  live); root garbling unproven. Fix: _parse_stock_counts skips malformed
  chunks (retained capped for diagnosis) in available_items and
  transferable_items; a stock survey can no longer end a run. NOT YET LIVE
  (needs runner restart; current run must not be disturbed).
- 05:51 run (WITH shared-borrow + multiplier-floor fixes): past the old
  1023s death -- splitter loans repeatedly borrowed the shared gear cell,
  both metal districts released, post-metal reserves filled, now on
  stone-brick at +1352s. Shared-borrow fix validated live.

### 2160s run: pipe/AM2/steel priority inversion + yield fix (2026-09-04)

- 05:51 run (with shared-borrow): new record 2160s. Full arc: 6 cells,
  3 seeds built/retired, both metal foundations, post-metal reserves,
  stone district, core-mall AM2 promotion. Death: no_progress at
  automation-science-pack None% -- final 470s looped provider-chest ->
  steel -> pipe -> AM2 -> steel. Steel never produced (no steel furnace
  ever built); pipe rung serial-handoffed behind the AM2 batch that needed
  steel; steel admission waited on pipe output. Classic priority inversion.
- Fix: a loan with zero step progress, blocked on inputs with no producer
  and no stock, yields its cell to a waiter whose own inputs flow (binding
  shields, credited progress, fresh steps, and dry harnesses exempt; each
  yield produces real output so no ping-pong). Pipe runs -> steel admitted
  -> AM2 resumes.
- Live in 05:51 run: shared-borrow, multiplier floor. Pending fresh
  campaign: canonical limits, parser hardening, yield fix.
- Evidence: focused 366, fast 713, deterministic 606 (3 pre-broken files
  excluded), compile + diff clean. Python/controller only.

### Loop cycle 2 opens: fresh campaign 06:32 on full fix set (2026-09-04)

- Previous cycle: 2160s record, died on pipe/AM2/steel priority inversion.
- Fresh via dashboard (server restart + mod deploy + reset + runner start,
  source-save mod_playground.zip). Live code now: shared-borrow,
  multiplier floor, canonical limits, parser hardening, yield-to-unblocker.
  NOTE: deploy synced the uncommitted trash-requester Lua to server + GUI
  copy; GUI restart still required before joining (docs).
- Opening nominal: gearx2 by 19s, copper seed 20s.

### 1005s run: sole cable cell borrowed, circuit starvation (2026-09-04)

- 06:32 run (reduced AM1 kit): cablex2 delayed to +375s for lack of AM1s.
  The belt seed (~+200s) and the AM1 seed (+350s) each borrowed the SOLE
  cable cell; the AM1 seed made circuits on it, cable output hit zero while
  two circuit batches starved, splitter stalled 0%, standing circuit cell
  never built. STUCK, "nothing producing electronic-circuit".
- Fix A (anchor honesty): find_line counts ghosts as producers, so a ghost
  can pose as the duplicate that remains after borrowing the only real
  machine. find_line gains include_ghosts (default True, all callers
  unchanged); the borrow anchor check and candidate positions use
  real-only counts.
- Fix B (no self-strangling): a feedstock cell may not be borrowed for a
  batch in whose ingredient closure it sits unless 3+ real producers run
  (borrow leaves 2). Belts-on-cable still allowed; circuits/inserters on a
  2x cable cell refused. Non-catalog targets have empty closures (unchanged
  behavior); same-recipe borrows unaffected.
- Test fallout: strict find_line fakes gained **_k (mechanical). One NEW
  pre-broken file observed on the clean tree (unrelated inserter vs
  fast-inserter in starter retirement):
  tests/test_cohesive_smelter_expansion.py::test_direct_starter_retires_only_after_the_full_refinery_is_healthy.
- Evidence: focused 407, fast 718, deterministic 611 (now 4 pre-broken
  files excluded), compile + diff clean. Python/controller only.

### Loop cycle 3 opens: fresh campaign 07:01 with cable-collapse fixes (2026-09-04)

- Fresh via dashboard. Live code adds: real-only anchor counts, closure
  self-strangling guard (both verified in loop: focused 407, fast 718,
  deterministic 611). Opening nominal (copper seed 20s).
- Watch items: sole-producer borrows must not recur; cablex2 timing on the
  reduced kit; circuit stock through the iron-foundation phase.

### 332s run: closure guard killed healthy rotation, reverted (2026-09-04)

- 07:01 run died +332s: AM1 batch found no borrowable cell with gear twins
  idle. Cause: my own closure guard (donor in target closure + count < 3)
  refused the healthy gear-twin borrow; cable was honestly at 1 (anchor
  refused). Forensics also showed the +71s belt borrow was legitimate (the
  second cable machine built ~+60s; prep repeats were stale), so the guard
  solved nothing and broke rotation. REVERTED: twin borrows allowed again;
  sole-producer protection rests on real-only anchor counts, which
  neutralize the ghost-inflated counts behind the 06:32 collapse under
  every ghost-origin theory.
- Evidence: focused 404, fast 715, deterministic 608 (4 pre-broken files
  excluded), compile + diff clean. Python/controller only.

### Loop cycle 4 opens: fresh campaign 07:14 post-revert (2026-09-04)

- Live code: shared-borrow, multiplier floor, canonical limits, parser
  hardening, yield-to-unblocker, real-only anchors. Closure guard gone.
- Watch items: AM1 batch borrows a twin (not refused); sole cable cell
  never borrowed (real-only anchors); circuit stock through iron phase.

### 503s run: bill-frozen loan gate raised instead of refreshed (2026-09-04)

- 07:14 run died +503s: mall_loan_gate_mismatch -- a drill loan's circuit
  step sat disabled at 11/18. The gate still carried the old step target
  while the step had advanced: configuration drift, which planning.md
  already says to refresh, not fail. Now the loan re-applies the current
  step gate and continues; only a step ignoring 3 refreshes raises.
- Evidence: focused 406, fast 717, deterministic 610 (4 pre-broken files
  excluded), compile + diff clean. Python/controller only.

### Loop cycle 5 opens: fresh campaign 07:27 with gate refresh (2026-09-04)

- Live code adds gate-refresh-with-bound. Watch items: drill-loan circuit
  steps must resume via GATE REFRESH lines, never mall_loan_gate_mismatch;
  attempts counter must not climb without craft progress behind it.

### 505s run: loan on provider-less half died at submit (2026-09-04)

- 07:27 run died +505s: configure_target_missing for a provider chest at
  (50.5, 33.5) on a circuit loan. The borrowed right half of a
  shared-provider cell has no chest at the formula provider slot (only the
  shared chest on the other side exists). Left halves worked by luck all
  along; the extra chest on cell (35,31) masked it there.
- Fix: borrow only complete halves -- the candidate loop now requires the
  formula provider slot to hold a passive-provider-chest, so such loans
  never start. Rotation preserved through left halves and dedicated cells.
- Evidence: focused 407, fast 718, deterministic 611 (4 pre-broken files
  excluded), compile + diff clean. Python/controller only.

### Loop cycle 6 opens: fresh campaign 07:39 with complete-half borrowing (2026-09-04)

- Watch items: no configure_target_missing on loan submits; right-half
  shared loans must not start; rotation continues through left/dedicated
  halves; drill-loan circuit steps resume via gate refresh if frozen.

### 1702s run: capped standing prep vs spent demand cells (2026-09-04)

- 07:39 run died +1702s: circuit reserve filled (200), loans restored, then
  prep wanted the STANDING circuit cell at 8/8 committed (drill/AM1 demand
  cells squatting) and spun on the cap deferral while splitter stock
  drained with no producer. "Nothing is producing electronic-circuit."
- Fix: capped standing prep reclaims one spent demand-owned cell in place
  (rationed-batch item outside standing/anchor/core sets, no loan, no
  queued demand, idle with stock). Reconfigures assembler, side-scoped
  requester section, provider (shared stays open, dedicated takes the
  canonical bar), and stock gate -- no ghosts, companion half untouched.
  Unreclaimable cap hands the pass to the mall (loans still rotate).
- Evidence: focused 411, fast 722, deterministic 615 (4 pre-broken files
  excluded), compile + diff clean. Python/controller only.

### Loop cycle 7 opens: fresh campaign 08:12 with slot reclaim (2026-09-04)

- Watch items: MALL SLOT RECLAIM lines when prep caps; standing circuit
  cell appears without new slots; no reclaim of anchors/baseline/loaned
  cells; spent-cell demands requeue through normal rotation if needed.

### 1020s run: stale craft-proof hid the servable task (2026-09-04)

- 08:12 run died +1020s: post-metal circuit reserve re-queued every pass
  while serve slept on the survey's stale None. Two coupled defects: (1) a
  lingering spare-phase loan re-proved each re-queued demand with the same
  counters, popping it before service -- the pop hid the very task that
  would advance the loan; (2) serve never recomputed next() after reserves
  re-queued.
- Fix 1: proof consumption -- a pop records the proved crafts; the same
  counters cannot retire the bill twice, while genuinely new crafts
  re-prove. Stock-based proof stays self-correcting (redrop re-fires).
- Fix 2: serve recomputes priorities.next() when its task is None and
  demands are queued; genuine all-deferred still sleeps as before.
- Test fallout: priority-wait fake gained next()->None (mechanical).
- Evidence: focused 414, fast 725, deterministic 618 (4 pre-broken files
  excluded), compile + diff clean. Python/controller only.

### Loop cycle 8 opens: fresh campaign 08:37 with proof consumption (2026-09-04)

- Watch items: re-queued demands survive stale proofs and get served;
  craft-proof pops only on fresh crafts; no PRIORITY WAIT with a ready
  task queued; post-metal reserves complete on stock, then stone.

### 3218s run: wait budget exhausted on productive oil build (2026-09-04)

- 08:37 run, new record 3218s: full arc through stone, steel link built,
  chemical ladder to rung 7+ (oil refinery), then BudgetExhausted on
  power_bridge_settle chaining power ~300 tiles to the oil district.
- Cause: run-total budgets (waits 400 at max_iterations 100) never
  replenish, while passes credit back on progress. Any sufficiently long
  productive run dies on them by construction. Fix: credit_progress_pass
  refunds one plan/remediation/diagnosis/wait each. Unproductive spinning
  still drains every counter, so each bound keeps working; the 12-pass
  state guard still backstops stasis.
- Evidence: focused 450, fast 725, deterministic 618 (4 pre-broken files
  excluded), compile + diff clean. Python/controller only.

### Loop cycle 9 opens: fresh campaign 09:35 with budget refunds (2026-09-04)

- Watch items: no BudgetExhausted on long productive builds; wait/plan
  counters stay bounded while productive; unproductive spins still trip.

### 1801s run: consumer starves its own feeder, no preemption (2026-09-04)

- 09:35 run died +1801s: splitter loan ate every circuit while the circuit
  batch waited 300s for a cell (serial handoff behind a progressing loan),
  then both stalled as WIP drained 164 -> 6. The yield fix explicitly does
  not fire on progressing holders.
- Fix: feeder preemption -- when the holder's bill is unfulfilled, the
  waiter feeds its current step, and the waiter has no producer and no
  spendable stock, the holder restores and the feeder runs first. Each
  cycle banks both outputs so it terminates at the holder's finite bill.
  Fulfilled holders stay on the spare-preempt path; shields, credited
  progress, and dry harnesses exempt.
- Evidence: focused 455, fast 730, deterministic 623 (4 pre-broken files
  excluded), compile + diff clean. Python/controller only.

### Loop cycle 10 opens: fresh campaign 10:13 with feeder preemption (2026-09-04)

- Watch items: holders consuming starved feeders yield (reason names the
  fed step); no holder/feeder ping-pong without output; splitter-class
  reserves complete instead of trickling on WIP.

### 1672s run: shield protected a holder from its own feeder (2026-09-04)

- 10:13 run died +1672s: splitter loan ate every circuit (WIP 231 -> 6)
  while the circuit batch handoffed behind it for 300s, then both stalled.
  Feed-preempt never fired: the splitter loan was foundation-binding, so
  the binding shield skipped it -- shielding the holder from the exact
  batch that would unblock it.
- Fix: feeder preemption is deaf to the shield (feeding serves the binding
  bill). Shield stays on the yield path, where the waiter need not feed.
  The original drills case cannot recur: a stockpiling waiter (spendable
  stock above zero) never qualifies as starved.
- Evidence: focused 456, fast 731, deterministic 624 (4 pre-broken files
  excluded), compile + diff clean. Python/controller only.

### Loop cycle 11 opens: fresh campaign 10:49, shield-deaf feeder (2026-09-04)

- Watch items: "yielding its cell ... which feeds" lines when holders eat
  starved feeders; binding holders still yield to feeders; no regress on
  the drills-vs-stockpile shape (stockpiling waiters must not preempt).

### 1531s run: belt-reserve wait with no waiter (2026-09-04)

- 10:49 run died +1531s: splitter deferred 243s on belts at 26 ("hold the
  reserve until stock recovers past 50") while belt production sat at zero
  and nothing was tasked with recovery -- background reserves only serve
  on an empty queue, which a stuck splitter never leaves. Four stale loans
  squatted cells alongside.
- Fix: the belt-reserve deferral now queues belt recovery at the floor and
  promotes it to blocking, so the reserve rebuilds through normal rotation
  instead of stalling. Stale-loan squatting stays under observation for the
  next cycle.
- Evidence: focused 476, fast 731, deterministic 625 (4 pre-broken files
  excluded), compile + diff clean. Python/controller only.

### Loop cycle 12 opens: fresh campaign 11:21 with belt recovery (2026-09-04)

- Watch items: BELT RECOVERY lines when reserves gate consumers; belt
  stock rebuilds past floors without stalling consumers; stale loans
  restore when their demands are met (rotation clears squats?).

### 2588s+ run: restore/defer churn never establishes steel (2026-09-04)

- 11:21 run: an AM2 loan borrowed and restored in the same breath for 300s
  ("needs unproduced external input steel-plate=4") while steel was never
  built anywhere. The external branch only established the prerequisite
  when NO loan existed -- but a loan always existed by then, so the run
  spun restore/defer forever.
- Fix: restore AND establish in the same pass (shortages/waits propagate
  to the normal queue/retry paths). The handoff deferral still signals the
  retry; the prerequisite gets built on the first restore pass.
- Evidence: focused 478, fast 733, deterministic 627 (4 pre-broken files
  excluded), compile + diff clean. Python/controller only.

### Loop cycle 13 opens: fresh campaign 12:09, restore+establish (2026-09-04)

- Watch items: external prerequisites get established on the restore pass
  (steel starter appears instead of restore/defer churn); AM2/core-mall
  batches advance past external gates; no borrow/restore ping-pong without
  establishment lines between them.

### 1611s run: restore barred a shared chest behind mixed stock (2026-09-04)

- 12:09 run died +1611s: a belt loan sat full_output with inputs on hand.
  Its shared provider held belts=36 + gears=638 + circuits=194 behind a
  count bar -- a loan restore had reset the bar to ~1 stack while 9 slots
  stood occupied, so no new output could ever enter. Input-full plus
  output-full is a true deadlock, correctly tripped by the guard.
- Fix: restores keep shared providers open (fill_chest) and apply the
  canonical count only to dedicated twins, with the live reference point
  threaded through every restore call site (shared verdict needs the
  right district; unknown geometry defaults to open, never to a bar).
- Evidence: focused 482, fast 735, deterministic 631 (4 pre-broken files
  excluded), compile + diff clean. Python/controller only.

### Loop cycle 14 opens: fresh campaign 12:45, shared-open restores (2026-09-04)

- Watch items: no count bars on shared providers after restores (provider
  stays open); dedicated twins converge on canonical counts; no
  full_output deadlocks on shared cells; mixed chests drain through demand.

### 3023s run: chemical handoff hot-spun borrow/restore (2026-09-04)

- 12:45 run died +3023s: bulk-inserters borrowed and restored the same
  cable cell every pass ("chemical handoff ... before establishing
  plastic-bar; retrying after re-observation") while the oil district
  needed minutes. Net-zero config churn every pass tripped the livelock
  guard with oil legitimately in flight.
- Fix: chemical handoffs propagate out of the mall-item wrapper (no longer
  swallowed into a quiet wait) and the task parks on a 60s retry horizon
  instead of hot-spinning; other work (the oil build) proceeds between
  retries. A rung that already produces is stale news and serves normally.
  Background path catches the same signal and keeps waiting.
- Evidence: focused 484, fast 737, deterministic 633 (4 pre-broken files
  excluded), compile + diff clean. Python/controller only.

### Loop cycle 15 opens: fresh campaign 13:42, chemical horizon (2026-09-04)

- Watch items: CHEMICAL WAIT lines park rung-blocked targets ~60s (no
  per-pass borrow/restore churn); oil/plastic establishment proceeds
  between retries; stale rungs serve normally.

### Oil findings: lone pumpjack, uncovered plastic chest (user live 2026-09-04)

- Plastic crawled on 9 crude/s against 40/s of plant draw (2 plants x 20).
  Patch survey: 9 tiles, room for 1-2 more jacks. Refinery basic draws
  20/s; one well cannot feed it, and plant count is irrelevant while
  supply-bound (identical throughput with one plant or two).
- Fix 1: build-time pumpjacks to saturate the refinery -- extra 3x3 spots
  on the same patch (footprint pitch, connector rotated, capped at draw
  coverage), each with its own routed crude link packet. No spots = today's
  single-jack behavior exactly.
- Fix 2: the plastic provider sat 28 tiles from its port (construction
  reach 55, logistic 25). Chaining never verified arrival: dropped final
  hops returned success. extend_roboport_coverage now re-checks after
  placing (settle + recheck, then loud StuckError), and oil output chests
  are re-verified on every visit plus right after the build, before health
  is measured.
- Plant count unchanged (waste-free either way at fixed supply).
- Evidence: focused 579, fast 737, deterministic 637 (4 pre-broken files
  excluded), compile + diff clean. Python/controller only.

### Loop cycle 16 opens: fresh campaign 15:30 with oil fixes (2026-09-04)

- Watch items: extra pumpjacks on the crude patch with own links;
  plastic provider inside logistic coverage from the start; no post-chain
  silent gaps (loud StuckError instead).

### Mine/refinery coherence trace (user live 2026-09-04)

- Iron 12 drills / 6 furnaces: mine led via demand/starvation rungs while
  the 6->12 refinery waited on oil-gated electric furnaces (10 steel + 5
  adv circuits + 10 brick each). The 12-furnace cohesion was already
  earmarked and building (+3159s "expanding iron-plate from 6 to 12"), so
  12/12 was in flight, not missing. Drill phase 48 is burst-sized display
  only; builds move rung-by-rung.
- Stone 6 drills / 6 furnaces: district mid-build toward the phase-12 mine
  with the refinery capped at 6; adequacy (ore_short) only runs
  post-output, then grows the mine. Converges without intervention.
- Real gap: nothing capped mine growth by refinery appetite, so phases
  could run to 24+ behind capped refineries. Fix: coherent_drill_cap =
  furnace appetite at live productivity and recipe ore ratio + one
  six-drill lookahead row (iron 6->12, stone 6->18, fresh 0->6). The
  ore-starvation path now falls through to refinery cohesion instead of
  extending an already-fed mine.
- Evidence: focused 581, fast 737, deterministic 639 (4 pre-broken files
  excluded), compile + diff clean. Python/controller only.

### Post-starter program opens (user directive 2026-09-04)

- Trigger: advanced circuits producing (leading edge; full core-mall
  readiness follows). Order: mall expansion -> mines/furnaces -> serial
  steel -> serial science for mining-productivity-4.
- Present/today: 8-cap bypassed post-core (uncapped dedicated cells),
  upgrades, retrofit, circuit-block promotion, furnace caps lift on the
  electric-furnace producer, drill phases + cohesion + coherence cap,
  one-furnace steel starter, science via mall ensure_produced + transition
  gate. Missing: dedicated serial steel district, serial science
  lines/labs integration (build_science_chain.py unconnected).
- This turn: named gate (_post_starter_phase) + once-per-run TRANSITION
  marker in the log; post-core smoke test (upgrades/retrofit/core-prep
  under mocked production) green. Steel/science builders land as runs
  approach the gate; loop keeps pushing the oil frontier meanwhile.
- Evidence: focused 584, fast 740, deterministic 642 (4 pre-broken files
  excluded), compile + diff clean. Python/controller only.

### Cycle-17 steel death trace (user 2026-09-04, no code change)

- Steel starter (conversion bill incl. 8 inserters) waited 340s while
  inserter batches completed in dribs (3/4, 10/14, 11/12) into a commons
  grazed by copper/stone mines, science conversion, and fast-inserters --
  all serializing on one rotating cell. Post-mortem stocks (inserters 13,
  circuits 206) show the commons was healing when the 12-pass guard
  tripped during the slow rebuild: transient overload, not a defect.
  Contributing structure: free pool slots unusable for lack of AM1s
  (reduced kit circularity: cells need AM1s, AM1s need cells), and no
  precedence for the highest-leverage unlock. No code change justified;
  watching inserter accumulation vs the steel bill next cycles.
- The +5 craft lines are the 5s poll sampler on machines at 100% (AM1 at
  speed 0.5 doing 0.5s crafts = exactly 5/poll). Batch sizes already carry
  the 20% rule (step 100 + headroom = 120 asks, spare ceilings billx1.2).
  Dent-per-demand is cells x speed (8 slots x 1 craft/s), not step size.

### Cycle-19 death: multi-pumpjack power scaffold collision (2026-09-04)

- 18:39 run died +2993s on ValueError (unhandled): the shared power
  scaffold drew from the first jack across the row and landed a substation
  on a new jack ((-276,-96) vs (-276.5,-94.5)). First live exercise of the
  new multi-pumpjack build.
- Fix (planners/resource_layouts): pumpjack sources drop power placements
  colliding with any site footprint; extend_power bridges the rest.
  Single-jack geometry never collides, so old behavior is unchanged.
  Verified against the exact live coordinates; regression test pins it.
- Evidence: focused 585, fast 740, deterministic 643 (4 pre-broken files
  excluded), compile + diff clean. Python/controller + planner data only.

### Ops note: dashboard down, native campaign fallback (2026-09-04)

- Dashboard (port 9137) unreachable; Factorio server survived it. Ran the
  documented native sequence directly (campaign manager fresh with the
  dashboard's own arguments): runner stop, server stop, deploy check,
  verified reset, server + runner start. No Lua changes this cycle, so no
  redeploy was needed. Dashboard restart left to the user (not required
  for the loop; native scripts cover every control).

### Oil scale-up: 4 refineries + crude to match (user 2026-09-04)

- 1 refinery basic draws 20 crude/s and makes 9 petroleum/s; one 9/s well
  left it (and both 20/s plastic plants) idle most of the time. Per user:
  4 basic refineries (~36 PG/s for the 40/s plant draw) with pumpjacks to
  feed 80 crude/s. Plant count untouched (identical throughput at fixed
  supply either way).
- Build: refinery row 1->4, sulfur repositioned east of the measured row
  end (fixed ox+18 would collide), cell 42x34 -> 64x44, local patch jacks
  to saturate (existing machinery), extra crude links routed per jack.
- Expansion: post-build, idle plastic + district jack capacity below
  80/s adds the biggest unused patch next (one patch per trigger, ghosts
  in flight defer, failures remembered per cell, never fatal). Trigger
  costs one find_line when healthy.
- Coverage: post-chain verification (settle + recheck, loud StuckError),
  oil output chests re-verified every visit + before health is measured.
- Evidence: focused 589, fast 740, deterministic 647 (4 pre-broken files
  excluded), compile + diff clean. Geometry proven offline (4-row +
  sulfur + plastic pairwise disjoint with real footprints).
