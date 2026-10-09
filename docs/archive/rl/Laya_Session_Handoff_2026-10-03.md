<!-- Path: docs/archive/rl/Laya_Session_Handoff_2026-10-03.md | Purpose: Preserve user intent, verified results, unfinished saved-world/mall work and restart instructions after an explicit stop. -->

# Laya session handoff — October 3, 2026

## 1. Stop state and first instruction for the next session

**The user explicitly stopped all ongoing work and requested this handoff. Do not start implementation, servers, collection or training merely because this document lists next steps. Resume when the user asks.**

- Repository: `/home/djsm/Github_Desktop/Factorio_Cursor_RL_Agent`.
- Latest committed milestone: **`7404a3d`**, `feat(RL Laya): add disposable saved-world mechanics adapter`.
- Important unfinished work is preserved in the worktree; **it has not been committed or deployed**.
- No current collection/training job was launched by the unfinished work. Both attempts to launch the new `kit-pilot-20261003` server were rejected before execution by an unavailable automatic approval service. Its directory does not exist.
- At handoff inspection, **no Factorio PID files remained** under either `data/laya-saved-world/*/server/` or `data/laya-playground-tests/*/server/`.
- Earlier saved-world test servers were explicitly stopped. Original save preservation was verified after those tests.
- The prior daytime training queue records `phase=complete`, updated October 2 at 11:05 IST. A fresh host-wide service/GPU check was unavailable; do not infer current global process state from that historical record.
- The independently managed Laya Operations Console may still be up at `http://127.0.0.1:9138/`. It was not stopped as part of this latest implementation task. No new work is chained by this session.

## 2. What the user asked for

### Overall product objective

Replace the brittle deterministic strategy runner with **Laya**, a fast local decision maker that learns from measured outcomes in disposable Factorio worlds:

- what to build next;
- whether and where to expand;
- which variant/scale to use;
- when to wait;
- how to identify bottlenecks and build/upgrade around them;
- eventually operate a self-expanding, self-sustaining factory and improve through further measured training.

The menu grows through curriculum levels toward the full workflow. The deterministic runner remains the working reference/rollback. Production promotion still requires a measured win against the actual deterministic workflow on matched held-out scenarios, not just the lab rollout.

The user originally asked to read `docs/archive/rl/Laya_Session_Handoff_2026-09-30.md`, audit the full Laya workflow and training process, identify significant improvements and limitations, and start implementing the improvements one by one. They subsequently authorized bounded background training, pause/resume, and an extension until 14:00 on October 2. Those deadlines are historical and do not authorize another unattended run now.

### Most recent sequence

1. Before starting further new work, **test Laya on `saves/mod_playground.zip` like the deterministic runner**.
2. When the missing saved-world adapter was identified, **start implementing the next steps**.
3. Ask for status, then authorize continuing toward measured alternatives, compatible training and a matched comparison.
4. Clarify the actual `mod_playground` layout and its starter kit, with a quad mall and a path to plastic/advanced circuits and independent expansion.
5. **Also simulate a mall environment and priority setup** with a separate specified kit, to learn **self expansion, what to build and when to build**.
6. Explicitly **stop everything being worked on and produce a handoff for a new session**.
7. Answer the pending mall questions while work remains stopped:
   - Plate input: **“Linearly increasing plate supply.”**
   - Output chests: **“Yes, 20 passive-provider chests.”**

Those final answers are authoritative and are recorded here; they did not revoke the stop instruction.

8. After the handoff was requested, the user proposed an alternative supply mechanism:
   **“every time it makes 2 mines, 10 belts, 4 inserters, 1 splitter the supply of iron or copper increases by 1.5.”**
   Record this latest option for the mall environment. It does not authorize restarting work.

## 3. Two distinct starter contracts — do not merge them

### A. Actual `mod_playground` opening

Existing infrastructure, retained from the supplied save:

- one electricity source;
- four placed substations;
- one roboport, with **50 logistic robots and 50 construction robots**;
- one placed storage chest for deconstruction returns;
- a placed passive provider containing the construction kit;
- an accumulator also exists in the observed save.

Provider construction stock specified by the user and confirmed by the earlier read-only save inspection:

| Item | Count |
|---|---:|
| Electric mining drill | 5 |
| Electric furnace | 30 |
| Medium electric pole | 50 |
| Substation | 20 |
| Regular electric inserter | 20 |
| Assembling machine 1 | 6 |
| Requester chest | 6 |
| Passive provider chest | 16 |
| Roboport | 25 |

The user describes a **12-slot quad mall**: start with four occupied horizontal left/right positions, then use manufactured long-handed inserters to open top/bottom positions and continue filling the bank. Six assemblers are supplied; twelve occupied slots require making six additional assemblers.

The intended initial furnace allocation is **12 iron / 12 copper / 6 stone**. This is a reference allocation and capability to expose/measure, not a hard-coded learned build order. Five supplied drills cannot saturate all 30 furnaces; manufacture/allocate more drills as the measured demand requires.

The user expects this kit to reach plastic and advanced circuits, manufacture further infrastructure and become self-sustaining **without additional gifted items**. This is intended acceptance, **not an achieved result**.

The save has substantial extra equipment and plates in a disconnected player's inventory. **Do not borrow/transfer them to make the test easier.** Funding uses actual provider/storage stock only, excluding requester/buffer WIP, player inventory and flying-bot cargo.

### B. New mall simulation requested last

A separate disposable scenario, with one starter chest containing:

| Item | Count |
|---|---:|
| Requester chest | 10 |
| Passive provider chest | 20 |
| Assembly machine | 6 |
| Inserter | 12 |

- The first confirmed option was **linearly increasing iron/copper plate supply**. Initial rates, slopes, limits, proportions and demand schedules have not been specified or implemented.
- **Latest alternative: earned supply upgrades.** Each complete manufactured batch of **2 electric mining drills + 10 transport belts + 4 inserters + 1 splitter** increases **iron or copper supply by 1.5**. This ties expansion to making useful infrastructure rather than only waiting for a time ramp.
- Unspecified details to resolve on resume: whether `1.5` means an additive **1.5 plates/s** or a **×1.5** multiplier; who chooses iron versus copper; whether the batch is consumed, deployed, or just manufactured; initial supply rates and maximum rates. Do not silently invent these as confirmed user requirements. The two supply mechanisms are alternatives, not an instruction to apply both simultaneously.
- Proposed accounting: count actual newly crafted output after episode start, never seeded inventory or item transfers. Each complete batch earns one upgrade exactly once; preserve partial-batch remainder and prevent double-credit. For self-expansion priorities, expose earned upgrade choices and their predicted supply effects to Laya. This accounting/design has not been implemented.
- The output chests are explicitly **passive-provider chests**.
- Existing power/bot infrastructure was proposed for this simulation; no new fixture was created. Reuse the known power/robot scaffolding as a declared disposable scenario fixture, not as an unrecorded source of free construction materials.
- Its purpose is to learn production priorities, recipe changes, when additional capacity is useful, manufacturing expansion materials, timing and waiting under a limited initial budget.
- Keep this supplied-input mall simulation separate from the original save opening and its promotion evidence. A good mall simulation score is not proof of full Factorio self-sustainability.

## 4. Prior training and audit progress inherited into this session

Read these existing documents for detailed history rather than reconstructing from scattered logs:

- `docs/archive/rl/Laya_Session_Handoff_2026-09-30.md`
- `docs/archive/rl/Laya_Background_Run_2026-10-01.md`
- `docs/archive/rl/Laya_Regression_Repair_2026-10-01.md`
- `docs/archive/rl/Laya_Day_Queue_2026-10-02.md`
- `docs/archive/rl/Laya_Level3_Live_Verification_2026-09-30.md`
- `docs/rl/Laya_Workflow_Audit_2026-09-30.md` and other audit documents, if needed. Several are untracked work belonging to prior sessions; preserve them.

Implemented prior improvements include:

- stepped labelling and policy iteration;
- completion-first counterfactual labels; cheap timeouts cannot beat completing alternatives;
- quarantine of tool/validator/environment failures from policy preferences;
- input-profile compatibility guards; health-bearing states cannot silently change old checkpoints;
- all-level features-teacher admission, rather than accepting a teacher that improves one level and breaks another;
- episode-relative production sampling and exact paused-endpoint catch-up, removing artificial initial-wait reward;
- corrected recent-data filtering, final training reserve, configurable matched evaluation cohorts;
- features set 7 with numeric option × context interactions, allowing demand to affect variant ranking;
- a bounded daytime queue with pause/deadline/ownership handling.

Relevant prior commits: `1f80b27`, `f7126fc`, `25f1a36`, `f2f2600`, `a6024be`, `b12bb55`, `eea5900`. The September 30 handoff describes the earlier twelve-commit tranche from `c8b5b8f` to `880efd8`.

### Latest completed student and independent lab evaluation

Checkpoint:
`data/laya-checkpoints/laya-day-20261002-learning-r21-final`

Independent lab seeds: **64001–64032**, 32 seeds per level. Costs below are **means**, not the original handoff's medians.

| Level | Scripted completion | Laya completion | Scripted mean cost | Laya mean cost | Laya mean change |
|---|---:|---:|---:|---:|---:|
| 1 | 32/32 | 32/32 | 6,515 | 6,568.28 | +0.8% |
| 2 | 23/32 | 23/32 | 17,711.25 | 18,015.16 | +1.7% |
| 3 | 32/32 | 32/32 | 12,213.75 | 13,188.59 | +8.0% |

- Zero runtime errors, zero safety violations and no completion losses on that cohort.
- Some medians improve, but means/tails do not. **No promotion.** These are lab diagnostics, not full deterministic-workflow results.
- The reused development cohort looked better; independent results expose a generalization gap.
- Features teachers failed their global gates; the collection continuation stayed scripted.
- An optional throughput pilot failed its configuration guard (context above 1,024 requires micro-batch 1 plus gradient checkpointing); no pilot training occurred.

Evidence:
`data/laya-pi/day-20261002-queue/status.json`, comparison logs in that directory,
`data/laya-pi/day-20261002-learning/summary.log`,
`data/laya-pi/background-20261002-resume/summary.log`.

Do not compare checkpoint round numbers across old/new runs as though they were one monotonic quality ranking.

## 5. Completed work in the latest saved-world tranche

### Read-only exact-save preflight

Evidence:
`data/laya-playground-tests/preflight-20261002/REPORT.md`

- Loaded an isolated source-save copy with frozen installed project mods, Factorio 2.1.20, Nauvis/player and loopback ports **34198/27018**.
- Observed the starter network, inventory, researched technologies, resource patches and existing electricity source.
- Established that the then-current Laya runner only executed training-cell families and had no saved-world adapter. Loading the save was not called Laya gameplay.
- Preserved source save/GUI/production runtime. Stopped the disposable server.

### Committed adapter — `7404a3d`

Implementation and documentation:

- `training/laya_saved_world.py`
- `training/laya_saved_world.lua`
- `tools/run_laya_saved_world.py`
- `schemas/laya_saved_world.schema.json`
- `training/laya_input_profile.py`
- `tests/test_laya_saved_world.py`
- `docs/rl/Laya_Saved_World_Adapter.md`
- `docs/rl/README.md` and one isolated `CURRENT_STATUS.md` milestone

Capabilities verified at the committed milestone:

- Separate disposable roots below `data/laya-saved-world/` and explicit OS process/save/mod/config/port identity checks.
- Immutable baseline/source hashes; frozen installed mods. **Fleet mode** avoids GUI syncing and repository-save snapshots.
- Read actual network provider/storage funding; subtract outstanding ghost bills. Preserve player/WIP stock separately.
- Compile unranked mining, furnace, gear/belt assembly, bot-coverage expansion and wait options.
- Validate internal footprints, live collisions, construction/logistic coverage and funds again immediately before submission.
- Execute only atomic funded ghosts through the existing layout executor; configure observed bot-built entities.
- Reconcile already selected pending configurations; never replay ambiguous interrupted submissions.
- Exact engine-tick stepping, independent production/consumption counters, durable pre/post receipts and measured feedback.
- New `saved-world-v1` input profile. `r21-final` refuses it before connection/inference; no metadata relabelling or hidden deterministic fallback.

### Live mechanics verification — not Laya decisions

Evidence:
`data/laya-saved-world/adapter-verified-20261002/verification.json`, receipts and logs.

| Explicit replay | Window | Outcome |
|---|---:|---|
| Mining | 60 game seconds | 37 iron ore; working miner |
| Smelting | 120 game seconds | 34 iron plates; working furnace |
| Gear assembly | 120 game seconds | 53 gears |
| Belt assembly | 120 game seconds | 36 belts |
| Coverage expansion | 60 game seconds | Second powered roboport; one connected logistics network |

All ended with zero pending ghosts. Windows include construction/configuration and do not establish steady-state joint capacity.

An earlier failed smelting test exposed reversed inserter geometry. The generator was fixed and retested from a fresh copy. Failed evidence remains in `data/laya-saved-world/adapter-smoke-20261002/`.

- Committed milestone: **21 focused tests passed**.
- Real tokenizer: final state with measured flows used **1,532/2,048 tokens**, 10 options, no model weights loaded.
- Both source and immutable baseline remained unchanged.
- Source SHA-256: `9763436071d2ad138e2844a81c7bbd6891218f612c9bf69dc5bede6517c6d5bb`.
- Graceful Factorio shutdown saves the advanced disposable copy; a changed copy hash after shutdown is expected and is recorded separately.
- The copied save has 253 researched technologies and the supplied electricity interface; do not claim this tests a natural research/power opening.
- **No saved-world Laya gameplay, compatible new checkpoint, full baseline comparison, plastic/advanced-circuit production or self-sustaining base was achieved.**

## 6. Unfinished work at the explicit stop

These edits implement the next tranche, but are **unit-tested only**. They have not passed live Factorio verification and must not be treated as the verified `7404a3d` behavior.

### New, untracked files belonging to this task

| File | Purpose/status |
|---|---|
| `training/laya_starter_kit.py` | Exact specified provider budget, infrastructure/bot checks, 12-slot/six-supplied-assembler distinction; grants no items |
| `planners/laya_quad_cell.py` | Select one slot from shared quad geometry; horizontal regular inserters, vertical long arms; shared requester sections/providers |
| `training/laya_saved_counterfactual.py` | Replay fingerprint, common sustain assessment, completion-first case conversion and ancestry-grouped splits |
| `tools/collect_laya_saved_world.py` | Sequential forks of one paused prefix, common measurement horizon, durable alternatives/labels/cases, stop on first tool failure |
| `tests/test_laya_saved_counterfactual.py` | Budget, quad geometry, replay matching, all-failed exclusion, completion-first and sustain-window tests |

### Existing files modified by this task after `7404a3d`

- `training/laya_saved_world.py`: shared quad slots, recipe-driven assembly choices, stone/steel furnace choices, physical network anchors, material-bill features, compact flow fields, prefix-save capture; menu reduced to six builds plus two waits.
- `training/laya_saved_world.lua`: live enabled recipe facts, infrastructure counts, expanded engine counters, exact existing-entity funding credits, stable network-anchor resolution and recipe checks.
- `tools/run_laya_saved_world.py`: preparation writes a starter-kit check.
- `schemas/laya_saved_world.schema.json`: recipe/infrastructure facts and optional machine recipe. Large diff is partly reformatting.
- `training/laya_dataset.py`: public `validate_case` seam using existing schema/target checks.
- `tests/test_laya_saved_world.py`: updated for quad action generation.

Latest executed verification:

```bash
.venv/bin/python -m pytest -q   tests/test_laya_saved_world.py   tests/test_laya_saved_counterfactual.py   tests/test_laya_input_profile.py --tb=short
```

Result: **32/32 passed**. This does not prove the new Lua recipe APIs, live chest reuse, quad transfer geometry, prefix restoration or full collector work in Factorio.

### Important unfinished collector details

- Default proposed pilot is iron-ore at 0.1/s, 7,200-tick common horizon, 1,200-tick sustain interval sampled every 300 ticks, four seeded menus.
- Each alternative must restore the exact paused start fingerprint, including tick; physical port positions normalize volatile logistic-network IDs.
- Full alternatives/receipts survive exclusion. Missing/mismatched/tool-failed/all-failed comparisons must not turn into positive labels.
- One source save and all its prefix siblings stay in **one dataset split**. Menu randomization is not an independent world. The current collector writes **train-only** cases for this lineage.
- Therefore it is **not sufficient by itself for fine-tuning**: the trainer also requires genuinely distinct calibration coverage for `saved-world-v1`. Never split sibling rows just to make training run.
- The collector has not run at all. No new cases/checkpoint exist.
- The new mall simulation, either supply scheduler (linear ramp or earned infrastructure batches), mall demand/priority observations and reward/acceptance contract have **not been implemented**.

### Blocking operational issue

Two attempts to run:

```bash
.venv/bin/python tools/run_laya_saved_world.py prepare   --root data/laya-saved-world/kit-pilot-20261003
```

were not executed. Automatic approval review failed first with a disconnected response stream, then with **HTTP 503: session bridge cooling down after repeated upstream timeouts**. This was a review-service failure, not a finding that the operation was unsafe. Do not bypass approval checks. The root does not exist; no server from these attempts needs stopping.

## 7. Proposed plan when the user resumes

1. **Inspect and finish the pending adapter tranche.** Preserve the worktree. Review the new Lua APIs, exact existing-entity credit and shared requester-section behavior. Run the already narrow tests; then deliberately load a fresh owned test root when the approval service is available.
2. **Validate the actual starter contract live.** Check the exact provider kit, placed power/substations/roboport/storage and 50+50 bots. Do not use player inventory or grant missing materials. Record any mismatch.
3. **Validate quad mechanics and prefix restoration.** Build individual left/right slots, manufacture long-handed inserters from real inputs, then build top/bottom slots. Prove shared chest configuration preserves other recipes. Test a saved paused prefix/fork before a batch. Fix mechanics before collecting labels; do not manually repair a world to conceal a generator bug.
4. **Implement the separate mall scenario requested last.** Exact kit: 10 requesters, 20 passive providers, six assemblers, 12 inserters. Implement a bounded supply mechanism from the user's alternatives: the earlier linear ramp or the latest earned upgrades for each batch of 2 drills/10 belts/4 inserters/1 splitter. Resolve the 1.5 unit/multiplier and upgrade semantics before dependent implementation. Add explicit competing demand/expansion obligations. Log fixture-delivered inputs separately from factory outputs, crafting and construction consumption; seeded stock must not earn upgrades.
5. **Expose priority/timing observations and alternatives.** Include current/forecast plate rates, ramp slope or earned-upgrade progress/choices, stock vs reserved/WIP, each product's demand/gap, measured production, machine starvation/blockage/power, bot/coverage state and expansion material shortfalls. Expose recipe changes, add/scale capacity, manufacture construction items, quad slots and waits. Legality can prune; it must not choose priorities.
6. **Measure competing actions under matched continuations.** Hold prefix, supply mechanism, demand schedule, mods, clocks and budgets fixed. For earned upgrades, hold the batch rule and initial supply fixed; policy-earned supply increases are measured consequences, not reset between actions. Use completion-first joint-service acceptance and decomposed delay/material/idle/starvation costs. Do not reward empty cheap plans, duplicate inactive machines or repeatedly waiting while demand remains unmet.
7. **Build compatible training/calibration cohorts.** Keep all same-world/prefix relatives together. Obtain independent starter-world/scenario lineages for calibration and untouched evaluation. Retain lab regression cases; check real tokenizer/head budgets before GPU work. Do not edit old checkpoint metadata to assert support.
8. **Train/calibrate a challenger, then actually run Laya on a fresh `mod_playground` copy.** Keep checkpoint lineage, raw decisions, errors and measured outcomes. Bound decision/time budgets. Model failure/uncertainty must stop safely with no deterministic fallback.
9. **Broaden toward the user's full acceptance.** Add scale choices and continuous transport, raw foundations, repair/deconstruction with storage returns, manufacturing every construction item, actual power/coverage expansion, oil/plastic/advanced circuits and longer self-sustain runs. The 12/12/6 furnace reference must be executable; its usefulness must be measured against ore supply and demand.
10. **Run the real matched head-to-head gate.** Actual maintained deterministic workflow and Laya start from identical save/stock/research/scaffolding/objectives/budgets/mod versions and matched paused timing. Mall supplied-input simulations and lab rollout wins cannot substitute. No production promotion without explicit approval, validation and rollback.

Do not automatically launch an overnight job or reuse the old 14:00 deadline. The user has stopped work.

## 8. Repository and operations precautions

- Read `AGENTS.md`, `docs/system_invariants.md`, `docs/rl/README.md`, `docs/rl/training.md`, `docs/architecture.md` and `docs/factorio_operations.md` as routed; source/history in this handoff does not override them.
- Keep real Nauvis, disposable RL and legacy autonomy isolated. Never redirect the deterministic entry point or call its strategy cascade behind Laya.
- Use `--fleet-mode` for the disposable deterministic server manager. Ordinary `start` snapshots into the repository save and can synchronize GUI mods; that would be wrong here.
- Existing installed shared-mod fixes are preserved by copying installed mods, not silently deploying dirty repository Lua.
- New saved-world Lua is a Python-owned RCON helper, not a deployed mod. Restart its Python consumer after edits. Changes to actual `factorio_mod`/`factorio_training_lab` require deliberate mod deployment and runtime reload.
- No subagents were spawned for this latest task. Do not spawn them unless explicitly authorized by the user or applicable instructions.
- Do not expose RCON secrets or complete Factorio process arguments. Secrets are server-local mode-600 files.
- Scope commits to owned verified work. Do not commit the unfinished tranche until live blockers are resolved and required verification is complete. This handoff is intentionally uncommitted.

### Unrelated dirty work to preserve

Pre-existing modifications include `CURRENT_STATUS.md`, `docs/deterministic/planning.md`, `factorio_mod/README.md`, `factorio_mod/trash_requesters.lua`, `orchestrator/bootstrap_steam_power.py`, **`saves/mod_playground.zip`**, `tests/test_infrastructure.py`, `tests/test_laya_evaluate.py`, `tests/test_laya_state_budget.py`, `tests/test_observatory_control.py`, `tools/README.md`, `tools/training_observer.html`, `tools/training_observer.py`, and `training/observation.py`.

Many untracked audit/optimization/handoff documents, observer assets, scripts, `systemd/`, `training/laya_control.py` and other tests also predate this tranche. Do not clean, reset, stash or stage them wholesale. The repository save was already modified before this work; its recorded hash, not an assumed clean Git state, is the test input.

## 9. Suggested next-session prompt

> Read `docs/archive/rl/Laya_Session_Handoff_2026-10-03.md`. Resume the pending saved-world/quad work and the separate mall simulation. Preserve unrelated changes. The mall kit is 10 requesters, 20 passive providers, six assemblers and 12 inserters. The user first chose linearly increasing plates, then proposed an alternative: each manufactured batch of 2 drills, 10 belts, 4 inserters and 1 splitter raises iron or copper supply by 1.5. Resolve the units and upgrade semantics before implementing that mechanism. Laya should learn production priority, timing and self expansion from measured outcomes. Validate the uncommitted mechanics live before collecting training labels. Keep copied-save gameplay, mall simulations and production promotion evidence distinct.
