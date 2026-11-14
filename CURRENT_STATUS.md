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

- 2026-10-13 10:02 IST — Fixed deterministic construction reservation leaks exposed by `solo-20261013T034945Z`: repeated transient submissions renew their bill; funding subtracts other unbuilt ghosts while excluding exact retries; additive ghosts cannot consume foreign reserved stock. Evidence: 122 focused tests passed, including native Lua and pre-fix failures; fast gate 952 passed/3 skipped/1 pre-existing import-firewall failure reproduced from HEAD. Lifecycle: Python controller restart/new frozen validation episode required; no mod deployment or Factorio restart, active campaigns untouched; no live rerun yet.

## [2026-10-15 13:47 IST] T3 Code harness delegation instructions
- What: AGENTS.md routes delegation through the active harness; T3 Code inherits model/provider defaults without model or effort overrides, and falls back to direct evidence reading when delegation is unsupported.
- Evidence: reviewed instruction diff and git diff --check pass. Required fast gate stopped with 285 collection errors, including duplicate saved-run test modules under data/; no subagent launch was exercised. Log: /tmp/factorio-t3-agents-fast-20261015.log.
- Lifecycle: instructions only; no runner, mod or Factorio restart/deployment required. New sessions load the updated charter.


## [2026-10-15 14:19 IST] Explicit T3 delegation tool contract
- What: AGENTS.md names T3 MCP delegate_task/task_status, requires discovering both before delegation, prohibits native collaboration tools in T3 and falls back to direct reading when tools are missing.
- Evidence: reviewed documentation diff and whitespace checks pass. Isolated managed Codex 0.162.0 requests confirmed the upstream two flags retain native tools while adding agents.enabled=false removes them; normal inference completed in both arms. Required fast gate again stopped with 285 collection errors, including saved-run import collisions under data/; log: /tmp/factorio-t3-explicit-tools-fast-20261015.log. T3-owned delegation remains untested because its tools are unavailable here.
- Lifecycle: instructions only; new sessions load the charter. No T3 runtime patch, runner restart, mod deployment or Factorio restart performed or required for this documentation change.

## [2026-10-21 01:29 IST] T3 tool-call audit and scoped test discovery
- What: Read-only audit of 5,849 stored Factorio tool calls on Starship and Miniship. Starship pytest now defaults to `tests/`, avoiding frozen experiment copies; both hosts document bounded supervision, retained test exit codes, and result reuse.
- Evidence: Default fast collection 4.46 s, identical 959 selected cases to explicit `tests/`; fast gate reports the same pre-existing import-firewall failure before and after. Historical five longest supervision waits total 7.78 h; this is occupied tool time, not campaign compute saved.
- Lifecycle: Effective for future test commands and agent reads; no campaign, server, scheduler or frozen code changed.

## [2026-10-21 02:37 IST] Faster scaffolding contract without reducing full-bundle coverage
- What: Fast scaffolding test uses the real composer with a four-roboport backbone; the same payload assertions still run against the complete electronics bundle in the slow composition family. Production code and the 959 selected fast test IDs are unchanged.
- Evidence: Isolated check 7.84 s -> 0.30 s (setup 7.54 s -> 0.02 s); 24 infrastructure/taxonomy cases pass including full composition. Fast gate 15.38 s with only the previously established import-firewall failure.
- Lifecycle: Future test runs only; no deployment, server restart or active campaign changes.

## [2026-10-21 16:52 IST] Audited host scheduler and gated batch admissions
- What: Fixed trial safety-cap bypass and premature throughput comparison; integrated locked, fail-closed host-gate admissions into the fixed batch pool. Defaults follow start24/performance30/balanced26/cool20. Added service template and `docs/rl/Laya_Host_Scheduler.md` with timed drain controls, mixed-legacy limits, and fresh-cohort migration. Python niceness now applies to the runner without also lowering the engine during preparation.
- Evidence: Exact staged snapshot: 85 focused tests pass, including competing cohort reservations, real batch-loop pause, synthetic expiry-to-admission, and thermal/memory/liveness caps during trials. Fast gate: 976 passed, 1 skipped, 1 existing import-firewall failure in unchanged `training/laya_lines.py` / `training/laya_ops.py`. Read-only Miniship audit saw target24, actual12, 244 queued; no live adoption or campaign-throughput improvement claimed.
- Lifecycle: Deploy/restart the installed scheduler explicitly, then freeze future cohorts with the new batch harness. Do not set `adaptive:false` on the old scaler-dependent harness or overwrite existing cohort status. Active frozen cohorts and the installed scheduler were not changed by this audit/fix session; the controller owns any authorized migration.

## [2026-10-25 22:35 IST] Cross-host cleanup completed; retention published
- What: Starship data 401.34 → 10.41 GB, Miniship 1,299.87 → 46.02 GB; worktrees 91 → 1 and 235 → 15. Retired 20 GitHub branches with pushed recovery tags and archived 49 Starship threads; removed review-fixes.
- Evidence: both full corpus copies verified (283,386 indexed files, zero mismatches); all 91 Miniship archives hash-checked and retired after corrected exemplar restoration (70 cohorts, 21 no-start). Retention 764ed6b passed the 1,366-case fast gate (one skip) and independent review. Details: docs/rl/Laya_Cleanup_2026-10-25.md.
- Lifecycle: laya/measurement updated on both hosts and origin for future freezes; no deploy/restart. C35 naturally completed 120/120; C36 and scheduler continue. Historical full-world limits are recorded in the report.

## [2026-10-26 02:24 IST] Cleanup dependency recovery
- What: Restored Miniship C24's reusable source save after cleanup broke a later freeze; recorded the controller's separate C32 analysis-tool recovery and the consumer checks required before future cohort retirement.
- Evidence: source matches C24's recorded SHA-256; both restored analysis modules match ab7e8d30; C36/C37 each passed 1,145 recorded hash checks. See docs/rl/Laya_Cleanup_2026-10-25.md and data/cleanup-20261025/dependency-recovery-20261026.json.
- Lifecycle: none; active freezes and controller ownership unchanged. Historical retired full worlds remain unavailable.

## [2026-10-26 03:21 IST] Development cache cleanup
- What: Pruned 41 old Starship package archives, two old Factorio downloads across hosts, and unused Miniship uv cache entries; retained installed/recent packages, source mirrors and live runtime cache paths.
- Evidence: available filesystem space increased 26.30 GB on Starship and 8.11 GB on Miniship. Both Python import checks passed; C36/C37 each passed 1,145 frozen-file hashes. Records: data/cleanup-20261025/cache-cleanup-20261026/.
- Lifecycle: none; packages, active cohorts and scheduler were not installed, edited or restarted.

## [2026-11-12 07:00 IST] Track B 2x2 A/B cohorts frozen and queued
- What: Replaced the unfair B-world-only paired cohort with two frozen cells that hold the mod pack constant (B's staged pack) and vary runner x start save: `laya-trackb-ab-bworld-2x-20261112` (B save) and `laya-trackb-ab-aworld-2x-20261112` (A save), 40 matched seeds 84100-84139 each, arms reference 369b7d6 + `run_laya_mixed.py` and b 5f7e8e48 + `run_trackb_arm.py`/patience48. Old spec `laya-trackb-ab-2x-20261112` marked superseded before start.
- Evidence: both freezes `ready`, 80 jobs each, shared predicate `0277ecc2`, self-consistent spec hashes; units started and waiting on C94 (0 started). Wiring smokes: B on B latched strict C4 at tick 572,648 and held C5; A stalls before C2 on B's world (78 decisions, 23 power_route_unavailable) and also stalls at C2 on A's save with B's mods (58 decisions); B cannot use A's mod pack (layout-receipt timeout) and runs fine on A's save with B's mods (proxy C4).
- Lifecycle: cohort units active and gated on C94; smokes end by themselves. No Track A unit, config or code touched; nothing pushed.

## [2026-11-12 08:30 IST] Track B headline cell frozen; constant-mods 2x2 withdrawn
- What: Parent decision applied. Both constant-mod cells (`laya-trackb-ab-bworld/aworld-2x-20261112`) are `superseded_before_start` with 160 jobs cancelled and units stopped (each track needs its own mod pack). New headline B side `laya-trackb-b-aworld-ownmods-2x-20261112`: A save 347eba65 + B mods, seeds 84000-84059, two identical arms at 5f7e8e48 (scheduler needs >=2; `b_rep` gives B A/A noise), 120 jobs, caps 3,600,000/36,000, predicate 0277ecc2, owner AB, unit live.
- Evidence: freezes `ready` and self-consistent; B-on-A wiring 91 decisions/0 failures/stop stalled (proxy C0-C3); reserve-variant smoke on seed 0 indistinguishable through strict C5 (C4 565,394 vs 565,547; rule unexercised). Host admission: 0 slots at 08:25 (gate 13-14 held by C94 3-4 and C95b 10); `_dispatch` only publishes on transitions so a queued cohort goes stale past 900 s - worked around with `laya-trackb-b-aworld-keepalive`.
- Lifecycle: headline unit waiting for a host slot; keepalive unit running; smokes stopped. Parent owns the host-target and mod-merge decisions.

## [2026-11-12 09:55 IST] Harness heartbeat/worker patch, headline refreeze, merge boundary
- What: Prepared the shared-harness fix on `trackb/harness-heartbeat-20261112` (`4c6f6792` publish throttling for waiting dispatches, `e4c4bb61` optional `max_workers` spec field), delta patch at `docs/handoffs/2026-11-12-dispatch-heartbeat-delta.patch`, applies cleanly on top of `a2ff7841`. Refroze the headline B side as `laya-trackb-b-aworld-ownmods-2x-20261112b` with `max_workers: 8` (120 jobs, 60 seeds, 2 identical arms at 5f7e8e48, A save 347eba65 + B mods 507f0718); the un-suffixed name is superseded with 120 jobs cancelled and none started.
- Evidence: 14/14 scheduler tests. Mod merge closed the receipt protocol (v2 stream + journals), the two missing actions and the requester-chest build-evidence record: B on A save + merged pack ran 59-107 decisions with 0 receipt/evidence failures (was 3 receipt timeouts in 2 decisions), but stopped at proxy C2 with 3-4 `atomic_footprint_blocked` plans traced to trees at a site the plan did not mark for clearing; B's own executor rejects the same plan, so the remaining difference is site choice from the nine divergent shared files the approved scope excluded. A on the merged pack: 201 decisions, 0 failures, window stop.
- Lifecycle: deploy the harness delta at C94's terminal (verify root scheduler equals a2ff7841, apply, run tests, message Controller A), then start the refrozen headline cohort; `laya-modmerge-aa-2x-20261112` stays frozen and unstarted. No installed mod, running cohort or Track A unit touched.

## [2026-11-12 10:45 IST] Merged pack reaches B-parity on A's save
- What: Found the blocked-site cause (`clear_natural_obstacles` always set by B, ignored by A) and ported B's opt-in obstacle clearing into the staged merged pack only, gated on the plan flag; A's own plans never set it, so A's executor path is unchanged. Merged pack at `data/laya-modmerge-20261112/mods`, built by `patch_mod.py`.
- Evidence: B on A save + merged pack: 103 decisions, 0 failures, proxy C0-C3, stop stalled — same phase reach and failure count as B's own pack on that save (91/0/C0-C3). Zero receipt, evidence or plan failures (before the port: 3 receipt timeouts in 2 decisions, then 3-4 blocked plans). A on the merged pack: 201 decisions, 0 failures.
- Lifecycle: `laya-modmerge-aa-2x-20261112` stays frozen and unstarted until the headline B cohort finishes; harness delta deploys at C94 terminal. No installed mod, running cohort or Track A unit touched.

## [2026-11-12 11:20 IST] Merge closed; stone-furnace arm blocked by the electric-only invariant
- What: Closed the mod-pack merge (two packs kept), superseded `laya-modmerge-aa-2x-20261112` (60 jobs cancelled, none started), recorded the outcome and evidence in docs/handoffs/2026-11-12-modmerge-outcome.md. Corrected the B-side tree finding: B always sets `clear_natural_obstacles`; the blocks were A's executor ignoring it. Prototyped then reverted a stone-furnace direct starter because the repo-wide electric-only invariant (`planners/recipe_data.py:374`, `_reject_fuel_entities`) forbids stone/steel furnaces.
- Evidence: B on A save + merged pack 103 decisions/0 failures/C0-C3 (parity with B's own pack 91/0/C0-C3); B home runs 0 failures and no obstacle/tree refusals; the prototype passed geometric validation but was rejected by `validate_build_plan` with "Electric-only invariant violated by: stone-furnace".
- Lifecycle: merged pack stays staged and unused; `laya-trackb-b-aworld-ownmods-2x-20261112b` (max_workers 8) waits for C94 terminal and the harness delta; the stone-furnace arm needs a product decision to revise the invariant, or the electric-furnace-supply alternative.

## [2026-11-12 11:20 IST] Phase audit corrected; merged-pack parity recorded as smoke-level only
- What: Re-audited every phase claim against `phases_reached` (proxy) and the strict sidecar. The journal `phase` field is the current goal, not a reached phase: b-on-a2 is C0-C3 (not C4), a-on-b and a-on-a-modb are C0-C1 (not C2), b-merged8 is C0-C3. Corrected table recorded in the handoff. Merged-pack result on A's save is smoke-level parity, not acceptance; (b1) strict C4 not met on A's world.
- Cohorts frozen: `laya-trackb-b-aown-2x-20261112` (control, B own pack on A save) and `laya-trackb-b-amerge-2x-20261112` (merged pack, same save/seeds) for (b2), 15 seeds x 2 arms each; `laya-trackb-b-home-2x-20261112` (B runner + B pack + B save, 60 seeds x 2 arms) for the three-number read. `laya-modmerge-aa2-2x-20261112` remains the (a) cell. All unstarted, gated behind the headline; earlier B-save b2 cells superseded before start.
- Lifecycle: headline `laya-trackb-b-aworld-ownmods-2x-20261112b` (max_workers 8) waits on C94 terminal + harness delta; acceptance cells start after the headline (B-home only if host idle). No B units running.

## [2026-11-12 11:30 IST] Merge open pending (a)/(b2); stone-furnace parked
- What: Amended the merge handoff: status "open, pending (a)/(b2)", the "candidate facts from shared files" cause withdrawn (real cause: A's executor ignored B's always-set `clear_natural_obstacles`). Acceptance artifacts frozen and unstarted: `laya-modmerge-aa2` (a), `laya-trackb-b-aown`/`laya-trackb-b-amerge` (b2), `laya-trackb-b-home` (B home). Stone-furnace arm parked on the electric-only invariant; parent taking it to the user; recorded that Starship's furnace-reserve rule was 3/5 vs 3/5, so the fallback needs electric-furnace allocation/priority, not the reserve weight.
- Evidence: merged-pack B run 103 decisions/0 failures/C0-C3 (own pack 91/0/C0-C3); A merged smoke 201/0/C0-C1; four cohorts frozen with predicate 0277ecc2.
- Lifecycle: headline `laya-trackb-b-aworld-ownmods-2x-20261112b` waits on C94 terminal + harness delta; acceptance cells start after the headline; no B units running.

## [2026-11-12 11:40 IST] b1 evidence moved to B's home save
- What: Froze `laya-trackb-b-home-merged-2x-20261112b` (B runner + merged pack + B save, seeds 84000-84014, 2 arms, 30 runs, max_workers 6, ports 76600/77600) as the (b1) strict-C4-latch cell, paired by seed with `laya-trackb-b-home-2x-20261112` (own pack, same seeds). Removed the "(b1) latch evidence" claim from the A-save merged cell `laya-trackb-b-amerge`. (b1) is met if strict C4 latches on the merged pack at a rate not clearly below the own-pack cell. The un-suffixed home-merged registry used ports that collide with amerge and is superseded.
- Evidence: freeze ready, 30 jobs, mods 59cc71f0, predicate 0277ecc2, seeds 84000-84014, max_workers 6. B on A's save cannot supply (b1): both packs reach only proxy C0-C3 there.
- Lifecycle: all acceptance cells frozen and unstarted; the hourly ladder starts aa2/aown/amerge after the headline, then home/home-merged, then reports. C94 at 79/100.

## [2026-11-12 13:10 IST] Stone-brick small line (electric-only rule kept)
- What: Implemented the user's rule on branch `trackb/small-stone-line-20261112` (9e8aab30, off 5f7e8e48): `small_line_candidates` offers a stone-brick line of k direct starters (k = electric furnaces on hand, 2..6, one furnace each) with a `small_line` fact; the runner offers it whenever stone-brick is in the plate set; variant `patience48-small-stone-line` prefers it. The six-furnace minimum comes from `training/laya_lines.py:37` (`LINE_ROWS=(1,3)`, `FURNACES_PER_ROW=6`) composing the six-furnace Start+End basic module.
- Evidence: new test (`tests/test_laya_small_stone_line.py`) covers 3-furnace sizing, plan validation, no stone/steel furnaces, and the negative cases; fast gate 1089 pass. Wiring smoke loaded the variant but the option was not exercised before the gate changed from `unmet` to the plate set; smoke stopped to keep the host free for the headline.
- Lifecycle: no frozen cell or ladder task touched; candidate arm for the next B cohort once the headline and acceptance cells finish. Rerun the wiring smoke on spare ports after the first headline admission.

## [2026-11-12 14:20 IST] Harness delta live; headline started but host-held
- What: C94 finished 100/100. Verified the root scheduler equalled a2ff7841, applied the heartbeat/max_workers delta, 14/14 scheduler tests, messaged Controller A. Started `laya-trackb-b-aworld-ownmods-2x-20261112b` (max_workers 8) and its keepalive.
- Evidence: no admission in 18 min. Host at `hold`, target 13-15, 90-91 °C, active 14; C95b occupies all slots (14 running, 52 queued, 17.2 MB status rewritten per waiting dispatch on its pre-patch coordinator); `may_admit` false because occupied >= cap.
- Lifecycle: headline unit stays live and admits the first free slot; acceptance cells wait for the headline per the ladder. Host target and C95b are not mine to change.

## [2026-11-12 19:25 IST] Port-base bug fixed by refreezing; headline admitted
- What: All six frozen Track B cohorts had port bases above 65535, so ports_free raised OverflowError at admission and nothing could start. Superseded them and refroze with valid bases: headline `...b-p2` 63000/62000, `aa2-p2` 63200/62200, `aown-p2` 63300/62300, `amerge-p2` 63400/62400, `home-p2` 63800/62800, `home-merged...b-p2` 64100/63100. Restarted the headline and its keepalive.
- Evidence: first job `C0-b-s84000` started 19:24:35 on game 63000 / rcon 62000 with a bootstrapped server; the unit survives and publishes normally (heartbeat patch), which it could not do before. Freezer validation patch ready on branch `trackb/harness-heartbeat-20261112` (`e49c37cc`, delta `docs/handoffs/2026-11-12-port-validation-delta.patch`, 15/15 tests) but not applied because the root scheduler now has Controller A's per-arm research_target change; A asked to ack.
- Lifecycle: headline running; acceptance cells frozen with valid ports and gated behind it via the hourly ladder.

## [2026-11-13 07:20 IST] Headline complete; acceptance batch launched
- What: Headline `...b-p2` finished 120/120 completed with all cleanup; the drain-safe adaptive handoff ran the tail without a restart. Launched `aa2-p2` (60), `aown-p2` (30) and `amerge-p2` (30) with the isolated coordinator and `--adaptive-workers`, no keepalive.
- Evidence: all port ranges end <= 65535 and same-protocol bind checks were free; aown admitted 2 running + 1 preparing at launch, the others queue for host capacity. C95b 120/120 complete; C96d cancelled (12/120).
- Lifecycle: ladder continues to the b1 pair after these three; corrected reporter for the reads.

## [2026-11-13 07:40 IST] Headline cohort review: B-away C4 32/60 and 26/60; next is B-home
- What: Reviewed `laya-trackb-b-aworld-ownmods-2x-20261112b-p2` (120/120). Strict C4 32/60 (b) and 26/60 (b_rep); paired p=0.238, inside the A/A spread; exposure dominates (above-median 28/30 vs 22/30, below 4/30 vs 4/30). C5 reached by the same runs as C4 and held 0; C2 0 (A-shaped), C3 null/unknown 60; C6-C9 0 with no recipe evidence. All stalled; classification unmapped (91 unknown/29 genuine_failure). 91 legacy tool_error counters are unconfirmed; 3 real SavedWorldError `item_shortfall:transport-belt` on s84047/s84052/s84055 (arm b).
- Evidence: corrected reporter on the registry; A home (C95b) C4 40/60 and 44/60 with one recipe-evidenced C6. Not like-for-like: B is away from home.
- Lifecycle: queue entry set complete; next cohort is the frozen B-home pair (`home-p2` 120 jobs, `home-merged...b-p2` 30) after the acceptance triple; then a B-side improvement arm targeting run length and the belt-abort. No adoption claim.

## [2026-11-13 09:30 IST] b2 control complete; merged-pack treatment fails on plan legality
- What: `aown-p2` (b2 control) finished 30/30: C4 8/15 (b) and 7/15 (b_rep), p=1.000, exposure-dominated; C3 null, C5 transient, C6-C9 0; one SavedWorldError. `amerge-p2` interim 28/30: C4 0/28, 18 SavedWorldError (9 per arm) vs 1 in the control, 16 of them `atomic_footprint_blocked`; blocked footprints are water/deepwater/occupied sites, not trees (obstacle port present).
- Evidence: merged-pack runs stall earlier (median ~150 decisions vs 213/159), so C4 is suppressed by illegal plans/exposure, not gate quality. (b2) treatment fails; keep two packs. `aa2-p2` (a) still running but moot for adoption.
- Lifecycle: next is the frozen B-home pair (`home-p2` 120, `home-merged...b-p2` 30) after `amerge-p2`/`aa2-p2` terminal; then a B-side improvement arm on B's own pack targeting run length and plan-legality aborts.

## [2026-11-13 11:00 IST] amerge-p2 invalid treatment: merged pack fails on water/offshore-pump observation
- What: Reviewed `amerge-p2` (30/30): strict C4 0/30 in both arms, 19/30 SavedWorldError (9 b / 10 b_rep, 17 atomic_footprint_blocked), median decisions ~151-154 vs 213/159 in the aown control. First traced failure (seed 84001 arm b): oil water-source pipe at (-97.5,14.5) atomic_footprint_blocked; the same seed completes on B's own pack. Pack-level fault, not an arm effect.
- Evidence: site plans identical between aown and amerge for seed 84000, so the divergence is in execution/observation of water and occupied sites, not in zone selection; the merged pack misses water/offshore-pump facts B's own pack has.
- Decision: (b2) treatment invalid; DiD not interpretable; merged pack not adopted and held; next cohort is `home-p2` (B home, own pack) once `aa2-p2` is terminal; `home-merged...b-p2` held until the pack is fixed.

## [2026-11-13 22:15 IST] (a) passes, (b2) invalid; B-home cohort started
- What: `aa2-p2` (A on the merged pack) finished 60/60 with zero material faults: C2 28/30 both arms, C3 26/25, C4 23/30 and 24/30, C5 23/2 and 24/3, C6-C9 0; A/A C4 6W/5L p=1.000. Paired vs C95b on the shared 30 seeds: C4 13W/9L pooled p=0.523 (no degradation) — (a) passes. Cross-track: the same pack fails only under B's runner (19/30 SavedWorldError, C4 0/15), so it is a runner/pack interaction, not a broken pack; (b2) stays invalid and the merged pack is not adopted.
- Evidence: A runs ~3x longer on the same pack (median 444/422 decisions vs B's ~152); first B failure traced to the oil water-source pipe at (-97.5,14.5), seed 84001 — B's planner targets water/occupied sites A's does not.
- Lifecycle: host drained; started `laya-trackb-b-home-2x-20261112-p2` (B home, own pack, 120 jobs) with the isolated coordinator and --adaptive-workers; `home-merged...b-p2` held. Next: world-fit read (A home, B away, B home).

## [2026-11-14 22:20 IST] B-home review: ~50% C4 home and away vs A ~70%; run-length arm queued
- What: `home-p2` finished 120/120: C4 28/60 (b) and 35/60 (b_rep), paired 13W/6L p=0.167 — the largest same-code A/A spread so far. Three SavedWorldError all in b_rep (84024/84035/84056), runs completed. Pooled B home 63/120 (52.5%) vs B away 58/120 (48.3%): world fit is not a measurable penalty; the gap to A (40/60 and 44/60) is the runner/strategy.
- Evidence: C4 tracks run length (above 453 decisions 20/30 vs 25/30, below 8/30 vs 10/30); B stalls at ~226 decisions vs A ~444. C5 transient, C6-C9 0, all stalled, no window ends.
- Lifecycle: froze and started `laya-trackb-b-home-patience96-2x-20261114` (patience48 control vs patience96 treatment, same code bea66974, 30 seeds x 2 arms, ports 64200/63200, --adaptive-workers) on the drained host. `home-merged...b-p2` held; merged pack not adopted.

## [2026-11-14 23:10 IST] patience96 null; stone reach is the terminal class; stone-first arm started
- What: patience96 (60/60) is a clean null: C4 17/30 control vs 16/30 treatment (p=1.000) despite +47% decisions and +38% wall — run length is not the C4 lever. Terminal bottleneck of the 27 non-C4 runs: stone-brick 16 (all stone:short with coverage/roboport blocks), plastic-bar unbuilt 8, copper under_capacity 3. The small stone-line arm (furnace count) is not this lever and stays gated.
- Evidence: patience cohort registry; last BOTTLENECK/BLOCKED lines from each non-C4 runner log; exposure split above 574.5 decisions 12/15 vs 11/15.
- Lifecycle: froze and started `laya-trackb-b-home-stonefirst-2x-20261114` (patience48 control vs stone-first weight 4.0, same code aaaa699e, 30 seeds 84400-84429, ports 64400/63400, --adaptive-workers) on the drained host. Falsifier: if the stone terminal count does not fall, the next step is a stone mine/coverage siting operation. `home-merged...b-p2` held; merged pack unadopted.

## [2026-11-15 09:40 IST] Stone-first is the best B arm (+17 pp C4); replication started
- What: `laya-trackb-b-home-stonefirst-2x-20261114` finished 60/60: C4 16/30 (patience48) vs 21/30 (stone_first), +17 pp, paired 7W/2L p=0.180, medians 228.5 vs 238 decisions (matched exposure), gain in both halves. Mechanism matches the traced stone:short terminal bottleneck. Four SavedWorldError (3/1), all completed. C6-C9 0.
- Evidence: registry per-arm counts and the corrected paired read; marked the queue entry complete (it still said running).
- Lifecycle: froze and started the replication `laya-trackb-b-home-stonefirst-rep-2x-20261115` (fresh seeds 84600-84629, same code aaaa699e, ports 64800/63800, --adaptive-workers). Adoption only if it reproduces; otherwise next step is a stone mine/coverage siting operation. `home-merged...b-p2` held.


## [2026-11-15 02:53 IST] Complete typed recipe survey and executable product contract
- What: derive supported solid operations from the full force survey, preserve recipe/product identity through quad slots and gates, and distinguish disabled, unsupported and unknown recipe evidence. Keep the existing furnace and fluid operation paths.
- Evidence: 163 focused behavioral checks passed. Final fast gate exit 0: 1,098 passed and one selected skip; five additional modules skipped at collection because optional lupa is absent. The critical recipe Lua tests ran through the available Lua interpreter. Captured recipe fields plus explicit refreshed normal-quality fixture inputs reach candidate material checks; missing historical quality facts remain unknown. No live result established.
- Lifecycle: freeze/restart affected runners with the Python, query-sent Lua helper and schema together after active campaigns; no installed-mod deployment or Factorio reload required. No runtime action performed.

## [2026-11-15 03:51 IST] Shared receipt reconciliation and explicit denominators

- What: validate contradictory envelopes; reconcile only actual invocation/transaction identities; retain initial and final receipt states, unknown and malformed slots, and duplicate/conflict counts. Runner summaries and collector use one function; controller publications count every immutable planned job and separate inherited/current milestone evidence from earned targets.
- Evidence: 51 focused passes; settled fast1107 passed,6 skipped(five optional-lupa collection modules,one fluid-chain case),exit0. Track B caller handshake/stream acceptance and retained stream replay remain open.
- Lifecycle: future matching frozen runner/controller copies required; no runtime, deployment, service, save or campaign action performed.


## [2026-11-15 04:09 IST] Preserve explicit upstream-construction deferrals

- What:shared normalizer classifies production_prerequisite_deferred as deferred,retaining cause and exception precedence;unknown codes stay unknown.
- Evidence:9 focused outcome/reconciliation checks passed;settled TrackB fast1,119passed,6skipped(five optional-Lupa collections,one known fluid-chain case),exit0. This combined gate includes the separately uncommitted handshake patch;its refusal-recovery acceptance remains open.
- Lifecycle:future matching frozen Python runner/controller copies required;no runtime action.
