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

- 2026-09-25: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.
- 2026-09-25: Migrated the isolated deterministic server and four disposable native RL workers to private Factorio 2.1.20 headless runtime (`build 87512`). Managers, campaign/dashboard defaults, current docs, active fixture versions, and persisted read-data configs now agree. The 2.1.20 changelog removals are guarded: the training Lua does not read `LuaPlayer.spectator` or `fuel_category`; spectator-controller assignment remains in use. Evidence: 33 focused tests, Python/shell/Lua syntax checks, matching deployed mod trees, 2.1.20 launch banners for all five instances, and read-only RCON tick probes (deterministic `2075404`; workers 01–04 `6468928`, `749758`, `751062`, `750269`). GUI mod copies were synchronized; deterministic server and all four workers are running, while the deterministic Python runner remains stopped.

- 2026-09-25: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: tests/test_reliability_campaign.py. Prediction: sustained plastic. Reason: fix.

- 2026-09-25: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-09-25: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-09-25: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-09-25: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-09-25: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: tests/test_reliability_campaign.py. Prediction: sustained plastic. Reason: fix.

- 2026-09-25: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-09-25: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-09-25: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-09-25: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-09-25: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: tests/test_reliability_campaign.py. Prediction: sustained plastic. Reason: fix.

- 2026-09-25: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-09-25: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-09-25: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

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

- 2026-09-28: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-09-28: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: tests/test_reliability_campaign.py. Prediction: sustained plastic. Reason: fix.

- 2026-09-28: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-09-28: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-09-28: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

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

- 2026-10-09: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-09: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: tests/test_reliability_campaign.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-09: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-09: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-09: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

## [2026-10-09] AGENTS.md modernized; docs archived
- What: AGENTS.md reframed around deterministic showcase, Laya focus and RL test bed, with experiment/adoption rules and subagent log reading. Rotated status entries before 2026-09-25 into docs/archive/status/. Archived 33 superseded handoffs, finished experiment reports, multi-model plans and point-in-time reviews to docs/archive/{rl,deterministic}/ with retargeted links; replaced the August roadmap. Open leads are listed in docs/archive/README.md.
- Evidence: status rotation lost no lines; no new broken relative links in docs; 402 doc-referencing tests pass. The fast gate cannot collect because untracked tests import missing in-progress modules (training.laya_control, training.laya_mall_growth).
- Lifecycle: docs only; nothing to restart or deploy.

## [2026-10-09] Laya review and lifecycle session handoff
- What: added docs/rl/Laya_Session_Handoff_2026-10-09.md with the completed 320-trial results, isolated a68d52d lifecycle fix, 54-test/101-case/two-world evidence, host process snapshot and proposed matched evaluation/C1/causal diagnosis/training sequence. Updated the current evidence index while preserving archived links.
- State: implementation remains on codex/laya-lifecycle-obligations; no new campaign or helper launched. Main deterministic 2.1.21 process observed alive; its factory progress was not remeasured. Private Laya checks are stopped; cap 40 is admission only.
- Lifecycle: handoff/index/status left uncommitted like earlier local handoffs; no runner restart, deployment or server action performed.

## [2026-10-10 16:40 IST] Completed task worktree cleanup
- What: reversibly archived 28 superseded task branches/worktrees; local branch count 45 → 17. Preserved complete snapshots, original commit refs and untracked/ignored notes in data/archive/worktree-cleanup-20261010/. Archived and pruned six missing-worktree registrations.
- Evidence: patch-equivalence audit plus read-only amended-fix review; every archived regular file checked against its source, and archive checksums/ref targets reverified. Main/integration/current and historical campaign evidence retained. t-speed retained because live helpers use it.
- Lifecycle: no process, unit, server, mod, push or merge actions; no runtime restart required.

## [2026-10-10 17:01 IST] Historical branch cleanup, second pass
- What: archived 10 further branch labels and seven worktrees; local branch count 17 → 7. Complete snapshots and original ancestry are retained under data/archive/worktree-cleanup-20261010-round2/ and refs/archive/worktree-cleanup-20261010-round2/. Historical unique ancestry is preserved, not treated as adopted.
- Evidence: direct/cherry-pick audit, Luna content review, per-file archive verification and final original-ref checks. Both dirty legacy worktrees retained with identical status; unmerged helper-recovery retained. Separately registered detached run snapshots kept in place.
- Lifecycle: no merge, push, runtime change, process stop, mod deployment or unit start/stop. The former loop unit was already not loaded at the pre-cleanup check.

## [2026-10-10 18:04 IST] Post-publication baseline cleanup
- What: after verifying main/origin-main/integration at 23bc988, archived five consolidated branch labels and complete worktrees; local branches 7 → 2. Canonical baseline main; primary checkout remains codex/deterministic-checkpoint-fleet at 32f7d2d. Both dirty legacy worktrees were preserved in snapshots and separate patches, not discarded.
- Evidence: loop/helper code patch equivalence excluding conflict-resolved status entries; archive per-file verification, final archive SHA-256 checks and exact preserved-ref checks. Recovery: data/archive/worktree-cleanup-20261010-round3/README.md. All original unique ancestry remains reachable.
- Lifecycle: no merge, push, main checkout switch, reset, process or unit action. Former development paths retired; create future development/campaign worktrees from main. Detached run snapshots retained.

- 2026-10-13 09:42 IST — Personally observed fresh normal deterministic run `solo-20261013T034945Z` from startup to natural end, no subagents/helper: 247 five-second samples, zero observation errors; six-furnace iron replacement produced and pioneer retired, then `no_progress` at +1228s on the medium-pole/steel dependency cycle; copper mine unpowered, research not started. Evidence: `data/deterministic-observation/solo-20261013T034945Z/report.md`. Saved final world and stopped only this run; no code fix, deployment or restart required.

- 2026-10-13 10:02 IST — Fixed deterministic construction reservation leaks exposed by `solo-20261013T034945Z`: repeated transient submissions renew their bill; funding subtracts other unbuilt ghosts while excluding exact retries; additive ghosts cannot consume foreign reserved stock. Evidence: 122 focused tests passed, including native Lua and pre-fix failures; fast gate 952 passed/3 skipped/1 pre-existing import-firewall failure reproduced from HEAD. Lifecycle: Python controller restart/new frozen validation episode required; no mod deployment or Factorio restart, active campaigns untouched; no live rerun yet.

- 2026-10-14: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-14: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: tests/test_reliability_campaign.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-14: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-14: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-14: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-14: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-14: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: tests/test_reliability_campaign.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-14: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-14: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-14: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-14: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-14: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: tests/test_reliability_campaign.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-14: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-14: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-14: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-14: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-14: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: tests/test_reliability_campaign.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-14: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-14: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-14: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

## [2026-10-15 13:47 IST] T3 Code harness delegation instructions
- What: AGENTS.md routes delegation through the active harness; T3 Code inherits model/provider defaults without model or effort overrides, and falls back to direct evidence reading when delegation is unsupported.
- Evidence: reviewed instruction diff and git diff --check pass. Required fast gate stopped with 285 collection errors, including duplicate saved-run test modules under data/; no subagent launch was exercised. Log: /tmp/factorio-t3-agents-fast-20261015.log.
- Lifecycle: instructions only; no runner, mod or Factorio restart/deployment required. New sessions load the updated charter.


## [2026-10-15 14:19 IST] Explicit T3 delegation tool contract
- What: AGENTS.md names T3 MCP delegate_task/task_status, requires discovering both before delegation, prohibits native collaboration tools in T3 and falls back to direct reading when tools are missing.
- Evidence: reviewed documentation diff and whitespace checks pass. Isolated managed Codex 0.162.0 requests confirmed the upstream two flags retain native tools while adding agents.enabled=false removes them; normal inference completed in both arms. Required fast gate again stopped with 285 collection errors, including saved-run import collisions under data/; log: /tmp/factorio-t3-explicit-tools-fast-20261015.log. T3-owned delegation remains untested because its tools are unavailable here.
- Lifecycle: instructions only; new sessions load the charter. No T3 runtime patch, runner restart, mod deployment or Factorio restart performed or required for this documentation change.

- 2026-10-15: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-15: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: tests/test_reliability_campaign.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-15: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-15: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-15: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-15: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-15: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: tests/test_reliability_campaign.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-15: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-15: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.

- 2026-10-15: Campaign fix FIX-0001 passed persistent-primary review and verification. Kind: behavior. Files: fix.py. Prediction: sustained plastic. Reason: fix.


## [2026-10-17 07:25 IST] Laya structural audit
- What: audited the ops/toolkit boundary and recent Luna/run evidence; recommendations in docs/rl/Laya_Structural_Audit_2026-10-17.md. No behavioral change or adoption.
- Evidence: all 272 completed-cohort terminal summaries checked: 0 C4, 265 stalls, 423 untyped_stuck outcomes; day snapshot separately 12/64 complete. Verified productive-pipe false stall and 125-repeat chest/plastic handoff; 46 scoped tests passed. Required T3 delegation unavailable, so existing Luna reports and raw evidence were read directly.
- Lifecycle: none. No runner/server/mod/save changes; user-systemd liveness unavailable due bus permission. Diagnosis only, not committed.


## [2026-10-17 07:49 IST] Weekly Laya reset inspection
- What: expanded structural audit across October 10-17 history, all 17 heads/47 worktrees, requested T3 session and the working-agent handoff. Report: docs/rl/Laya_Weekly_Reset_Audit_2026-10-17.md; exhaustive commit/branch evidence under docs/rl/laya_helper_runs/weekly-audit-20261017/. No implementation or adoption.
- Evidence: indexed 186 product commits (160 patch groups), 154 T3 snapshots (47 trees), 210 changed paths; reconciled 35-run failed observer loop, later campaigns/persistent segments, 24-job pilot, 216-job matrix and 272 completed fresh trials. Identified earlier research/progress/queue/power repairs missing from recent lineage. Current checkout 46 focused passes; immutable e12e04c recheck 170/171, isolated stale-fake failure preserved.
- Lifecycle: none. No campaign, source save, service, mod, code branch, merge, push or model was changed. Fresh-start rule retained; no live checkpoint/resume authorized. Diagnosis only, not committed.


## [2026-10-17 23:00 IST] reset-chest cohort + UPS-gated sidecars complete; normal rung run
- What: reset-chest-20261017 (control a63e074 / rung f1469e1 / chest f40ef39) plus 13 sidecar slots (extra/ramp.py, add one slot while pooled p95>=110 and p98>=100 UPS) on the same frozen arms; helper OpenCode v2 flag fix 04f3458; normal run `laya-normal-rung-20261017` (rung + fix) with built-in Muse helper and a Sonnet 5.5 T3 observer.
- Evidence: 282 planned jobs: 117 completed, 165 not_started_deadline, 0 crashes. 36 seeds matched across all arms: proxy C2 control 19 / rung 23 / chest 7; strict C2 14/17/2; C4 2/2/0; untyped_stuck 30/21/16. Chest is a clear C2 regression; rung vs control is not established. Laptop ceiling ~10-11 concurrent at speed 2 (12 gave p98 90-99). Ramp only grows; it needs a shrink path.
- Normal run (seed 76900): stalled at C1 after 83 decisions/19.2 game-min; Sonnet observer: iron-bound belt bill (132-145 belts) starved while gear/cable/brick cells kept consuming plate, belt/splitter loans deferred with no free assembler; no gate-raise/throttle/add-iron option offered. Findings: docs/rl/laya_helper_runs/normal-rung-20261017-s76900-sonnet/findings.md.
- Lifecycle: all units stopped. Launch caveat: the runner's detached helper lives in the unit cgroup and is killed at RUN END unless the unit uses KillMode=process; Muse final review re-run manually as laya-normal-rung-helper-20261017. Nothing to redeploy.

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

## [2026-10-21 01:30 IST] T3 tool-call audit workflow guidance
- What: Factorio T3 audit found long supervision sleeps, repeated test gates and self-matching process polls. Added guidance for scheduled wakes, process/session completion, test exit status and result reuse; existing pytest testpaths=tests is retained.
- Evidence: Read-only extraction of 1,730 stored Miniship tool calls; pytest configuration parsed and documentation diff checked. No tests or campaigns launched on this host.
- Lifecycle: Future agent/test invocations only; active frozen campaigns unchanged.

## [2026-10-21 02:38 IST] Smaller fast scaffolding test with retained full composition coverage
- What: Fast payload check uses a real four-roboport backbone; all original assertions still run against the complete electronics bundle in the slow composition family. Production code unchanged.
- Evidence: 24 infrastructure/taxonomy tests pass, including complete bundle checks. Fast gate 38.69 s; exit 1; failures: test_current_repository_obeys_import_firewall. Starship measured the isolated check at 7.84 s -> 0.30 s and retained exactly the same 959 fast test IDs.
- Lifecycle: Future test invocations only; active frozen campaigns unchanged.


## [2026-10-26 20:36 IST] Laya offline-learning discovery and preparation
- What: Prepared the data/learning/evaluation design and pinned model review; derived a 192-row discovery sample and full C35–C38 480-job ledger. All sample rows remain train_ready=false.
- Evidence: 4,664 decisions inspected from 16 trajectories; all 192 schema/source/behavior/terminal/lineage round trips pass. Seven indexed source hashes matched; nine whole sources newly hashed. One common starting-save hash; 292 inspected menus exceed the current 20-option model contract (max 29). See docs/rl/Laya_Offline_Learning_Prep_2026-10-26.md and data/laya-training-prep/20261026/.
- Lifecycle: Nothing. Raw corpus, active C39, services/schedules and Starship untouched; no training, commit or push. Full curation and label/transition/menu compatibility gates precede the later Starship phase.

## [2026-10-26 21:49 IST] Broader System One model and fine-tuning discovery
- What: Expanded the shortlist to Jiwo v2 for general quality, Vela-309M for native 29-choice compact inference, and d1-600M for a documented Vulkan route. Pinned training-source review recommends Kev-0.8B as the first new fine-tune with existing Laya as control; Vela needs a trainer adapter. See docs/rl/laya_helper_runs/training-discovery-20261026/system-one-{shortlist,finetune-review}.md.
- Evidence: Two completed delegated source/metadata audits, four additional pinned training-source snapshots, and read-only Miniship identification (Radeon 680M, 3 GiB firmware VRAM, about 28 GiB host RAM, Vulkan). Complete-menu, soft-target, context and augmentation contracts reviewed; document links/whitespace and desktop/mobile comparison previews pass. No model inference, training, 8 GiB fit or Factorio improvement demonstrated.
- Lifecycle: Nothing. Documentation and source snapshots only; no model weights, installs, SSH, runtime/service/schedule/world changes, commit or push. Raw corpus and unrelated dirty changes preserved.

## [2026-10-26 21:55 IST] Prepared model/checkpoint cohort sequence
- What: Recorded the user's direction to compare models and fine-tuned checkpoints across upcoming cohorts. The roadmap separates base-model screening, Kev/Vela adaptation, within-run checkpoint progression and untouched-seed confirmation; proposed budgets and conditional advancement are explicit, with no campaign IDs allocated.
- Evidence: Clean-reference launcher inspection confirms guided/uniform policy selection; a learned-policy adapter and proof of checkpoint decision authority are admission requirements. Plan links/whitespace/job arithmetic and desktop/mobile sequence previews pass. Plan: docs/rl/Laya_Model_Checkpoint_Cohort_Plan_2026-10-26.md.
- Lifecycle: Nothing. Planning only; no training, inference, Starship access, queue/service/controller/world change, commit or push. Existing campaigns and unrelated changes preserved.

## [2026-10-26 23:29 IST] Reviewed data, checkpoint runtime and Starship fit launched
- What: Implemented and locally committed complete-menu model policy/HTTP runtimes, behavior telemetry, per-arm capacity and indexed weak export as 2511e4b in the isolated model-launch branch. All120 base-model IDs are prepared outside the registry; C39 remains active and unchanged.
- Evidence: 73 focused checks; fast 1,345 passed/one skipped; independent reviews found no material defect. All three released models pass37-option inference. Final weak-v3 has2,506 reviewed examples from360 trajectories/109,446 decisions. Starship CUDA fit started23:07:05IST,1729train/217steps/eight-hour cap; step20 observed finite. BF16/SDPA/TF32/checkpointing active; reference DeltaNet/conv kernels remain. Separate FLA0.5/causal-conv1d1.7 imports pass; full GPU performance/numerical benchmark awaits current fit completion.
- Lifecycle: Active Starship warm-start service; no restart/reboot/promotion/push. Miniship needs C39 terminal/base cleanup, final-bundle/capacity qualification and separately counted wiring smoke before120 jobs. Existing owned wake updated to30min; next23:58:13IST. No production, installed-mod or host-target changes.

## [2026-10-26 23:38 IST] Switched Starship warm start to native fused kernels
- What: At the user's instruction, stopped the reference fit (last logged50/217) and restarted from released Kev under laya-kev-warmstart-fused-20261026.service at23:36:39IST, with identical data/seed/recipe and distinct checkpoint-A-fused/log/freeze. Original evidence preserved; no intermediate adapter was available.
- Evidence: Longest7695token fused CUDA preflight completed one real optimizer step, zero truncated/rejected rows, finite gradient norm56.66061,3.25GBpeak device allocation. Native FLA0.5/causal-conv1d1.7 selected;3116original source/asset hashes unchanged and474additional environment/attempt files frozen. Full fit reached10/217,finite loss1.873,1.057s/record vs reference first10interval4.570 (early4.32× gain); full-run steady throughput and numerical-gradient comparison remain unmeasured.
- Lifecycle: Fused fit running with fresh eight-hour cap; existing30min continuation now follows it (next23:58:13IST). Nothing else to restart/deploy. Miniship120jobs remain gated behind unchanged C39/base/capacity/wiring. No reboot, root, GPU-unlock, production or push action.

## [2026-10-27 00:02 IST] Kev fused warm start completed and frozen
- What: Saved and hashed the final adapter/head/tokenizer bundle before evaluation. New held-out evaluator prepared and reviewed; independent calibration sidecars use calibration only.
- Evidence:217optimizersteps,all1729records,0truncated/rejected,4,038,158forwardtokens,24.12min,3.06GBpeak CUDAallocation;finitegradnorms. Checkpointmanifest8cb871fd49386afaeec0794fe755b4fcf84ba2e1407212dcdc99f1645bb4ce26. Eight evaluator tests pass; actual inference pending. Benchmark context-limit helper defect corrected; reference three-step retry active.
- Lifecycle:Trainingservice exited successfully; no further full fit. Run sequential kernel/evaluation checks with CUDAfree.120modeljobs remain unadmitted while C39has3running;no base decision/adoption/push.

## [2026-10-27 00:14 IST] Kev held-out weak-imitation evaluation completed
- What: Completed released-versus-warm-start GPU evaluation and froze independent calibration sidecars plus an offline artifact bundle; checkpoint files unchanged and verified local backup retained.
- Evidence: Both777-row evaluations have0errors;all1554source/menu/label/target joins verified. Dev401weak-label agreement144/401(35.91%)→367/401(91.52%);calibration-only fitted NLL1.599540→0.211850,Brier0.750600→0.127789. Eight evaluator tests pass. Matched kernel stress test10.225×faster,with measured BF16 gradient differences(cos0.993909,relativeL2=0.110220);no numerical-equivalence claim. See docs/rl/laya_helper_runs/model-launch-20261026/training-result.md.
- Lifecycle: Starship fit/evaluation units complete,GPUidle;no further full fit or automatic adoption. C39still119complete/1running;120base-model jobs remain unadmitted pending terminal/base/capacity/wiring gates. Existing30min wake continues,next00:28:15IST. No push or production/host-target change.

- **2026-10-27 00:31 IST:** Jev checkpoint feasibility checked against official TypeSafe docs: public customer fine-tuning/LoRA unavailable; Jev/Jiwo identity clarification pending, no fit/upload. C39 now 120/120 completed and unit inactive/success; final outcome and exposure audits started through Personal_2 T3, base decision and full cleanup verification pending. Prepared model cohort remains unlaunched. Lifecycle: no launch, restart, deployment or push.

- **2026-10-27 00:47 IST — C39 final:** 120/120 completed, unit inactive/success, scoped live processes and registry reservations empty. Strict C6-ever reference13/60 versus integrated0/60 (paired P=0.00024414); C2/C3/C4 regress, active C5 incomparable. Retain reference9d9b279 for the model cohort; no integrated adoption. Final result: docs/rl/Laya_C39_Result_2026-10-27.md. Lifecycle: no restart/deploy/push; memory qualification and four-job wiring smoke still gate model admission.

2026-10-27 00:56 IST — New English Laya weak-v3 fit launched on Starship after both real accumulation8 kernel profiles and fresh-process exact save/reload checks. SDPA native BF16 flash/efficient attention + fused AdamW selected; 217 updates/1729 records, no truncation. Flex gives no clear aggregate speed gain; first-gradient cosine0.9529/relativeL2 0.3173 is not equivalence. Same frozen source/data, bounded8h unit laya-english-warmstart-20261027; no automatic adoption. Three dedicated final base-model CPU servers started in own19GiB/no-swap slice for capacity qualification; no Factorio model jobs admitted yet. Lifecycle: leave fit intact; qualify capacity then four-job smoke. No push/admin/unlock/production changes.

2026-10-27 01:08 IST — Authorized120-job released-model cohort launched (30seeds xguided/Kev/Vela/Jiwo), all120planned; initial3jobs admitted, not120completed. C39 final reference retained; three simultaneous37-option serving checks and all4separatewiringjobs pass (7verifiedlearnedchoices,0backend/tool/safety/untypedfaults,cleanup complete). Common concurrency3/perarm1,4GiBworldpool under19GiB own aggregate slice; originalunqualified4proposal preserved in capacity-v2 freeze. Fullreport docs/rl/Laya_Model_Cohort_2026-10-27.md. Laya optimizedfit stillactive,143/217lastobserved finite. Lifecycle: leave activefreezes untouched;30minboundedmonitor/final accounting byOct31 00:20IST. No push/admin/production/mod/hosttarget changes.

2026-10-27 01:18 IST — English Laya optimizedfit completed217/217updates,1729records,0truncated/rejected,4,261,929tokens,1207.19s(20.12min),3,496,233,984peakallocated/5,888,802,816peakreservedCUDAbytes,allgradientsfinite. Finalfreshreload exact0logiterror. Manifest e325e5bb2028ff744b8ee2ace3ec27aa242ccee0bc18b808dbc93fdc6da65f72 freezes10files/336,304,724bytes; verifiedlocalbackup data/laya-models/checkpoints/laya-english-weak-v3-20261027. Released777rowevalactive,trainedeval/calibrationstillpending. Main120cohortactive3running/117queued, noadoption. Lifecycle: serializeevalsafterGPUrelease;leavecampaignfreezealone. No push.

2026-10-27 01:27 IST — English Laya warmstart COMPLETE:217updates/1729records,20.12min,3.26GiBallocated/5.48GiBreserved,0truncated/rejected; finalfreshreload exact. Both777rowevals0errors/1554source-token-menu-targetjoins; devweakagreement39/401(9.73%)→212/401(52.87%),paired39both/173trainedonly/0baseonly/189neither. Cal-onlyfitteddevNLL1.656523→1.474701,Brier.791212→.695224,ECE.126961→.229601(worse);baseT100bound,trainedT.972739. Localverified10file336.3MBcheckpoint+6fileEnglishbase; manifest e325e5bb2028ff744b8ee2ace3ec27aa242ccee0bc18b808dbc93fdc6da65f72; offlinebundle3126d91fdc7194ec5c9e30e28d436bb91c39b360c8a374f4f213a383d1dea1bd. Report docs/rl/laya_helper_runs/model-launch-20261026/laya-training-result.md. Weakimitationonly; newserializerrequireslaterCPU/servingqualification, noRL/adoption. StarshipGPUfree,do not repeatfits/evals. Main120basecohortactive3running/117queued,lastboundedread; retain30mincontinuation/finalaccountingbyOct31 00:20IST. Lifecycle: noStarshipactionneeded;leaveactivecohortfreezealone. No push/admin/production/mod/hosttargetchanges.

- 2026-10-27 01:35 IST: Reconciled model-launch state fields that still claimed the already-active120-job cohort and completed4-job wiring were unlaunched; original state archived. Bounded health receipt `data/laya-orchestration/model-launch-20261026/supervision/20261026T200444Z/health.json` verifies147 learned choices/146 completed input-menu-journal joins with0backend/telemetry errors and no memory events;3running/117queued. Separate typed-fault receipt retains Vela stone-extension infrastructure_collision despite runnerfailures0. Evidence is interim, no winner/adoption. Lifecycle: none; active freeze/services unchanged, owned30min continuation retained through final accounting, no push.

- 2026-10-27 02:06 IST: User-requested model-cohort capacity increased to15actual running worlds (guided4/Vela4/Jiwo4/Kev3),4completed/101queued. Original7started attempts preserved;113never-started jobs transferred disjointly into4new frozen capacity segments, logical denominator120unchanged. New wrapper f08eb73:4focusedtests and1347fastgate exit0; first15health shows7.09GiBavailable,0modelerrors/0memoryevents. Source administrative cuts are ownership transfers, not experiment outcomes; final accounting must join5registries and show capacity strata. Lifecycle:4bounded user units launched, own aggregate slice25GiB/new11GiBworldpool; no original-world restart, root/production/mod/hosttarget changes or push.

- 2026-10-27 02:09 IST — Transfer-aware supervision:6completed/15running/99queued;120unique logicalIDs,21uniqueattemptstarts,113administrative source transfers verified. All6completed summary/strict-ever/final acceptance, decision/outcome counts and cleanup receipts match raw journals.359learnedchoices and357completed state-render-menu-journal joins verified,0backend/telemetry errors;6.91GiBhostavailable, freshheartbeats,0memoryevents. Completed faults retained separately:1untyped_stuck and1infrastructure_collision despite failurecounter0. Evidence `data/laya-orchestration/model-launch-20261026/supervision/20261026T203905Z/health.json`. No interimwinner; capacity strata retained. Lifecycle:none;healthy units/worlds untouched, same30minschedule next02:28:19.804IST. No push.

- 2026-10-27 02:14 IST — User conditional request to add3more evaluated; retain15. Live host available6.91GiB, world pool8.30GiB. Prior measured simultaneous full-menu serving peak14.36GiB versus current non-world charge9.84GiB implies4.53GiB reserve; adding3worlds at current mean charge estimates1.66GiB more, leaving~0.72GiB host available during that peak (~2.38GiB at15), below existing4.5GiB admission floor. This is a conservative envelope estimate, not a new stress benchmark or proof of exact future usage. No capacity/host-target/freeze/service/world changes. Transfer-aware live status9completed/15running/96queued,120logicalIDs,0observed backend errors or memory-limit/OOM events,maximum heartbeat age9.92s. Evidence `data/laya-orchestration/model-launch-20261026/capacity18-review-20261027/20261026T204428Z/assessment.json`. Lifecycle:none; healthy runs continue; recheck memory during the existing30min supervision. No push.


- 2026-10-27 02:19 IST — User-authorized background RAM cleanup: gracefully closed unused ChatGPT desktop app and its child Codex backend; all original app processes exited, removing approximately732MiB pre-close proportional resident usage. Host available now7.48GiB (live workload changes confound exact host delta). T3/CLIProxyAPI/host scheduler/model servers/all4capacity supervisors retain exact PIDs+invocations; unverified separate agent sessions preserved. Cohort stays15 concurrent; no host-target/freeze/world/policy edits. Evidence `data/laya-orchestration/model-launch-20261026/background-ram-cleanup-20261027/20261026T204901Z/final.json`. Lifecycle: desktop app closed; cohort and Personal_2 route continue unchanged. No push.


- 2026-10-27 02:49 IST — User-authorized isolated Vela FP32 Radeon/WebGPU smoke passed18recorded calls at78/813/1653tokens across original and reduced display modes; unchanged152tensor/graph external ONNX storage fixed two4GiB monolithic-loader OOMs. Two timestamp-instrumentation exits are retained; final units success/0, all finite/repeatable outputs. Desktop1280x720@60/scale1 plus wallpaper plugin disabled saved204.31MiB idleVRAM (799.46→595.15MiB), backups retained; lock remains secure. Long1653token case still uses373.71MiB client GTT, so not wholly within reservedVRAM/full-menu qualification. Bounded cohort check29complete/15running/76queued, same server/supervisor identities and0owned cohort memory events; no active GPU test. Report `docs/rl/laya_helper_runs/model-launch-20261026/vela-radeon-vram-test-20261027.md`; receipts `data/vela-vram-test/20261027/result.json`. Lifecycle: display/user-plugin change applied; cohort CPU packages/freeze/capacity unchanged; existing30min supervision continues, no push. No FP16 inference or Starship work.


- 2026-10-27 03:12 IST — Model cohort transfer-aware check:42completed/15running/63queued (host snapshot03:05:55IST); all120logical IDs/113administrative transfers preserved. Raw38completed jobs match1,978decisions/outcomes/38cleanup receipts;956choices/953source joins,5pendingrequests/3pendingsource rows,0backend/validationerrors. Retained inspected faults:10typed+2untyped across52started jobs (completed subset9typed+1untyped); runnerfailures0 is separate. Shared11GiBworldpool has947max reclaim events,0OOM/kill; parent25GiB localmax0, no new events in follow-up.24freeze identity checks pass;7.26GiBhostavailable/freshheartbeats. Receipt `data/laya-orchestration/model-launch-20261026/supervision/20261026T212924Z/health.json`. Lifecycle:none; capacity15 and active freezes unchanged; same30minschedule next03:28:22IST. No push.


- 2026-10-27 03:19 IST — User-authorized expanded Vela FP32 GPU test completed; separate loopback9172 inference session started and verified.71bounded stress calls through1,525packedtokens/29options pass;7positiveHTTPrequests(including3concurrent) shareoneworker,3invalid/oversized requests rejectbeforeGPU. Larger3072-class stress stoppedby4.5GiBhost-memoryguard after12successful calls through2,043tokens; noGPUcrash/OOM. Tested client peak sharedGTT389.43MiB, so GPU execution is not wholly reservedVRAM. Unit vela-gpu-fp32-standalone-20261027:12hbound/4GiBcap/noswap/hostguard. Cohort boundedcheck50complete/14running/56queued, all7CPUserver/supervisor identities unchanged,0OOMkills; raw38terminal audit remains separately scoped. Evidence data/vela-vram-test/20261027/expanded-result.json. Lifecycle:newstandalone GPUunit only; noFactorioadmission/CPUreplacement/capacitychange/push; same30minwake next03:28:22IST.


2026-10-27 03:35 IST — User requested observation from Starship. Running Vela game sockets are loopback-only; started separate2h user relay laya-vela-observe-20261027, Tailscale100.99.132.51:40424→127.0.0.1:40420, restricted to Starship100.92.154.27/32. UDP listener active; Starship Tailscale pong1ms. No GUI join asserted. CPU cohort and GPU standalone remain separate. Receipt data/laya-orchestration/model-launch-20261026/starship-observation-20261027/relay.json. Lifecycle:bounded relay only; no frozen campaign/server/mod changes or push.


2026-10-27 03:38 IST — Incremental cohort audit accepted: fixed03:28:43IST snapshot55 completed/15 running/50 queued; all55 raw-certified (38 carried +17 new),1,281 learned choices/source joins,8 pending requests/2 pending execution outcomes. Two new power_route_unavailable faults; inspected prefixes total12 typed+2 untyped,0 backend/tool/safety/runner failures. Parent rehashed199 line receipts and24 frozen identities. World13,150 max/reclaim events,0 OOM/kill,aggregate localmax0; serialized HTTP health timeouts are monitoring limits. Receipt data/laya-orchestration/model-launch-20261026/supervision/20261026T215842Z/health.json. Lifecycle:none; freezes/capacity15 unchanged; same30min continuation next03:58:23IST. No push.


2026-10-27 03:57 IST — User-authorized12h reviewed cohort sequence03:48–15:48IST set up. Scheduler quiet ceiling4 until05:18, natural drain (03:55 snapshot72 completed/11 running/37 queued), then ceiling15 subject fresh health. Same app controller now10min, next04:04:29; independent bounded quiet-restore/admission-cutoff timers installed. DeepSeek4.1Flash paid route failedfunds; free route design complete; Personal_2Codex accounting/admission review running. Complete current120+cleanup+reviews before selecting/freezing next matched single-factor cohort. No currentfreeze/attempt edits/reruns; no interimadoption. Newcohorts admissionend15:48, finalaccounting16:08, exceptional old-attempt drain bounded. Protocol data/laya-orchestration/model-launch-20261026/autonomy12h-20261027/protocol.md. Lifecycle: timed controls/continuation only; no production/push.


2026-10-27 04:01 IST — Personal_2accounting/admission research accepted (2a3c6c99244e). Final120report must add fresh-ever/predicate-version projection; prior55rawstatus certifications are not relabeled as that missingprojection. Guardedderivedfinaladapter+focusedCPUtests delegated in existingPersonal_2 route; no duplicateaudit or finalexecutionwhileactive. Task registry data/laya-orchestration/model-launch-20261026/autonomy12h-20261027/preparation-tasks.json. Budgetfuturecohorts byqualifiedcap/worst-case130minwaves, calibrationfactor explicit; no interimadoption. Lifecycle:none;quietceiling4/timers/activefreeze unchanged. No push.


2026-10-27 04:20 IST — Final-only accounting adapter parent-reviewed;17CPUtests pass,not run on activecohort. Fixed operational host collector to preserve hashed status bytes;synthetic concurrent-update regression passes and5/5livecopied hashes match;canonicalfastgate exit0(1626selected). Bounded04:18 snapshot80completed/4running/36queued,12.33GiBavailable,freshheartbeats,0OOMkills,worldmax35343unchanged/aggregate localmax0;raw55terminalcertification separate. DeepSeek12case latency review accepted with parent qualifications:45sourcehashes match,livegame advancesduringinference;no causalstrategy/adoptionclaim. Quiet4until05:18/finite12hcutoff/timers unchanged. Evidence autonomy12h-20261027/supervision/20261026T224825Z. Lifecycle:collector only;activefreezes/worlds/packages unchanged,no push.


2026-10-27 04:31 IST — Quiet-period delta review accepted: 25 new raw/status terminal joins plus55 carried =80 certified;362 new learned joins,1643 across inspected prefixes;10 prior pending records resolved,1request/2execution outcomes still captured pending. Parent checked218 critical line hashes and both5-file snapshots. New faults:2power_route_unavailable+2untyped_stuck;inspected totals14typed/4untyped,0backend/timeout/tool/safety/runner errors. Separate live04:30 snapshot81completed/4running/35queued,12.249GiBavailable,freshheartbeats,0OOMkills;worldmax35343unchanged,aggregate localmax0. Quiet ceiling4until05:18;both boundedtimerswaiting/GPU9172healthy;10mincontroller continues within03:48–15:48window. Not final120/versioned endpoint certification oradoption. Receipt data/laya-orchestration/model-launch-20261026/autonomy12h-20261027/supervision/20261026T230130Z/parent-review.json. Lifecycle:none;activefreezes/attempts/packages/powerprofile unchanged,no push.


2026-10-27 05:08 IST — Accepted seven new raw/status terminal audits (87 certified total), 1,865 learned choice/source joins and 5,977 observed outcomes in carried plus selected prefixes. Parent independently rehashed 88 current terminal/fault/pending receipts and five status copies. One new starter_deconstruction_failed fault is retained; inspected totals are 15 typed and four untyped faults, with three requests captured pending and zero backend/tool/safety/runner errors in this scope. Separate 05:06 IST host snapshot: 90 completed, four running, 26 queued; 12.27 GiB available, fresh gate/heartbeats, no unit identity change or OOM kill, cumulative world reclaim/max 35,343 unchanged and aggregate local max zero. Verified local tooling commit 17ac0cfe0dedc0361106de960f27ad237856adf7 preserves the guarded adapter and physical collector; parent corrected consistent garbage-collected transient-unit handling without bypassing historical scope/process/port checks. Adapter 17 + collector 31 synthetic tests pass; canonical fast gate exit 0 (1,626 selected). Neither live final tool has run. Lifecycle: nothing to restart/deploy; active freezes/worlds/packages stay intact. Quiet ceiling four until 05:18; existing restore/cutoff timers and finite 12-hour controller remain enabled, next controller 05:14:37 IST. Evidence autonomy12h-20261027/supervision/20261026T233500Z and 20261026T233800Z. No push.


2026-10-27 05:16 IST — Accepted fixed90 raw/status terminal certification: 87 carried plus three new (Jiwo80321/22, Kev80326). Added91 learned joins and176 source outcomes; inspected prefix totals1,956 choice/source joins,6,154 decisions/6,153 outcomes. Parent checked51 current terminal/fault/pending/sample-join line hashes and five status copies. Two new Vela power_route_unavailable faults (80328#7,80329#6) retained; inspected totals17typed+4untyped, zero backend/tool/safety/runner errors. Previous3pending requests resolved; current capture3requests+1execution outcome pending, not errors. Fresh/versioned final projection still outstanding. Separate05:15 host snapshot90completed/4running/26queued,12.319GiBavailable,freshheartbeats/gate,unitidentities unchanged,zero new memory events/OOMkills. Quiet restore05:18:11 andcutoff15:48:11timers waiting; no duplicate controls or activefreeze edits. State/tasks/sequence updated; no live review tasks remain. Parent receipt autonomy12h-20261027/supervision/20261026T234500Z/parent-delta3-review.json. Lifecycle:none; same10mincontroller next05:24:37IST; no push.


2026-10-27 05:20 IST — Existing quiet-restore callback succeeded at05:18:11IST after fresh18.52s gate and12.40GiB available; requested ceiling15. First published gate15 is timestamped05:18:52.924IST, separately retained from callback time and last observed quiet gate. Post-restore05:19:26 host snapshot90completed/13running/1preparing/16queued (14reservations; arms guided4/Jiwo4/Kev3/Vela3),7.718GiBavailable,freshgate/heartbeats,unchanged supervisor/model identities,0OOMkills. World local max/reclaim counter now36225 (+882 since05:15),aggregate localmax0; inherited counts are not additional crashes. Four captured prior pending IDs resolve to outcomes; this is not current all-job pending certification or extra exposure counts. Raw-certified90 remains separate. State/sequence preserve restored load stratum, powerprofilebalanced,parent unchanged. Restore timer completed;15:48cutoff timer and finite10mincontroller intact. Evidence autonomy12h-20261027/supervision/20261026T234900Z/controller-note.json andcapacity-phase-transition.json. Lifecycle: supported pre-authorized timed ceiling restore only; no activefreeze/world/package edits,restarts,newcohort orpush.


2026-10-27 05:35 IST — Accepted the fixed91 raw/status terminal audit:90 carried plus Vela80327;132 new choice/source joins,507 outcomes and511 decisions. Observed-prefix totals are2,090 model choices/2,088 source joins/6,665 decisions/6,660 outcomes. Parent rehashed119 critical current lines and5status copies; one new guided80315#13 power_route_unavailable is retained (18typed/4untyped total);11unique pending identities remain across overlapping categories. Full-menu metadata and fresh/versioned final endpoints remain outstanding. Separate05:33:32 live snapshot92completed/12running/16queued,8.101GiBavailable,freshgate/heartbeats,unchanged exactunitidentities and zero memory-event deltas/OOMkills; worldlocalmax40099/aggregate localmax0. Restore complete;15:48cutoff timer waiting,GPU9172healthready,profilebalanced. No live review tasks remain. Evidence autonomy12h-20261027/supervision/20261027T000400Z/parent-restored-delta-review.json. Lifecycle:none;finite10mincontroller remains enabled,next05:44:38IST;no activefreeze edits,restarts,newcohort orpush. Canonical localHEAD17ac0cfe0dedc0361106de960f27ad237856adf7 unchanged; prior tests reused.


2026-10-27 05:47 IST — Fixed05:45 snapshot99completed/8running/13queued (guided4/Jiwo3/Vela1),10.136GiBavailable,freshgate/heartbeats,5status hashes verified; unchanged worldlocalmax40099/aggregate localmax0, no newly observed OOM. Kevsegment28/28 and30logicalKev jobs terminal; its transientunit is now not-found/inactive/dead. This is not historical exit-success proof or whole-cohort closure; previous cgroup/invocation retained for final scope. All ongoing supervisor and three CPUserver identities unchanged; servers retained until full cleanup. New Personal_2/gpt-6.1-sol/xhigh audit model-restored-delta-journals-20261027T0545-r1 covers only8new terminals+selected tails,carrying91certification/2,088joins and deduplicating11captured pending followups. Restore remains completed,15:48cutoff timer waiting,GPU9172healthready,profilebalanced. Evidence autonomy12h-20261027/supervision/20261027T001500Z. Lifecycle:none;no activefreeze/world/package/control changes,restarts,newcohort orpush. Same finite10mincontroller next05:54:38IST.


2026-10-27 05:52 IST — Accepted fixed99 raw/status terminal certification (91carried+8new); added107choices/108source joins/542decisions+outcomes. Observed-prefix totals2,200requests/2,197choices/2,196source joins/7,207decisions/7,202outcomes. Parent rehashed168critical current lines and5status copies. NewVela80329#102 starter_deconstruction_failed construction-bot timeout retained:19typed/4untyped; operationtimeout is distinct from backend/modeltimeout (0 in inspected prefixes). Eleven priorpending identities resolved/deduplicated;8unique captured pending identities remain across overlapping categories. Fresh/versioned endpoints/menu-metadata hashes/physicalclosure stilloutstanding. Separate05:51:24health remains99completed/8running/13queued,9.835GiBavailable,freshgate/heartbeats,unchanged unitidentities andworldlocalmax40099/aggregate localmax0;no newly observed OOM. Restorecomplete/cutoff15:48timerwaiting/GPU9172healthready/profilebalanced;allthreecohortCPUserversretained. No live reviewtasks remain. Parentreceipt autonomy12h-20261027/supervision/20261027T002100Z/parent-restored-delta2-review.json. Lifecycle:none;no source/world/package/control changes,restarts,newcohort orpush;finite10mincontroller next05:54:38IST.


2026-10-27 05:56 IST —05:54:55 live snapshot100completed/7running/13queued (guided4/Jiwo3),10.475GiBavailable,freshgate/heartbeats,5status hashes verified;worldlocalmax40099/aggregate localmax0unchanged,no newly observed OOM. Vela28/28segment and30logicaljobs terminal; unitnow consistentnot-found/inactive/dead and pinned historicalcgroupabsent. This is not whole-cohort closure or historical exit-success proof; all ongoing supervisor/model identities unchanged and threeCPUmodelserversretained. Accepted rawcertification99 remainsseparate;Vela80329 terminal is not yet raw-certified. All8captured pending identities resolve bybounded appended tails (12reads,1cap reached aftertargetfound);no extra whole-choice/exposure counts or currentall-jobpending=0claim. Restorecomplete/cutoff15:48waiting/GPU9172healthready/profilebalanced;no live reviewtasks. Evidence autonomy12h-20261027/supervision/20261027T002500Z. Lifecycle:none;noactivefreeze/world/package/control changes,restarts,newcohort orpush;finite10mincontroller next06:04:38IST.


2026-10-27 06:09 IST — Fixed06:06:21 host snapshot104completed/4running/12queued; all90model-arm jobs now terminal, guided14terminal/4running/12queued. Available12.294GiB, heartbeat2.85s/gate13.73s; five status-copy hashes match. Jiwo supervisor now not-found/inactive/dead and its pinned previous cgroup explicitly absent; all ongoing supervisor/CPU-server identities unchanged. Readable local/inherited memory counters have zero new deltas; worldlocalmax40099/aggregate localmax0, no newly observed OOM (absent finished cgroups remain unknown readings). Accepted rawcertification99 stays separate. New Personal_2/gpt-6.1-sol/xhigh task model-restored-delta-journals-20261027T0606-r1 covers five new terminals+nine selected journal prefixes and deduplicates eight prior pending followups. Restore completed, existing15:48 cutoff timer waiting, GPU9172healthready with7requests, profilebalanced. Evidence autonomy12h-20261027/supervision/20261027T003700Z. Lifecycle:none; allthreecohortCPUserversretained; no activefreeze/world/package/control edits,restarts,newcohort orpush. Same finite10mincontroller enabled,next06:14:39IST.


2026-10-27 06:19 IST — Accepted fixed104 raw/status certification (99carried+5new); added37choices/38sourcejoins/818decisions/820outcomes, giving2,234requests/choices/sourcejoins and8,025decisions/8,022outcomes in inspected prefixes. Parent rehashed103critical lines,63retained prefixes and5status copies; Personal_2/gpt-6.1-sol/xhigh completed with no pendingchildren. Newguided80315#281 untyped_stuck for op.extend.stone-brick/full_output retained:19typed/5untyped faults; backend/modeltimeout/tool/safety/runner0 in inspected scope. Eight priorpending identities resolved/deduplicated. Allthree newly captured pendingoutcomes now resolve by bounded append reads (3reads,2caps aftertargetfound); this adds no whole-row/exposure counts and is not freshwholecohortpending=0. Separate06:18:12 health106completed/4running/10queued,12.307GiBavailable,heartbeat3.81s/gate1.27s; five status hashes match,unitidentities unchanged,zero new readable memory-event deltas/worldlocalmax40099/aggregate localmax0/no newly observed OOM;absentcgroups unknown. Fresh/versioned final endpoints/menu-metadata hashes/physicalclosure remain outstanding. Existing06:15 control receipt:restorecomplete/cutoff15:48timerwaiting/GPU9172ready7requests/profilebalanced;unchanged CPUFP32healthbundle identities match. No live reviewtasks. Evidence autonomy12h-20261027/supervision/20261027T004900Z. Lifecycle:none;allthreecohortCPUserversretained,noactivefreeze/world/package/control changes,restarts,newcohort orpush. Samefinite10mincontroller next06:24:39IST.


2026-10-27 06:26 IST — The fixed06:24:54 snapshot has107completed/4running/9queued, all remaining work guided;12.445GiB available, heartbeat4.00s/gate42.50s. Five status hashes match, ongoing unit identities are unchanged, and readable local/inherited memory counters have zero new deltas (world local max40099/aggregate local max0; no newly observed OOM; absent finished cgroups remain unknown readings). Accepted104 raw/status certification and2,234model source joins remain separate from live107. New Personal_2/gpt-6.1-sol/xhigh task model-guided-delta-journals-20261027T0625-r1 certifies guided80314/15/16 plus selected17/18/19/20 appended tails, carrying previous semantics and deduplicating three captured pending followups. No duplicate live work or model-arm journal re-audit. Restore remains completed; exact15:48 cutoff timer is waiting, GPU9172healthready/same invocation/7requests and profilebalanced. Evidence autonomy12h-20261027/supervision/20261027T005500Z. Lifecycle:none; all three cohort CPU servers retained, no activefreeze/world/package/control edits, restarts, new cohort or push. Same finite10mincontroller enabled, next06:34:39IST.


2026-10-27 06:38 IST — Accepted fixed107 raw/status terminals (104 carried plus guided80314/15/16). Added638decisions and637outcomes; inspected totals8,663decisions/8,659outcomes, with2,234model requests/choices/source joins carried unchanged. Parent verified61critical raw lines, five status copies,17retained-prefix/manifest hashes and29selected captured prefixes. New guided80319#12 power_route_unavailable for op.foundation.copper-plate retained; totals20typed/5untyped faults, backend/modeltimeout/tool/safety/runner0 within inspected scope. Three prior pending identities deduplicated; all four newly captured pending outcomes resolve by four bounded append reads with no caps. Follow-up adds no whole-row/exposure counts and does not establish a fresh whole-cohort pending=0 inventory. No live review tasks remain; Personal_2/gpt-6.1-sol/xhigh preserved. Separate06:37:24 health107terminal/4running/9queued,12.205GiBavailable, heartbeat3.43s/gate9.47s; five hashes match, exact units unchanged, zero new readable memory events (worldlocalmax40099/aggregate localmax0/no newly observed OOM; absent finished cgroups unknown). Existing06:35 control receipt retains restored15,15:48cutoff waiting,GPU9172ready7requests/profilebalanced. Full120/fresh-versioned final endpoints/menu metadata/physical closure remain outstanding. Evidence autonomy12h-20261027/supervision/20261027T010700Z. Lifecycle:none; three cohort CPU servers retained, no activefreeze/world/package/control edits, restarts, successor cohort or push. Same finite10mincontroller next06:44:39IST.


2026-10-27 06:58 IST — Fixed06:54:51 snapshot remains107completed/4running/9queued,12.217GiBavailable,heartbeat9.37s/gate33.27s; five copied hashes and ongoing unit identities match. Guided supervisor5GiB limit recorded532new local max events (parent world/aggregate inherited maxima40631); world localmax40099 and aggregate localmax0 unchanged, no newly observed OOM/kills, completed absentcgroups unknown. No memory/policy change or restart. New Personal_2/gpt-6.1-sol/xhigh task model-guided-append-journals-20261027T0656-r1 certifies only four guided appended source/clean prefixes, carrying107terminal/2234modeljoins and deduplicating four parent pending followups; no completed audit replay/model-arm rereads/final projection/winner. Fresh06:57:26 health shows three unchanged CPUFP32 serversready/qualified bundle identities match; GPU9172ready/sameworker/7requests, restorecompleted,15:48:11cutofftimerwaiting,profilebalanced. Evidence autonomy12h-20261027/supervision/20261027T012500Z. Lifecycle:none; all three cohort CPUserversretained, no activefreeze/world/package/control edits, newcohort orpush. Same finite10mincontroller next07:04:40IST.


2026-10-27 07:09 IST — Accepted append-only guided review: +886clean decisions/+886source outcomes/source-clean joins, totals9549decisions/9545outcomes;2234model requests/choices/sourcejoins and107terminal certifications carried. Parent independently verified67critical live/captured line hashes plus18input/status/prefix bindings; allfour captured pending outcomes resolve by4bounded append reads/no caps, adding no whole counts or currentall-jobpending=0 claim. Fault totals20typed/5untyped unchanged; fresh/versioned endpoints/menu metadata/physicalclosure remain outstanding. Separate07:04:54 live snapshot109completed/4running/7queued,12.289GiBavailable,heartbeat7.81s/gate33.73s; five hashes and ongoing unit identities match. Guided80317/18 nowterminal but not yet raw/statuscertified; new Personal_2/gpt-6.1-sol/xhigh task model-guided-terminal-delta-20261027T0708-r1 covers these two plus selected19/20/21/22 appended prefixes, deduplicating four exact followups. Guided unit recorded1356new local max events/cumulative1888; inherited parentmax41987,world local40099/aggregate local0 unchanged,no newOOM/kills,absentfinishedcgroups unknown. Existing07:05control:restorecompleted,15:48:11cutofftimerwaiting,GPU9172ready7requests/profilebalanced;06:57CPUready/bundleidentities retained. Evidence autonomy12h-20261027/supervision/20261027T013500Z. Lifecycle:none; allthreecohortCPUserversretained,no activefreeze/world/package/control edits,restarts,newcohort orpush. Same finite10mincontroller next07:14:40IST.


2026-10-27 07:25 IST — Accepted fixed109 raw/status terminals (107 carried plus guided80317/18): +442 clean decisions/source outcomes/source-clean joins; totals9,991 decisions/9,987 outcomes, with2,234 model request/choice/source joins carried. Parent independently verified106 critical live/captured lines, retained input/status/prefix bindings and7 new fault identities. Faults26 typed/6 untyped; tool/safety/runner additions0. Four captured pending outcomes resolve in four bounded append reads without caps; no added whole counts or fresh all-job pending=0 claim. Filtered ledger capture offsets and runner tick origins verified; final/versioned endpoints, full-menu metadata and physical closure remain outstanding.

Separate07:22:40 health:111 completed/4 running/5 queued,115 starts/120 planned,12.370GiB available, heartbeat6.53s/gate16.65s. Five fixed status hashes and ongoing unit identities match. Guided local memory max3420 (+529 since07:17:39); world local40099/aggregate local0 unchanged, inherited43519; no newly observed OOM/kills, absent completed cgroups unknown.07:23:03 controls confirm quiet restoration completed, exact15:48:11 cutoff timer waiting, GPU9172 ready/same worker/7 requests and balanced profile. New Personal_2/gpt-6.1-sol/xhigh review model-guided-terminal2-delta-20261027T0724-r1 covers newly terminal19/21 plus selected20/22/23/24 append ranges, deduplicating four followups. Evidence: autonomy12h-20261027/supervision/20261027T014800Z and20261027T015500Z. Lifecycle:none; all three cohort CPU servers retained; active freeze/world/package/policy/control unchanged. Same10min controller stays enabled through the finite reviewed sequence; next 2026-10-27T07:34:41.387000+05:30. No push.


2026-10-27 07:37 IST — Accepted fixed111 terminals (109 carried plus guided80319/21), with537 new clean decisions/538 source joins; totals10,528 decisions/10,525 outcomes and2,234 unchanged model joins. Parent checked79 critical live/captured lines and40 file/hash bindings. No new faults;26 typed/6 untyped retained. Three captured pending outcomes resolve in three bounded append reads/no caps, adding no whole counts or fresh all-job pending=0 claim. No live review remains; later80320 terminal awaits a useful future delta review.

Separate07:34:27 snapshot112 completed/4 running/4 queued,116 starts/120 planned,12.176GiB available, heartbeat9.66s/gate1.08s. Five fixed hashes and ongoing unit identities match. Guided local memory max4298 (+618 since07:26:12); world local40099/aggregate local0 unchanged, inherited44397, no newly observed OOM/kills; absent finished cgroups unknown. Fresh health confirms all three unchanged CPUFP32 qualified bundle identities (canonical JSON hashes), GPU9172 ready/same worker/7requests, restored15, exact15:48:11 cutoff waiting/profilebalanced. Evidence: autonomy12h-20261027/supervision/20261027T020400Z. Lifecycle:none; no runtime changes/server stops/push. Full120terminal/all5inactive/scoped cleanup precedes final tools, complementary Personal_2+DeepSeek4.1Flash final reviews, decision and budgeted successor. Same10min controller enabled,next07:44:41IST.


2026-10-27 07:47 IST — New Personal_2/gpt-6.1-sol/xhigh terminal3 review covers80320 terminal/status bindings and roughly22.5MB new source ranges for guided22/23/24/25;80320 accepted complete source/clean semantics carry unchanged. Deduplicates three exact parent pending followups. Accepted111/10,528decisions/10,525outcomes/2,234modeljoins/26typed+6untyped stays fixed pending review. No duplicate livework or completed semantic replay.

07:45:03 health112 completed/4 running/4 queued,12.317GiB available,heartbeat9.21s/gate32.32s; five fixed hashes and ongoing unit identities match. Guided local memory max5211 (+545 since07:38:12); world local40099/aggregate local0 unchanged,inherited45310,no new OOM/kills,absent completed cgroups unknown. Exact15:48:11 cutoff waiting,sameinvocation/restorecompleted/profilebalanced;07:36 ready qualified CPU identities/GPUhealth retained. Evidence supervision/20261027T021451Z. Lifecycle:none; activefreeze/world/package/policy/limits unchanged,three CPU servers retained,no successor/push. Same10min controller next07:54:41IST. Full120terminal/all5inactive/scoped clean precedes final reviews/decision and successor execution.


2026-10-27 08:03 IST — Accepted 112 fixed-snapshot terminals (111 carried plus guided80320). Parent verified 98 critical receipts and 41 file/hash bindings; appended totals are 11,196 clean decisions / 11,194 source outcomes, with 2,234 model joins carried. New guided80322#279 untyped stone-brick and80325#13 typed power-route faults bring retained totals to27 typed /7 untyped. Both captured pending outcomes resolved in two bounded append reads; this adds no whole counts or current all-job pending=0 claim.

The separate07:55:07 snapshot has114 completed /4 running /2 queued,118 starts /120 planned. The already-running Personal_2/gpt-6.1-sol/xhigh terminal4 review is now registered in all three manifests; it covers new terminals80322/24 and selected23/25/26/27 append ranges without repeating accepted semantics. Host available12.292GiB, heartbeat9.25s/gate34.40s. Guided local memory max6143 (+932); world local40099/aggregate local0 unchanged, inherited46242; no newly observed OOM/kills, absent completed cgroups unknown.07:57 controls confirm restored15, the exact15:48:11 cutoff timer waiting, unchanged ready qualified CPU identities, GPU9172health-only and balanced profile. Evidence: autonomy12h-20261027/supervision/20261027T022452Z. Lifecycle:none; active freeze/world/package/policy unchanged, three CPU servers retained, no push. Same10min controller enabled, next08:04:42IST. Full120 terminal +all5 inactive +scoped cleanup still precedes final accounting, complementary Personal_2/DeepSeek4.1Flash final reviews, executive decision and a useful budgeted successor.


2026-10-27 08:09 IST — Accepted 114 fixed-snapshot terminals: 112 carried plus guided80322/24. Parent verified 127 critical live/captured receipts and 36 file/hash bindings. Added 443 clean decisions and 441 source outcomes/joins; totals 11,639 decisions /11,635 outcomes, with 2,234 model request/choice/source joins carried. Five guided80326#13–17 power_route_unavailable faults for op.foundation.copper-plate bring retained totals to 32 typed /7 untyped. All four captured pending outcomes resolved in four bounded append reads without caps; this adds no whole counts or current all-job pending=0 claim. No live review remains. Final/versioned endpoints, original menu metadata hashes and physical closure remain outstanding.

Separate 08:05:30 health: 114 completed /4 running /2 queued, 118 starts /120 planned; 12.206 GiB available, heartbeat 8.48s/gate 54.28s. Five fixed status hashes and ongoing unit identities match. Guided local memory max7334 (+1191); world local40099 and aggregate local0 unchanged, inherited47433; no newly observed OOM/kills, absent completed cgroups unknown. Fresh 08:06 controls retain restored15, exact 15:48:11 cutoff timer/same invocation, balanced profile and GPU9172 ready/same worker/7 requests, health only. Scheduler reports four active slots, target15, 72.2°C and total467.7 UPS. Qualified CPU identities from07:57 remain pinned; no server changes. Evidence: autonomy12h-20261027/supervision/20261027T023529Z. Lifecycle: none; active freeze/world/package/policy unchanged, all three CPU servers retained, no push. Same 10-minute controller enabled, next08:14:42 IST. Full120 terminal +all5 inactive +scoped cleanup precedes final accounting, complementary Personal_2/DeepSeek4.1Flash final reviews, executive decision and useful budgeted successor.


2026-10-27 08:18 IST — Fixed08:15:05 health has115 completed /4 running /1 queued,119 starts /120 planned; guided80323 is newly terminal. Accepted certification remains114 with11,639 clean decisions /11,635 source outcomes,2,234 model joins and32 typed /7 untyped faults. New T3 Personal_2/gpt-6.1-sol/xhigh review model-guided-terminal5-delta-20261027T0817-r1 covers80323 terminal/status joins, appended23/25/26/27 and first28 prefixes; four exact parent followups must be deduplicated. Previous task completed with no pending children; no duplicate live work. New task registered in state/sequence/task manifest and resolved route/options verified.

Host available12.369GiB, heartbeat8.76s/gate26.11s; five fixed status hashes and ongoing unit identities match. Guided local memory max8371 (+1037); world local40099/aggregate local0 unchanged, inherited48470; no newly observed OOM/kills, absent completed cgroups unknown. Fresh controls retain restored15, exact15:48:11 cutoff timer/same invocation, balanced profile and GPU9172 health-only/same worker/7requests. Scheduler reports four active slots, target15,71.5°C and470.5 totalUPS. Parent existingCLI/cliproxy-personal-2 process route verified. Evidence: autonomy12h-20261027/supervision/20261027T024505Z. Lifecycle:none; active freeze/world/package/policy unchanged, all three CPU servers retained, no push. Same10min controller enabled,next08:24:43IST. Full120 terminal +all5 inactive +scoped cleanup precedes final accounting, complementary Codex/DeepSeek4.1Flash final reviews, decision and useful budgeted successor.


2026-10-27 08:26 IST — All120 planned logical attempts have now started, with no logical prior attempts/reruns. Fixed08:25:00 snapshot:117 completed,3 running (guided80327/28/29),0 queued. Accepted certification remains114; terminal5 review for fixed115 is still working on Personal_2/gpt-6.1-sol/xhigh with no pending children/question/approval. Later terminals80325/26 remain outside that review; no duplicate task or interim winner. Five fixed status hashes and ongoing unit identities match; no newly observed OOM/kills. Evidence: autonomy12h-20261027/supervision/20261027T025500Z. State/sequence updated. Lifecycle:none; freeze/world/package/policy and three CPU servers unchanged, no push. Same finite10min controller enabled,next08:34:43IST; final120terminal/all5inactive/scoped cleanup and complementary final reviews still precede executive successor action.


2026-10-27 08:35 IST — Accepted115 fixed-snapshot terminals (114 carried plus guided80323). Parent verified165 critical live/captured receipts and31 file/hash bindings. Added908 clean decisions /909 source outcomes/joins; totals12,547 clean /12,544 source, with2,234 model joins carried. New80323#359 untyped stone-brick and80328#11 typed power-route faults bring retained totals to33 typed /8 untyped. All three captured pending outcomes resolved in three bounded append reads/no caps, adding no whole counts or current all-job pending=0 claim. Separate08:31:51 health remains117 completed /3 running /0 queued; all120 started. New Personal_2/gpt-6.1-sol/xhigh terminal6 review covers80325/26 terminal joins and selected25/26/27/28 appended ranges plus first29 prefixes; registered in all manifests, no duplicate live work. Evidence: autonomy12h-20261027/supervision/20261027T030150Z. Lifecycle:none; freeze/world/package/policy and three CPU servers unchanged, no push. Same10min controller enabled,next08:44:43IST. Full120 terminal/all5 inactive/scoped cleanup precedes final tools, complementary Codex/DeepSeek4.1Flash final reviews, executive decision and useful budgeted successor.


2026-10-27 08:40 IST — Accepted117 fixed terminals after83 parent-critical receipts/35 hash bindings; +284 clean/+285 source, cumulative12831/12829, modeljoins2234; faults33typed/8untyped unchanged. Live117completed/3running/all120started; both captured pending outcomes resolved without adding counts. State/sequence/root+canonical evidence docs synchronized; final120/versioned endpoints/physicalclosure/complementary Codex+DeepSeek reviews and decision still required. Evidence autonomy12h-20261027/supervision/20261027T030757Z. Lifecycle:none; same finite10min controller and cutoff retained, all three CPU servers retained, no push.


2026-10-27 08:57 IST — Registered one new Personal_2/gpt-6.1-sol/xhigh review of only new guided80327/28/29 journal ranges; accepted117/120 terminals unchanged,live117completed/3running. About37.6MB new append data; no completed semantic replay or interim winner. Five status hashes/seven active unit invocations verified; guided local max+941 without new OOM/kills. Evidence autonomy12h-20261027/supervision/20261027T032501Z. Lifecycle:none; same finite10min controller/cutoff retained,no runtime changes/push.


2026-10-27 09:11 IST — Accepted +588clean/+588source append joins (13419/13417 totals),carrying117 terminals/modeljoins2234; parent30critical/27bindings/9samplejoins passed. Faults34typed/9untyped;25carried receipt metadata gaps filled without semantic replay/recount. Live118completed/2running; new Personal_2/xhigh review covers80328 terminal and new27/28/29 append ranges. Evidence autonomy12h-20261027/supervision/20261027T033506Z. Lifecycle:none;same finite controller/cutoff,three CPU servers retained,no push.


2026-10-27 09:30 IST — All120 logical jobs completed at09:22:30,all5 supervisors inactive,zero occupied slots/reservations. Accepted terminal7 fixed118:29critical receipts,26file/hash bindings,6source-clean samples,43carried fault bindings;totals13846clean/13845source/2234modeljoins/34typed9untyped.80329#430 resolved in1bounded read,no extra whole counts. New Personal_2/gpt-6.1-sol/xhigh terminal8 review certifies27/29 and remaining closed append ranges;no duplicate live work. First physical collector confirms all120 runner PID claims absent,owned cgroups empty,ports released,but reports process-scope unknown for3permission-denied infrastructure PIDs;parent investigating without root. Evidence supervision/20261027T035229Z. No finaldecision/adoption/successor before complete evidence/physical closure. Same10min controller retained;exact cutoff15:48:11 unchanged. Lifecycle:none;freeze/world/package/policy/servers unchanged,no push.


2026-10-27 09:37 IST — Accepted all120fixed terminal certifications: carry118+guided80327/29,30critical receipts/19filebindings/3sourceclean samples/43fault bindings. Added48clean/49source joins;final13894clean=13894source/joins,including11660guided+2234model. Pendingunion0;80329#430deduplicated and missing semantics completed,no newfaults34typed+9untyped. Scopedphysical collector retains blocked3infrastructurecwdunknown;separate parent attestation positively binds systemdmanager/PAM/privilegedportal helper without root,all120runnerclaims absent,cgroups/ports clear. Final immutable ledger/inputassembly running Personal_2/gpt6.1sol/xhigh;parent latency merge covers2234choices individually and51chronological localmemory observations retained,no observed OOMkills/readable scopes,absent completed cgroups unknown. Finalprojection/complementary Personal_2+DeepSeek4.1Flash reviews/executive decision/useful successor remain. Evidence supervision/20261027T035229Z. Exact15:48:11cutoff waiting/sameinvocation;restorecompleted;profilebalanced. Same10mincontroller enabled. Lifecycle:none,no activefreeze/world/package/policy/servers changes,no push.


2026-10-27 09:42 IST — Original120 full logical accounting:120completed/120planned,original7+113transferredattempt0,administrative113zeroextra. Final13894sourcecleanjoins/2234modelchoices/0pending;34typed+9untypedbugs retained. Immutable preparation1491payloads+seal=1492files;parent verified1079copiedbindings/all120jobhash+ledgerpairs and all330accepted source/clean/model prefixes equal closedEOF. Two stale historical cross-report hashes resolved via contemporaneous parent acceptedactualbytes; original references retained. Strict source-bound C6ever guided7/30,Kev0/30,Vela0/30,Jiwo0/30;finalguided5/30models0/30. Adapterexit3 retains21capacityperiodunknown cases;C2..C5unknown/versionguard cells and exactpairedstats remain explicit,proxies separate. Physicalcollector stillblocked3unreadable infrastructurecwd;separate parent positiveidentityattestation+cgroups/ports/120absentrunnerclaims retained for independent challenge. No atomic/historicalreleaseatcleanup claim. New complementary T3 Personal_2/gpt6.1sol/xhigh and actual DeepSeek4.1Flash-free finalreviews bothrunning,unique rounds,registered;executiveadoption/nextfactor decision pending. No successor overlap/reruns. Exactcutoff15:48:11waiting/sameinvocation,restorecompleted,same10mincontroller enabled,next09:44:50IST. Lifecycle:none;qualified3CPUservers retained pendingreuse/stopdecision,GPU9172health-only,freeze/world/package/policy unchanged,no push. Evidence autonomy12h-20261027/supervision/20261027T035229Z.


2026-10-27 10:00 IST — Both completed120 reviews accepted (Personal_2/gpt-6.1-sol/xhigh and actual opencode/deepseek-v4.1-flash-free; paid route previously insufficient funds). Executive decision: retain guided, set aside released Kev/Vela/Jiwo, no merge/adoption. Strict C6-ever guided7/30 versus each learned0/30; final5/30 versus0/30. All120planned actualattempt0/13894joins/34typed+9untypedbugs retained; administrative113 add zero trials. Adapter exit3/21capacity gaps, all120 comparison_eligible:false, menu/legality/weight/predicate/load/power/terrain limits remain. Separate parent physical-scope attestation accepted without root; collector remains blocked, no historical/atomic closure claim. Selected next factor: existing weak-v3 Kev versus released Kev weights only, common explicit CPU FP32/eager/merge path and external T2.3510958125672174, with guided reference; fixed2freshseeds80400/1 x3arms=6pilotjobs at3concurrency, no adoption authority. Native BF16 metadata override must be repaired and actual dtype/merge verified before exact full-menu qualification/wiring/freeze/admission. Isolated successor implementation delegated through T3 to verified Personal_2 inheritedmodel/options; no duplicate review. Fresh09:59 health120completed/zerooccupied/13.958GiBavailable; exact cutoff15:48:11waiting/sameinvocation, restorealreadycomplete, same10mincontrollerenabled next10:04:51. Lifecycle:none yet; old CPU units unchanged pending scoped stop/replacement. Decision: data/laya-orchestration/model-launch-20261026/autonomy12h-20261027/executive-decision-20261027.json. No push.

2026-10-28 15:21 IST — Added maintained docs/rl/Laya_Cohort_Results_Index.md: C1–C43 and retry/no-start records, earlier October campaigns, model trials and separately dated C44–C46 pending status; linked from the RL README. DS41F OC Go draft parent-reviewed against ledgers, selected source reports and three Terra reviews. Documentation coverage/table/source checks pass (49 resolved references); isolated baseline fast gate: 1,122 passed, one skipped, one existing import-firewall failure (laya_ops -> autonomous_builder/stage_services), with no Python changes. Lifecycle: none; active campaigns, saves, mods and policies unchanged; no push.


2026-10-28 16:02 IST — Added independent opt-in consumer menu, solid fluid filtering, committed-owner bill facts and shared permanent-production operations; receipts distinguish prerequisites/provider availability from sustained output. Added policy/executor timing and event/run fault summaries. Evidence: 83 focused tests in original checkout; isolated fast gate 1130 passed, 3 skipped, known baseline import-firewall failure. No cohort benefit/adoption claimed. Lifecycle: freeze new code and queue disposable validation behind active campaigns; no active runner/mod/server change.


2026-10-28 16:42 IST — Deployed the isolated cohort dashboard with guarded concurrency ceilings, UPS/thermal controls, durable cooling recovery, Starship viewing and reversible client-mod sync. Added 6h/1d/7d telemetry, newest-first cohorts, persistent run reviews and curated 7–28 October history (57 rows/150 arms; pending/invalid outcomes explicit). Evidence: 24 focused checks passed; fast gate 1155 passed, 3 skipped, known baseline import-firewall failure. Live scheduler handshake/history and Starship HTTP tunnel verified; cooling and mod replacement remain fixture-only. Lifecycle: dashboard/scheduler/recovery timer deployed; campaigns, server saves/mods unchanged. Sonnet UI refinement next; no push.


2026-10-28 17:10 IST — Integrated Sonnet 5.5 dashboard refinement: Overview/Performance/Jobs/History, shared chart tooltips and legends, responsive light/dark layout, clearer admission controls and review workflows. Parent fixed in-flight review draft preservation, invalid numeric ranking and old queued records appearing active. Evidence: 25 focused checks, 89 isolated browser checks, read-only live four-tab/day/week checks; fast 1155 passed/3 skipped plus known baseline import-firewall failure. Lifecycle: frontend served immediately; browser refresh only, no service/campaign restart or live control/mod-sync action; no push.


2026-10-28 17:31 IST — Added descriptive curated-cohort chronology with separate base/experimental panels, reached/final/retention metrics, window filters and keyboard/tooltips. Parent removed unsupported significance verdicts, qualified connectors by known predicate/window metadata, kept unknown-only gaps and individual replicate-arm counts, and required ten compatible metadata points for descriptive medians. Evidence: 116 browser fixtures/25 focused passed, live read-only UI/Starship checks; fast 1155 passed/3 skipped plus known baseline import-firewall failure. Lifecycle: assets served immediately; browser refresh only, no service/campaign/control/mod changes; no adoption or improvement claim, no push.


2026-10-28T18:45:21+05:30 — Miniship GUI launcher and idle Starship deterministic-server selectors now target Factorio2.1.24 build87808. Parent verified versions/hashes; Terra checked GUI data parity and Starship disposable copied-save load/clean shutdown. Miniship package/usr/bin stays2.1.21; GUI rendering untested, Starship persistent server stopped. Receipts data/factorio-upgrades/2.1.24/. Lifecycle: new GUI launch/fresh Bash and next authorized Starship server start use2.1.24; active Miniship cohort servers remain2.1.23 until C44 cleanup, C45/C46 intentionally held. Owner notified; no policy/adoption claim.


### 2026-10-28 21:06 IST — dashboard admission target and standing power modes

Integrated the missing delegate checkout into the served cohort dashboard: +1/+2/+4 admission-target nudges and persistent Auto/Cool/Balanced/Performance controls. Temporary cooling preserves the prior profile; stale recovery cannot override an explicit mode. Evidence: 30 focused tests, 140 isolated browser fixture checks, JS syntax, live API dispatch and Starship tunnel asset checks; fast gate has only the known training/laya_ops.py import-firewall failure. Restarted only laya-cohort-dashboard.service at 21:05 IST; recovery service already resolves this updated script. Host config SHA256 stayed 9c18bc795ed28dba0fe21d376079c4c0e14ef4877bbc2cd4ba89333e5218895d; no live mode/target was applied, no campaign/server/mod/save changes. Refresh the browser to load the controls.


### 2026-10-28 21:12 IST — dashboard temperature control fixed

Corrected the dashboard scheduler flag from unsupported --cool-temp-limit to --cool-temp. Two real-CLI regressions prove settings apply above/below the cool threshold on temporary hosts and preserve mode/ceiling. All 32 focused checks pass; fast gate retains only the known training/laya_ops.py firewall failure. Restarted only the dashboard. Live invalid-input dispatch reached temperature validation; host configuration remained byte-identical. User must resubmit the desired temperature; no campaign or live temperature was changed by this fix.


## [2026-10-29 01:27 IST] Factorio 2.1.24 cohort upgrade verified; C45/C46 successors launched
- Private build 87808 and full matching data verified against Starship provenance; original runtimes, pre-upgrade selectors and copied saves retained. Copied-save migration/reload, schema/RCON and disposable water/steam temperature fixtures passed; C46 consumer products_finished/unit_number fields verified. 63 focused tests passed; fast gate: 1155 passed, one skipped, known pre-existing import-firewall failure only. No throughput or policy benefit measured.
- C44 finished 200/200 with physical cleanup. Old held C45/C46 accounted 120 cancelled-before-start each, zero starts, durable drain retained, supervisors exited. New engine-labelled 2124 successors preserve original source/mods/seeds/120-job designs/windows. C45 actual engine/data/mod load verified; C46 remains queued behind new C45. Original Oct31/Nov1 06:00 IST deadlines retained, shared host admission unchanged.
- Lifecycle: future manager/dashboard defaults select 2.1.24; running successors use private frozen-manager XDG mappings. Existing idle worlds were not migrated/resumed. Receipts, rollback and exact units: data/factorio-upgrades/2.1.24/state.json. Mixed-start/C47 work remains gated on actual C46 completion/cleanup; no production save, installed mod or policy adoption changed.

## [2026-10-29] Mixed-start checkpoint implementation, offline validation

- Added paired 40% C0 / weighted C1–C6 allocation, fresh strict segment evidence,
  operation-boundary capture, full registered-sidecar restore, a candidate bank,
  host-admitted concurrent jobs and per-origin/arm/generation reporting.
- Proven reference 9d9b279 is retained through an explicit common-interface port;
  C45/C46 treatment adoption remains with the cohort owner. Focused integration:
  92 passed; required fast gate has only the known training/laya_ops.py baseline
  import-firewall failure. Real C1–C6 capture/reload remains unproved.
- Lifecycle: no C47 freeze or launch; next is bounded disposable source setup under
  independent systemd deadline. Old 2.1.23 drain holds and C44–C46 freezes preserved.

## [2026-10-29] First disposable mixed-start source setup is live

- Source01 runs proven reference9d9b279 plus the hash-recorded common interface,
  seed91000, ports44100/37100. Actual Factorio2.1.24 executable/data and initial
  operation decisions verified. This is one setup job within the total12 cap.
- Independent systemd RuntimeMaxSec7500/control-group cleanup; source wall cap7200.
  No C1–C6 reload validated and no C47 freeze/launch/handoff. No policy result or
  adoption claim; existing timer and old-engine drains remain unchanged.

## [2026-10-29] Checkpoint capture schema failure accounted; source02 live

- Source01 passed strict C2/C3 but both captures failed because the draft decoder
  rejected nested Any-valued reference census rows. No checkpoint was validated.
  Stopped at an operation boundary; supervisor, PIDs, ports and reservation clean.
- Fixed JSON census serialization and tested round-trip against the actual proven
  reference schema. Focused56 passed; fast1170 passed/1 skipped plus known baseline
  firewall failure. Source02 now uses corrected frozen interface, seed91001,
  ports44200/37200 and an independent finite systemd deadline. Charged setup4/12
  includes two failed captures. C47 remains unfrozen; no completion handoff/push.

### 2026-10-29 21:25 IST — mixed-start source02 candidate only

Source02 (`laya-mixed-c47-source02-2124-miniship-20261029`, reference `9d9b279`, Factorio2.1.24 build87808) captured strict C3 at tick340966: generation `laya-6e823d5929b94f1d815fa1fcc524a6fe`. World/sidecar hashes and paired context pass offline verification; no independent reload receipt exists. C1 fresh predicate and C2 ownership checks rejected their captures, raising conservative setup accounting to6/12. The source continues within its existing22:35 IST deadline; no rejected capture is retried. C45/C46c remain120/120 accounted/clean, inactive, and old2.1.23 drains remain held. Lifecycle: no new launch, restart, deployment, freeze, commit or owner handoff; C47 remains unavailable pending all C1–C6 reloads.

### 2026-10-29 22:24 IST — C47 blocked; owned schedule disabled

Source02 ended `stalled` after690 decisions and6593.96server-seconds. Fresh strict C6 was observed (tick554353; also true at end), but C6 capture failed the ownership audit. C3/C4/C5 candidates have verified immutable bundle hashes/context; none has an independent reload receipt. C1 fresh predicate and C2/C6 ownership checks rejected captures. Both source jobs are fully accounted and physically released; C45/C46c remain120/120 clean/inactive, and old2.1.23 admission drains stay held.

Recorded conservative accounting is7/12 (two supervised source jobs plus five failed capture charges), leaving five entries for at least six independent reloads and new C1/C2/C6 captures. C47 therefore remains the requested120-job A/A design, unfrozen/unqueued/unlaunched; no allocation substitution or owner completion handoff. Only this thread's schedule was disabled (`enabled=false`, `nextRunAt=null`); its hourly interval was retained. Resume requires a compatible complete externally validated bank or revised setup authority, followed by capture fixes and validation within the owned window. Implementation remains incomplete and uncommitted; offline tests and three captures do not prove reload readiness or benefit. Lifecycle: no running owned jobs, no production/other-owner mutation, no push.

### 2026-10-30 02:00 IST — mixed-start setup resumed

User selected C47 mixed-start first and a revised setup budget. Prior7 charges remain; up to12 further charged attempts are permitted (19 total). Source03 (`laya-mixed-c47-source03-2124-miniship-20261030`) is running one disposable C0 reference job, seed91002, ports52000/52200, targeting C1/C2/C6 capture only. Pause-held fresh predicates and fail-closed persistence of the live material allocation projection address plausible source02 failures; a capture error requests a safe stop. Factorio2.1.24 build87808 binary/data and first decision were verified. Deadline 2026-10-30 04:16 IST, RuntimeMaxSec7500 and frozen ExecStopPost cleanup; budget8/19 charged. 45 focused tests pass; fast suite1173 pass/1 skip with the known pre-existing import-firewall failure. Existing hourly schedule reenabled; no C47 freeze/queue/handoff or policy adoption. Research endpoint remains unmeasured in C46c (`observed_pairs=0`); source save snapshot reports253 researched entries and no mining-productivity-4 key.

### 2026-10-30 02:12 IST — corrected S1 evidence and research reporting

C46c final status files show S1 proxy phase in24/60 reference and33/60 consumer runs; the job-list phase summary omitted S1. Neither cohort report observed mining-4 research completion. A read-only source03 RCON probe found the ranged `mining-productivity-3` technology at level4, enabled and unresearched; the missing `mining-productivity-4` key alone does not imply an unavailable target. Full matched results and the C45 duplicate-terminal sensitivity are recorded in `docs/rl/Laya_C45_C46c_Results_2026-10-30.md`. Working-tree optional research-target wiring and per-origin/arm reporting are not deployed; 49 focused pass, fast1177 pass/1 skip/known baseline import-firewall failure. C47 A/A research target stays off by default. Source03 remains active from its unchanged frozen files; controller-to-thread cohort-end notification remains unimplemented.

### 2026-10-30 14:25 IST — C47 mixed-start A/A launched (C1 excluded)

C2–C6 restart checkpoints have independent live reload receipts; strict C1 is not attainable by the gated mall (0/853 evaluations), so C47 is an explicit 6-origin, 200-job A/A (C0 80, C2–C4 20 each, C5–C6 30 each, 15 workers, deadline 2026-10-31T09:00Z). Evidence: 5 live reload receipts, a 30k-tick restart run from C6, a dry-run enqueue, and 4 jobs running under real host admission; no policy claim. Lifecycle: C47 runs from a frozen harness (tree ab8592379b52f1f1); do not edit, redeploy or restart under it. Working-tree scheduler/bank/runner changes are uncommitted. Completion handoff sent to thread 94dcfaf2.

### 2026-10-30 17:55 IST — cohort size directive: back to 120-150 jobs

Per user: the queued successor and all future cohorts return to 120-150 jobs (60-75 pairs), exact count chosen by the cohort owner; this supersedes the 180-200 sizing for those cohorts only. C47's frozen 200-job A/A plan is untouched and runs to completion (never edit or restart a live campaign). One-running/one-queued retained. Owner thread 94dcfaf2 notified. Lifecycle: nothing to restart; directive applies at the next cohort freeze.

### 2026-10-30 18:40 IST — cohort sizing 120-150 @30 workers; 3-day concurrency cut discarded

Per user (latest, supersedes the same-day 3-day scale request): queued successor and future cohorts are 120-150 jobs (owner picks exact within band) at pre-cut nominal 30 workers per cohort; the temporary -25% concurrency cut is discarded, and 'more active cohorts' means more workers per cohort, not more cohorts at once (one running + one queued stands). C47's frozen 200-job/15-worker plan is untouched. Code: tools/laya_regression_scheduler.py MIN/MAX_MIXED_JOBS=120/150, MAX_MIXED_WORKERS=30, band guard replaces the old 200-job cap (working tree only; C47 frozen harness unaffected). Evidence: focused tests 15/15, fast gate 1181 run with only the known import-firewall baseline failure. Action required: queued C48 (laya-c48-2124-miniship-20261030) is staged at the superseded 200 jobs/15 workers and must be resized by its owner before it starts. Lifecycle: nothing to restart; bounds take effect at the next cohort freeze. Not committed.

## [2026-10-31] C47 A/A converged; C48 consumer test live; host-slot starvation root-caused and fixed
- What: C47 (laya-mixed-c47, 200 defiant A/A jobs on 9d9b279, 2.1.24) completed 200/200 (188 completed + 12 wall-budget stops, 0 crashes/faults). Dual review (DeepSeek v4.1-flash + Codex personal_2 gpt-6.1-sol): identical arms, every paired strict-ever delta |d|<=5.6 pp (min p .267) -> converthe, no adoption from an A/A. C48 (reference 9d9b279 vs consumer 580eb11, 150 jobs = 75 pairs) released and live after reconciling 12 legacy `timeout` rows to `wall_cap` (C47 campaign was already terminal; originals archived in the cohort's logs/). C49 deliberately held until C48 terminal, with three conditional branches recorded; C48's censoring rule pre-registered in its freeze.json before any terminal read. Separately: the host-wide slot scheduler had pinned all cohorts at 4 slots since Oct 29 evening because its UPS health floor (p95>=98) was tuned for >=2x-speed worlds while 1x-speed C47 worlds measure ~60 UPS -> floors re-tuned to 52/46 (backup config.pre-ups52-20261030.json), bump 30 applied; C47 then ran its full frozen 15 workers and C48 releases near the ~20-23 memory/thermal plateau.
- Evidence: data/laya-orchestration/laya-loop/log.md (2026-10-30 17:xx, 19:35, 19:36; 2026-10-31 08:35 entries); data/laya-orchestration/laya-loop/reference-line-next-candidates.json (c47_review block); data/laya-experiments/laya-c48-2124-miniship-20261030/freeze.json censoring_rule; data/laya-experiments/laya-mixed-c47-2124-miniship-20261030/logs/pre-reconcile-scvocab-20261031T0810/.
- Lifecycle: C48 running (150@30, engine 2124 mapping, until 2026-11-02T06:00 IST); queue has one running + one queued slot; resumption of the hold happens at the C48-completion wake. No commits, no policy shift, no push.

## [2026-11-01] C48 null: consumer 580eb11 dropped; C49 fact bundle blocked on a frozen revision
- What: C48 (laya-c48-2124-miniship-20261030, 150 jobs = 75 C0-origin pairs, reference 9d9b279 vs consumer 580eb11) completed 150/150 (70 stalled + 5 game_window per arm; zero runner/harness errors, zero wall caps). Pre-registered primary strict C6 ever (deterministic c6-v1 latch, symmetric): reference 10 vs consumer 7, -4.0 pp, consumer-only 6 / reference-only 9, p .607, bootstrap 95% [-14.7,+6.7] -> NULL. Guards C2 59/57, C3 54/52, C4 39/43, C5 39/42 (all p >= .618). Secondary C6 receipt 32/38, shared v2 output 32/40, phase reached 30/38, accepted_final 8/0; the v2/v3 receipt difference is secondary only and the primary latch is receipt-independent. Faults: untyped 21/21; power_route 12/56 events concentrated in two consumer seeds (post-C5 zero); bug 46/123; maintenance runs 1/6. C46c's +16.7 pp (4390a18) did not replicate on the option-restricted 580eb11. Review: Codex personal_2 gpt-6.1-sol (valid null, do not adopt, null branch); DeepSeek v4.1-flash unavailable ('Go usage limit exceeded' twice; no substitute model). Decision: no adoption, consumer line dropped. C49 = C3->C4 fact bundle per the recorded null branch, but it is not frozen: the freezer checks out a git revision per arm, the project repo is under a no-commit instruction, and the three facts need implementation (chemical {target,rung} not in the decision state; producer existence/refusal absent; C6 per-cell status unobserved). The guideline catalog references none of these features, so a facts-only arm would be a wiring test unless a rule/operation consumes them.
- Evidence: data/laya-experiments/laya-c48-2124-miniship-20261030/job-status.json + freeze.json; data/laya-orchestration/laya-loop/reference-line-next-candidates.json (c48_review, c47_review.c49.blocker); data/laya-orchestration/laya-loop/log.md 2026-11-01 05:35 entry.
- Lifecycle: C48 unit inactive since 2026-10-31 14:17 IST; host idle (0 slots, no allocations); queue empty. C49 needs authorization to commit the fact bundle (or a data-local experiment repo) and a decision on wiring vs fact-use before freezing. No commits, no push.

## [2026-11-03] C49 frozen and live: C3→C4 chemical-park and producer facts with deterministic consumers
- What: Implemented the C49 fact bundle on the reference line and froze it via local branch commits (no push): `f754fbf` = 9d9b279 + common restart interface (both arms, so the only difference is the treatment), `11689af` = + facts. The deterministic chemical gate now hands `{target, rung}` to the ops menu (`ladder_holds`), which offers the rung as `produce <rung>` with `prerequisite_for`/goal attribution and holds the parked target for `WAIT_EXPIRY` (a handoff holds too but stays out of ladder progress). Per-item producer status (machines/built/starved/blocked/working) reaches the decision digest and produce/mall_slot features; `mall_slot` is not offered for a recipe a standing (not lent) mall cell already makes. No catalog or doc edits. 5 focused tests; fast gate 1342 passed on the treatment commit.
- Evidence: data/laya-orchestration/laya-loop/c49.json + c49-smoke.json; data/laya-experiments/laya-c49-2124-miniship-20261103/{freeze.json,regression.json,planned-jobs.json}; smoke laya-c49-smoke-2124-miniship-20261103 (2/2 completed; reference 21 chemical-gate re-entries vs facts 5 at equal ticks; facts first gate decision offered `op.produce.chemical-plant` with `op.produce.plastic-bar` suppressed). On the stacked arm the only fast-gate failure is `tests/test_training_isolation.py::test_current_repository_obeys_import_firewall`, caused by the pre-existing restart interface (`orchestrator/operation_session.py -> training.laya_restart_state`) in both arms, not by the facts.
- Lifecycle: `laya-c49-2124-miniship-20261103.service` active (150 jobs = 75 C3-origin pairs, seeds 80805-80879, 2.1.24, 540k ticks/7200 s, strict_target C5, until 2026-11-05 09:00 IST, 30 workers); host scheduler allocated 30 slots. Queue has C49 launched and no successor queued yet. Factorio/mod restarts: none. Push: none.

## [2026-11-03] C49 review: not adopted on 3/0 discordance; C50 exact replication live
- What: C49 (`laya-c49-2124-miniship-20261103`, 75 C3-origin pairs, reference f754fbf vs facts 11689af) completed 150/150, zero evidence errors, zero caps. Primary strict C4 ever (`c4-v1`): 72/75 vs 75/75, +4.0 pp, 3/0 discordant, exact McNemar p .250, bootstrap [0.0,+9.33] pp. The gap is exactly three reference stalls (80807, 80812, 80842) stopped by the 18,000-tick progress guard; facts had zero stalls. Mechanism fired (chemical parks 1330->565, lower in 75/75 pairs; handoffs 66->21) but the hold always expires before the next park (78/78 gaps > 1800 ticks), so the rung option is the lever. Cost: C4 later in 50/72 pairs (+4.5k ticks), batches +11.6%/decision, pending-construction 14->26. One facts-only typed bug (seed 80826, `mall_cell_lifecycle_conflict`, release_recipe_changed) fails the README "no increase in safety/tool failures" gate literally.
- Evidence: data/laya-experiments/laya-c49-2124-miniship-20261103/job-status.json + freeze.json; data/laya-orchestration/laya-loop/reference-line-next-candidates.json (`c48_review.c49.review`); log.md 2026-11-03 11:30.
- Decision: both reviewers (DeepSeek v4.1-flash, Codex personal_2 gpt-6.1-sol) say do not adopt yet; do not reject. C50 (`laya-c50-c3-facts-2124-miniship-20261103`) is live: identical arms, 75 fresh pairs seeds 80900-80974, confirmatory statistic pre-registered before data as the pooled C49+C50 exact McNemar on C4 ever (150 pairs, ~56% power), C50 alone also reported, stall rate co-primary, plus the 80826 bug precondition and earlier-service check.
- Lifecycle: C49 unit inactive since ~10:08; C50 unit active (`laya-c50-c3-facts-2124-miniship-20261103.service`), 150 jobs at 30 workers, until 2026-11-04 20:00 IST. No code change, no mod redeploy, no push.

## [2026-11-03] C50 + pooled read: bundle not adopted; C3→C4 line closed as saturated; C51 live on C4→C6, C52 queued
- What: C50 (`laya-c50-c3-facts-2124-miniship-20261103`, 75 fresh C3-origin pairs, same arms f754fbf/11689af) completed 150/150, zero evidence errors, zero caps. Corrected to `strict_reached` (ever): C4 72/75 vs 74/75 (3/1, p .625); C5 ever equals C4 ever (the apparent C5 reversal was a final-snapshot artifact on seeds 80902/80914/80918/80973). Pre-registered pooled C49+C50 confirmatory statistic: strict C4 ever 144/150 vs 149/150, +3.3 pp, 6/1, exact p .125, bootstrap [0.0,+6.7] → **fails p<.05; no adoption**. Pooled stalls 6 reference / 1 facts; stalls-excluded sensitivity is 143/143 both arms (0 pp), so the whole signal is the stall tail. Mechanism replicated (parks 1565→593, median 20→7/run; costs also: batches 2997→3533, pending-construction 18→26). Mall-cap events reverse between cohorts (70/5→6/3 then 5/1→60/6) so they are seed-dominated, not a guard win.
- Decision: C3→C4 is a saturated endpoint (reference 144/150 = 96 %, ~4 pp headroom, ~8 % power at 75 pairs). The unadopted bundle moves to the C4→C6 segment (C47 A/A reference C6 55 %, 45 pp headroom; sulfur/advanced-circuit parks still fire), with no code change. C51 (`laya-c51-c4-c6-facts-2124-miniship-20261103`) is live: C4 bundle, strict_target C6, 75 pairs seeds 81100-81174, until 2026-11-04 08:00 IST. C52 (`laya-c52-c4-c6-facts-2124-miniship-20261103`) is frozen and queued (seeds 81175-81249, until 2026-11-05 08:00 IST) as stage 2 of a pre-registered pooled 150-pair C6 read.
- Evidence: data/laya-experiments/laya-c50-c3-facts-2124-miniship-20261103/job-status.json; data/laya-orchestration/laya-loop/c51.json + c52.json; reference-line-next-candidates.json; log.md 2026-11-03 23:05.
- Lifecycle: C50 unit inactive; C51 unit active (150 jobs, 30 workers); C52 frozen but not started (unit inactive). One running + one scheduled. No code change, no mod redeploy, no push.

## [2026-11-03] C50 reviews: bundle abandoned; C52 replaced with the ownership-link fact
- What: DeepSeek's C50 review (landed after C51 launched) agrees with Codex: **do not adopt; do not retest the bundle.** At the pooled 6/1 discordance, 75 pairs has 5.4 % power and the 75-pair MDE (8.0 pp) exceeds the observed 3.3 pp, so no in-band design can decide it. The effect is a rare stall hazard (all 7 pooled discordances are stalls; reference 6/150 vs facts 1/150), not C4 durability. (DeepSeek's pooled "4 facts" stall row is arithmetically wrong; 6/1 is correct.) It names the better fact: the ownership link from a material shortage to the blocker's option — at 80904, oil-refinery was short 2 for plastic-bar, but its produce option had `prerequisite_for: []`.
- Action: C51 was already live, so it is not edited; its C4→C6 stage-2 partner (old C52, same bundle) was **cancelled before start** and the pooled C6 read withdrawn — C51 is now a single-cohort segment estimate, not an adoption test. The replacement **C52** (`laya-c52-c3-ownership-2124-miniship-20261103`) is frozen and queued: reference `f754fbf` vs `3094797` (one factor on the clean line), C3 bundle, 75 pairs seeds 81000-81074, until 2026-11-05 08:00 IST.
- Evidence: `data/laya-orchestration/laya-loop/{c51.json,c52-ownership.json,log.md}`; `data/laya-experiments/laya-c52-c3-ownership-20261103/freeze.json`; new tests `tests/test_laya_ops_ownership.py`.
- Lifecycle: C51 unit active (11 running / 139 queued); C52 frozen, unit not started. One running + one scheduled. No push.

## [2026-11-03] Two-track loop: A/B odd-even chain, no idle gap
- Policy (owner): after every cohort, schedule the next; run two tracks A (odd) and B (even); the controller reports each terminal.
- Implementation: each cohort's successor is frozen and its unit started before the predecessor finishes. `run_campaign` waits on `predecessors_done` and dispatches at the predecessor's terminal, so the next cohort starts with no gap. The waiting unit is the review window — if the review changes the design, stop it and refreeze before any job starts.
- Chain: **C51 (A, live)** → **C52 (B)** → **C53 (A)** → **C54 (B)**, all four units active; C52/C53/C54 have 150/150 jobs queued and none started. Track A = park bundle at C4→C6, C51+C53 pooled strict C6 read. Track B = ownership link at C3→C4, C52+C54 pooled repeat-block primary with C4 ever milestone.
- Evidence: `data/laya-orchestration/laya-loop/{queue.json,c51.json,c52-ownership.json,c53-c4c6.json,c54-ownership.json}`; freeze/plan under each cohort's registry.
- Lifecycle: four units active (C51 running jobs; C52–C54 waiting on predecessors). No code change, no push.

## [2026-11-04] C51: park bundle closed; Track A C53 replaced with ownership link at C4→C6
- What: C51 (`laya-c51-c4-c6-facts-...`, C4→strict C6, f754fbf vs 11689af, 75 pairs) finished 147 completed + 2 prepare infra failures (one per arm) + 1 facts wall timeout, zero evidence errors. C4 74/74; C5 51/53 (+2.7 pp, p .83); **C6 29/75 vs 25/75 (−5.3 pp, 10/14, p .541, bootstrap [−18.7,+8.0])**; stalls 45/48. The C49/C50 C5 advantage did not reproduce; the park bundle is **closed without adoption**. Sensitivities (drop the 2 prepare pairs, drop the timeout) do not reverse.
- Track A replacement (Codex recommendation, zero-code): **C53** `laya-c53-c4-c6-ownership-...` — f754fbf vs **3094797** (ownership link, one factor), C4 bundle, 75 pairs seeds 81300-81374, primary strict C6 ever with exact paired McNemar; power ~26/57/86 % for +10/+15/+20 pp at C51's discordance. Separate claim from Track B, no pooling. Old C53 stopped before start (150 queued, 0 run) and marked cancelled_before_start.
- Log finding for the segment: plastic reaches a stocked plateau (4832 network, craft counter frozen at 2467), so C5's `plastic_craft_counter_advances` fails; stalls end on engine-unit/sulfur/productivity-module/rail shortages, `bootstrap_mall_slot_cap` 154, `district_expansion_unbuildable` 56.
- Correction recorded: Track B's stall guard parenthetical "6/150 vs 4/150" is wrong; facts pooled stalls are 1/150 (C49 0 + C50 1), so the guard is "not above the reference 6/150".
- Evidence: C51 `job-status.json` + logs; `reference-line-next-candidates.json`; `laya-loop/log.md` 2026-11-04 07:10.
- Lifecycle: C52 (Track B) running 12/138; C53 and C54 frozen, units active and waiting. No code change, no push.

## [2026-11-04] C51 DeepSeek review: endpoint corrected; C53 kept but amended to the proxy primary
- DeepSeek confirms the C51 numbers and closes the park bundle, then corrects the Track A design: at C4 origin C6-ever is a **stall** endpoint (every non-stalled run that reached C5 reached C6), so 75 pairs only decides ≥18.8 pp; and `material_shortage` is only 2.8 % of terminal stall decisions there. Dominant terminal code is `bootstrap_mall_slot_cap` (70 %), a circular dependency: the mall is capped at 8 until advanced-circuit production starts, but the AC packet needs mall-built infrastructure.
- Decision: keep C53 as the isolated ownership link at C4→C6 (Codex's single factor) but **amend its pre-registration before the first job** to primary = repeat `material_shortage` blocks per 1,000 decisions (≥20 % relative, paired p < .05), with C6-ever as secondary at the honest 18.8 pp MDE, and guards on stalls/typed conflicts/C5/server time. Its mechanism is active at 84.7 blocks per 1,000 decisions, MDE 17.5 % relative, power 0.98 at 25 % relative.
- Recorded for C55 (after C53): the mall-slot **staged-packet operation** (packet yields the bootstrap slot until deliverable) with `bootstrap_mall_slot_cap` blocks per 1,000 decisions as primary.
- Withdrew both superseded registries (old C52 park-bundle C4→C6, old C53 park-bundle C4→C6): 150 jobs each marked `cancelled` with cause, so their seeds/ports cannot dispatch.
- Known infra defects recorded, not fixed under live campaigns: prepare stderr discarded (`CalledProcessError` repr only), no retry for `infrastructure_failure`, all cohort port bases inside `ip_local_port_range 32768-60999` (ephemeral collision risk), reference always admitted first, dual C6 definitions (Laya acceptance 100 % vs strict 54), vacuous tick cap (wall-censored).
- Lifecycle: C52 running (14/136), C53 and C54 frozen with units waiting. No code change, no push.

## [2026-11-04] C52 ownership closed; C53 truncated; C55 dense-mall restart fix live
- C52 (`laya-c52-c3-ownership-...`, C3→C5, f754fbf vs 3094797): 150/150 completed, zero evidence errors. **Primary proxy failed in the wrong direction**: repeat `material_shortage` blocks per 1k decisions 66.5 reference vs **78.1** ownership (+17.3 % relative, paired t p .140, CI [−3.8, +26.7]); C4/C5 ever 72 vs 70 (p .6875); stalls 3 vs 5. Ownership-link family closed without adoption.
- Lifecycle: C53 (same factor at C4→C6) had 11 admitted jobs; the coordinator lock prevents external queue cancellation, so the unit was stopped and the cohort marked **truncated** (8 completed, 142 cancelled, `admission_reserved` cleared). No confirmatory p, no pooling. C54 cancelled before start; the C52+C54 pooled analysis is withdrawn.
- **C55** (`laya-c55-c4-c6-densemall-...`) frozen and live: f754fbf vs **c715c49** — the dense bay now opens from the world-state starter-release probe, because a checkpoint restart never populates the in-process ledger and the pre-AC cap stayed 8 (the circular C4→C6 blocker, 70 % of C51 terminal decisions). C4 bundle, 75 pairs seeds 81500-81574, ports 21600/22600 (below the ephemeral range), primary `bootstrap_mall_slot_cap` blocks per 1k decisions (≥20 % relative, p < .05), C6-ever secondary at the honest 18.8 pp MDE.
- Evidence: C52 `job-status.json` + journals; `laya-loop/c55-densemall.json`; `tests/test_quad_mall.py` 24/24; fast gate only the known interface import-firewall failure.
- Lifecycle: C55 unit active (6 slots and climbing); C52 complete, C53 truncated, C54 cancelled. No push.

## [2026-11-04] C52 correction (null, not harm); C55 live; C56 scheduled
- DeepSeek's C52 review corrects the headline: the +17.3 % is a per-item expansion artifact (multi-item packets score up to 6 blocks per decision). Under the literal pre-registration — one block per shortage decision, matching `faults.material_shortage` — it is **+5.1 %, t 0.53, p .600**; single-item shortages only **−5.0 %, p .645**. The factor is dead either way, but C52 is a **null, not a regression**. The manipulation is near-inert (`goal_need` 49.4→50.3 %) and is partly erased at `training/laya_ops_runner.py:735` (pre-existing: typed prerequisites overwrite shortage owners).
- C53 stays stopped (8 completed / 142 cancelled, 4 pairs, uninformative; its amended primary is only in the log/status, not `regression.json` — registry drift recorded). C54 stays cancelled (0/150); the pooled C52+C54 analysis is withdrawn.
- **C55** live (6 running / 144 queued): dense-bay restart fix `c715c49`, C4→C6, cap-block rate primary. **C56** frozen and waiting (stage 2, seeds 81600-81674, same arms): pooled 150-pair cap-rate read pre-registered before data.
- Follow-up recorded: DeepSeek's one-slot reservation for the cap-lifting advanced-circuit packet (allow `slot_limit + 1` only when its bill is deliverable, capped at one slot) if C55/C56 show 12/12 still binding. Not implemented yet — it needs the cell bill before the cap check.
- Lifecycle: C55 unit active, C56 unit active and waiting. No push.

## [2026-11-04] C55: dense-bay fix rejected (cap was a symptom); C56 stopped
- C55 (`laya-c55-c4-c6-densemall-...`, 75 pairs, f754fbf vs c715c49): 150/150 terminal, zero evidence errors. **Primary mechanism succeeded** — `bootstrap_mall_slot_cap` blocks fell **90 %** (271.9 → 27.2 per 1,000 decisions, paired −244.7, CI [−281.8, −206.1]) — but **strict C6 ever fell** 43/75 → 40/75, stalls rose 32 → 33, median wall time rose **72 %** (1024 → 1765 s), and the arm produced the only 2 wall timeouts. The cap was a symptom, not the C6 binder.
- Removing the cap shifted the stall to `production_prerequisite_deferred` (276 → 469 terminal decisions), `material_shortage` (85 → 219) and chemical handoff (12 → 99). Runner logs name the causes: 193 iron-refinery extensions blocked by real infrastructure at a fixed site, and a long tail of "borrowed cell is producing temporary X for the X seed" loan churn.
- C5/C6 anomaly: strict C6 ever exceeds strict C5 ever (ref 43 vs 41; dense 40 vs 44); 32/23 runs reach C6 without ever satisfying C5. The C5 predicate is a 120 s advancing-craft window, not a gate — do not use C6/C5 ratios.
- **C56** (stage 2, same factor) stopped at 21 completed / 129 cancelled; `admission_reserved` cleared. Dense-bay branch not adopted; `c715c49` stays an unmerged local branch.
- Lifecycle: no cohort running/scheduled pending the C55 review (deliberate gap). No push.

## [2026-11-04] C55 rejected (cap was a symptom); C57 loan-horizon hold live
- Codex's C55 review confirms the split: cap blocks fell **90 %** (271.9 → 27.2 per 1k, CI [−282.4, −206.6]) but strict C6 fell **43/75 → 40/75** (21/18 discordant), stalls 32 → 33, median wall **+72 %** (1024 → 1765 s), total server-seconds **+55 %**, with 2 densemall wall timeouts. Mall footprint rose from 8 to 8-18 assemblers, so the cost is expanded footprint, not construction starvation. Verdict: **reject `c715c49`**; keep it unmerged and keep the restart-bug diagnosis plus regression test as evidence.
- C5 is not a gate: its predicate needs advancing craft counters at every successive observation for ≥120 s, so 32/43 reference C6 runs never satisfy it. C4-origin cohorts will use **strict C6 only**, C5 descriptive.
- **C57** live: `laya-c57-c4-c6-loanhold-...`, f754fbf vs **bd08c49**. The loan-horizon hold suppresses a request only while `economics.loans` still names its seed and releases on completion, cancellation or invalidated ownership — aimed at the 101 AM1 / 78 AC / 29 PPC / 23 steel-chest loan-churn deferrals. 75 pairs seeds 81700-81774, strict C6 primary (exact paired; 80 % MDE ~23 pp), guards include a 10 % total-cost ceiling.
- Evidence: C55 `job-status.json` + `freeze.json`; `laya-loop/c57-loanhold.json`; `tests/test_laya_ops_loan_hold.py`; fast gate only the known interface import-firewall failure.
- Lifecycle: C57 unit active (2 preparing / 148 queued); C55 complete, C56 truncated. No push.

## [2026-11-04] C6-v1 is a chest metric (verified); C57 amended; c6-v2 needed
- DeepSeek's C55 review exposed a milestone defect: `c6-v1` accepts any owned recipe with advanced circuits as an ingredient, so **mall chests satisfy it**. Verified: **83/84 C55 C6 passes are chests** (passive-provider 61, requester 22) plus 1 productivity-module; **C51 is 56/56 chests**. Every C4→C6 cohort result to date measures chest timing, not AC industrialization — which is why cap/ownership/dense-bay interventions moved C6 by noise.
- C57 amendment recorded **before unblinding**: co-primary becomes loan-gated deferrals per 1,000 decisions (base 152.9 dense / 121.8 ref, SD 102.9, MDE₇₅ 33.3/1k, power 0.97 at a 30 % cut); C6 must be reported split by consumer recipe; a null chest-C6 with mechanism success does not close the loan-hold line.
- C55 corrections: 148 completed + 2 wall timeouts; cap residual in 22 runs (53/75 zero-cap); loan deferrals 2,703 and collision 2,002 (the earlier 193/101/78 counts were ~10× low and inverted the cause ranking); wall +72 % is mostly volume (+44 % decisions, +13 % s/decision).
- Next: **C58** iron-refinery alternate-site extension (collision refusals/1k, MDE₇₅ 16.2, power ~1.00 at 30 % cut), then **C59** the real binder — a production AC-consumer line with **c6-v2**. No further cohort frozen until those exist.
- Lifecycle: C57 running; C55 complete; C56 truncated; no new unit. No push.

## [2026-11-05] C57 interrupted at 101/150, resumed
- The C57 unit exited with `Result=success`/`ExecMainStatus=0` at 03:37 while 49 jobs were unresolved (11 running, 38 queued); `run.out` empty, no journal stop line, host healthy. Cause unexplained; not a deliberate truncation.
- Resumed the same root. `recover()` recorded the 11 interrupted attempts as `infrastructure_failure` / `coordinator_interrupted_attempt` (not gameplay stalls) and cleared admissions; 37 queued re-dispatched. State now: 101 completed + 11 infra + 37 queued + 1 preparing.
- The interim read (loanhold C6 28 vs reference 20; stalls 22 vs 31) is directionally positive but C6 is the chest-dominated metric; the operative primary remains the amended loan-gated deferrals per 1k decisions, with C6 split by consumer. Pairing will report both-completed pairs and the 11 infra failures explicitly, plus infra-excluded and caps-as-failure sensitivities.
- Open defect: clean-exit-with-stranded-jobs; instrument the CLI exception path and `run_campaign` completion condition if it recurs.
- Lifecycle: C57 unit active; no push.

## [2026-11-05] C57 interruption corrected: power cut, not a coordinator defect
- Cause: accidental power cut on the runner side. The resumed unit (active since 04:23:26) is draining the remaining queued jobs; no second coordinator is needed or permitted.
- Verified 04:40: 115/150 terminal (53 reference + 51 loanhold completed + 11 infrastructure_failure), 6 running, 29 queued, host 6/5 slots, 82.8 C, 19.1 GB free.
- The 11 orphaned attempts remain `infrastructure_failure` / `coordinator_interrupted_attempt`, one attempt per seed, not retried. The loanhold read will use **139 usable runs** (150 − 11), ~69-70 both-completed pairs, with infra-excluded and caps-as-failure sensitivities.
- The earlier "clean-exit-with-stranded-jobs" concern is closed as environmental. No other cohort needs resuming.

## [2026-11-05] Game speed restored to 2x (120 UPS) for new restart cohorts; host gate is speed-relative
- Cause of the 120 -> 60 UPS drop: the restart-cohort scheduler (`tools/laya_regression_scheduler.py`, first used 2026-10-29 19:53 IST) never passed `--speed`, so runs took the runner's 1.0 default. Run-log `RUN START ... speed=` lines show C45/C46c/C48 at 2.0 (batch launcher) and C47 plus C49-C57 at 1.0. It was not a Factorio, engine or config change.
- Fix: `game_speed` is now a frozen cohort field (default 2.0, validated 0.25..8) and every run receives `--speed`. Roots frozen earlier keep 1x on resume, so C57 is unchanged. Fast tests cover both cases. Uncommitted: the file belongs to the untracked restart-scheduler infrastructure.
- Host gate (`7c41148`): floors compare 1x-equivalent UPS (measured / the world's logged speed); the dashboard also shows measured UPS and speeds. Deployed to `~/.local/share/laya-host/` (backup `laya_host_scheduler.pre-speed-relative-20261105.py`), unit restarted; with C57 it reads speed 1.0, ~58 UPS, hold.
- Speed is its own factor, like the engine: never pool or compare arms across speeds (rule in `docs/rl/Laya_Restart_Segment_Contract.md`).
- Lifecycle: C57 continues at 1x; the first cohort frozen after this runs at 2x; no push.

## [2026-11-05] Review applied: UPS speed correction, infra committed, c6-v2 built, 2x successor frozen
- UPS: my earlier "no setting moved 120→60" was wrong. The restart scheduler never passed `--speed`, so C47/C49–C57 silently ran at 1.0 while C45/C46c/C48 ran at 2.0. Owner fixed `enqueue` (frozen `game_speed`, default 2.0) and the host gate (`7c41148`, 1x-equivalent UPS). Speed is its own factor; never pool 1x with 2x.
- Committed the restart infrastructure as one local commit **`fde2a6d`** (no push): scheduler, checkpoint bank, restart state/segment evidence, mixed design, restart runner and their tests (2323 insertions). Fast gate only the pre-existing `test_training_isolation` failure from uncommitted `training/laya_ops.py`.
- Built **`c6-v2`** as **`b1ae062`** (on `f754fbf`): the consumer leg excludes logistics chests. Re-score of existing journals: **C51 0/150** and **C55 1/150** production-consumer C6 passes — c6-v1 was measuring chest timing. Constraint: the checkpoint bank is c6-v1, so restart cohorts need bundle re-validation before c6-v2; full-origin cohorts are unaffected.
- **Successor frozen and waiting:** `laya-c58-c0-c6v2-2x-2124-miniship-20261105`, A/A at `b1ae062`, full-origin C0, 75 pairs seeds 81900-81974, **game_speed 2.0**, ports 28600/29600, until 2026-11-08 08:00 IST, after C57. The earlier f754fbf/c6-v1 C58 was cancelled before start.
- Next: re-score C57 at terminal; then C58's c6-v2 2x baseline; then the Terra items (real AC consumer line, post-C6/research ops, outcome-aware ranker). Cohort-size changes beyond 120-150 jobs are a proposal only.

## [2026-11-05] C57 no adoption (inert hold); C59 real AC consumer frozen and waiting
- C57: c6-v1 28/70 vs 37/69, but the paired 69-pair read is **19/10, +13.0 pp, exact p .136**; **c6-v2 0/70 vs 0/69** (every pass chest-only); the amended loan-deferral co-primary is **+5.9 % worse**. The hold never fired: `loan_holds` keyed on `target_item`, which the survey does not emit, and only 8/1,719 requests even satisfy its request=seed condition. Failed manipulation, not a rejected hypothesis.
- **C58** running: 2x c6-v2 A/A baseline (`b1ae062`, seeds 81900-81974, `game_speed 2.0`).
- **C59** frozen and waiting on C58: `laya-c59-c0-consumer-2x-...`, full-origin C0, 2x, 75 pairs seeds 82000-82074, arms `b1ae062` vs **`a0590e2`**. One factor: offer a `productivity-module` consumer while the C6 advanced-circuit goal is active and it is not stocked. Primary strict **c6-v2 ever**, exact paired. The phase-target variant was rejected because it breaks proxy `phase_reached`.
- Commits: infra `fde2a6d`, c6-v2 `b1ae062`, consumer `a0590e2`. No push.

## [2026-11-05] C57 review closed; C59 nudge cancelled; C60 2x c6-v2 baseline frozen
- DeepSeek agrees with Codex: **no adoption, hold inert**. Corrections recorded: the registered loan metric includes `temporary_mall_batch` and a pooled denominator (n=69, not 75, and my ppd-only per-run mean is not the registered estimator); the c6-v1 +13.6 pp is A/A noise from a dead factor (19/10, p .136), usefully calibrating that **<15 pp chest-C6 differences are noise**. The hold would touch only ~15 % of churn even with the right key (85 % is the parent's request), so the line is closed as mis-specified at the wrong level.
- **C59 cancelled before start**: its menu nudge targeted an option already offered 13,789 times and cap-blocked in 61 % of choices; it adds no operation or cell. The real service treatment needs a dedicated consumer cell outside the cap via `production_service`, which is **not present in the `b1ae062` reference arm** (newer working-tree code) — a code port, not a freeze.
- **C60** frozen and waiting on C58: second 2x full-origin **c6-v2 A/A baseline**, `b1ae062` both arms, 75 pairs seeds 82100-82174, `game_speed 2.0`. It measures c6-v2 A/A variance, currently unknown.
- Scheduler: **`ba3d537`** accepts a bundle whose origin predicate version is unchanged, so c6-v2 no longer blocks c4/c5 restart cohorts.
- Commits: `fde2a6d` infra, `b1ae062` c6-v2, `ba3d537` bundle check. No push.

## [2026-11-05] C58 A/A: c6-v2 passes nothing at C0; service port is the gate
- C58 (A/A, both `b1ae062`, C0, 2x, 75 pairs seeds 81900-81974): **c6-v2 0/75 in both arms**; C4 49/45, C5 49/45; stalls 69/71 + 6/4 tick timeouts; median 3246/3052 s; zero evidence errors; all runs at speed 2.0 with measured UPS 115-118.
- Every C6 evidence block failed across ~6,043 snapshots with `excluded_logistics_chests` present — the chest-backed v1 passes are gone by construction. This is a predicate result, not a harness failure.
- C60 (second A/A, `b1ae062`, seeds 82100-82174) is running.
- **Next gate:** the dedicated AC consumer cell outside the 8-assembler cap via `production_service` is the only route to a c6-v2 pass, and that operation is **absent from `b1ae062`** (`training/laya_chain_facts.py`, `add_production_service_options`, and the ops lambda all missing; newer uncommitted working-tree code only). The port must land before the service cohort can be frozen.
- No adoption (A/A). No push.

## [2026-11-05] Service consumer ported; C61 smoke running
- C58 (A/A, c6-v2, C0, 2x): 0/75 both arms; 6,043/6,043 C6 blocks failed, 809 with new AC production and **zero** consumer output. Valid A/A, predicate result. C60 (second zero baseline) stopped.
- Ported the real consumer path into the reference arm: `production_service` operation (`ensure_produced(upgrade_bootstrap=True, stock_target=10, minimum_machines=2)`), a default-off `Settings.production_service` flag, and a C6-only `productivity-module` menu option gated on an enabled catalog recipe.
- Commits: `8d437e1` (port off), `ede46f6` + `09bbfd6` (origin-version restore and reached-only milestone/reload checks), `78e8557` (flag on). The c6-v2 predicate change needed four reload checks made version-aware; reached definitions still fail closed.
- **C61 smoke running:** `laya-c61-smoke4-c4-service-2x-20261105`, C4 bundle, 2x, 6 pairs seeds 82200-82205, `09bbfd6` vs `78e8557`. Gate: option offered/selected, two dedicated machines, a module crafted, c6-v2 production-consumer evidence; fail → fix before 75 pairs.
- No push.

## [2026-11-05] Service port fixed; C62 C0 smoke running
- C58 DeepSeek review confirms the A/A: c6-v2 0/150 with 809 production-true snapshots and `consumer_deltas={}` in all 6,043 — no owned AC consumer ever existed. C60 remains stopped.
- DeepSeek found three port blockers before the 150-job spend: `productivity-module` had no `LINE_RECIPES` entry (so the service diverted to an AC mall seed), the option was C6-only and vanished at the S1 transition, and sizing/receipts were ambiguous. Fixed with the live AM2 PM recipe (AC 5 + EC 5, 15 s) and a C6+S1 option window; 4 focused tests.
- Commits: `6c55ae6` (fixes, switch off), `8b0b5b2` (switch on, one-line). Earlier: `8d437e1` port, `ede46f6`/`09bbfd6` origin-version restore.
- **C62 C0 smoke running:** `laya-c62-smoke-c0-service-2x-20261105`, full-origin C0, 2x, 6 pairs seeds 82250-82255, `6c55ae6` vs `8b0b5b2`, ports 22600/23600. Gate: 0 infra, option offered ≥90 % of C6 decisions, selected ≥4/6, ≥2 PM machines in ≥2/6, a PM crafted in ≥2/6, ≥1 c6-v2 consumer-output snapshot.
- Then (if the gate passes): C0 full-origin 75 pairs, seeds 82300-82374. No push.

## [2026-11-05] C62 service smoke: option fires, cell never builds; C63 repair running
- C62: 12/12 completed, zero infra, speed 2.0. Strict C4/C5: reference 6/6 vs service 5/5; c6-v2 0 both.
- The service option fired (**1,265 offers, 70 selections** across 5/6 service runs; control 0), but **zero PM producers**: the selected call returns `material_shortage` from `expand_copper-plate_system` (drills 12 need 2 short, inserters 57 need 19, poles 1, PPC 1, splitter short). The service expands the copper-plate system first and stalls before any PM cell.
- Gate failed (0/6 builds), so the 150-pair cohort is **not** frozen. **C63 repair smoke running:** same arms, C0, 2x, seeds 82260-82265, `max_game_ticks 800000` with the 7,200 s wall, gate unchanged.
- If C63 still stalls on construction, the service is not viable at C0 within budget; the next cohort becomes the construction-supply line, not the 150.
- Reviews (DeepSeek/Codex) running on the C62 evidence. No push.

## [2026-11-05] C62 gate failed; C63 final budget smoke; C64 construction-supply hold queued
- Codex confirms C62 fails the gate and corrects the diagnosis: of 70 selected service outcomes, **41 material shortages** (copper 6, cable 21, AC 13, PM 1), 24 temporary batches, 3 prerequisite deferrals; seed 82251 reached PM planning but needed **4 AM2 / 11 PPC with 2 / 5 available**. The chain reaches PM planning; it cannot fund the construction bill. No dispatch defect.
- **C63 kept** as the final budget smoke (800k ticks, full gate). If construction still blocks, the service-only branch closes; no more timeout extensions or 150-job service cohort.
- **C64 queued (smoke):** a reusable construction-supply repair — hold the production service while its last `material_shortage` names unstocked items, releasing when the bill is covered. Commit `ce60a80` on `8b0b5b2`; arms service-on vs service-on+hold; C0, 2x, seeds 82270-82275, after C63.
- Pipeline: C63 running + C64 waiting. DeepSeek C62 review still out. No push.

## [2026-11-05] PPC deadlock found; C63/C64 stopped; C65 diagnostic running
- DeepSeek's C62 review overturns the copper diagnosis: copper expansion completes; the blocker is a **PPC bootstrap deadlock** — PPC needs 1 AC, every AC/PM cell needs 1 PPC, the lone PPC is spent earlier, so AC never starts and PPC can never be made. `chemical_capability_handoff` on PPC: 191/215 service selections. The PM cell is ghosted in 5/6 service runs, short AM2 2 + PPC 1; sites are not collision-blocked.
- **C63 stopped**: the 800k cap is inert (10/12 C62 runs stop on the 18k progress guard); new seeds broke pairing. **C64 stopped**: the hold cannot create PPC.
- **C65 diagnostic running:** A/A at fresh seeds 82300-82305 (C0, 2x, 12 jobs) to confirm the deadlock rate and provide the control for the reservation cohort. No adoption.
- Recorded defects: contaminated control (PM line recipe ships in both arms; only the flag differs), uninstrumented PM gate, cap overcommit (12-15 vs cap 12), and a dead prerequisite path (PPC/requester/long-handed absent from `LINE_RECIPES`, so `_core_mall_prerequisites` cannot fire).
- Next: PPC reservation at the builder bill/admission hook, 6-pair smoke, then 150/75. No push.

## [2026-11-06] C65 A/A null; C66 guard-sensitivity smoke running
- C65 (A/A, both `8b0b5b2`, C0, 2x, seeds 82300-82305): 12/12 completed, zero infra; C5 4/6 vs 5/6, **C6 0/6 both**, median 3312 vs 3656 s. The arms are indistinguishable, so the PPC deadlock question is **not resolved** at n=6.
- The PPC reservation needs a builder bill/admission hook; `preview_mall_allocation` returns only `(origin, side)` and the ledger is project-based, so it cannot be placed safely without more code work.
- **C66 running:** guard-sensitivity smoke — `STALL_TICKS` 18k vs **40k** (commit `8120156` on `8b0b5b2`), C0, 2x, 6 pairs seeds 82400-82405. It tests whether the progress guard (which stopped 10/12 C62 runs) truncates the PPC/AC chain, rather than the tick cap C63 changed.
- No push.

## [2026-11-06] C66 null; PPC reservation implemented; C67/C68 running (12-run cohorts)
- C66 (guard 18k vs 40k, C0, 2x, seeds 82400-82405): 6 genuine per arm, no infra, median 3328 vs 3340 s; C5 6/6 vs 4/6, C6 0/6 both. The guard did not help; the PPC deadlock is structural.
- Implemented the PPC reservation (`146cb07` on `8b0b5b2`): while AC production has not started, a non-AC cell whose bill needs the last PPC is deferred (`ppc_reserved_for_ac`); the AC cell may still claim it; unknown telemetry does not preempt. Fixed the fast-gate regression in `test_material_reservations.py` by guarding the telemetry call; suite 85/85.
- **C67 running** (`laya-c67-smoke-c0-ppc-2x-20261106`, 6 pairs seeds 82410-82415) and **C68 waiting** (6 pairs seeds 82420-82425) for a pooled 12-pair read; arms `8b0b5b2` vs `146cb07`, C0, 2x.
- Owner: mini server in cool/power-saving mode ~3 h, so the cohorts are chained, not parallel. No push.

## [2026-11-06] C67 PPC reservation: directional edge, no C6; C68 stage 2 running; C69 waiting
- C67 (6 pairs seeds 82410-82415, C0, 2x): 12/12 completed, 6 genuine per arm, no infra, speed 2.0. Strict C4/C5 reference 3/6 vs reserve 4/6; **C6 0/6 both**; median 2517 vs **3386 s (+35 %)**. Not evidence at n=6.
- **C68** (same arms, seeds 82420-82425) is the pre-registered stage 2 and is running; the pooled 12-pair read decides.
- **C69** frozen and waiting (construction-bill hold, `8b0b5b2` vs `ce60a80`, seeds 82430-82435) as the next ready single factor; cancellable before start if the reviews close the service line.
- Reviews (DeepSeek/Codex) running on C67 and the C69 keep/cancel question. No push.

## [2026-11-06] C67 guard never fired; C69 cancelled; C70 AC-start baseline waiting
- Codex confirms C67 accounting and corrects the read: **AC production started 3/6 reference vs 4/6 reserve**, so "no AC start" was false, and the reservation guard **`ppc_reserved_for_ac` produced zero receipts** in all six reserve runs — C67 did not exercise it. The +1 AC run is not attributable.
- **C69 cancelled** before start: the hold releases on the previous deficit, not the full required bill (6 vs 11 PPC), and suppresses the service after upstream shortages (55 chemical-plant-stage).
- **Next treatment** (Codex): a PPC-producing bootstrap cell whose construction bill needs **zero PPC**; primary = newly produced PPC delivered to usable stock; 6 pairs. Not yet implemented.
- **C70 frozen and waiting:** fresh-seed A/A AC-start baseline (both `8b0b5b2`, seeds 82440-82445, C0, 2x, after C68) to give the control rate for the bootstrap cell.
- Guard limitations recorded: `stock <= 1` misses multi-PPC bills; transferable stock is not reservation-adjusted; `_production_started` accepts lifetime output.
- C68 running; C70 waiting. DeepSeek C67 review still out. No push.

## [2026-11-06] C67 guard unreachable; C68 stopped; C71 compact-cell scope running
- DeepSeek's C67 review: AC did start (7/12), but the **PM consumer cell is 0/12**. The reservation guard fired **0 times** because its only call site is gated `not upgrade_bootstrap`, while `production_service` is hard-wired to `upgrade_bootstrap=True` — unreachable for the PM consumer. The C6 option was also shadowed by the generic helper, and its 10/2 params never reached the op (the lambda took only `item`), so the service always ran a promoted line with a 15 AC / 22 AM1 / 20 LH / 15-19 PPC bill.
- **C68 stopped**: byte-identical replicate of C67; cannot discriminate. **C70 cancelled** before start.
- Implemented the **scope fix** (`8499b9d` on `8b0b5b2`): the C6 service is a compact one-machine cell (`upgrade_bootstrap=False`, `stock_target=1`, `minimum_machines=1`, `allow_promotion=False`), with the scope params passed through the operation. Focused 42/42; fast gate only the known firewall failure.
- **C71 running:** `laya-c71-smoke-c0-scope-2x-20261106`, C0, 2x, 6 pairs seeds 82440-82445, `8b0b5b2` vs `8499b9d`; primary c6-v2 ≥3/6 vs ≤1/6, secondaries PM machines/output, selections, wall.
- No push.

## [2026-11-06] C71 compact-scope null; C72 A/A baseline running
- C71 (6 pairs seeds 82440-82445, C0, 2x): 12/12 completed, no infra, speed 2.0. Strict C1/C2/C3/C4/C5 **1/5/5/4/4 in both arms**; C6 0/6; median 3013 vs 2961 s. Service selections 89 vs 74, but **0 PM cell sightings in both arms** — the compact scope did not build a cell or move a milestone.
- Selected outcomes: reference `temporary_mall_batch` 25 / `material_shortage` 55 / `untyped_stuck` 3 / deferred 5; scope deferred 24 / shortage 28 / prerequisite_advanced 3 / cap 17 / chemical handoff 2.
- **C72 running:** A/A baseline (both `8b0b5b2`, seeds 82450-82455, C0, 2x) to measure the natural AC/PM rate while the next operator is chosen.
- Reviews (DeepSeek/Codex) running on whether the compact cell dispatched and what the next 12-run single factor is. No push.

## [2026-11-06] C71 dispatched but blocked at admission; C73 permanent-consumer smoke waiting
- DeepSeek's trace: the compact PM option **dispatched with correct params**, was **diverted by the bootstrap loan (24/74)**, **refused by the cap (17/74 at 15/12)**, then AC-starved (28/74). The quad PM project reached `ready` (AM1 2 / inserter 2 / LH 2 / PPC 1) and was still not placed — admission, not geometry or selection.
- Corrections: `146cb07` was **not** in C71's arms (guard absent, not unreachable); `upgrade_bootstrap=False` is **inside** the bootstrap cap; the compact service is byte-identical to `mall_slot`; `minimum_machines=1` is a floor (quad/paired geometry); strict C1 was 0/6 both.
- Implemented **permanent-consumer admission** (`db70250`, with the guard cherry-picked as `210778a`): `production_service` skips the seed loan and the cap block; `upgrade_bootstrap=False` keeps the PPC guard reachable; `allow_promotion=False` keeps the cell compact. Focused 127/127; fast gate only the known firewall failure.
- **C73 frozen and waiting** (after C72): 6 pairs seeds 82460-82465, `210778a` vs `db70250`; primary PM machines ≥1 and output >0 (≥3/6 vs ≤1/6); guards zero cap/loan on PM, committed ≤16/12, wall ≤+10 %.
- This is the service line's **final 12-run cohort**: PM still 0/6 with a clean manipulation check closes it and moves to the copper/AC bottleneck. No push.

## [2026-11-06] C72 A/A: one C6 pass, no signal; C73 running; C74 cable baseline queued
- C72 (both `8b0b5b2`, C0, 2x, seeds 82450-82455): 12/12 completed, no infra. Strict C2/C3/C4/C5 service_a 6/6/5/5, service_b 5/5/5/5; **C6 1/6 in service_b only**, and that pass is "both" (chest + production) under c6-v1, so it does not discriminate. Median 3290 vs 4057 s (+23 %), no milestone gain.
- **C73 running** (permanent-consumer admission, `210778a` vs `db70250`, seeds 82460-82465): the service line's final 12-run cohort.
- **C74 queued** (both `db70250`, seeds 82470-82475): copper/AC bottleneck A/A baseline for the next single factor.
- Reviews running on C72 and the next cable/AC factor. No push.

## [2026-11-06] Correction: C72's single C6 pass is the first c6-v2 production-consumer pass
- Job `C0-service_b-s82453` records **`predicate_version: c6-v2`**, both checks true, `consumer_deltas: {'electric-furnace': 2034}`, and all eight chest types excluded — so the pass is a **production consumer**, not a chest. The earlier "both under c6-v1" read was wrong.
- This is the **first c6-v2 positive** after C51 0/150, C55 1/150, C57 0/139, C58 0/150: the base can reach a production AC consumer via an **electric-furnace cell**, without the PM service. C72 is therefore not a pure null (1/12 c6-v2, one arm).
- C73 (permanent-consumer PM) is still running; the electric-furnace path is now the strongest c6-v2 lead. The C72 review packets used the wrong framing and their verdicts should be weighted with this correction.
- No push.

## [2026-11-06] C72 Codex review: no service effect; next is cable capacity after C74
- Codex: C72 gives no service-effect evidence (A/A; the sole C6 pass is service_b seed 82453, one discordant pair, exact p=1). The pass is **c6-v2** with AC delta 463 and **electric-furnace consumer delta 2003**, not PM output — and the observer sums lifetime `products_finished` for machines currently assigned the recipe, so reassignment can inflate the delta; treat 2003 as an endpoint, not proof of 2003 furnaces.
- Close the service line if C73 PM is 0/6 with dispatch/loan/cap/funding/build verified. That closes the intervention under this budget, not PM feasibility generally.
- **Next after C74:** 6 pairs testing one additional permanent **copper-cable capacity cell** through the existing builder; primary = attributable AC output increase over the final 7,200-game-tick window; manipulation check = built cable capacity and output. Reconsider AC feed only if C74 shows cable producing adequately but failing delivery.
- C73 running; C74 queued. No push.

## [2026-11-06] C72 pass is an artifact (0/12 valid); C73 not reached; service line closed; C74b cable baseline running
- DeepSeek: the single c6-v2 pass reassigned a mall cell to `electric-furnace` and `find_line` summed the entity's **lifetime** `products_finished` (2,003); **2,003 × 5 AC = 10,015** against 463 AC produced — impossible. C72 is **0/12 valid c6-v2**, not 1/12. Final held C5 is 1/6 vs 0/6 (the 5/6 figures were monotonic-ever), and the pass had C5 false — C6 without C5 is not a milestone. The +23 % median is A/A concurrency.
- **C73 closed**: the permanent arm stalled at C0 (151/237 decisions) while reference reached C5; the PM option appeared **0 times** vs 210 in reference — PM was not reached, so this is not an admission test. The `permanent_consumer` change carries an early-stall regression.
- **Service line closed** under this budget (not PM feasibility generally).
- **C74b running:** clean A/A cable baseline on `8b0b5b2` (seeds 82470-82475, C0, 2x); C74 was cancelled and rebased.
- **Next C75:** +1 permanent copper-cable capacity cell; primary attributable AC output over the final 7,200 ticks; manipulation = cable capacity/output up, AC-cell starvation down, zero cap/loan diversion.
- No push.

## [2026-11-06] C74b: 62 % same-code variance; C75 cable-capacity cohort running
- C74b (both `8b0b5b2`, C0, 2x, seeds 82470-82475): 12/12 completed, no infra, speed 2.0. Strict C2/C3/C4/C5 **3/3/3/3 vs 5/4/4/4** between identical arms; C6 0/6; median wall **2362 vs 3816 s (+62 %)**. This is the strongest same-code variance example: **12-job smokes cannot detect effects below ~15-20 pp**, and the earlier 20-35 % wall penalties are within run-to-run noise, not arm costs.
- T3 delegation tools were unavailable in the current catalog (only four device tools exposed), so I applied the fallback and reviewed C74b directly from the evidence.
- **C75 running:** `laya-c75-smoke-c0-cable-2x-20261106`, C0, 2x, 6 pairs seeds 82480-82485, `2f2d6a0` (switch off) vs `8ed46eb` (one permanent copper-cable capacity option when the C6/S1 AC need is input-starved). Primary: attributable AC output over the final 7,200 game ticks, paired; manipulation: cable cell built/working, capacity/output up, AC-cell starvation down, zero cap/loan diversions.
- 6 focused tests; fast gate only the known firewall failure. No push.

## [2026-11-06] C75 cable arm was a tool_error wiring failure; C76 rebuilt on the fixed operation
- C75 (reference `2f2d6a0` vs cable `8ed46eb`, 6 pairs seeds 82480-82485): 12/12 completed, no infra; strict C3/C4/C5 4/4 both, C6 0/6, +9 % wall (inside the 62 % same-arm spread). The **manipulation failed**: the cable option was offered 63 times, selected 9 times, and **all 9 were `tool_error`** — `TypeError: lambda() got an unexpected keyword argument 'upgrade_bootstrap'`, because the arm was based on `8b0b5b2` (pre-scope lambda).
- Rebuilt on `8499b9d`'s scope-aware operation: `736914c` (option, switch off) and `29f91d8` (switch on).
- **C76 running:** `laya-c76-smoke-c0-cable2-2x-20261106`, C0, 2x, 6 pairs seeds 82490-82495, `736914c` vs `29f91d8`; primary attributable AC output over the final 7,200 ticks; manipulation = zero tool_error, cable cell built/output up, AC-cell starvation down.
- 6 focused tests; fast gate only the known firewall failure. No push.

## [2026-11-07] Multi-arm restart cohorts; starved-requester telemetry
- What: `tools/laya_regression_scheduler.py` accepts 2-6 measurement arms (control + one-factor variants on shared seeds; mixed-start A/A stays 2). Loan-cell telemetry now names the ingredient the requester itself lacks ("starved") and probes its count in the requester's network ("reachable").
- Evidence: commit b7f741f; scheduler tests 13/13, fast gate 1183/1184 (known firewall failure from other uncommitted work). C58-C77: AC requester short of plastic-bar in 1,556/1,978 lines (79%) with >=50 base-wide; the old line read `missing -` and never probed.
- Lifecycle: scheduler change applies to new enqueues (C78/C79 unaffected); telemetry reaches cohorts only when cherry-picked onto arm bases. Owner (94dcfaf2) told to freeze a multi-arm C80; controller (30223f04) given an idle alert. No push.
