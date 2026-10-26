<!-- Path: docs/rl/laya_helper_runs/c38-diagnosis-20261026/terminal-audit.md | Purpose: Read-only audit of the C38 terminal accounting and stop mechanism. -->
# C38 terminal audit — controller claim vs raw evidence

**Scope.** Read-only audit of `data/laya-experiments/laya-c38-2123-miniship-20261026` (C38), frozen arm source under `arms/*/code`, and per-run evidence. No worlds, RCON, new probes, services, saves, refs or campaigns were touched. Only this file was written. Highest evidence level: frozen-cohort raw evidence (journals, runner logs, reservation ledger, batch events); no independent replay.

**Primary sources.** `report.md`, `report.json`, `job-status.json`, `planned-jobs.json`, `batch-events.jsonl`, each run's `evidence/clean-decisions.jsonl`, `evidence/decision-states.jsonl`, `logs/laya-runner.log`, acceptance JSON under `evidence/acceptance/`, `server/logs/deterministic-material-reservations/`, `server/factorio-current.log`; `~/.local/share/factorio-rl/run-ledger.jsonl` read-only for corroboration.

---

## 1. Accounting: controller claim is correct, with one decomposition nuance

| Item | Raw evidence |
|---|---|
| Planned jobs | **120** — `planned-jobs.json` (60 seeds × 2 arms); `batch.json:14-75` seeds 80160–80219, `:76-92` arms |
| Terminal attempts | **120 started / 120 terminal**, no duplicate or missing job — `batch-events.jsonl` (`attempt_started` 120, `attempt_terminal` 120); `report.json` `planned_jobs=120`, `ledger_events=240` |
| Completed / prepare-failed | **119 completed, 1 `prepare_failed`** — `robots-s80213`; `report.md:3`; `job-status.json:8552` |
| Stop of completed runs | **119/119 `runner_stop=stalled`, `termination_cause=stalled`**, return code 0, `failures=0`, `untyped_stuck=0` |
| Decision-limit stops | **117** with `stall.decisions=12` (`decision-idle-v2`) |
| Idle-limit stops | **2** with `stall.decisions=11`, `idle_ticks` **19,654** (`robots-s80201`, run `robots-s80201-s80201-1792996782`) and **19,750** (`clearance_robots-s80201`, run `clearance_robots-s80201-s80201-1792996580`). Both > 18,000, both C0-only, both 33 total decisions |
| Proxy phases reached | **C0 119, C1 23, C2+ 0** — `job-status`/`run-ledger` (`highest_milestone`: C0 96, C1 23) |
| Strict acceptance ever | C0 119, C1 **38** (C0-only 81 + C0/C1 38; prepare-failed 1 has none) |
| Strict acceptance final | C0 **118**, C0+C1 **1** (`robots-s80192`), none 1 (prepare-failed). **No C2 ever or final**; acceptance JSON counts C2 failed 822 / unknown 119, C2 passed 0 |
| Matched pairs | **59** (`report.json`); the s80213 pair is incomplete because robots-s80213 never ran |

**Nuance:** the controller's “94 C0 / 23 C1” is exact only for the **117 decision-limit** subset. Across all **119 completed** runs it is **96 C0 / 23 C1**; the two seed-80201 idle-limit runs are the extra C0 pair. “12-decision stall” means the guard counter `stall.decisions=12`, not 12 total decisions: total decisions range **23–149** (median 45).

**Interpretation:** because 117/119 stops are the new 12-decision branch and both arms are symmetric, C38 cannot establish that the converged base is incapable of C2, and it is not a usable between-arm clearance contrast. It is a stop-contract-limited cohort until rerun under the agreed contract; this audit does not claim the factory would otherwise succeed.

Corroboration: `run-ledger.jsonl` has 119 C38 rows, all `outcome=stalled`, `highest_milestone` C0 96 / C1 23; failure text is “phase C1 unmet” for C0 and “phase C2 unmet” for C1 (the latter is the *next* phase label, not evidence C2 was reached).

---

## 2. Stop mechanism in frozen source

Frozen arm: `data/laya-experiments/laya-c38-2123-miniship-20261026/arms/robots/code` at revision `9764fb68f` (`manifest.json` `repository_revision`; `batch.json:81-83`).

- `training/laya_ops_runner.py:57` — `STALL_TICKS = 18000` (“five game minutes”).
- `training/laya_clean_runner.py:64` — `stall_decisions: int = 12` (shared `Settings` default used by the ops runner).
- `training/laya_ops_runner.py:1672-1683` — idle ticks accumulate as elapsed game ticks minus non-wait tool ticks; the stop is an **OR**:
  `if stall["idle_ticks"] >= STALL_TICKS or stall["since"] >= settings.stall_decisions: stop="stalled"`.
- `training/laya_ops_runner.py:1617-1635` — the reset signals are `research_units`, `machines`, `missing_materials`, `goal_gap` (95% closure), `material_crafts`, `owned_batch`, `installed`, `network`, `ladder`, `funded_plan`. **Raw network stock is not a signal.**
- `training/laya_ops_runner.py:1714-1717` — serialized `stall` block, version `decision-idle-v2`, limits 18,000 / 12.

**Consequence:** for all 117 decision-limit runs, `idle_ticks` at stop was **2,244–14,039** (median **4,476** ≈ 74.6 game-seconds), i.e. the **decision branch, not the five-minute idle branch**, ended them. This OR is a **contract change relative to C37**: C37's frozen `data/laya-experiments/laya-c37-2123-miniship-20261026/arms/robots/code/training/laya_ops_runner.py:1198-1201` is tick-only (`if after["tick"] - stall["tick"] >= STALL_TICKS`), and its runner log ends `STALLED: 18000 ticks without a built operation...` (C37 `robots-s80100/logs/laya-runner.log`, +3456s). The 12 constant still exists in C37's separate `laya_clean_runner.py:64`, but C37's ops runs did not use that branch. C38's 117 decision-limit stops therefore test a stop contract that C37 did not run; do not compare the two stop rates as if the contract were fixed.

The stop is checked **after** a completed decision and final observation query; it is not a mid-tool interrupt. Any live construction would stop being serviced at cleanup, not be killed mid-call.

---

## 3. Representative trace — `robots-s80160` (run `robots-s80160-s80160-1792972320`)

This is one of 18 terminal streaks that begin with the stone-brick op, and one of the 117 decision-limit stops.

**Observation → choice.** At decision 18, tick **259169** (`logs/laya-runner.log:216`), phase C1, stone-brick `0.0/0.25/s` and stone stock 0; four mall assemblers disabled; copper-cable/gear output blocked. The policy chose `op.starter.stone-brick`, **p=0.457**, with facts `stuck=true`, `repeat_fails=3`, `need_no_progress=1`, `available_items={passive-provider-chest:6}`, `missing_items={}`, `last_attempt_available_items={passive-provider-chest:7}` (`decision-states.jsonl`, index 18).

**Operation.** `_bootstrap_direct_plate_line` prints `PLATE STARTER: stone-brick begins …` (`autonomous_builder.py:3430-3439`) then calls `_serve_direct_plate_starter(..., submit=True)` (`:3297-3355`), which calls `assert_affordable(..., reserve_project=True)` (`:3333`) **before** `_submit`. `assert_affordable` (`orchestrator/construction_coverage.py:342-421`) computes the remaining bill, takes `ledger.shortage_targets(name, uncommitted)` (`:410`), and raises `MaterialShortage` (`:417-421`). Outcome: `material_shortage`, `supply_wait`, **no ghosts submitted**, 297 ticks (`decision-states`, index 18; runner log `:217-227`).

Exact message:
`direct_stone-brick_starter needs material the base does not have -- passive-provider-chest: need 2, short -5`
Details: `available.passive-provider-chest = 7`, `required = 2`. The negative “short” is **surplus vs the printed target**, not physical scarcity (`parts_mall.py:97-103`).

**Why the tool refuses despite 7 chests.** The reservation ledger for the run (`server/logs/deterministic-material-reservations/laya-robots-s80160.json`):
- `dependency_budget.targets.passive-provider-chest = 7` (`:14`), with claimants including chemical/mall projects (`:3-13`);
- `chemical_refinery_and_plastic_machines` reserves 1 chest (`:30-34`);
- `direct_stone-brick_starter` requires 1 chest, has reserved everything except the chest, and is `supply_wait` (`:277-299`).

`_rebalance` first funds constructing projects, then holds `dependency_budget - claimed` (7−1=6) from the pool (`material_reservations.py:366-367`); `allocatable_stock` subtracts those holds (`:484-524`). With 7 physical chests, 1 chemical reservation and 6 budget-held, only **1** is allocatable; the refusal target is **2** (the ledger project requires 1; `assert_affordable` adds pending ghosts), so it raises. The option facts at `training/laya_ops_runner.py:671-684` subtract only **foreign project reservations** (1 chemical), not the 6 dependency-budget holds, hence `available_items=6` and `missing_items={}` — a false-feasibility fact. The option also reports `reservation_owner={passive-provider-chest:["direct_stone-brick_starter"]}` (self), while the ledger’s actual `reservation_owners` names only `chemical_refinery_and_plastic_machines`.

**Progress and stop.** The last progress reset was tick **259070** (`progress_guard.last_reset.reasons=["funded_plan"]`, summary line 40), at the end of decision 17’s `temporary_mall_batch` (tick 257017 + 2,053 tool ticks). The terminal streak is decisions **18–29** (12 decisions), tick **259169 → 272314**, observed stop tick **272588**: **13,419 game ticks** (~223.7 game-s), **2,421 counted idle ticks**, wall **+180 s → +295 s** (115 s). The runner log line 364 is `STALLED: 2421 idle game ticks / 12 decisions without a funded submission, work in progress or goal progress`.

During those 12 decisions, live starter production continued and network stock rose: iron-plate **237 → 508 (+271)**, copper-plate **30 → 168 (+138)**, iron-gear **28 → 32 (+4)**, copper-cable 121 → 121; total **+413 items** (`decision-states.jsonl` indexes 18 and 29). Stone-brick stayed 0; `construction_shortfall` was empty. The four mall assemblers remained `disabled_by_control_behavior`; C1 failed `stone_starter_ready`, `at_least_four_working_mall_assemblers`, `iron_gear_producer_working`, `copper_cable_producer_working` (runner log `:363-365`). Summary: 30 decisions, stop `stalled`, C0 only.

**Answer for this run:** no **chosen operation** was cut off — the stone-brick starter failed preflight every time and no ghost was placed. Live **starter production/stock** was cut off after the stop (+413 items in the final window), but that is stock, not goal-rate progress, and the guard does not count raw stock. The decision cap ended a 12-decision retry loop of two stuck options, not an in-flight build.

**Repetition evidence.** The same refusal occurs 8 times in the run (decisions 15, 18, 19, 22, 23, 27, 28, 29), interleaved with 5 transport-belt `temporary_mall_batch` outcomes in the final window. At decision 29 both stuck options still tie at **p=0.457** (weight 3.0); the two wait options are only **0.043** each; facts say `stuck=true`, `no_progress=5`. The first occurrence was decision 15, tick 256149, but decision 17’s funded temporary batch reset the guard at 259070, so the terminal 12-count starts at 18.

---

## 4. Cohort terminal pattern (not just s80160)

For the 117 decision-limit runs, the first no-progress decision of the terminal streak is distributed as:

| First terminal outcome | Runs |
|---|---:|
| `material_shortage` (`supply_wait`) | 41 — 18 `direct_stone-brick_starter`, 7 mall copper-cable, 6 mall transport-belt, 3 mall inserter, others refinery/mall kit |
| `temporary_mall_batch` (`supply_wait`) | 27 |
| wait / no outcome code | 24 |
| `production_prerequisite_deferred` (`planned`) | 12 |
| `rationed_mall_borrow_deferred` | 9 |
| `producing` | 2 |
| `chemical_capability_handoff` | 2 |

This is a family of mall/chest reservation refusals and waits, not one operation. Cross-run observations:

- **105/119 runs** contain at least one material-shortage message with a **negative “short”** (printed target < physical stock); **83/119** have one in the final 12; **908** such messages total. This is evidence of allocation withholding, not proof every line is false (some messages also name genuinely missing items such as drills).
- **All 119** runs had positive network-stock growth over the final window (sum of positive `needs` deltas; median **+592 items**; copper-plate rose in 119, iron-plate in 117). Every completed run therefore stopped while starter production was still accumulating stock. The guard does not treat stock as progress.
- **49/119** runs ended with a non-empty `construction_shortfall`: electric-mining-drill 364, transport-belt 352, splitter 24, fast-inserter 8. These are pending ghost bills at the stop; they are not proof bots were actively building, but they are live commitments left without further servicing.
- Final mall sample: **110/119** runs had `working_sample_share=0.0`; aggregate final status counts were `disabled_by_control_behavior` 119, `working` 9, `item_ingredient_shortage` 7, `no_recipe` 1. C1 was accepted at some checkpoint in **38** runs but final in only **1** — the C1-ever/final gap is a major intermediate outcome, separate from the stop count.
- Both arms are symmetric: robots **47 C0 / 12 C1** + 1 prepare-failed; clearance **49 C0 / 11 C1**. The between-arm clearance factor is not differentiating the stop pattern; the common frozen base (converged main + robot exclusion) or environment is the candidate, not clearance. Do not pool with C37.
- The **2 idle runs** are different: `robots-s80201` and `clearance_robots-s80201` each made 33 decisions, 11 terminal waits (`wait.1800`/`wait.300`), and hit `idle_ticks` 19,654 / 19,750 > 18,000. Both are C0-only with mall cells disabled and no useful action; the five-minute idle branch fired correctly. They are not evidence of a too-early decision cap.

---

## 5. `prepare_failed` — `robots-s80213`

- Timing: preparation started **12:23:30.5**, terminal **12:23:34.6** (4.07 s), no runner, no decisions (`job-status.json:8552`; `batch-events.jsonl:201-202`).
- Wrapper: `logs/robots-s80213.out:6-13` — `run_laya_clean.py:135` calls `run_laya_saved_world prepare`; harness maps non-zero to `RuntimeError: prepare exit 1` (`harness/tools/run_laya_batch.py:322-327`, `:366`).
- Exact inner failure: `arms/robots/code/data/laya-saved-world/laya-c38-2123-miniship-20261026/robots-s80213/server/factorio-current.log:51-54`:
  `Opening socket at 127.0.0.1:39912` → `Hosting game at 127.0.0.1:39912` → **`Error CommandLineMultiplayer.cpp:360: Hosting multiplayer game failed: Can't bind socket: Address already in use`** → quit.
- Port formula: `39900 + slot 12 = 39912` (`batch.json:94`; manifest `game_port:39912`). Historical 39912 users in C38: `robots-s80166`, `clearance_robots-s80181`, `clearance_robots-s80197`, plus the failed job. All three completed users’ server logs end `Socket closed` / `Goodbye` and have no `factorio.pid`; the last, clearance-s80197, exited **12:14:25**, ~9 minutes before the failure.
- No concurrent C38 job had 39912 in its manifest at 12:23:30 (all 120 manifests checked); there was no duplicate attempt. C37/C36 port bases are 39800/39700, so a C37/C36 game server is not the holder.

**Evidence limit:** the manager’s preflight `port_available` (`scripts/manage_linux_deterministic_server.sh:413-434`) passed — otherwise it would have died with “game port … already in use” before Factorio — and Factorio failed its bind 1–2 s later. Preserved evidence proves a **port-39912 bind conflict**, but does not identify the holder (no process/`ss` capture was taken, and the read-only scope forbade a new probe). Clean prior shutdowns argue against an orphan from those worlds; a race with an unrecorded process is the remaining hypothesis. The clearance-s80213 sibling completed 72 decisions, C0-only; its pair is excluded, leaving 59 matched pairs.

---

## 6. Previous-cohort comparison (mechanism only; no pooling)

C38 seeds **80160–80219**, C37 **80100–80159**, C36 **80040–80099** — **no shared seed**, so there is no matching-seed comparison. Revisions differ: C37 robots `32c1ef5` / control `d9402dab1`; C38 both arms `9764fb68f` plus arm patches (`batch.json:76-92`). The stop contract also differs: C37 ops runs used the tick-only 18,000 rule; C38 uses the 18,000-or-12-decision rule. This is a second uncontrolled boundary, independent of arm and seed.

One mechanism contrast is still informative: C37 `robots-s80100` (479 decisions, reached C2/C3/C4) also chose `op.starter.stone-brick` first at decision 12, tick 250777, p=0.727, but failed on a **real** shortage of 6 electric mining drills; by decision 34 it needed 2 and was short 1, and progress resets kept the run alive. C38 `robots-s80160` fails on a **reservation** shortage (printed target 2, physical 7, allocatable 1), with no craft progress and only 12 decisions to stop. This contrast is descriptive only: different seeds, revisions and windows, so it cannot attribute the C38 regression.

For context, C37 completed 120/120 (robots: 58 stalled, 2 window_timeout; C2 50/60), while C38 is 119/120 and C2 is 0/60 in both arms. Do not pool these cohorts.

---

## 7. Findings, hypotheses and uncertainties

**Verified evidence**
1. 120 planned = 119 completed + 1 prepare-failed; 117 decision-limit stops = 94 C0 + 23 C1; 2 seed-80201 idle stops; 96 C0 / 23 C1 over all completed runs; no C2.
2. The 12-decision branch, not 18,000 idle ticks, ended 117/119 completed runs; median idle at stop was 4,476 ticks (~75 game-s).
3. In s80160, the chosen stone-brick op was refused before submission because the ledger allocated only 1 of 7 physically present passive-provider-chests; the option fact said 6 available. No ghost was placed; 12 no-progress decisions then stopped the run while +413 starter items accumulated.
4. Across all 119, stock was still rising at stop; 49 had pending construction shortfalls; 110 had zero working mall sample; C1 was ever accepted in 38 but final in 1.
5. The prepare failure is an `EADDRINUSE` on 39912 with no run and no decisions; the prior clean users of that port are documented.

**Causal hypotheses (not proven by this audit)**
- **H1 (strong):** the option-fact layer exposes transferable stock but not dependency-budget holds (`laya_ops_runner.py:671-684` vs `material_reservations.py:484-524`), so a blocked op looks feasible (`available=6`, `missing={}`), is selected repeatedly, and the 12-decision cap stops the run. A fact-level fix (allocatable/reserved facts before new rules) is the smallest candidate, but the policy also keeps a `stuck=true` op at p=0.457, so a rule/weight fix may be needed independently.
- **H2 (moderate):** the 12-decision OR is new relative to C37's tick-only ops stop, and it binds at median ~75 game-seconds of idle time while production is live. That makes C38's stop rate a stop-contract result, not a like-for-like C37 comparison. Whether the OR is a deliberate contract or an accidental merge is for the parent's code-history audit; this audit only shows it was not active in the C37 frozen ops runner.
- **H3 (moderate):** `disabled_by_control_behavior` may be a false-negative for gated/full mall cells: C1 v2 counts `working`, `full_output`, or a recent craft (`checkpoint_milestones.py:449-460`), not `disabled_by_control_behavior`, while the builder treats that status as healthy idle (`autonomous_builder.py:5341-5349`). This could explain the C1-ever 38 → final 1 gap; it does not explain C2=0 and needs predicate review.
- **H4 (weak):** the prepare failure is a host-level port race; holder unknown.

**Uncertainties**
- No process table or port capture at 12:23; the 39912 holder cannot be named. Prior server logs show clean shutdowns.
- Stock deltas come from `needs[].stock` in decision states; they show live production, not goal-rate service. They should not be read as sustained C1/C2 service.
- `short -N` is a target-minus-stock print, not a per-item allocation audit; messages can mix a surplus item with a genuine shortage of another item.
- The run ledger’s “phase C2 unmet” is the phase label after C1, not a C2 reach.
- No RCON/live probe and no replay were performed; the audit cannot distinguish a stale process from a new port grab, nor prove the C1 predicate would change the stop.

**Lifecycle:** no action taken. No code, commit, branch, service, schedule, world, save or installed mod was changed. If a fix is adopted, queue it behind the active campaign and follow the adoption rule; that is outside this read-only audit.
