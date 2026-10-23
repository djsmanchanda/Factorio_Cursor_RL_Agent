# Path: Factorio_Cursor_RL_Agent/CURRENT_STATUS.md
# Purpose: Recent project status log. First thing any agent reads when resuming work.

# CURRENT_STATUS

Append-only. Newest entries at the bottom. Entries older than about two weeks
move to `docs/archive/status/<YYYY-MM>.md` (currently everything before 2026-09-25).

## [2026-09-25] Laya session handoff reconciled
- Files: docs/archive/rl/Laya_Session_Handoff_2026-09-25.md
- What: Consolidated the user's intended authority split, deterministic coexistence rule, complete Laya architecture, implemented foundation, operational workflow, evidence limits, training recovery state, and next milestones. Reconciled the v2 training artifact with process state rather than trusting its stale status file.
- Evidence: The v2 mining-selector process is absent and has no final adapter/evaluations; last telemetry reached optimizer step 123/188, while the newest complete atomic recovery snapshot is step 100. The temporary cleaned dataset is absent and must be rebuilt before resume. No runtime, save, model, service, or deployed mod was changed while producing the handoff.
- Lifecycle: none. GPU training may be resumed from step 100 after rebuilding the 2,111-case dataset; Factorio and the deterministic workflow remain unchanged.

- 2026-09-25: Migrated the isolated deterministic server and four disposable native RL workers to private Factorio 2.1.20 headless runtime (`build 87512`). Managers, campaign/dashboard defaults, current docs, active fixture versions, and persisted read-data configs now agree. The 2.1.20 changelog removals are guarded: the training Lua does not read `LuaPlayer.spectator` or `fuel_category`; spectator-controller assignment remains in use. Evidence: 33 focused tests, Python/shell/Lua syntax checks, matching deployed mod trees, 2.1.20 launch banners for all five instances, and read-only RCON tick probes (deterministic `2075404`; workers 01–04 `6468928`, `749758`, `751062`, `750269`). GUI mod copies were synchronized; deterministic server and all four workers are running, while the deterministic Python runner remains stopped.















## [2026-09-25] Laya L0 exploration loop live on 2.1.20
- Commits: 64ff454, 7b83d6e, c2735f1, 3bc5f1b, 1f1a4dc, b2ebbb6, 0910a72.
- What: separate `laya-exploration-disposable` gate (untrained base checkpoint drives, seeded epsilon, no capability manifest, never comparison/label evidence); laya pinned 0.3.20; live-found fixes for ready->running staleness, reselect recovery, remaining-budget pruning, reversed furnace inserters and infeasible furnace targets.
- Evidence (observed live, worker training-linux-04): mining 2/5 then furnace 4/5 completed under exploration; 193 journaled decisions with full option sets in data/laya-exploration/. One mining select_candidate overflowed the 1,024-token v2 limit.
- Next: L1 dataset rebuild (outcome-ranked history + these journals), finetune fixes, fresh run; size context from measured state lengths.

## [2026-09-27] Laya connected-workflow evidence contract
- Files: training/laya_{work_graph,supply_evidence,branch_decisions}.py, focused tests, docs/rl/training.md.
- What: added measured branch lifecycle, prerequisite/peer and blocker projection, per-item stock/rate/committed-bill evidence, and bounded disposable `serve_goal` choices with stale-evidence rejection. Current primary runner and model capability scope are unchanged.
- Evidence: 34 focused tests pass, including the belt/gear/iron plus independent construction case. No live episode, model training, deployment, or production authority change was performed.
- Lifecycle: none. A compatible model and connected disposable operation handlers are still needed before this choice can execute in Factorio.

## [2026-09-27] Disposable workflow report seam
- Files: training/laya_{workflow_observation,lab_workflow}.py, factorio_training_lab/{episode_measurement,scenario_validation}.lua, schemas/training_scenario.schema.json, tools/inspect_laya_workflow_report.py, focused tests and docs.
- What: isolated training reports now expose bounded per-item production counters and measured rate windows; explicit workflow item requests cover iron, gears, and belts. The read-only adapter turns those reports into branch/supply evidence and a bounded `serve_goal` question, preserving unknown facts and rejecting stale or cross-scope choices.
- Evidence: 92 focused tests pass, 26 optional Lupa tests skip; Lua syntax and the CLI Lua behavior checks pass. No live episode or model training was run. Gear and belt execution handlers remain to be built.
- Lifecycle: synchronize changed training-lab Lua to affected worker and GUI mod copies, then restart affected Factorio runtime and Python consumer before live use. No deployment or restart performed.

## [2026-09-27] Connected workflow runs live under serve_goal
- Commit: 57e7ced (builds on Codex 6d1c4d5/abad687).
- What: connected_workflow lab family (iron -> gears -> belts + independent construction), serve_goal over the measured work graph, lab report/idempotency fixes.
- Evidence (observed live, worker 01, exploration gate): cw2-401 completed with all four branches closed in 6,520 ticks; construction was built while iron proved itself; gears/belts offered only after their prerequisites closed. serve_goal states peak at 1,000/1,024 tokens.
- Lifecycle: training-lab mod redeployed to worker 01 and the GUI profile; worker restarted.

## [2026-09-27] Laya outcome-learning loop end to end
- Commits: 25a5d94..a9fae41, 12b0d12 (+ helper fine-tune fixes fa41b65).
- Parallel batches at 8x (32 episodes/115 s), deeper build-variant layer, counterfactual labels (398 faithful, noise ~180 ticks), 2048-context training, held-out evaluation.
- Held-out (32 seeds): features-only policy trained on counterfactual labels beats the rollout on 30/32 (median cost 5,806 vs 7,751); Laya v1 26/32, median 14,279 (wait.1s loop; needs policy iteration + decision cost).
- Lifecycle: training lab redeployed to workers 01-03 (and GUI profile); workers 01-03 running at speed 1.






## [2026-09-29] Stepped policy iteration beats the scripted rollout (overnight)
- Commits: c8b5b8f (16-slot, policy-iteration labels, decision cost), 7f51f6b (stepped paused-clock episodes), 7fbdcad (demand-scaling level 2), 2ed47e6/e72468c (demand + time features, phase-independent rate windows, overnight driver).
- Evidence (observed live, 12 stepped workers, held-out seeds 4001-4032): features policy after 9 rounds beats the rollout on 32/32 seeds at both levels (L1 6,628 vs 6,854; L2 7,719 vs 8,093, mean -1,173 ticks; completions equal). Laya r4 fine-tune 32/32 L1 and 28/32 L2 (27/32 faster); later Laya fine-tunes on all rounds regressed into wait loops on L2 (r8: L1 median 6,075, L2 1/32).
- Next: Laya fine-tune on policy-iteration rounds only (r3+), more rounds from features-ov-r9. Results: data/laya-pi/overnight/summary.log.
- Lifecycle: training-lab mod redeployed to workers 01-04, 06-13; workers stopped by the driver.

## [2026-09-29] Laya beats the rollout once labels match live inputs
- Commits: f0d7f61 (shared guideline context for labels and live runs; offline evaluator option order), 00a5586, 1c0252c (Laya Operations Console on :9138).
- Evidence (observed live, stepped, held-out seeds 4001-4032): Laya r16 (trained on guideline-matched rounds 12-16) L1 32/32, median 6,163 vs rollout 6,854 (30/32 faster; best of all policies); L2 28/32, 7,888 vs 8,093 (26/32 faster). Features r17: 32/32 faster at both levels (L2 7,568). Before the guideline fix Laya looped on waits live despite 52-57% offline top-1.
- Level 3 (circuits competing for iron) is uncommitted: the second furnace does not raise plate output at level 3 (0.6/s), 6/8 seeds time out; needs a live diagnosis on worker 05.
- Lifecycle: next run resumed from r17 at 20:50 (7.5 h, 12 workers).

## [2026-09-30] Laya workflow audit completed
- Files: docs/rl/Laya_Workflow_Audit_2026-09-30.md.
- What: audited observation, options, replay labels, training, measurements, evaluation and unattended control; proposed a staged path through bottleneck recovery, finite construction economy and matched deterministic promotion.
- Evidence: reanalysed stored disposable runs and labels; r28's lower medians hide worse mean costs, and 269/618 round-27 labels have only non-completing alternatives yet teach full-weight waits. Identified latched L3 objectives, ineffective shared-supply expansion, transient ground items counted as infrastructure, and absent upgrade/repair authority. Twenty-two focused existing tests pass; no new live episode or GPU run.
- Lifecycle: none. Audit only; no worker, save, installed mod, service, active training configuration or production authority changed. No diagnosis-only commit created.

## [2026-09-30] Completion-first Laya training evidence implemented
- What: v4 replay outcomes separate completion, policy timeout and non-policy failure; unresolved all-failed preferences are excluded. Historical cases require verified re-export, features training respects weights/train splits, complete-cohort comparisons report mean/tails and completion losses, and future driver launches retain continuations on blocked comparisons and pin ETL/training to their snapshot. Console ranking prioritizes completion.
- Evidence: 56 focused tests and shell/JavaScript syntax checks pass. Re-exported rounds 19–30 into data/laya-pi/relabel-v4: 4,915 validated cases, 1,138 all-failed labels excluded; rebuilt splits are 3,571 train / 541 calibration / 533 held-out / 270 test. Original evidence is preserved; no new Factorio episode or GPU fine-tune was run.
- Integration: the active driver's unfrozen ETL call initially skipped its r30 fine-tune when old cases were quarantined. Corrected sibling resolution restores usable inputs for subsequent phases; the missed fine-tune was not rerun. Running worker processes were not restarted.
- Lifecycle: launch a new Python driver/consumer for the complete v4 pipeline, then fine-tune and independently evaluate a new checkpoint. No lab-mod deployment or Factorio restart is needed for this milestone.

## [2026-09-30] Revised Laya level-3 supply and measurement implemented
- What: versioned L3 cells route both iron furnaces into a shared plate store feeding gears/belts and circuits. Joint sustain replaces latched historical product successes; phase-owned surviving entities establish expansion completion and reopen on loss. Dropped items are telemetry rather than construction. The Python runner refuses old L3 measurement evidence; automatic level rotation is unchanged pending live capacity checks.
- Evidence: 105 focused Python/Lua behavior tests pass, including shared-store reachability, all tier footprints/budgets, earlier-level scenario hashes, ownership/retry bounds, lost service, transient-item audits and stale-report refusal. Lua syntax and diff checks pass. No Factorio episode, deployment, worker restart or training run was performed.
- Remaining: the provisional L3 demand range (belts 0.06–0.35/s, circuits 0.02–0.18/s) needs stepped capacity sweeps with/without expansion and a longer simultaneous-output soak; offline tests do not establish throughput gains.
- Lifecycle: deploy the lab mod to affected stopped disposable workers and GUI, restart affected Factorio runtimes and Python consumers, then run fresh L3 probes before adding it to training rotation. Preserve ongoing workers and original evidence until that lifecycle is authorized and scheduled.

## [2026-10-01] Laya level 3 deployed and measured live
- What: deployed lab Lua to disposable workers 01–14 and GUI copies, retaining the already deployed shared trash-requester fix. Found and corrected an uncovered circuit inserter through scoped electrical evidence; added pole-coverage regression checks. Future runs rotate levels 2/1/3, bootstrap L3 with rollout until its first accepted features round, and resume explicit seed blocks/accepted continuations. Console supports L3.
- Evidence: scripted rollout 32/32 on seeds 7001–7032 (maximum 34 decisions); eight paired 25-minute capacity cases at stepped speed 32 show belts ~0.208/s without expansion versus ~0.416/s with it, with circuit delivery also improving. Zero power/entity/bounds/budget failures after correction; fresh joint-sustain-v2 reports and matching mod hashes on all workers; real-tokenizer maximum 1,286/2,048 tokens on the smoke trajectory. Thirty-one focused tests plus shell/JavaScript syntax checks pass.
- Limits: controlled lab mechanics and scripted-baseline evidence, not learned-policy or real-base promotion. Short delivery windows remain bursty; saturation/depletion/endurance is unproved. Full record: docs/archive/rl/Laya_Level3_Live_Verification_2026-09-30.md; raw reports/procedures: data/laya-level3-validation/.
- Lifecycle: verified workers 01–13 returned to stopped state; updated demo worker 14 remains running. Console reloaded as user service laya-operations-console-v4 on :9138; refresh its page and restart/reopen the GUI before joining if an old GUI session is loaded. No new training run, GPU fine-tune or monitoring schedule started.

## [2026-10-01] Bounded Laya curriculum experiments
- What: added an explicit new-round cap, optional frozen-reference evaluation, configurable fine-tune cadence/final pass, and validated LAYA_EPOCHS. These allow one complete 2/1/3 rotation followed by one small fine-tune without changing default training behavior.
- Evidence: 14 focused runtime/real-driver harness tests pass; shell syntax is valid. The harness verifies three-level rotation, termination after three new rounds, and shutdown of only selected workers.
- Lifecycle: launch a new frozen driver to use these controls. Live experiment evidence is recorded separately; no learned checkpoint or production promotion is claimed by these tests.

## [2026-10-01] Scoped factory bottleneck observations verified
- What: added versioned phase-owned WIP/source stocks, machine starvation/blockage/working status, electrical health, sampled working fraction, resource/entity allowances, and independent consumption counters. Shared rendering feeds labels and live decisions; features set 5 distinguishes unknown telemetry and preserves old model widths. The strict bridge schema resolves its local health schema.
- Evidence: 91 focused checks; worker 05 scripted L3 4/4; all 120 real-tokenizer requests fit 2,048 tokens (max 1,932); 96 observations expose consumption/health. Two faithful counterfactual prefixes on seed 8107 exported two health-bearing cases. Initial schema failure retained. Report: docs/archive/rl/Laya_Improvement_Execution_2026-10-01.md.
- Lifecycle: only probe worker 05 received the health mod and is stopped again. Deploy/restart the stopped fleet and GUI plus consumers after bounded-20261001 finishes, then retrain on the new observations. That authorized experiment is running frozen 2db0ee6: at most three six-seed rounds, one two-epoch fine-tune, fresh development/reference evaluation, six-hour host cap and scoped cleanup. No production promotion.

## [2026-10-01] Phase-owned restoration choices verified
- What: previously executed connected-workflow cells can expose model-selectable restoration choices after measured entity loss. Missing bills are priced and rebound against fresh ownership; survivors are retained and exact legality/budgets stay in the lab. Features set 6 preserves set-5 dimensions. Explicit failed recycle reports now surface immediately.
- Evidence: 48 focused checks. Worker 05 outlet-loss diagnostic: zero belt delivery over 6,000 ticks, then one inserter restored with four survivors retained, zero constraint violations, joint completion at 7,800 ticks and successful recycle. A repeated recycle returns its actual error in 0.030s rather than waiting 120s. Original probe errors remain preserved; this proves mechanics, not trained repair behavior.
- Lifecycle: restart affected Python consumers to use restoration choices; no further Lua change. Probe worker 05 is stopped. bounded-20261001 remains frozen at 2db0ee6 and labeling r34; seeded fault training, active-layout upgrade provenance, finite economy and the matched promotion suite remain outstanding. Details: docs/archive/rl/Laya_Improvement_Execution_2026-10-01.md.

## [2026-10-01] Laya continuation safeguards and wind-down
- What: teacher probes on each new seed block reject completion/mean regressions and block runtime/safety/all-failed evidence; named runs can import prior case prefixes. Safe lab horizons stay policy timeouts. Failure packets reference deep health by hash/journal. The outer driver re-execs frozen code, fixing the observed Bash file-offset crash after a worktree edit.
- Evidence: 40 focused checks plus shell syntax, including original-driver mutation during a round and health-bearing timeout packets. bounded-20261001 finished r34 and r35 before exit 2; r35 features L1 completed 32/32, mean 6,088, p95 6,104, cheaper on all development pairs. The r33 teacher collapsed on higher-demand training seeds; last live teacher probe remained blocked by the packet failure, now covered by regression tests. No new adapter was trained.
- Lifecycle: user requested quick wind-down. Workers 01–13 are verified stopped; demo 14 and console remain running. No successor run/schedule. Deploy the verified health mod to the stopped fleet/GUI and restart frozen consumers before the next cohort; verify the corrected teacher/failure path first. Full handoff: docs/archive/rl/Laya_Improvement_Execution_2026-10-01.md.

## [2026-10-01] Overnight training admission and rolling student window
- What: LAYA_CASE_WINDOW_ROUNDS keeps each Laya fine-tune on recent rounds across imported case prefixes; completed adapters receive offline held-out evaluation before live cohorts. Features continuation admission now checks matched completion/mean cost before storing its accepted pointer.
- Evidence: six focused driver/teacher checks pass, including three-level rotation after original-script mutation, scoped worker shutdown, history/window selection and offline-before-live ordering; shell syntax is valid.
- Lifecycle: start a new frozen driver to apply these Python/shell orchestration changes. Eight-hour run setup/deployment and live teacher evidence are recorded separately after verification.

## [2026-10-01] Eight-hour health-aware Laya overnight run active
- What: launched overnight-20261001-health on workers 01–04/06–13, frozen 67d84a4, stepped speed 32, three labellers, 24 seeds/round. Six-hour collection budget, eight-hour host limit to 11:51 IST, scoped cleanup; two-epoch fine-tunes every third round with an eight-round case window and offline-before-live evaluation. Resume 35; banks L1 5683/L2 6619/L3 8109.
- Evidence: fixed teacher/failure path live: rollout and r35 features 2/4, zero tool/safety errors, features admitted on mean cost. All 14 deployed mod copies match, preserved shared fix f4c2178ded05; demo reloaded and remains separate. Fresh health-profile baselines: L1 32/32, L2 28/32, L3 32/32, zero errors/safety. Console active with 12 workers and immutable outer driver verified. Dataset preflight has train/calibration/held-out coverage and excludes legacy cases.
- Lifecycle: already deployed/reloaded affected disposable runtimes and GUI files. Reopen an old GUI session to join. Host supervision stops this training fleet after completion/failure/deadline; demo and console remain running. No successor run or real-base promotion. Details: docs/archive/rl/Laya_Improvement_Execution_2026-10-01.md.

## [2026-10-01] Laya overnight regression safeguards
- What: global features teachers must pass every enabled curriculum level before reuse; checkpoints now record retained training input profiles and reject unrecorded health observations. Explicit offline diagnostics grant no shadow eligibility. Preserved adapters remain unchanged.
- Evidence: 53 focused tests/harness checks pass; real-tokenizer replay retains 1,719 train/262 calibration questions with zero overflow. Paired GPU replay of eight initial states changes r26 from wait 8/8 to build 8/8 when only health is removed; r40 still waits 8/8. The new gate rejects actual r40 features cohorts for 21 L3 completion losses. Details: docs/archive/rl/Laya_Regression_Repair_2026-10-01.md.
- Lifecycle: new Python consumer/frozen driver required; no Lua deploy or Factorio restart. No new live run, fine-tune or promotion. Student wait-loop correction and fresh all-level validation remain required before another overnight run.

## [2026-10-01] Laya wait-reward measurement artefact repaired
- What: episode-relative 60-tick sampling plus exactly-once paused-endpoint catch-up removes the window shift that rewarded an initial wait. Sampler versions now travel through reports, labels and cases; new overnight trainers reject old global-clock evidence.
- Evidence: 46 tests pass, 26 optional Lupa tests skip. Live worker-05 counterfactuals on L3 seeds 8109/8110 now price an initial 1s wait 120 ticks above building; scripted L1/L2/L3 each complete 4/4 with zero safety violations. Mixed ETL retains two corrected cases and excludes 665 old cases. Details: docs/archive/rl/Laya_Regression_Repair_2026-10-01.md.
- Lifecycle: deployed/reloaded worker 05 and GUI with preserved shared hash f4c2178ded05; worker 05 stopped after verification. Remaining fleet needs lab deployment/reload and new consumers before collection. No fine-tune, unattended run, real-base action or promotion; fresh training coverage remains next.

## [2026-10-01] Bounded Laya run reserves final training/evaluation time
- What: added LAYA_FINAL_RESERVE_SECONDS and shared LAYA_EVAL_START/COUNT controls so collection leaves a final learning window and every evaluator/gate uses the same configurable cohort.
- Evidence: 10 focused driver/teacher tests pass, including no new collection inside the reserve, scoped worker shutdown and custom-cohort propagation; shell syntax is valid.
- Lifecycle: launch a new frozen driver. Background deployment/start evidence is recorded separately after verification.

## [2026-10-01] Five-hour corrected-sampler Laya run active
- What: started background-20261001-corrected at 21:54 IST, frozen 25f1a36, under laya-corrected-20261001-5h.service. Hard stop October 2 02:54 IST, scoped cleanup and task sleep inhibitor. Twelve workers 01–04/06–13, 24 seeds/round, levels 2/1/3, current-sampler cases only, 75-minute final reserve, one final two-epoch fine-tune; fresh development seeds 45101–45132.
- Evidence: all twelve deployed lab/shared copies and GUI match; shared fix f4c2178ded05 preserved. GPU ready. Fresh scripted L1 32/32, L2 26/32, L3 32/32, zero runtime/safety errors. Collection uses episode-relative-v1 and has recorded 96 labels; service limits/worker ownership/inhibitor and console detection verified. Details: docs/archive/rl/Laya_Background_Run_2026-10-01.md.
- Lifecycle: deployed and active; reopen an old GUI session before joining. Workers 05/14 stopped; console restored at 127.0.0.1:9138 and stays up independently. No successor run or real-base promotion; inspect results after completion.

## [2026-10-02] Paused corrected-sampler run resumed within original budget
- What: resumed as background-20261002-resume/rc02 after 5,831 active seconds previously used; remaining hard limit 12,168 seconds, stop by 05:18 IST plus cleanup. Retained 357 complete interrupted-round labels, skipping consumed round-5 seeds; next round 6 starts L3 8224, with rc01/rc02 history and unchanged final training reserve.
- Evidence: corrected dataset retains 2,218 cases (train 1,579/calibration 326/held-out 207/test 106), no exclusions. All twelve installed mod trees still match. Baselines repeat L1 32/32, L2 26/32, L3 32/32 with zero runtime/safety errors; round-6 collection, console and sleep inhibitor are active. No adapter had been trained before pause.
- Lifecycle: new frozen f2f2600 driver and selected workers active; no mod redeployment needed. Original pause/evidence preserved, lineage linked in manifests. Console restored at 127.0.0.1:9138; no automatic promotion or successor run. Details: docs/archive/rl/Laya_Background_Run_2026-10-01.md.

## [2026-10-02] Contextual variant teacher and bounded daytime queue
- What: features set 7 learns numeric-option × observed-context tradeoffs, fixing state-independent ranking between build variants while preserving old model vectors. Added a deadline-aware queue for teacher admission, further corrected collection/fine-tuning, independent paired validation and a conditional throughput pilot; predecessor pauses and interrupted seed blocks are protected.
- Evidence: 20 focused tests pass, including demand-dependent variant choices, legacy-vector compatibility, deadline refusal and explicit-pause protection. Queue subprocess smoke check passes; Python compiles and scoped whitespace checks pass.
- Lifecycle: Python-only; launch a new frozen queue/consumer. The active f2f2600 run remains unchanged. Queue launch and the user-authorized 14:00 deadline are recorded separately after verification.

## [2026-10-02] Work extended to 14:00 with follow-up jobs queued
- What: activated laya-until-1400-20261002.service, frozen b12bb55, with a shared 14:00 IST deadline. It waits for the existing frozen run, then fits/gates the contextual teacher, continues corrected learning, runs independent paired seeds 64001–64032 and conditionally tests micro-batch throughput. Original run/code remain intact.
- Evidence: supervisor deadline, active waiting state and sleep inhibitor verified; ownership-aware cleanup protects the predecessor. New queue/teacher controls passed 20 tests; no live teacher or new student improvement is claimed yet. Details: docs/archive/rl/Laya_Day_Queue_2026-10-02.md and data/laya-pi/day-20261002-queue/.
- Lifecycle: Python-only queued consumers; no mod redeploy. All jobs stop by 14:00 plus cleanup, with no automatic real-base promotion. A later pause must stop the supervisor and any still-active predecessor; workers 05/14 are outside the allocation.

## [2026-10-02] Disposable saved-world mechanics adapter verified
- What: added a separate exact-copy mod_playground adapter/CLI, saved-world-v1 observation schema/profile, unranked legal mining/smelting/assembly/coverage options, current-provider funding with ghost reservations, bot-only atomic execution, exact tick stepping, configuration reconciliation and measured feedback. Lab identity guards and deterministic entry points remain intact.
- Evidence: 21 focused tests pass. Fresh live mechanics replays produced 37 ore, 34 plates, 53 gears and 36 belts, then built a second powered roboport; zero pending ghosts after every transaction. Real-tokenizer state with measured flows fits at 1,532/2,048 tokens. r21-final refuses the new profile before connection/inference. Source and immutable baseline hashes unchanged; no Laya gameplay, training, matched comparison or promotion claimed. Details: docs/rl/Laya_Saved_World_Adapter.md; evidence data/laya-saved-world/adapter-verified-20261002/.
- Lifecycle: both disposable test servers stopped. Start new adapter processes for these Python/RCON-helper changes; no installed mod deployment, production/GUI restart or user action required. Next: collect matched saved-world continuation outcomes, train/calibrate a compatible checkpoint, then run the independent saved-world policy and full baseline comparison.

## [2026-10-03] Laya quad mechanics and matched saved-world collection verified
- What: completed the pending starter-kit/recipe-driven quad and saved-prefix collector tranche. Live failures fixed by deferring all ghost settings until construction and correcting shared vertical pickup/output geometry; stable slot-owned requester sections preserve other recipes during changes. Added the confirmed consumed-batch mall supply accounting seam (1 plate/s each initially, +1.5 plates/s to the queue-prioritized input).
- Evidence: 59 focused tests pass. Exact kit/50+50 bots and paused prefix restoration verified in Factorio 2.1.20. Fresh north/south slots delivered 18/10 belts; manufactured two additional assemblers before south expansion, zero pending ghosts. Eight exact 3,600-tick fork alternatives produced one validated train-only diagnostic case. Final real-tokenizer state fits 1,811/2,048 tokens; source hash unchanged. Evidence: data/laya-saved-world/quad-final-20261003/ and cf-pilot-20261003/; docs/rl/Laya_Saved_World_Adapter.md and Laya_Mall_Supply.md.
- Lifecycle: all owned test servers stopped; restart affected Python consumers, including deterministic runners using shared quad geometry. No mod deployment or production/GUI restart. Mall accounting is unit-tested only; separate live fixture/queue integration and independent calibration/evaluation remain next. No new checkpoint, Laya gameplay, unattended run or promotion.

## [2026-10-03] Horizontal-first quad expansion protected
- What: top/bottom slots require complete funded long-handed-inserter geometry; the shared generator rejects regular substitution. Selected Laya horizontal modules reserve future vertical footprints against other builds without submitting their ghosts or material bills. Reservations persist through pending construction, restart and prefix forks and enter observations/replay identity.
- Evidence: 61 focused tests pass. Live legality probes: 20 regular/0 long-handed inserters admit zero vertical slots; an intruding pole is rejected; a funded vertical slot requires two long-handed inserters. Exact paused-prefix fork restores the reservation fingerprint; source hash unchanged. Evidence: data/laya-saved-world/quad-reservation-verification-20261003.json and quad-reserved-fork-20261003/reservation-restoration.json. Probes placed no entities/ghosts.
- Lifecycle: all test servers stopped. Restart affected Python runners; no mod deployment or production/GUI restart. No training, new model decisions or promotion.

## [2026-10-03] Eight-hour Laya generalization window active
- What: launched laya-eight-hours-20261003.service, frozen 1b8e8a7, with the original hard deadline 11:58:02 IST plus up to two minutes of scoped cleanup. Twelve disposable workers 01–04/06–13, stepped speed 32, three labellers, new L1/L2/L3 seed banks 17001/18001/19001; rolling 12-round student window, final two-epoch fine-tune/calibration with 150-minute reserve and separate 45-minute independent comparison on 65001–65032. No automatic restart, successor or promotion.
- Evidence: 15 driver/queue/teacher checks pass. Dataset preflight: 4,455 train/676 calibration questions, workflow-health-v1, real-tokenizer zero overflow. GPU ready; installed mod trees match across allocated workers and preserve shared hash f4c2178ded058478. Startup rollout/reference each complete L1 32/32, L2 28/32, L3 32/32 with zero runtime/safety errors. New round-22 L2 replay journals, service/cgroup/deadline/inhibitor and console detection of 12 workers verified; source save unchanged. Evidence: data/laya-pi/background-20261003-generalization/; docs/archive/rl/Laya_Background_Run_2026-10-03.md.
- Lifecycle: new frozen consumers and allocated workers active; no mod deployment or real-base/GUI change. Console and worker 14 remain separate. Final training/evaluation pending; saved-world/mall training remains outside this run because independent calibration/live fixture are missing. Stop the complete window with systemctl --user stop laya-eight-hours-20261003.service.

## [2026-10-03] Bounded generalization run completed
- What: ten rounds (22–31), 5,246 measured labels and 4,736 new cases; r31-final trained for two epochs/542 optimizer updates, calibrated and offline-ready. All queued stages finished at 09:35 IST before the maximum deadline.
- Evidence: independent seeds 65001–65032: L1 32/32, L2 26/32, L3 32/32, matching rollout completion with zero runtime/safety errors. Mean cost vs rollout: -0.77%, +2.98%, -2.71%; better than prior r21-final on every level, but no promotion. Paired reports/checkpoint under background-20261003-generalization; completion details in docs/archive/rl/Laya_Background_Run_2026-10-03.md.
- Lifecycle: service exited successfully; all twelve owned workers stopped, GPU idle, source unchanged, no successor. No user action required. Saved-world/mall training and full deterministic comparison remain pending.

## [2026-10-03] Separate supplied-input mall verified
- What: implemented mall-world-v1 with an exact starter kit, seeded finite customer queue, bot-funded quad slots, per-machine pause/resume, additive consumed-batch supply and engine-owned durable accounting. Vertical slots remain reserved until long inserters and an assembler are funded; older checkpoints cannot run this profile.
- Evidence: 77 focused tests; fresh live mechanics consumed the exact 2-drill/10-belt/4-inserter/1-splitter batch, changed iron 1 -> 2.5 plates/s, verified exact delivery and nonce replay, built a top slot with two manufactured long arms and an extra AM1, then served 12 circuits/32 belts with zero ghosts at tick 30,180 (late mechanics sequence, not a policy win). Real tokenizer: 1,389/2,048 tokens, eight options. Copied prefix restored identical facts without regrants and preserved receipt idempotence. Source save unchanged; evidence under data/laya-saved-world/mall-live-20261003-f{,-restored}/; details docs/rl/Laya_Mall_Supply.md.
- Lifecycle: all owned test hosts stopped. New Python mall consumer/fresh disposable episode only; no mod deploy, GUI/real-base restart or background training launch. Scenario-grouped measured learning, compatible calibration/training and baseline evaluation remain next.

## [2026-10-03] Live mall spectator viewing enabled
- What: added bounded normal-speed spectator viewing to the isolated mall CLI, with named/single-observer selection, disconnect waiting and an engine-budget clamp. Started mall-view-20261003-a from the verified immutable prefix and opened Factorio using a separate matching-mod client profile.
- Evidence: 81 focused tests pass. Factorio 2.1.20 joined as LeonidasUIP; engine confirms the exact mall surface/force and spectator control. Two seconds at speed 1 delivered five iron/two copper plates with zero ghosts; source preserved. Live view evidence/state is under data/laya-saved-world/mall-view-20261003-a/. No trained mall policy is running.
- Lifecycle: private server and GUI intentionally available for user viewing. Watch advances only the remaining episode budget, then leaves the world paused for inspection. No deployed-mod, normal GUI profile or real-base change.

## [2026-10-03] Mall live-view stutter fixed
- What: replaced repeated one-second paused training steps with one continuous speed-1 viewing window and a lightweight protected supply pump. Viewing uses a separately declared maximum ten-minute window, permanently marks the copy viewing-only, and refuses training steps/decisions; engine window expiry remains bounded without callbacks or mod changes.
- Evidence: 83 focused tests pass. Measured old full-query RPC overhead at ~0.95s each. Twelve consecutive live samples all advanced, averaging 60.34 UPS (minimum sampled 59.95), with every supply receipt independently matching Python integer accounting. The full ten-minute window ended at exactly 36,000 ticks with 1,500 iron/600 copper plates and a matching final ledger. Cached engine receipt snapshots remain immutable after later deliveries. LeonidasUIP remains on the private mall surface as a spectator; smooth-clock evidence under mall-view-20261003-a.
- Lifecycle: replaced only the owned Python viewer; private Factorio server/GUI remain connected. No mod deployment or GUI/real-base restart. Ten-minute viewer pauses on expiry/disconnect and leaves the world available for inspection.

## [2026-10-04] Belt-only 180/s smelting array verified
- What: added an isolated, inert-by-default smelting fixture and mechanics runner that composes the existing Start-Middle-Middle-End line units into 16 parallel lines / 288 furnaces, with four express input fanouts and four express output merges. Regular template belts, inserters and splitter priorities remain unchanged; production paths have zero requester/logistic chests. Bots build from a declared one-time fixture budget, with live legality/funding/coverage checks and explicit fixture ownership.
- Evidence: iron and copper both passed 1/4/8/12/16-line measurements. At 288 furnaces, engine counters measured exactly 180 ore/s consumed and 180 plates/s produced over 7,200 ticks; each output belt delivered 45/s in every 30-second subwindow, with zero denied feed, ghosts or accumulating belt ore. Final iron h / copper b checks also conserve construction items and retain header/line decision journals and saves. 30 focused tests, native Lua parsing and Python compilation pass. Every attempt is recorded as rl-lab; every setup failure/cause is preserved in docs/archive/rl/Laya_Smelting_Array_Test_2026-10-04.md. Source save hash unchanged.
- Lifecycle: owned smelting hosts stopped; no further action for existing runtimes. Only private test mod copies received the helper. Existing clean-runner/mall/viewer/production servers, installed mods and consoles were untouched. Future existing-worker use requires explicit lab-mod deployment and worker/consumer restart; new private test launches handle their own copy/reload. Scripted mechanics evidence, not a learned policy win or promotion.

## [2026-10-04] Context and connected-production experiment suite implemented
- What: added explicit request-horizon/context variants to the clean runner without changing defaults, dependency/stock-lock observations, observed productivity arithmetic, reusable circuit/plastic component adapters, protected transport/fixture power observations and a frozen bounded suite with two-hour checkpoints. A dedicated read-only Muse Spark 1.3 Contributor xhigh observer reviews the whole suite in one session; model tools cannot edit, execute shell or mutate worlds.
- Evidence: 64 focused tests plus Python/native Lua checks. Funded live mechanics delivered 3 circuits/s over 7,200 ticks; bot requests of 1 craft yielded ~0.71/s versus 3/s at 15 crafts. The actual refinery/plastic chain delivered ~1.98 plastic/s with 18 gas/s consumed and recorded research effects. A raw buffer-1 pilot stalled before copper, so no default change or success claim. Every preflight tool failure is preserved and documented; no neural checkpoint was trained/promoted. Source save unchanged.
- Lifecycle: all owned pilot hosts stopped. Fresh private copies only; no installed-mod deployment, console change or existing-runner restart. The eight-hour launch/units/deadline are recorded in data/laya-experiments/context-growth-20261004/suite.json and docs/archive/rl/Laya_Context_Experiment_Block_2026-10-04.md; background results remain to be measured.

## [2026-10-04] Eight-hour context experiment block launched
- What: launched twelve isolated experimental hosts with code frozen at 17e2029, private copied inputs/mods, slots 34300–34311 / 27200–27211, and a separately bounded read-only Muse xhigh observer retaining its original session. The eight-hour block (including preflight observation) runs 04:57–12:57 IST; actor fleet started ~05:17. Automatic review snapshots/requests are scheduled at 06:57/08:57/10:57/12:57.
- Evidence: services active; initial twelve host process/port identities, source hashes, starter kits and live journals/heartbeats verified in data/laya-experiments/context-growth-20261004/launch-verification.json. No batch winner or raw C4 completion claimed. Circuit/plastic fixture evidence remains separate from raw C1/C2 cohorts; interrupted/tool-failed comparisons are excluded. Results/findings are saved to the suite folder; no unsolicited chat-delivery capability is claimed.
- Lifecycle: exact remaining-window systemd caps own only the suite actor/observer cgroups. Existing console, installed mods, real saves and unrelated runtimes untouched. Controls and ownership are in docs/archive/rl/Laya_Context_Experiment_Block_2026-10-04.md.

- 2026-10-04: Recovered the context-growth experiment Muse observer: exclusive leases prevent duplicate observers, failed reviews retain transcripts and pending requests, and retries respect cooldowns. Six focused tests plus a live exact-model recovery review passed. Only the owned observer was restarted; actor fleet/frozen code and the 12:57 IST deadline are unchanged.

- 2026-10-04: Implemented the six-family Laya mechanics/context matrix: 554 matched trials, earned-checkpoint search, legal repair/cleanup choices, physical transport/plastic perturbations and read-only Muse review with bounded sessions. 54 focused tests/native Lua checks passed; circuit repair and plastic outage/recovery were observed live. Raw checkpoint probe stalled at C0 and correctly exported nothing. Private workers only; existing runtimes and concurrent mall-slot changes remain isolated.

- 2026-10-04: Launched the 554-trial mechanics matrix on frozen 814be8d after the old 1194-job campaign ended. Eight checkpoint attempts are active with copied source/exact-kit checks passing; the fleet grows to at most 12. Muse 1.3 Contributor xhigh saved its first review and now waits two minutes between reviews. 53 tests passed on the frozen code. Owned services end by 2026-10-05 13:00:51 IST or queue completion; no external runtime restart/deployment is required.

- 2026-10-04: Verified the runner/context/history hooks restored after 306346a against 814be8d AST, and re-integrated their committed form with rotating mall slots. 47 focused runner/context/mall-slot/line/site tests passed. The active matrix stays pinned to 814be8d and private mods; 6/8 attempts earned C1 and three distinct earned checkpoints were assigned. Recipe-return deployment remains a between-campaign operation.

- 2026-10-04: Added a pinned, guarded between-campaign recipe-return deployer; 11 deployment/executor/quantity tests pass. It waits for matrix termination and stopped shared consumers, excludes dirty mod work, verifies both installed copies and starts no server. A read-only baseline-copy survey found zero crude wells inside +/-128 (proof saved; survey host stopped). Live chest-delivery verification remains open. Active experiment snapshots are unchanged.

- 2026-10-04: Verified isolated eight-hour follow-up screens, mall gate/request knobs and line-site observations; private belt-repair replication sustained 3 circuits/s after 1200-tick repair, zero ghosts. Existing comparisons stay frozen; shared redeploy waits for both campaigns and stopped consumers. See docs/archive/rl/Laya_Followup_Window_2026-10-04.md.

- 2026-10-04: Launched frozen 450-trial follow-up behind the active matrix, Muse xhigh every 120s after review, eight-hour cutoff 23:54 IST and two-hour saved check-ins. 69 checks plus private repair/earned-C1 smoke runs verified; guarded redeploy waits for both actors. Launch proof in data/laya-experiments/mall-slot-followup-20261004/launch-proof.json.

- 2026-10-04: C2 subagent audit found absent legal line actions and provider-chest budget deadlocks; added shifted legal origins and reduced/full output alternatives. 46 focused checks and live-fact geometry replays pass; full C2 reliability remains under isolated matched recheck. See docs/archive/rl/Laya_C2_Audit_2026-10-04.md.

- 2026-10-04: Started18 paired C2 rechecks (b22ebfa vs7c31722), two private workers and Muse xhigh observer, same23:54IST cutoff. First patched episode is producing construction stock; C2 reliability unproven. Existing450-trial snapshot unchanged and deploy guard waits for all3 campaigns.

- 2026-10-04 20:28IST: C2 matched rechecks improved to3/6 updated vs0/6 control, zero execution errors; checkpoint1 remains unresolved. Fixed repeated Muse-session timeout recovery (5checks), preserving transcripts and120s cadence; observer restart only, experiments remain frozen.

- 2026-10-04: User-authorized observer replacement selects opencode/space-bunny-free xhigh after Muse limit. Configurable model/variant starts fresh sessions and preserves provenance;6observer checks pass. Only observer restart, no worker/mod changes; existing23:54IST deadline retained.

- 2026-10-04: Both observers launched on Space Bunny Free xhigh; native variant and CLI response verified. Larger reviews actively read evidence; attempt cap increased600s with120s pause andoriginalcutoff. Model proof under data/laya-experiments/space-bunny-observer-20261004/.

- 2026-10-05: Prior C2 fixed rechecks finished6/9 versus0/9 controls. Started an isolated441-trial seven-hour variety campaign and separate two-arm local neural learning-rate comparison; cutoff10:03:39IST. Frozen code, private inputs, no promotion. See docs/archive/rl/Laya_Seven_Hour_Variety_2026-10-05.md.

- 2026-10-07 03:40 IST: Launched an 8-hour ops-runner A/B campaign (`laya-ops-ab-20261007`, user systemd, ends 11:40 IST at the latest). It tests baseline against the mall-first-10/30 ordering variants: a C1 window (16 seeds, speed 4), then a C2 window (12 seeds, speed 2). It runs from a frozen copy at data/laya-experiments/ops-ab-20261007/code (cf6c387 + the dirty tree as of the last live run). The speed-4 smoke run reached C0 in 47 s. All 12 first-wave worlds are deciding. No mods were deployed and no other runtime was touched.

- 2026-10-07 12:20 IST: Ops-runner A/B results (ops-ab-20261007). In the C1 window, runs reaching C1: baseline 1/16, mall-first-10 4/16, mall-first-30 8/16. No arm reached C2 in the C2 window. Laya now sees a compacted history of its own decisions (fd7b4a5); in those C2 runs, 60% of decisions had repeated an option that already failed the same way twice. Launched `laya-ops-history-20261007`, which tests mall-first-30 with the no-repeat, follow-through and unstick history rules, alone and combined, against the baseline. It adds seed rounds until 20:20 IST. Frozen code; no deployment or restart needed.

- 2026-10-07 23:55 IST: Adopted ops-mall-first-30 into mission.ops-mall-slots / ops-foundation. C1 reached by 20/52 runs against 2/52 for the old weights (ops-history-c2-20261007), confirming 8/16 against 1/16 the night before. No arm reached C2. The history rules gave no significant gain (all three: 24/52 vs 20/52), so they stay inert. New stall cause: belt mall slots that lend the circuit cell, which starves drills; mall-slot options now carry a `borrows` fact, and a new mission.ops-no-idle-loans rule is inert by default. Launched the 8-hour `laya-ops-loans-20261008b` A/B (the first launch was stopped after a brief crash, fixed in 19b0ef5): other-slots x1, no-idle-loans, both, and history, against the new default.

## [2026-10-08] Laya ops system and documentation audit
- What: reviewed the last 200 dated updates, current/frozen ops code, documentation and recorded campaigns. Found overlapping mall weights (core x180; the other-slots-x1 arm also reduces core weights), a partial-foundation/steel cycle, missing economic/lender facts, ambiguous success/history receipts, later-goal action gaps, proxy milestone/research mismatches and crash-index survivorship. Report and prioritized performance plan: docs/rl/Laya_System_Audit_2026-10-08.md.
- Evidence: 32 focused tests pass; offline contract probes and 312-run/24,605-decision history review. Read-only live service check; 00:27 IST snapshot has 38 indexed loan runs, all stalled, no recorded unexpected tool exceptions or observed traceback recurrence. Final campaign results remain pending.
- Lifecycle: audit only; no runtime/mod/save/model changes, policy adoption or diagnosis-only commit. Existing dirty work and active frozen campaign preserved; no restart required.

## [2026-10-08] Laya active documentation reconciled and trimmed
- What: scoped soft strategy priors for legal exploration, conditional grid rules, explicit anchor/rotating-cell/batch roles and blocked-batch exceptions; synchronized shorter mission/ops quotes. Replaced stale current handoff with a runtime evidence index, preserving its September 21 body in the archive. Runner contract now separates actual capabilities, migration debt, proxy milestones and research acceptance; steel lifecycle and provisional adoption docs agree. Numeric weights/scopes and frozen experiments are unchanged.
- Evidence: 52 focused tests pass; all catalog quotes/phase loads and relative links verified. Exact guided distributions and rule visibility preserved on 32 recorded decisions; serialized decision state shrank 9.8% with identical facts/options. The three active guides are 31% shorter by word count; unrelated dirty files preserved.
- Lifecycle: documentation/catalog prose only; no runner/mod/Factorio restart or deployment required. Newly built guideline packs have new source hashes; retain frozen sources and representation compatibility for historical training/replay.

- 2026-10-08 01:20 IST: Audit follow-up committed (aba1bfb, bcb88b7, 8b07bc3):
  - honest operation receipts
  - exclusive mall weights (same effective core x180 / other x6)
  - crash-proof experiment accounting
  - stock, reservation and loan facts for Laya
  - deterministic milestone predicates reported beside the rate proxies
  - a fix to the shared furnace survey for Factorio 2.1
  A smoke run reached proxy C1 while deterministic C1 failed. Queued `laya-ops-honest-20261008` to start when the loans campaign ends. Runners using the checkpoint monitor need a restart to pick up the survey fix.

## [2026-10-08] Deterministic workflow and self-diagnosis audit
- What: audited mission/lifecycle, observation, scheduling, bootstrap/loans, planning/execution, recovery, stagnation, evidence/campaign and checkpoint regression flow. Confirmed fragmented dependency coverage, unchanged-demand promotion defeating deferral, non-causal progress/budget renewal, stale report ticks, template/start assumptions, prototype drift and outcome/provenance gaps. Report: docs/deterministic/Deterministic_Workflow_Audit_2026-10-08.md; recommends read-only causal diagnosis and incremental preview/execute/reconcile contracts shared with Laya.
- Evidence: 177 focused tests pass, one existing pole-batch fixture fails in isolation (missing mocked capability query); offline probes reproduce key mechanisms. Refreshed the stale context only into /tmp, checked raw historical outcomes, and verified inspected source hashes remained stable despite concurrent unrelated commits. No new live episode or map-generalization claim.
- Lifecycle: audit only; implementation, validation/deployment and promotion remain future work. No runtime/save/mod changes or restart; report/status left uncommitted under the diagnosis-only rule.

## [2026-10-09] Factorio 2.1.21 defaults aligned and validated
- What: aligned native training/server/campaign managers and dashboard defaults with the already-installed 2.1.21 engine (build 87673); updated current operations/training docs and documented version-matched rollback. Retained historical 2.1.20 evidence and fixtures.
- Evidence: 63 focused manager/dashboard tests, shell syntax and whitespace checks pass. Read-only live RCON confirms deterministic Nauvis/player with both project mods on 2.1.21; installed GUI is also 2.1.21 and all 21 persisted worker read-data configs match. Disposable mall smoke on loopback 34221/27221 verified exact kit/50+50 bots, bot-funded gear production (13 gears), zero pending ghosts, request multipliers 1/4/15 and unchanged source hash. Evidence: data/laya-saved-world/factorio-2.1.21-smoke-20261009/upgrade-validation.json.
- Lifecycle: test host stopped; existing deterministic server preserved and standard workers remain stopped. No mod deployment or further Factorio/GUI restart required; future manager launches use 2.1.21. The private 2.1.20 runtime remains available; rollback also requires a compatible pre-upgrade save copy. No policy-performance claim or experiment adoption.






## [2026-10-09] AGENTS.md modernized; docs archived
- What: AGENTS.md reframed around deterministic showcase, Laya focus and RL test bed, with experiment/adoption rules and subagent log reading. Rotated status entries before 2026-09-25 into docs/archive/status/. Archived 33 superseded handoffs, finished experiment reports, multi-model plans and point-in-time reviews to docs/archive/{rl,deterministic}/ with retargeted links; replaced the August roadmap. Open leads are listed in docs/archive/README.md.
- Evidence: status rotation lost no lines; no new broken relative links in docs; 402 doc-referencing tests pass. The fast gate cannot collect because untracked tests import missing in-progress modules (training.laya_control, training.laya_mall_growth).
- Lifecycle: docs only; nothing to restart or deploy.

- 2026-10-13 10:02 IST — Fixed deterministic construction reservation leaks exposed by `solo-20261013T034945Z`: repeated transient submissions renew their bill; funding subtracts other unbuilt ghosts while excluding exact retries; additive ghosts cannot consume foreign reserved stock. Evidence: 122 focused tests passed, including native Lua and pre-fix failures; fast gate 952 passed/3 skipped/1 pre-existing import-firewall failure reproduced from HEAD. Lifecycle: Python controller restart/new frozen validation episode required; no mod deployment or Factorio restart, active campaigns untouched; no live rerun yet.

## [2026-10-15 13:47 IST] T3 Code harness delegation instructions
- What: AGENTS.md routes delegation through the active harness; T3 Code inherits model/provider defaults without model or effort overrides, and falls back to direct evidence reading when delegation is unsupported.
- Evidence: reviewed instruction diff and git diff --check pass. Required fast gate stopped with 285 collection errors, including duplicate saved-run test modules under data/; no subagent launch was exercised. Log: /tmp/factorio-t3-agents-fast-20261015.log.
- Lifecycle: instructions only; no runner, mod or Factorio restart/deployment required. New sessions load the updated charter.


## [2026-10-15 14:19 IST] Explicit T3 delegation tool contract
- What: AGENTS.md names T3 MCP delegate_task/task_status, requires discovering both before delegation, prohibits native collaboration tools in T3 and falls back to direct reading when tools are missing.
- Evidence: reviewed documentation diff and whitespace checks pass. Isolated managed Codex 0.162.0 requests confirmed the upstream two flags retain native tools while adding agents.enabled=false removes them; normal inference completed in both arms. Required fast gate again stopped with 285 collection errors, including saved-run import collisions under data/; log: /tmp/factorio-t3-explicit-tools-fast-20261015.log. T3-owned delegation remains untested because its tools are unavailable here.
- Lifecycle: instructions only; new sessions load the charter. No T3 runtime patch, runner restart, mod deployment or Factorio restart performed or required for this documentation change.

## [2026-10-17 09:37 IST] Reset: prerequisite choices and demand-driven observations
- What: Named prerequisite handoffs become selectable outside the proxy phase and shortage menu cap; current parent outcomes retire old handoffs. Enabled goal production and recipe/item counters follow live demand and its ingredient closure. No rule weights changed.
- Evidence: 66 focused tests pass, including Lua observation behavior; fast gate 963 pass / 1 skip / 1 inherited import-firewall failure. Offline only.
- Lifecycle: use the reset branch for new disposable runners; observation Lua travels through the adapter. No deployment, restart or campaign modification performed. Earlier recovered mod changes still require staging from frozen code before live evaluation.

## [2026-10-17 09:45 IST] Reset: selected oil remaining bill drives choices and retries
- What: Exact installed/pending/unplaced material facts retain immutable transaction identity. Selected missing items become choices before repeated failures; parent options carry current owned bills. Retries charge only uninstalled placements, and paid construction reservations release. Installation high-water marks count delivery progress while bounding rebuild cycles.
- Evidence: 388 focused checks pass; a regression requests 10 rather than 2 pipes with the prior affordability function. Recorded decision 378 of control-s76403 replays its original four options and gains plastic with the correct goal dependency. Fast gate 964 pass / 1 skip / unchanged inherited import-firewall failure. No live success claimed.
- Lifecycle: start successor disposable runners from the reset commit after campaign ownership/deadline checks, using committed mod_playground and mods staged from frozen code. No campaign edit, deployment, restart or production action performed. Finite-cell WIP/slot release, network delivery and fresh strict C4/C5 remain open.

## [2026-10-17 10:03 IST] Reset: finite-cell release and durable WIP recovery
- What: Standing finite cells share persisted loan identity and vacate after completion. Borrowed cells keep their identity through verified restoration. Exact machine refunds, held hands and requester excess recover without spills or overwriting companion requests; full providers retain pending ownership across restart. Gear/cable anchors and capability requests are preserved.
- Evidence: 298 focused checks; fast gate 973 pass / 1 skip / unchanged inherited import-firewall failure. Luna medium identified and reviewed the restart/spill fixes. Engine API smoke remains required.
- Lifecycle: stage changed Lua/schema from a frozen commit and start fresh disposable instances. Completed day campaign 64/64, unit inactive. No existing runtime or installed mods changed.

## [2026-10-17 10:07 IST] Reset: reachable, conserved material delivery
- What: Validate provider/consumer network identity before cell/stage delivery; zero moved is not progress. Transfer only accepted destination stock with rollback on source refusal, and scope destination to player force.
- Evidence: 378 focused checks, including Lua conservation and disconnected-network cases; fast gate 973 pass / 1 skip / same inherited import-firewall failure.
- Lifecycle: fresh frozen lifecycle smoke `a93ee97` is running on ports 35440/28440 under a bounded user unit. Network change requires new frozen Python consumers. No active day campaign remains; no deployed installed mod/GUI copy was changed.

## [2026-10-17 10:17 IST] Reset: engine verification and heavy matched testing
- What: Verified actual finite-cell refunds, full-provider conservation and two save/load acknowledgement boundaries on an isolated API fixture. Fixed atomic Lua test module lookup and catalog state leaking across offline tests.
- Evidence: 146 affected checks pass; fast gate 973 pass / 1 skip / unchanged firewall failure. Original full suite: 3840 passing cases, 18 failures, 39 skips; nine loader failures corrected, six catalog-order failures reproduced on predecessor and corrected; two other fake-client failures plus firewall reproduce on predecessor. Full confirmation follows this test-only commit. Smoke ended after 772s / 119 decisions at game-tick cap: proxy C0/C1, strict C0, two real cell restorations; no C4/C5 claim.
- Lifecycle: 32 planned fresh trials (16 matched seed pairs), 8 concurrent, lifecycle `a93ee97` versus network `76d24cb`, under `laya-reset-paired-20261017.service`, October 17 deadline 16:30 IST. Frozen cohort unchanged by test fixes; per-minute offline metrics retain all jobs. Separate fixtures/smoke stopped their servers. Installed GUI/server mods and production untouched.

## [2026-10-18 01:38 IST] Bound pytest discovery to current source
- What: default pytest discovery targets tests/; frozen campaign copies remain independently verifiable from their own code roots.
- Evidence: root discovery previously entered data/ and produced import/collection collisions; bounded fast gate collected the current suite. One preexisting import-firewall test still fails; committed HEAD independently reproduces the same four violations in laya_lines.py/laya_ops.py. No safety rule was relaxed.
- Lifecycle: none; test configuration only.

## [2026-10-18 01:38 IST] Two-machine task workspaces
- What: added local-only task_workspace.py status/create commands and documented one explicit base SHA, branch and worktree per task. Parent work and machine-local runtime state stay separate; all synchronization/integration is explicit.
- Evidence: nine disposable-Git integration tests pass, including dirty/staged parent preservation, collisions, literal shell characters in paths, upstream counts and hook suppression. Current host is miniship; other laptop checkout has not been inspected or configured.
- Lifecycle: none; no remote push, machine provisioning or runtime action performed.

## [2026-10-18 01:41 IST] Expose blocked mall demand and terminal acceptance
- What: rejected slot claims retain owned material bills, network/allocatable snapshots and reservation owners; existing produce menus request fixed network targets for those blockers. Unchanged claim stock sets the existing retry fact. Runner/batch reporting separates ever-passed from final predicates, preserves unknown observations and discloses observed denominators.
- Evidence: 49 focused tests pass (ops, claim-demand, batch, acceptance and disposable Git workspace checks). Required fast gate: 967 passed, 1 skipped, 1 preexisting import-firewall failure; committed 690983f reproduces the same four forbidden imports. Evidence remains offline tests; no replay, live run, matched improvement or adoption. Logs: /tmp/laya-resume-all-focused-20261018.log and /tmp/laya-resume-final-fast-20261018.log.
- Lifecycle: all laya units were absent at resumption; no runtime action performed. Future runners need a fresh frozen copy of these Python changes; no Lua deployment required.

## [2026-10-18 01:46 IST] Typed service blocker receipts
- What: unsafe power routes return power_route_unavailable with endpoints/pole/blocked-count/reason; collisions with real infrastructure return infrastructure_collision with plan and occupant evidence. Failure classification and placement/clearing behavior are preserved.
- Evidence: 28 service/economy tests and 74 collision/ghost/quad regressions pass. Required fast gate: 968 passed, 1 skipped, 1 preexisting import-firewall failure. No routing improvement or live recovery claimed; this fixes failure evidence only. Logs: /tmp/laya-service-{typed,regressions,fast}-20261018.log.
- Lifecycle: no runtime action; future Python runners need fresh frozen source. No Lua deployment required.

## [2026-10-18 01:52 IST] Mall capacity and loan economics observations
- What: integrated Sol worktree's read-only economics projection: committed slot headroom, reservation owners including fully allocated bills, logistic-network plate stock, loan identity/targets/measured crafts and durable prerequisite credits. Decision digest and bounded brief retain unknown observations and distinguish headroom from affordability/borrowability.
- Evidence: 67 focused policy/runner/economics/budget checks pass. Required fast gate: 968 passed, 1 skipped, 1 preexisting import-firewall failure. Evidence is unit and mocked runner integration only; no replay, live result or policy adoption. Logs: /tmp/laya-economics-{integrated,fast}-20261018.log.
- Lifecycle: no runtime action; fresh frozen Python source is required in future runners. No mod redeployment or Factorio restart required for this slice.

## [2026-10-18 01:59 IST] Fluid-aware recipe observations and status
- What: optional typed recipe_observations preserves enabled/disabled state, item/fluid ingredients and observed yields/categories/speeds while retaining the solid-only operation catalog. V2 status distinguishes missing/disabled/unsupported metadata, shows fluid demand leaves and nominal chemical-item capacity. Exporter indexes crafting prototypes once per survey; unknown fluid supply does not erase known recipe availability.
- Evidence: 126 focused integration/consumer tests pass, including mocked Lua-to-schema-to-board checks and three enabled-state regressions from independent Sol review. Lua syntax and scoped whitespace checks pass. Required fast gate: 989 passed, 1 skipped, 1 preexisting import-firewall failure. Actual Factorio execution and effective fluid delivery remain unverified; no policy or checkpoint adoption.
- Lifecycle: no live action. Future runs must freeze Python, schema and training/laya_saved_world.lua together and start a fresh runner. This helper is sent by the adapter, not an installed mod; no installed-mod deployment or Factorio restart required solely for this change. Logs: /tmp/laya-fluid-{integrated,fast}-20261018.log.

## Guarded standing-cell stock gates (2026-10-18, 02:35 IST)

Laya can explicitly raise an owned paired mall cell's stock gate against an active
material bill. Versioned executor guards recheck identity, recipe, prior gate and
requester before writing; readback and submission evidence remain distinct.
Configuration receipts preserve placement ownership, including legacy batched
refreshes and loan restoration. Final review found no remaining scoped blockers.
239 focused checks pass in both the working tree and an exact staged-source
export. The export fast gate has 1,094 passes, three skips and the known
four-violation import-firewall failure (working tree: 1,104 passes). Offline evidence only. Activation needs
deliberate executor-mod deployment, Factorio reload and a fresh matching runner;
no live lifecycle action was performed.

## Starship fixes integrated and verified (2026-10-18, 02:36 IST)

Gate source e7be89e is combined with Starship's bootstrap-cycle repair
(f7916e4 -> 1b8b2cb) and unchanged shared mining geometry
(405fe98 -> 8df965a). The bootstrap uses the recipe dependency graph and actual
support bill; geometry preserves deterministic exports and training coordinates.
358 focused checks pass in a committed-source export, independent of local edits.
Combined fast gate: 1,107 passed, three skipped, one existing firewall failure
(two laya_ops imports remain; both laya_lines violations are removed). Evidence:
/tmp/laya-combined-{focused,fast}-20261018.log. No live result is claimed.
Unrelated tracked changes remain intact. Bootstrap/geometry need fresh frozen
Python in the next runner; guarded gates additionally need deliberate mod
deployment and Factorio reload. No runtime, installed mod, service or save changed.

## Laya startup no longer runs the deterministic opener (2026-10-18, 03:05 IST)

Ops sessions now call `initialize_operation_runtime` (ledgers, recipe catalog,
caches) instead of `_open_the_run`; Laya chooses `reserve_steel_seed` itself.
The selected fence verifies funding against current stock and stays retryable
when underfunded. A planned/submitted/producing seed reports `seed_committed` or
`seed_producing` without re-running the two-pole fence, so the belt-side seed's
one-pole plan bill is never dropped (Opus review P2, reproduced and fixed).
Evidence: 46 focused tests; exact staged-source export fast gate 1,121 passed,
3 skipped, 1 known firewall failure (same two laya_ops imports). Unit level
only. Lifecycle: fresh frozen Python for the next runner; nothing deployed.

## Ten-hour Opus testing window (2026-10-18, 02:50-12:50 IST)

What: Claude Opus 5.5 orchestrated disposable-world testing of the integrated
fixes. Live smoke and cohort 1 showed every stock-gate raise rejected: Factorio
2.1.21 reads item signals back without `type`, the executor guard required it
(fixed 8a3bcc8). The cohort-tested reset line (laya/reset-20261017, f1469e1)
was not in this branch; merged it (dbd385f, 88093ab), added the chemical-ladder
embodied-rung credit (09c093a) and the review's two cross-line fixes (dabdfd5).
The d0851ec status entry above says 03:05 IST; the commit was 02:56 IST.

Evidence (frozen worktrees, Factorio 2.1.21, save 347eba650650, guided, speed 2,
360,000-tick window, seeds disjoint from earlier cohorts):
- Cohort 1 `opus-10h-20261018` (base eeb3900 -> gate e7be89e -> starship c4745ce
  -> startup d0851ec): 128 planned, 103 completed, 8 interrupted, 17 not started
  (truncated by a decision recorded before reading outcomes). 24 matched seeds:
  proxy C3 16/19/14/19, C4 0 everywhere; strict final C2 14/15/14/12. No
  significant factor effect. 0 tool failures. Raises 202/202 rejected (guard
  defect). Steel fence 27/27 `reserved`.
- Cohort 2 `opus-10h-c2-20261018` (rung f1469e1 / merged 88093ab / ladder
  09c093a): 108 planned, 72 completed, 36 not started at the deadline; 24 matched
  seeds. Proxy C2 13/13/15, C3 9/12/13, C4 2/0/1. Strict ever C2 8/6/11, C3 2/2/4
  (rung final acceptance unknown: older runner). Pipe-rung regressions 38/39/25.
  0 tool errors. Raises 35/35 `stock_gate_verified`. Steel fence 72/72 `reserved`.
  Paired merged->ladder strict C2 +9/-4, rung->merged +6/-8: not significant.
- Offline: full suite on dabdfd5 4,074 passed, 39 skipped, 3 failed (two also
  fail on both parents, plus the known two-import firewall).

Decision: no adoption claim. The merged line is a regression-free integration
(no tool errors, no proxy/strict loss beyond noise); the ladder credit reduces
but does not remove pipe regressions. The guard fix is verified at deployed-
runtime level. Lifecycle: no units or worlds left running; future runners need a
fresh frozen copy of dabdfd5 with its mods staged into each disposable world.

- 2026-10-18 20:51 IST: Starship cohort 4 started at 20:38:51 IST, deadline October 19 03:38:51 IST (unit cap 03:48:51), 96 planned jobs on 48 matched seeds, concurrency 8. Frozen review 421e3de vs cell 5271f7f; source save/harness/mod hashes verified. First four-minute snapshot: all eight jobs deciding, zero actual tool/runner errors; no adoption claim. Terminal accounting scheduled 03:50 IST. Existing servers/GUI/mods untouched.
- 2026-10-18 20:51 IST: Construction coverage now owns its real funded submission, power bridge, roboport and run-state definitions; 33 moved definitions remain AST-identical and stage exports/state keep identity. Focused: 347 passed plus one clean-base pole-shortage fixture failure; fast: 1150 passed, 3 skipped, only the deferred autonomous_builder firewall violation. Lifecycle: source-only; future runners require fresh frozen code. Active cohort arms were not changed.

- 2026-10-19 08:50 IST: Cohorts 3 and 5 complete on miniship (disposable worlds, frozen arms, Factorio 2.1.21). Cohort 3 (09c093a vs 421e3de, 39 matched of 96 planned; 8 interrupted + 9 unstarted by host resets; attempt 1 lost entirely): no factor effect (strict C2 18/17 paired +10/-11, C3 7/3 +2/-6, C4 0). Cohort 5 (421e3de vs 37dc304, 95 matched across batch parts a/c/d/e; part c lost to a reboot): exact parity (strict C2 41/41 +24/-24, C3 12/12 +9/-9, C1 3/3; proxy C4 1/0). The 37dc304 loan-cell pipe credit never applied live: 104/104 queries failed on Factorio 2.1's renamed crafter_input inventory (fixed fba641b). The stale loan-tag release loop fixed in 37dc304 also hit the reference arm (review-s77583, 56 release_owner_missing). C1 blockers (next arm): fewer than four working mall assemblers 2047 samples, starter not ready ~600-800 each. Stock-gate raises 127/127 verified; 0 tool errors. Adaptive slot harness (910f579) ran live: 6-18 slots, UPS 103-118. Post-review fixes 90284f6 (C1 status/starter evidence) and 5d1d866 (capacity/starter/no-progress decision facts) are pushed but not yet cohort-tested. No adoption. Lifecycle: no units left running; future runs need a fresh frozen copy of 5d1d866.

- 2026-10-19 12:30 IST: Cohort 6 complete on miniship (prev 37dc304 vs facts 5d1d866, 48/48 matched seeds, 96/96 jobs, adaptive slots 15-19). The loan-cell pipe credit is now live (facts arm: 0 unreadable queries vs 48 on prev) and pipe-rung reopenings fell 41 -> 24. Strict C2 21 vs 17 (paired +10/-6), proxy C2 29 vs 24 (+13/-8), strict C3 2 vs 4 (+2/-4), C4 0/0: not significant, no adoption. 0 tool errors, 0 release failures, 60/60 raises verified. New C1 evidence (1,059 samples): mall machines were disabled_by_control_behavior 1,800, working 961, ingredient shortage 654; starters ready ~70% of samples and absent (retired) ~30%. Strict C1 therefore fails mostly on stock-gated idle cells and retired starters, a predicate-intent question pending the user. Lifecycle: no units running; data/laya-experiments/laya-c6-20261019/analysis.txt.

- 2026-10-20 05:16 IST: Fixed the C7 s78026 robot/pole survey race: stationary-entity lookup ignores flying bots, so a bot cannot hide the power endpoint. 49 ghost/service tests pass (three execute the Lua query against robot-before-pole fixtures); fast gate: 1150 pass, one skip, existing laya_ops/autonomous_builder import-firewall failure. No campaign mutation. Fresh frozen runners pick up Python query changes; no mod deployment.

- 2026-10-20 05:20 IST: Added a bill-sized first advanced-circuit batch in an existing mall cell, transferable-output completion, restoration and ingredient-source protection. Chest consumers accept delivered seeds without pretending they are permanent production. 298 focused checks pass; 10/11 new regressions fail against the parent source (duplicate-source positive control passes). Fast gate: 1161 pass, one skip, existing import-firewall failure. Fresh frozen disposable runners required; no installed mod or real-save action.

- 2026-10-20 05:22 IST: Finite consumers now accept a complete transferable ingredient seed bill without claiming permanent upstream capacity. 300 focused checks pass; fast gate 1163 pass, one skip, only the existing import-firewall failure. Earned-C5 replay already delivered four circuits and restored its lender; consumer replay follows on a fresh frozen copy. C7 full accounting is recorded (396/396 jobs, no adoption). New frozen runners only; no installed mods or real save changed.

- 2026-10-20 05:24 IST: Started C9 `laya-c9-miniship-20261020`: 48 seeds × five cumulative arms, 240 planned jobs, common save/mod hashes and fresh final acceptance, C5/C6 endpoints. Seed admission ends 19:20 (21:00 budget), hard cap 23:00; adaptive 18 (6–25). Raw-start smoke 2/2 × eight decisions, zero failures; earned replay delivered four circuits, then actual two-chest crafts consumed two circuits, both lenders restored. Mechanics evidence only; no adoption. New frozen disposable runners active; no production/mod/GUI/Starship lifecycle action. Claude continuation scheduled 06:15 IST.

- 2026-10-20 13:35 IST: C9 complete, 240/240 planned jobs (239 stalled, 1 window_timeout). C5 ever/final ref 8/7, plastic 12/12, rung 14/13, extend 10/10, batch 15/11; C6 0 everywhere; batch vs ref C5 +10/-3 (p=0.09), no endpoint contrast significant; tool errors 0; batch has the most collision/untyped_stuck runs (11/5 of 48). No adoption. `docs/rl/Laya_C9_Result_2026-10-20.md`. C10 running, C11 queued behind it.

- 2026-10-20 19:05 IST: C10 complete, 144/144. Full stack on new seeds: C5 14/48 ever, 11/48 final (C9: 15/11), C6 0, no tool errors. Copper and norung arms invalidated by the 05952ad link TelemetryError (91/61 tool errors, 14/11 safety stops); fixed in 030f8de, 0 errors in C11's first 47 live second-collector surveys. No adoption. `docs/rl/Laya_C10_Result_2026-10-20.md`. C11 running.

- 2026-10-21 00:17 IST: Starship menu worktree removes top-four shortage truncation so small rejected mall-cell construction bills remain selectable; recipe/plate checks and exploration remain. New 1/2-assembler regressions reproduce the omission before the fix; 55 focused tests pass. Fast gate: 1172 passed, one skipped, existing autonomous_builder import-firewall failure reproduced on HEAD. Coverage is deferred pending an exact shared plan/coverage preview; local handoff records evidence/API, no order workaround. Lifecycle: source-only; future fresh frozen runners, no mod/server action. No live result.

- 2026-10-21 00:53 IST: Takeover verified menu/C6 choice-output/loan lifecycle fixes (433 focused tests) and fleet 2.1.23 defaults (49 tests); fast gate 1179 pass/1 skip, only preexisting import firewall failure. C11 complete 144/144: C5 ever/final copper16/14, ready17/15, reconcile16/15; no adoption, legacy C6 exploratory. Initial 2.1.22 C12/C13 stopped for user-requested upgrade and fully accounted separately. Fresh C12 (192 jobs, initial12 slots) and C13 (144, initial6) now run solely on miniship, 2.1.23 build87750; no starship game runs after02:30, already zero. New Opus5.5 high owner scheduled03:32 IST with55-minute checks. Lifecycle: new frozen runners active; both headless engines/GUI launch targets updated, older runtimes retained; no real-save action. See docs/rl/Laya_Cohort_Ledger_2026-10-21.md and Laya_C11_Result_2026-10-21.md.

- 2026-10-21 02:28 IST: First observed Laya C6 choice/output-v2 achievements in the live 2.1.23 cohorts: C12 2/21 completed (ref-s79400, seed-s79401), C13 4/19 (ref-s79401, menu-s79400/s79403, loan-s79402); each stored receipt has explicit produce.advanced-circuit plus a scoped engine item delta of 1–2 after choice, with recycler absent. Deterministic legacy C6 remains separate (C12 0 ever/final, C13 2/0). C5 ever/final 3/3 and 4/1; no adoption from partial cohorts. 21+11+160=192 C12, 19+7+118=144 C13; zero tool errors/safety stops, typed failures remain. Evidence: data/laya-orchestration/takeover-20261021/checkin_helper_runs/20261021-0227.md and each world's latest-status receipt. Lifecycle: none; both frozen miniship campaigns continue, starship excluded, Opus handoff 03:32 IST.

- 2026-10-21 05:05 IST: Steel-seed bill facts (`934f9cb`): the parameterless steel operation gains `unchanged_failure`, and its bill items count as goal-serving. 19/79 completed C12/C13 runs had retried an unchanged drill/inserter-short steel seed ≥3 times (max 77). Focused runner tests pass 38/38; the new regression fails on the parent; smoke was 8 decisions with 0 failures. Shared host slot budget in `tools/run_laya_batch.py` (`57feb31`): slots are a host-wide total and older cohorts get first claim; admission is memory-headroom-gated and paced (47 batch/scheduler tests). C14 `laya-c14-2123-miniship-20261021` launched on miniship: 150 jobs = seeds 79400–79474 × ref c1b8680 / steelbill 934f9cb. Host total starts at 20 and may grow to 30 (min 10), UPS floors p95 105 / p98 95; admission until 2026-10-22 02:00 IST, cap 83000 s. Fast gate: 1179 passed, 1 skipped, only the known firewall failure. C12/C13 are unchanged. No mod, server or real-save action.

- 2026-10-21 05:25 IST: Stopped C14 attempt 1 at 05:18. Its shared admission ignored host health, so C12/C13 shed slots (C13 7→4, C12 7→6) while C14 grew: older-first priority was inverted. Attempt 1 accounting: 150 planned = 1 completed, 4 interrupted, 1 shutdown runner_error, 144 not started; it is excluded from results. Fix `28a2bb8`: a shared batch admits at most one trial per 5-minute window, only after a measured window that did not shrink the host total, and only with ≥4.5 GiB free (47 batch/scheduler tests). Relaunched as `laya-c14b-2123-miniship-20261021`: same arms/seeds/settings, 150 jobs, until 2026-10-22 02:00 IST, cap 82000 s. C12/C13 unchanged.

- 2026-10-21 09:45 IST: Vacant mall-half reuse (`ef38247`). A released finite cell keeps its assembler and chests but loses its recipe, so it was invisible to lenders and to slot allocation. That caused the top no-C5 stall ("no borrowable assembler", 13/58 C12/C13 runs; root cause in local `checkin_helper_runs/bclass-borrow-20261021.md`). The borrower now reuses a complete, owned, recipe-less half as a finite cell when nothing else may lend; last-producer protection is unchanged. 8 new and existing borrow tests pass; wide mall/Laya/bootstrap suites 891 passed (only the 2 known acceptance-reporting failures); fast gate 1186 passed + the known firewall failure. A disposable 2.1.23 world confirmed the survey finds a recipe-less assembler and ignores it once configured. Not yet in any cohort: queued as the next matched arm after C12 completes. No mod, server or real-save action.

- 2026-10-21 12:00 IST: Accidental A/A test. C12 `seed` and C13 `ref` are byte-identical frozen code (same save, seeds and engine, run concurrently). On 33 common completed seeds: C2 6 vs 13 (discordant 5:12), C4/C5 4 vs 8 (2:6), C3 7 vs 6. Per-seed outcomes are strongly stochastic, so matched seeds control little, and arm differences of roughly ±7/33 are within run-to-run noise. On the common seeds 79400–79432, reference arms show no C2 decline (C10 full 12, C11 11–12, C12 ref/raw 14, C13 ref 13); the lower pooled C2 in C12/C13 comes from newer arms (furnace 9, seed 6, menu 8), which are not distinguishable from noise either. C2 has plateaued at ~35–45% since C3. Implication: future adoption needs larger effects or more seeds per arm; read single-cohort contrasts against this A/A spread.

- 2026-10-21 13:45 IST: C12 admission closed at 13:05 (last block whose 6000 s window fits before 14:45): 157 completed, 3 running, 32 not_started_deadline (seeds 79440–79447). Result follows completion. At the user's request, C15 is queued behind C14b: steelbill 934f9cb vs vacancy ef38247 × seeds 79400–79474 = 150 jobs, until 2026-10-22 18:00 IST. Attempt 1 (`laya-c15-2123-miniship-20261021`) was stopped after 2 minutes (1 interrupted, 149 not started; excluded) because a newer shared cohort was not bounded by the older shared C14b's host total. Fixed in `1e8428f` (admission uses min(own slots, older shared slots); 47 tests). Relaunched as `laya-c15b-2123-miniship-20261021`, which waits behind C12/C13/C14b.

- 2026-10-21 14:30 IST: C12 complete: 192 planned = 160 completed (40/arm, seeds 79400–79439) + 32 not_started_deadline, with no errors. C2 ref/raw/furnace/seed 15/16/11/7; C5 ever/final 14/5, 11/9, 10/8, 6/1; new Laya C6 12/7/8/6 (33/160). No adoption: no target improvement; furnace and seed lower C2; seed-vs-furnace C5 final 1:8 (p=0.039) is a single nominal hit within the A/A spread. tool_error and safety stops 0. [Result](docs/rl/Laya_C12_Result_2026-10-21.md). Stop-class analysis is running; C15b (vacancy) is already queued, so no new launch is needed.

- 2026-10-21 14:50 IST: More Miniship slots at user request (laptop now in performance mode). Harness `e2bb022` adds `--resume` (finished jobs kept; interrupted work rerun in fresh `<id>-r<attempt>` worlds with history) and `adaptive.grow_step`; 49 batch/scheduler tests pass, and a dry run on C14b's status kept 15 completed and requeued 5 in flight. C14b and C15b were stopped gracefully and restarted with `--resume`: host start 20, grow 2 per healthy window, admission every 120 s, UPS floors unchanged at 105/95. Two C14b runs that failed with RconError at the stop are relabelled interrupted (raw status kept) and rerun. The pre-resume status is preserved as `job-status.before-resume-1445.json`. C13 is unchanged (frozen cap 8).

- 2026-10-21 15:10 IST: Observed-foundation fact (`d8e3f7c`): an acceptance sample's foundation-ready predicate advances the plate stage, so a starter whose foundation bots finished after a wait outcome is offered for retirement. C12 analysis: 30/111 never-C2 runs had both foundations ready but a starter present, and 24 had no retirement option. Runner tests 39/39, Laya suites 671 passed (only the 2 known acceptance-reporting failures), fast gate 1186 + the known firewall failure. At the user's request, C15b (0 jobs ever started) is superseded by `laya-c15c-2123-miniship-20261021`: cumulative steelbill 934f9cb → vacancy ef38247 → foundation d8e3f7c × seeds 79400–79449 = 150 jobs, harness d8e3f7c, same shared-budget settings, until 2026-10-22 18:00 IST, queued behind C14b. [C12 result](docs/rl/Laya_C12_Result_2026-10-21.md) now includes the stop-class analysis.

- 2026-10-21 16:50 IST: C13 complete: 144/144 completed (48/arm), no errors. C2 ref/menu/loan 18/14/18; C5 ever/final 12/3, 19/2, 19/2; new Laya C6 10/18/17 (45/144). Menu vs ref C5 13:6, new C6 14:6 (p 0.17/0.12), the largest positive signal so far, but menu lowers C2 (6:10) and loan lowers C3 (2:7). No adoption. Collisions 17/43/41 events. [Result](docs/rl/Laya_C13_Result_2026-10-21.md). The stop-class analysis is running. A host scheduler service (`laya-host-scheduler`, started 16:29 outside this thread) now publishes a host gate (target 12) that C14b/C15c follow.

- 2026-10-21 17:15 IST: Moved Miniship cohorts onto the audited host scheduler (docs/rl/Laya_Host_Scheduler.md, Starship commit `4f7a88b`).
  - The installed `laya-host-scheduler` was updated to the audited script (hash verified; config and the previous copy kept) and restarted.
  - Harness fix `81f0735` on `work/miniship/host-scheduler-harness-20261021`: the locked occupancy count crashed on the registry's legacy bare-list status (`ten-hour-20261015`), so no gated cohort could admit. Its 29 scheduler and batch tests pass.
  - C15c (0 started) is replaced by `laya-c15d-2123-miniship-20261021`: same arms and seeds (steelbill/vacancy/foundation × 79400–79449), fixed pool 30, gate-locked admissions. Its first attempt on 4f7a88b admitted nothing and is preserved.
  - C14b is drained, not killed: a `_legacy-drain-gate` record (unit `laya-legacy-drain-gate`) caps only legacy supervisors at 0, so its in-flight runs finish. Its remaining seeds 79435–79474 continue as `laya-c14c-2123-miniship-20261021` (80 jobs, ports 37200+).
  - All future cohorts use harness 81f0735+ with `{"concurrency": 30, "adaptive": false, "host_budget": {"registry": ..., "stale_seconds": 180, "admit_interval_seconds": 10, "admit_min_available_gb": 4.5}}`. It has no --resume.

- 2026-10-21 22:20 IST: The steel-bill fact (`934f9cb`) looks harmful to C2. C14 steelbill vs ref C2 discordance is 4:5 (C14b) + 1:9 (C14c, n=20, p≈0.02), 5:14 combined (p≈0.06). In seeds where ref reached C2 and steelbill did not, steelbill chose op.steel 4–5× less often (8 vs 36; 12 vs 64), extend ~2.5× less and retire_starter ~2× less. The unchanged-failure penalty suppresses steel-seed retries even after the missing items were produced and consumed elsewhere (the test is stock-based, not production-based), so steel, and with it poles for extension and retirement, arrives late. C15d's vacancy/foundation arms inherit steelbill (adjacent contrasts stay valid). Prepared `work/miniship/laya-nosteelbill-20261021`: c1b8680 → vacancy `0bf1bdc` → foundation `7fd2320` (cherry-picks of ef38247/d8e3f7c; 217 focused tests pass; fast gate 1186 + the known firewall failure). It will be queued as the next cohort when C14c completes. No adoption or removal decision until C14 completes.

- 2026-10-22 02:55 IST: Miniship hard-stopped at ~02:39 IST (the journal ends mid-stream, with no shutdown; it rebooted at 02:44). The CPU had run at 87–93 °C for hours, the user later confirmed an accidental power cut (not thermal). Only the enabled host scheduler came back; the transient cohort units did not. The jobs left "running" (C14c 4, C15d 8) had dead worlds and stale reservations that blocked admissions. Harness `65d5613` (branch work/miniship/host-scheduler-harness-20261021; 31 tests) adds `--resume` for scheduler-gated cohorts: terminal jobs are kept, unfinished work reruns in fresh `<id>-r<attempt>` worlds with the dead attempt recorded and its reservation released. C14c (76 completed kept, 4 rerun) and C15d (108 kept, 8 rerun, 34 queued) were restarted under it; pre-resume statuses are kept as `job-status.before-reboot-resume.json`.

- 2026-10-22 04:45 IST: C14 complete (C14b 71 completed + C14c 80/80). 75 matched pairs. C2 ref 26 / steelbill 18 (discordant 10:18, p=0.19); C5 ever/final 25/11 vs 20/5; new Laya C6 21 vs 17. No adoption; steelbill (`934f9cb`) is dropped from future bases. No errors or safety stops. [Result](docs/rl/Laya_C14_Result_2026-10-22.md).

- 2026-10-22 04:50 IST: Launched C16 `laya-c16-2123-miniship-20261022`, a cumulative cohort without steelbill: ref `c1b8680` → vacancy `0bf1bdc` → foundation `7fd2320` × seeds 79400–79449 = 150 jobs. Harness `65d5613` (scheduler-gated; `bash run.sh --resume` after any host stop), ports 37300–37329/30300–30329, admission until 2026-10-23 06:00 IST, cap 100000 s. A first launch failed on a run.sh argument bug before any job and is preserved as run.attempt0-argerror.out.

- 2026-10-22 06:00 IST: Loan-release fix `d39b6c7` (branch work/miniship/laya-nosteelbill-20261021, on top of foundation 7fd2320). Root cause (local read-only codex study `checkin_helper_runs/release-owner-missing-20261022.md`): `_submit_bootstrap_loan` restored loans that `_start_bootstrap_loan` had only proposed, before their requester tag existed (missing chemical predecessor, or bill already met). C13 shows 98 `release_owner_missing` refusals, all labelled infrastructure_collision and retried. A new donor is now `proposed` until configured, and restoring a proposed loan is a no-op. Refused mall-cell releases now raise a typed `mall_cell_lifecycle_conflict`; the Lua ownership check is unchanged. 3 new regressions pass; wide mall/Laya/bootstrap suites 1154 passed (only the 2 known acceptance-reporting failures); fast gate 1189 passed + the known firewall failure. Not yet in a cohort: next cohort C17 = foundation 7fd2320 vs release d39b6c7 when C15d completes.

- 2026-10-22 06:35 IST: C15d complete: 150/150, no errors. C2 steelbill/vacancy/foundation 21/25/27; C5 ever/final 21/2, 24/3, 21/3; new Laya C6 21/21/18. vacancy vs steelbill C2 12:8, C3 13:7; foundation vs vacancy C2 13:11, new C6 10:13. Everything is within the A/A spread, so no adoption; all arms carry the dropped steelbill. [Result](docs/rl/Laya_C15_Result_2026-10-22.md).

- 2026-10-22 06:40 IST: Launched C17 `laya-c17-2123-miniship-20261022`: foundation `7fd2320` vs release `d39b6c7` × seeds 79400–79474 = 150 jobs. Harness 65d5613 (scheduler-gated; `bash run.sh --resume` after a host stop), ports 37400–37429/30400–30429, admission until 2026-10-23 10:00 IST, cap 110000 s. It shares host slots with C16. Mechanism check: release_owner_missing/collision counts should fall in the release arm.

- 2026-10-22 15:05 IST: Loan-credit fix `e37464d` (branch work/miniship/laya-nosteelbill-20261021, on top of release d39b6c7). Root cause (local read-only codex studies `checkin_helper_runs/c16-stop-classes-20261022.md`, `c16-starved-loan-20261022.md`): a bootstrap loan's prerequisite credit (`completed_step_targets`) never decreased, so after the final recipe consumed the credited items a renewed batch looked funded and starved. C16 ended with 12 ingredient-starved pole loans on zero network iron-stick, 8 still carrying credit. Renewing a finished final batch now charges its crafts against the credits of that recipe's ingredients and replans on what is left. 2 new regressions; fast gate 1191 passed + the firewall failure present since c1b8680. A second fault (the rotating-mall whole-bill check restores a live pole batch during delivery, 2 runs) is not addressed.

- 2026-10-22 15:09 IST: Launched C18 `laya-c18-2123-miniship-20261022`: release `d39b6c7` vs credit `e37464d` × seeds 79400–79474 = 150 jobs. Harness 65d5613 (scheduler-gated; `bash run.sh --resume` after a host stop), ports 37500–37529/30500–30529, admission until 2026-10-24 06:00 IST, cap 150000 s. Shares host slots with C17. Mechanism check: final starved loans with zero stock of a credited prerequisite should fall in the credit arm.

- 2026-10-22 16:40 IST: C16 complete: 150/150, no errors. C2 ref/vacancy/foundation 15/25/35; C3 6/22/22; C5 ever/final 13/3, 24/4, 25/4; new Laya C6 11/21/22. vacancy vs ref C2 16:6, C3 18:2 (p=0.0004), C5 14:3, new C6 13:3; foundation vs vacancy C2 13:3 (p=0.021), later milestones level. **Adopted per docs/rl/laya_variants/README.md as the Laya cohort base:** vacancy `0bf1bdc` and foundation `7fd2320` on branch work/miniship/laya-nosteelbill-20261021 (cohort experiments only; no production promotion). C5 final remains the plastic-plateau caveat. [Result](docs/rl/Laya_C16_Result_2026-10-22.md).

- 2026-10-22 20:20 IST: C17 complete: 150/150, no errors. C2 foundation/release 60/59; C3 39/39; C5 ever/final 47/12, 45/7; new Laya C6 40/41. Pairs: C2 11:12, C3 18:18, C5 16:18, C5 final 7:12, new C6 19:18. Loan-release refusals 169 in 30 worlds → 1 (typed) in 1; collisions 171 → 0; untyped 50 → 24. **Not adopted by the endpoint rule** (no target completion gain); kept in the experimental line as a lifecycle correctness fix, and both C18 arms carry it. [Result](docs/rl/Laya_C17_Result_2026-10-22.md).

- 2026-10-22 20:30 IST: Started-foundation fix `b372672` (branch work/miniship/laya-nosteelbill-20261021, on top of credit e37464d). C17 stop classes (local `checkin_helper_runs/c17-stop-classes-20261022.md`, verified in `c17-stone-c3-verify-20261022.md`): once a plate's product stopped being short, the menu kept only its starter retirement, so a foundation left at a coverage wait or a failed retirement was never offered again (7 release C2-but-never-C3 runs; 3 with only the stone starter built). A foundation the world observes not ready now stays selectable after its plate is satisfied; nothing past it is invented. 10 further C3 failures (six furnaces built, some idle) remain unexplained because the journals do not persist the foundation survey tuple. Tests: test_laya_ops_runner 39 passed; fast gate passed apart from the known firewall test.

- 2026-10-22 20:31 IST: Launched C19 `laya-c19-2123-miniship-20261022`: credit `e37464d` vs started `b372672` × seeds 79400–79474 = 150 jobs. Harness 65d5613 (scheduler-gated; `bash run.sh --resume` after a host stop), ports 37600–37629/30600–30629, admission until 2026-10-25 06:00 IST, cap 190000 s. Queued behind C18.

- 2026-10-22 20:43 IST: Prepared Starship integration of current no-steel runtime, shared scheduler and owner C17/C19 records, preserving b372672. Added stock/flow and exact foundation evidence, next-craft loan/WIP checks, persistent scheduler soft limits, separate C6/fault accounting and outcome export; extracted the selected-operation session to restore the import boundary and isolated mocked status writes. Evidence: 383 focused passes; required fast gate 1238 passed/3 skipped (no firewall deselection). Lifecycle: handler activation and isolated fresh cohorts only after active ownership; no remote deployment, server restart or 55-minute-loop change. See docs/rl/Laya_Review_Fixes_2026-10-22.md.

## 2026-10-23 14:22 IST — shared steel dependency admission

The compact steel seed can use a healthy owned iron starter before replacement submission when the exact outstanding iron-refinery bill has a steel-dependent live recipe closure. Canonical seed funding, foreign reservations, site checks and measured retirement requirements remain in force. Evidence: 55 steel/bootstrap regressions passed; curated fast gate passed (1,236 collected, one skipped). This is offline behavior evidence, not cohort adoption. Lifecycle: new frozen disposable runners only; active C22/C23 unchanged.

## 2026-10-23 14:29 IST — durable cohort controls

New frozen batch supervisors support idempotent run/drain/queued cuts, immutable original plans and append-only attempt records, predecessor admission without reserved slots, a single-owner lock, live reports and cleanup-failure reservation retention. Interrupted queue retries preserve full attempt evidence and refuse live owned processes. Evidence: 38 batch/scheduler checks including a real child-process boundary passed; fast gate 1,241 passed/one skipped. Runtime performance/adoption is unmeasured. Lifecycle: freeze new supervisors; no installed scheduler or active C22/C23 changes.

## 2026-10-23 14:38 IST — unfinished-loan credit backing

Starved unfinished final batches now replan unsupported direct ingredients against current allocatable supply, owned cell WIP and assigned cargo. Fed/unknown cells keep their receipts; reduced credit persists through the existing tag/transaction path. Evidence: 283 stock/loan checks passed, then all90 material-reservation checks including full partial-batch reconfiguration passed; fast gate1,248 passed/one skipped. No live/cohort benefit claimed. Lifecycle: new frozen disposable Python runners; no installed mod change.

## 2026-10-23 15:39 IST — research objective and coherent ops recovery

Added opt-in level-aware mining-productivity-4 completion, funded bot-built lab/requester operations, explicit guarded research selection, selected production scaling and a measured 0.25→0.5→1→2+ science-demand curriculum. Coherent ops bundles bind paused save, controller/ledger/RNG/research state, source/mod/engine identity and cumulative budgets; legacy science-readiness stays separate. Evidence: 92 focused tests passed; fast gate 1,281 passed/one skipped. Disposable r5 mechanics observed one working lab, delivery and consumption of all four packs; immutable-bundle review verified tick241561 and all three remaining choices after actual reload. The original validation assertion failed because its caller mutated the recovery prefix; retain it and the correcting recovery-review.json. This pre-supplied fixture is not earned research completion, corpus eligibility or adoption. Lifecycle: freeze new disposable Python runners; no mod redeploy, scheduler change or active C22/C23 restart.

## 2026-10-23 15:41 IST — C24/C25 dependency-gated queues

Started bounded disposable C24/C25 waiting supervisors on frozen verified harness1b2b7da, each120 jobs and zero admitted/reserved worlds at verification. Separate steel/credit-only arms preserve legacy540000-tick/6000-second windows, rotated seeds and distinct ports. They wait for predecessors plus explicit evidence-backed baseline settlement; no candidate is auto-adopted. Main-repository freeze.json files bind source/mod/harness hashes and the isolated C25 patch. Fresh heartbeats, reports and four-day/four-hour systemd limits verified. Lifecycle: automated owner closes C22/C23 and releases a matching baseline gate, or preserves/cancels these plans and creates new freezes if the base changes; no active-arm edit, installed scheduler change or mod redeploy.

## 2026-10-23 16:08 IST — Starship frontier experiment source verified

- Added opt-in temperature, family-balanced exploration and factual binary novelty, plus research-demand growth factors with unchanged .25 start and earned acceptance. Defaults remain unchanged; experimental sampling modes fail closed for ops capture/resume.
- Preregistered 14 candidates, three 60-job screens and one 12-job research pilot (192 planned), all <=60 per cohort. Control derives from9ac7937 and is explicitly experimental. Hard end2026-10-24 03:51:47 IST; see docs/rl/Laya_Frontier_2026-10-23.md.
- Evidence:110 integrated focused checks,41 harness/scheduler checks, fast gate1353 passed/1 skipped. Replay of1080 retained decisions confirms all12 policy/prior interventions change probabilities at some offered choices (exposure varies). No live outcome or adoption yet.
- Lifecycle: freeze this commit, run private disposable wiring checks, then start owned bounded Starship units; no installed-mod/GUI/production action. Existing dirty root and Miniship cohorts preserved. No subagents; one separate Personal_2 Sol6.1 xhigh thread supplied policy source for main-agent review/integration.

## 2026-10-23 20:35 IST — preserve fresh report ticks

Report deduplication now retains the observation tick: unchanged state observed later remains fresh evidence, with the existing retention bound. The new bridge-to-research regression reproduces the frozen frontier failure before the fix; 70 focused checks and the fast gate (1,354 passed, one skipped) pass afterward. All12 original research-growth pilots lack fresh final research evidence and measured demand-rung exposure; their ramp comparison is inconclusive. Sampling completed60/60, C6-v2 control7/12 versus experimental4–5/12; no adoption. Details and a separate12-job repair preregistration: docs/rl/Laya_Frontier_Report_Freshness_2026-10-23.md. Lifecycle: new frozen Python runners only, queued behind original screens; no active frozen source, mod, world or installed service changed by this fix.

## 2026-10-23 20:43 IST — frontier supervision recovered; repair queued

Recovered both requested Starship histories and transferred supervision to Miniship thread94dcfaf2-9531-4234-bde5-b6ae616de9b1. Old timers are backed up/disabled; new30-minute monitoring starts21:02:30 IST, terminal accounting03:53 Oct24, hard end unchanged03:51:47. Separate frozen3cf5f37 comparison:12 jobs, seeds81100–81105, control vs fresh-ticks, same host gate and6000s/540000 windows; verified r2 supervisor active with12 queued/0 reservations behind bottleneck. First supervisor's missing-module startup failure is preserved; no world attempt occurred. Independent raw-record accounting confirms0/3026 fresh research observations beyond initial300 ticks across the twelve original pilots (first500 records/job). Latest original counts: sampling60/60, research12/12, capital9 complete/15 running/36 queued, bottleneck60 queued. Lifecycle: only the new dependency-gated supervisor started; active frozen arms, installed mods, production and Miniship campaigns remain unchanged. See dated continuation handoff and report-freshness record; no live repair result or adoption yet.

## 2026-10-23 20:46 IST — shared 55-minute observation

On explicit user instruction, delivered Starship frontier ownership/evidence/deadlines and repair caveats to mcp:94f918da-f279-4342-bc00-c5d125c67e16 and updated its EXISTING55-minute schedule prompt. Cadence, thread binding and Miniship scope preserved; next check21:00:40 IST. Disabled this continuation's duplicate30-minute timer. Terminal accounting03:53 Oct24 and the03:51:47 hard end remain here; observer scope on Starship is read-only. Exact observer and continuation schedule IDs are separate in remote state; terminal cleanup must retain the shared Miniship55-minute schedule. Lifecycle: timer/message handoff only, no campaign change or new observer thread/schedule.

## 2026-10-23 22:27 IST — thermal frontier cuts prioritize freshness

At automatic target4 (96.05 C sample), drained capital/bottleneck and cancelled only queued jobs via four accepted idempotent requests. Verified capital44 completed/4 running/12 cancelled, bottleneck60 cancelled/zero started, freshness12 queued with admission ready. All72 cuts remain in original plans/ledgers; running worlds continue. Check-in exit0, zero freeze errors, frozen source and manifest/plan hashes unchanged. Lifecycle: existing supervisors apply controls; no code/mod deployment, Factorio restart, manual thermal override or extra observer. Last admission02:06:47 and hard end03:51:47 Oct24 unchanged; shared55-minute observation and03:53 accounting remain. See frontier protocol capacity amendment and remote operator-decisions/frontier-thermal-freshness-priority-20261023-v1.json.

## 2026-10-23 22:30 IST — acknowledged user-approved Starship thermal settings

Readback confirms the observer's explicit-user-directed host config change:temp_limit_c93->96 and temp_hard_c95->98, only two differences from config.pre-warm-20261023.json. Retained those values; target4, no manual override or restart. Frozen sensor helper falls back to hottest sysfs zone on Intel, so scheduler temp_c is not necessarily CPU package temperature; record sensor provenance before interpreting peaks. No measured capacity gain. Remote state/operator record and tracked handoff preserve this separate change; freshness priority, cuts, deadline and observer cadence stand. Lifecycle:none.

## 2026-10-23 22:34 IST — superseding user-approved 12-server host controls

Verified observer's second explicit user instruction:thermal thresholds200/210 effectively disable scheduler thermal gating; manual target12 has no expiry. Retained latest controls and preserved prior96/98 acknowledgement in remote history; backup config.pre-nothermal-20261023.json. Observed10 active worlds:4 capital+6 freshness (3 per arm),6 freshness queued, zero completed pairs. Freshness's frozen concurrency6 remains authoritative; no active manifest/source edit. All72 cuts, planned denominators and deadlines unchanged. Lifecycle:existing scheduler already applying controls, no additional target command or restart; no comparison result/adoption claimed.

## 2026-10-23 22:51 IST — freshness repair has live mechanism exposure

Matched repair cohort6 running/6 queued,3 per arm,0 complete pairs. Post-initial availability within first500 decisions/job:repair391/391 known vs control0/358; objectives391 vs0; option offers/selections535/15 vs6/0 under existing check-in semantics. Source/freeze checks pass. These are repeated samples, not trial denominators; no demand-rung advance, earned research, paired outcome or adoption. Remote immutable observation snapshot and state now record live mechanism evidence. Lifecycle:none; current host controls, cuts, hard end and55-minute observer continue.

## 2026-10-23 23:51 IST — target22 verified; original screens closed without confirmation

Applied direct user request host target12->22/no expiry, exit0/readback22, within max24; no controller notification per user. Original192 jobs terminal:120 completed/72 queued cuts. Steel30 leads positive10-pair C6 candidates (+.20,3:1), not adopted. No confirmation/A/A:60 full6000s budgets require>=16,364s on22 slots even ignoring occupied worlds, versus14,152s remaining. Recorded all candidate contrasts and variant hash in remote no-launch decision. Freshness6 completed/6 running,3 pairs,C6-v2 tied2/6; usable research1876/1876 repair vs0/1615 control, no earned completion observed. Lifecycle:existing scheduler picked up target; no restart, new cohort or source/freeze change. Hard end and55-minute cadence unchanged.

## 2026-10-24 00:17 IST —7200-second ordinary trial allowance

Set shared new-cohort default7200 and updated generators; longer15000-second research windows retained. C26/C27 safely drained/stopped before any admission, original records archived, both arms amended7200 and wrappers resumed behind C25; verified60 queued each, hashes intact, original unit horizon preserved. C25 and Starship live attempts keep6000 because no safe hot extension exists. Evidence:frontier37 focused/1358 fast+3 skips; Miniship36/1326+3 skips, all exit0; live waiting-supervisor readback verified. Lifecycle:new Python freezes for future defaults; no Factorio/mod restart or controller message. See docs/rl/Laya_Wall_Allowance_2026-10-24.md and data/laya-orchestration/wall-allowance-7200-20261024/.

## 2026-10-24 00:46 IST — Starship frontier terminal and cleaned

Reconciled219 planned:147 completed/72 cancelled before starting, no missing or live jobs. Frozen-source/asset checks pass; caught exceptions0, typed outcomes preserved separately. Freshness12/12 gives six pairs: usable research2313/2313 repeated samples vs0/2223 control, C6-v2 repair4/6 vs5/6, paired0:1; repair research known6/6 with0 earned, control unknown. No performance adoption or confirmation. Timing archived for all147 completions with a disclosed foundation8 C3 timestamp omission. Lifecycle:idle owned scheduler stopped, all owned units inactive/cgroups empty, both frontier timers disabled; shared55-minute Miniship observer retained, no controller notification. Host config retained. C26/C27 already7200 by safe pre-admission amendments; C25 stays6000. See docs/rl/Laya_Frontier_Result_2026-10-24.md and remote final-accounting.json.

## 2026-10-24 01:24 IST — 72 omitted Starship jobs resumed in a new authorized window

User approved a new12-hour window and no Opus controller notification. Preserved original terminal/cut evidence and froze separate completion ledgers:12 omitted capital jobs at6000s,48 original completions carried unchanged, then60 bottleneck jobs at7200s. Same original5fa9a89 source/arms/seeds/save/mods/ticks/predicates and Factorio2.1.23; original research staleness remains. Preflight verified exact mapped IDs, carried records and hashes; live01:27:17 evidence has12 capital running,60 bottleneck waiting, all3 owned units active, zero freeze errors and progressing private-world ghost construction. Lifecycle:bounded new user units launched, hard end13:21:41 IST/cohort end13:16:41; new completion-specific55-minute monitor next02:23:07 and terminal accounting13:23 (old-window IDs remain disabled), shared Opus/Miniship observer untouched, no controller message or adoption. See docs/rl/Laya_Frontier_Completion_2026-10-24.md; remote data/laya-orchestration/frontier-completion-starship-20261024/state.json.

## 2026-10-24 01:37 IST — six bottleneck worlds admitted alongside capital

On the user's conditional start6-more request, checked all12 fresh capital heartbeats/no observed process failures and10.9GiB available. Archived the zero-admission bottleneck contract/status and stopped only its waiting supervisor; sole batch change removed the capital dependency, with identical60-job plan/order,7200s budget, source/assets and13:21:41 hard end. Published temporary shared target18 before resuming under the remaining original systemd limit. Live01:37:16:capital12 running, bottleneck5 running/1 preparing/54 queued; capital supervisor PID unchanged. Freeze/state amendment and ledger receipts preserve original evidence. Lifecycle:only waiting bottleneck supervisor relaunched; both owned monitor prompts restore prior target22 after capital terminal/zero reservations. No Opus notification, no installed-mod/production change. See docs/rl/Laya_Frontier_Completion_2026-10-24.md and remote amendments/parallel-six-20261024-v1/.

## 2026-10-24 02:51 IST — capital complete; bottleneck continuing

All12 resumed capital jobs completed, joining48 carried records for60/60 with12 known C6-v2 endpoints/arm:control6, mall-flat6, foundation8 6, batch-free7, steel30 8; own-control discordances2:2,3:3,3:2,4:2. Strict C6 ever/final control and steel30 both2/1; no adoption/confirmation. Capital caught exceptions0, typed bugs separately nonzero. New72-job accounting at02:49:26 complete/18 running/28 queued, including bottleneck14/18/28; source/freeze checks clean. Lifecycle:capital unit inactive; restored prior host target22 after zero capital reservations, bottleneck retains18 concurrency and13:21:41 hard end. Both completion T3 timers were disabled through UI setEnabled calls and remain off; no next wake, optional re-enable preference pending, independent systemd deadline intact. No Opus message. Full result/receipts:docs/rl/Laya_Frontier_Completion_2026-10-24.md and remote state.capital_complete.

## 2026-10-24 04:02 IST — user re-enabled completion checks in this thread

On explicit user answer, re-enabled the existing55-minute completion monitor (next04:56:53 IST) and13:23 terminal accounting, both bound here; no duplicate timer/thread and no Opus message/shared-observer change. Readback/state records supersede prior disabled notes. Immediate check:58/72 new jobs complete,14 running,none queued; capital12 complete/original60, bottleneck46 complete/14 running, zero freeze errors. Lifecycle:timer-only change; no world/runner restart, manifest/budget change or hard-end extension (13:21:41 IST). Refreshed prompts handle current parallel workflow, final own-control accounting, owned cleanup and timer self-disable. See docs/rl/Laya_Frontier_Completion_2026-10-24.md.

## 2026-10-24 04:22 IST — awake-until-complete and one-shot automatic suspend armed

User requested keeping Starship awake until remaining jobs finish, then automatic sleep. At04:15:64/72 new complete,8 running, no freeze errors. Verified temporary sleep/lid block user service; it performs final checkin/cleanup then releases. One YubiKey-approved root command armed a one-shot SYSTEM service whose wait runs as djsm and triggers privileged systemctl suspend only after all mapped jobs terminal, no reservations/worlds, cleanup/freeze checks clean, owned units inactive and inhibitor released. Receipts/state retain commands and verification; actual suspend remains deferred. Lifecycle:no Factorio restart or persistent power/SSH/polkit changes; both existing prompts recognize intentional sleep/SSH loss and preserve final files for retrieval. No Opus message. See docs/rl/Laya_Frontier_Completion_2026-10-24.md and remote state.automatic_sleep.
