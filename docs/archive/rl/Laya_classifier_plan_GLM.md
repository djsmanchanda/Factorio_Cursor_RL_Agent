<!-- docs/archive/rl/Laya_classifier_plan_GLM.md | Purpose: Design for a fine-tuned Laya decision layer on top of the deterministic Factorio tooling, reusing RL training infrastructure and historical run logs. Design-only; not yet authoritative. -->

# Laya_classifier_plan_GLM — Laya Decision Layer Design (exploration result, not yet implemented)

Status: **design proposal**. Nothing here changes authority yet; `docs/rl/README.md`, `docs/rl/training.md`, and `docs/deterministic/*` remain the operating docs until this design is adopted and they are updated.

This document is grounded in the repository as it exists (2026-09-19) and in the Laya model card (`convaiinnovations/laya`), the Laya GitHub repo (`NandhaKishorM/laya`), and its fine-tuning notebook (`notebooks/laya_finetune_typed_decisions_2xT4_kaggle.ipynb`).

---

## 0. Executive summary

- The repository already has the exact seam Laya needs: a **select-one-of-N-validated-candidates** decision boundary (`training/episode.py:144` → `policy.select(candidates, observation, seed)`), fully decomposed rewards (`training/rewards.py:96`), candidate catalogs with numeric features (`training/candidates/mining_delivery.py:409`), a durable SQLite transition store (`training/store.py:16`), and promotion/holdout machinery (`training/evaluation.py:92`).
- Laya (ModernBERT-large, 421M, non-autoregressive, ~33 ms/forward pass, typed `choice`/`score`/`noul` questions over a JSON state) fits this seam almost exactly: it never generates text, its answer space is defined at request time (dynamic option lists need no retraining), and it outputs calibrated probabilities that can drive confidence-gated fallback to the deterministic baseline.
- Hard limits that shape the design: **512-token state budget** on the English checkpoint (the fine-tuning notebook trains at `max_len=1024`, `head_max_len=256`), **choice questions must stay under ~20 options**, `score` is the weakest primitive (favor `choice` + `noul`), shipped temperatures are overconfident (must refit on our data), and it is **near-chance zero-shot** — fine-tuning is mandatory, not optional.
- Historical data is plentiful and already structured: ~7,000+ RL transitions across ~30 run directories (several `experience.db` SQLite files, biggest 58 MB / ~4,800 episodes), plus the entire deterministic-run evidence trail (`deterministic-events.jsonl`, `deterministic-mission-state.json`, `deterministic-blockers.jsonl`, `last-10-runs.md`, campaign decision logs). All logs are currently unclassified and end in failure of some kind — which is itself label material (typed blockers → `noul` health/recovery targets; hindsight relabeling).
- Recommended architecture: **hierarchical question packs, not one giant decision**. Each decision point in the existing controllers maps to a small set of typed questions whose options are generated dynamically from existing tooling (priority list, action catalog, candidate generators, RCON tool registry). Hard safety/legality/budget/authorization stays deterministic; Laya only ranks, gates, and classifies.
- Staged path: logging → offline dataset → supervised distillation → temperature calibration → shadow mode in both training and deterministic runs → RLCD on-policy in disposable episodes → bounded promotion via the existing evaluation/promotion gates.

---

## 1. How the current system actually works

### 1.1 Two systems, two control planes

| | Deterministic runtime | RL training |
|---|---|---|
| Runtime | Nauvis/player, real save (`docs/deterministic/README.md`) | Disposable worker surfaces (`factorio_training_lab/`), loopback ports 35000+i / 28000+i |
| Controller | `tools/autonomous_run.py` → `tools/autonomous_builder.py` → `stage_*` → planners → `factorio_mod/` | `tools/run_training_batch.py`, `tools/run_adaptive_training_batch.py`, `tools/run_ab_training_batch.py` → `training/episode.py` → `training/factorio_bridge.py` |
| Decision style | Hand-authored branch code + priority queue + pure rankers | Select-one-of-N candidates via `DiagonalLinUCB` contextual bandit (`training/policies.py:82`) |
| Evidence | JSON reports from `factorio_mod` RCON commands, JSONL event logs, mission state | SQLite `experience.db` transitions, per-slot live JSON telemetry |

### 1.2 The RL loop as it exists

1. **Scenario generation** — `training/scenarios/mining_delivery.py:136` (`generate_mining_delivery_scenario`) and `generate_staged_mining_delivery_scenario` (:221, 5→15→30 items/s ladder), `training/scenarios/furnace_refining.py:16`. Each produces a schema-validated contract (`schemas/training_scenario.schema.json`): seed, environment, resource patch, fixtures, construction budget, objective (stages), constraints, `reward_profile`, `reward_weights`.
2. **Provision → observe** — `training/factorio_bridge.py:38` `FactorioTrainingBridge.provision()/observe()` (RCON-only, loopback). Lua side: `factorio_training_lab/episode_world.lua:602` `/training_provision`, `factorio_training_lab/episode_measurement.lua:423` `/training_observe` (600-tick rolling delivery window, per-drill capacity audit, power connectivity, footprint, budget overruns).
3. **Candidate generation** — `training/candidates/mining_delivery.py:409` `mining_delivery_candidates()` compiles 2–3 BuildPlan variants (minimum drills vs +1, power routing variants, belt-routing variants, obstacle detours), each with numeric features (predicted rate, capacity margin, belt tiles, route excess, land, cost). Staged episodes regenerate candidates per stage (`training/episode.py:97` `_stage_candidates`).
4. **Selection** — `training/episode.py:144` `policy.select(candidates, initial, seed)`; policies in `training/policies.py`: `DeterministicBaseline` (:57), `DiagonalLinUCB` (:82). Feature vectors via `training/features.py` `FeatureRegistry` (active V3 = `mining-efficiency-v2`, 26 names).
5. **Execution** — `bridge.execute()` (`training/factorio_bridge.py:201`): chunked `/training_upload` (1,600 B × ≤256) then `/training_execute` (Lua `plan_execution.lua`, `place_entity` only, cumulative budget checks).
6. **Measurement → reward** — 5 s polling loop (`training/episode.py:168`), `training/rewards.py:96` `reward_components()` → 9 components (completion, throughput, elapsed_ticks, materials, poles, route_excess, land_usage, unproductive_drill_capacity, failed_placements) + total. Safety is a separate disqualifying gate (`rewards.py:207` `safety_violation()`; failure kinds safety/identity/budget/fixture never train, `training/policies.py:32`).
7. **Transition record** — `training/episode.py:215–250`, schema `schemas/training_transition.schema.json` v1.2.0: `observation` (float dict), `candidates` (id/plan_hash/features), `chosen_action_id`, `stage_action_trail` (per-decision pre/post observations + execution report), `result` (status + typed failure_kind), `metrics` (20 required numeric fields), `reward` (decomposed), `next_observation`.
8. **Learning / population / promotion** — `tools/run_training_batch.py:253` `_learn_policy`; elitist+mutation population (`training/population.py:64`); cohort learning gated on 100 terminal episodes in `tools/run_adaptive_training_batch.py:590–793`; paired greedy held-out evaluation on frozen seeds (:717–738); `promotion_decision` (`training/evaluation.py:92`, lexicographic `FitnessVector.rank_key` :36); champion checkpoints in `data/training/policy.json` and the `policies` table (`training/store.py:22`, lineage via `parent_policy_id`).

Verified sample transition (from `data/training/experience.db`, row 1): candidates `mining-direct-v0-…` / `mining-direct-v1-…`, observation `{delivered_rate_per_tick, resource_remaining, sustained_ticks, target_rate_per_tick}`, decomposed reward total 19.43.

### 1.3 The deterministic controller's decision surface

The runner's pass loop makes its decisions in a small number of seams (all hand-authored):

| Decision | Where it lives today |
|---|---|
| Task selection / prioritization | `PriorityList.next` (`tools/priority_list.py:105`), `_survey_pass` (`tools/autonomous_builder.py:12209`), ready-task cap ≤3/pass (`_serve_ready_pass` :12779) |
| Serve vs wait | `_serve_ready_pass` bounded `PRIORITY WAIT` (:12836–12848), `max_waits=4/pass` (`tools/controller_budget.py:94–104`) |
| Build vs repair vs retire | `ensure_produced` cascade (:8019), `_serve_healthy_line` vs `_repair_stalled_line` (:5125/:5167), `diagnose_replenishment` (`tools/build_decisions.py:107`), `mine_retirement.py` |
| Mine siting / expansion | `choose_mining_origin[_with_reserve]` (`tools/stage_extraction.py:300/364`), `build_mining_stage(expand=…)` (:2838) |
| Transport mode / belt tier | `_transport_mode` (`tools/stage_transport.py:426`), `choose_belt_tier` (:491) |
| Power | `_top_up_solar_generation` (:3635), `extend_power`/`extend_roboport_coverage` (`tools/stage_services.py:1170/1630`) |
| Research | `rank_research_candidates` (`tools/research_scheduler.py:161–227`) — pure allow-list ranker, selects only on `research_not_selected` diagnosis |
| Loans / mall borrowing | `_start_bootstrap_loan` (:7030), `_borrow_free_mall_cell` (:6581), preempt/yield (:6809/:6963) |
| Interrupt / livelock | `_livelock_step` (:12415), `_refuse_to_spin` (:12445), `ControlBudget.credit_progress_pass` (`tools/controller_budget.py:34`) |
| Failure recovery | `WorkStateSignal` typed states (`orchestrator/work_state.py:16`), `MaterialShortage` → mall demand (`tools/mall_bootstrap.py:88–113`), blocker ledger (`orchestrator/mission_state.py:170` `record_blocker`) |
| Checkpoint/acceptance | `checkpoint_milestones._c0.._c6` (`orchestrator/checkpoint_milestones.py:338–508`), `production_acceptance.monitor` (`tools/production_acceptance.py:55`) |

An **action catalog** with a greedy picker already exists (`core/action_catalog.py:273`, `core/baseline_policy.py:108`) — this is the deterministic counterpart of "generate candidate choices from tooling".

### 1.4 What the agent can observe and control

**Observation (deterministic side)** — `orchestrator/live_base.py` narrow Lua surveys: tick, resource/water geography (`nearest_resource` :175, `water_tiles` :1096, `find_clear_area` :461), line health (`find_line` :64, `machine_health` :2133, `progress_counters` :2087), power (`pole_network_id` :891, `network_generation_kw` :1677, accumulator storage :1760, `unpowered_entities` :2184), logistics/stock (`available_items` :1485, `transferable_items` :1507, ghost bill `pending_construction_items` :1979, `ghost_blockages` :1118), plus file-report readers (`request_snapshot`, `science_status`, `logistic_inventory`, `research_status`, `export_recipe_catalog` via `orchestrator/game_bridge.py:44`).

**Observation (training side)** — `episode_measurement.lua` metrics → `training/episode.py:28` `_observation()` (tiny float dict). Deliberately minimal today.

**Control** — every mutating action goes through authorized RCON commands: `/execute_ghost_plan`, `/build_layout_plan` (`layout_executor.lua:1006`, per-action authorization map :679–685), `/execute_construction`, `/execute_upgrade_plan`, `/execute_deconstruction_plan`, `/set_research`, plus sandbox-only provisioning/seeding. All enforced by `core/execution_authorizer.py:52` authorization objects (`schemas/execution_authorization.schema.json`: `approved_actions`, `denied_actions`, `scope_limits{max_count, block_filter}`) and Lua-side scope checks.

### 1.5 Existing LLM integrations (precedents)

- Bounded autoresearch: `training/research/controller.py` (`build_research_packet` :52, allowlisted numeric envelopes via `training/research/proposals.py:48`), `training/research/llama_client.py` (loopback OpenAI-compatible client), `tools/run_autoresearch.py`.
- Helper agent (post-run reviewer): `helper_agent/packet_builder.py` (bounded case packets), Hermes-style evidence-only JSON prompts (`Helper_Agent/config/prompts/review_system_prompt.md`).
- Campaign supervisor prompts with structured decision blocks: `tools/campaign_prompts.py` (`SYNTHESIS:` :17–57, `REVIEW_DECISION` :106, `CAMPAIGN_DECISION` :121–145).
- Quarantined legacy advisory schemas that already define the "propose, never execute" pattern: `rl_observation.schema.json`, `rl_action_proposal.schema.json` (`requires_authorization: const true`), `rl_feedback.schema.json`.

---

## 2. Laya study

### 2.1 Architecture and primitives

- **Backbone**: ModernBERT-large (395M, bidirectional, fully fine-tuned) + decision head (2 transformer layers, option-marker scorer, act/escalate head), 421M total. **Non-autoregressive**: no generation, nothing to parse, nothing to hallucinate — output is typed answers with probabilities in one forward pass (~33 ms single question, 7.2 ms/question batched).
- **Primitives**:
  - `choice` — selected option + probability per option + confidence. The answer space is defined **at request time** (option markers scored at their own `` token) → dynamic candidate lists need no retraining.
  - `score` — expected level on an ordinal rubric + distribution (weakest primitive; SST-5 0.372).
  - `noul` — calibrated P(true) (strongest primitive on the typed-decisions benchmark: 0.857).
- **Three checkpoints**: `convaiinnovations/laya` (English, ModernBERT-large, **512-token context**), `laya-multilingual` (mmBERT-base, 1024, weaker on English), `laya-typed-decisions` (fine-tuned on its benchmark's own training split, 1024 context). We would fine-tune our own from the English checkpoint.

### 2.2 Fine-tuning approach (from the notebook)

Dataset rows are `{id, workflow, state, questions, gold}` JSON strings: `state` is any JSON document, `questions` maps question-id → `{type, instructions, criteria}`, `gold` maps question-id → `{label, probabilities, noul|score}`. One row = one "case" with several questions; per-question items are built by `laya.common.build_sequence` (state + question + one marker per option, respecting `cfg["max_len"]` and `cfg["head_max_len"]`).

Training is **RLCD**: sample G=4 zero-mean-Gaussian perturbations of the logits, reward with a strictly proper scoring rule (`proper_reward`, log+spherical w_sph=0.75 + RPS for ordinal), GRPO-style group-mean baseline advantage, REINFORCE with a full-weight soft cross-entropy guidance term. Notebook hyperparameters on 2×T4 DDP: 4 epochs, micro-batch 8, grad-accum 4 (effective 64), LR encoder 2.5e-5 / head 1e-4, σ 0.4→0.1, ~4–5 h for ~30k questions. **Trains at `max_len=1024`, `head_max_len=256`** (the base config's 512/192 can be raised during fine-tuning). Post-training: per-(question-type) temperature fitting via LBFGS on held-out logits, written into `rl_agent_config.json` (`temperature`).

### 2.3 Constraints that shape our design

| Constraint | Design consequence |
|---|---|
| Near-chance zero-shot (0.362) | Fine-tuning on our domain is mandatory; treat base weights as a fast encoder to specialize. |
| ≤ ~20 options per `choice` | Never one giant decision; hierarchical coarse→fine questions. |
| `head_max_len` fixed budget per option (192 base / 256 when trained) | Option labels must be short structured strings (e.g. `expand_iron`, `build_greencircuits`), not paragraphs. |
| `score` weakest | Prefer `choice` for levels (e.g. priority buckets) and `noul` for booleans. |
| Shipped overconfident; temperature refit needed | Plan a calibration step on our held-out data per (question type, option count), exactly like the notebook. |
| 512-token state budget (base) / 1024 trainable | State digests must be compressed; the notebook proves 1024 is trainable — we should adopt max_len=1024, head_max_len=256. |
| English-only | Everything we render (state, questions, options) stays English, compact, deterministic. |
| One forward pass answers all questions | We can afford a full hierarchical question set (state health, priority, interrupt, next task, recovery) per decision point at negligible cost. |
| 8 GB VRAM constraint in this repo (`docs/rl/training.md` CUDA surrogate section) | 421M fp16 + gradient checkpointing fits 8 GB; use the notebook's 2×T4 DDP recipe or a single 8 GB GPU with the `ResourcePhaseLock` (`training/scheduler.py:117`) keeping LLM compute out of concurrent training phases. |

---

## 3. What Laya could reasonably take over (decision map)

Not one giant question. The architecture naturally supports a **hierarchical question set**, because (a) every question is answered in the same forward pass, and (b) each existing deterministic decision seam already produces the data needed for one small question.

**Level 0 — health gates (`noul` questions, hard-gated by deterministic validators first):**
- `state_healthy` — "Is the current run state healthy?" (from `checkpoint_milestones._c0.._c6`, `science_diagnosis`, unpowered-entity counts).
- `failure_needs_recovery` — "Does this failure require recovery action?" (from typed `WorkStateSignal` / blocker codes).
- `construction_stalled` — "Are pending ghosts actually progressing?" (from `ghost_blockages`, bot counters).

**Level 1 — prioritization (`choice`, ≤8 options):**
- `next_priority` — "What should we prioritize?" Options generated from the live priority list (`PriorityList` items), material ledger gaps, checkpoint milestone failures, research queue. Today: `_survey_pass` + `PriorityList.next`.

**Level 2 — task policy (`choice`/`noul`):**
- `task_policy` — "For the selected task: build new / repair existing / expand current / wait for supplies / yield to another task / interrupt?" Today: `ensure_produced` cascade, `_serve_healthy_line` vs `_repair_stalled_line`, `_blocked_loan_to_yield`.
- `interrupt_current` — "Should the current task be interrupted?" (noul + choice), today: `_feeder_waiter_preempts`, `_livelock_step`.

**Level 3 — parameter choices (`choice`, options generated dynamically):**
- `what_to_build` — recipe/line family (from `LINE_RECIPES` / recipe catalog, gated by `validate_builder_target`, chemical-ladder gates `_CHEMICAL_RUNG_PARK_CODES`).
- `how_much` — machine count tier (6→12→24→48…, `_controller` capacity arithmetic).
- `where` — site selection (options = surveyed candidate sites from `choose_mining_origin_with_reserve`, `bootstrap_cell_origins`, `resource_patches`).
- `transport_mode` / `belt_tier` / `inserter_type` (today `_transport_mode`, `choose_belt_tier`, `_stage_inserter_type`).

**Training-side equivalent:**
- `select_candidate` — "Which candidate build plan?" Options = the 2–N generated candidates with their features rendered. Today: `policy.select`.
- `stage_gate` — "Advance stage now or keep building?" (staged episodes, `training/episode.py:186`).
- `budget_score` — score-type question for "sufficient throughput vs cost" tradeoff judgments.

**Calibration questions (per run, answered alongside):**
- `expansion_urgency` (score), `research_now` (noul), `wait_more` (noul), `retry_vs_recover` (choice).

Each question's **options are generated from existing tooling**, never a fixed global list (see §7). Confidence gating: if `confidence < threshold` (e.g. 0.7 after calibration), fall back to the deterministic picker (`DeterministicBaseline` / `PriorityList.next` / `rank_research_candidates`) and log the disagreement.

---

## 4. What must remain deterministic (hard boundary)

Per `docs/system_invariants.md` and the code boundaries, Laya must **never** do any of the following — these stay in code exactly where they are:

1. **Plan synthesis** — Laya selects from generated, validated BuildPlans; it never emits coordinates, entity names, or plans. Plan validation stays in `planners/plan_validation.py:108` and `planners/preflight.py`.
2. **Legality/budget/authorization** — `assert_affordable` (`stage_services.py:403`), `core/execution_authorizer.py`, scope limits, confirmation tokens, surface/force checks stay untouched.
3. **Execution** — all RCON execution stays in `orchestrator/game_bridge.py` / `training/factorio_bridge.py` / the Lua mods.
4. **Reward accounting and safety classification** — `training/rewards.py` computes; Laya may *predict* outcomes but never writes rewards.
5. **Promotion decisions** — holdout evaluation and `promotion_decision` stay in `training/evaluation.py`; Laya is promoted like any other policy through the same gates.
6. **Rate/measurement math** — capacity, throughput, belt/inserter math from `docs/reference/factorio_mechanics.md` stays in code (count≠capacity; measured at consumer).
7. **Fail-closed stops** — `StuckError`, `_refuse_to_spin`, budget exhaustion, "go around not through", infinity-entity bans (`stage_services.py:575–582`, `plan_validation.py:182`): Laya can advise, cannot override.

The seam rule: **Laya proposes a choice among pre-validated options; deterministic code authorizes and executes; if Laya's chosen option fails any hard validator, the run fails closed exactly as today** — and the failure becomes training data (AGENTS.md "learn, don't accumulate exceptions").

---

## 5. How existing RL infrastructure is reused for Laya fine-tuning

| Existing component | Reuse for Laya |
|---|---|
| `policy.select(candidates, observation, seed)` seam (`training/episode.py:144`) | New `LayaPolicy` class in `training/policies.py` implementing the same `select/update/to_dict/from_dict` interface — swap-in behind the existing cohort/promotion machinery with zero episode-loop changes. |
| Candidate catalogs (`training/candidates/mining_delivery.py`) | Unchanged generators; we render their `features` + plan summary into option labels. Growing catalogs = richer questions automatically. |
| Scenario curriculum (`training/scenarios/*`, `training/curriculum levels 1–8` in docs) | Training cases per curriculum step; held-out seed split reused verbatim from `scenario_split` (`training/evaluation.py:11`). |
| Decomposed rewards (`training/rewards.py`) | (a) Supervised gold labels = chosen candidate; (b) RLCD reward = episode reward components; (c) soft labels = reward-proportional distributions over candidates when counterfactual rewards exist (A/B and population episodes run identical seeds — `run_ab_training_batch.py`, alternating cohorts). |
| `stage_action_trail` (`schemas/training_transition.schema.json:41`) | Per-decision training examples: each trail entry has pre/post observation, execution report, and outcome — a natural multi-decision decomposition of one episode. |
| Population/checkpoint/lineage (`training/population.py`, `training/store.py` policies table) | Store the fine-tuned Laya checkpoint as a policy row (`policy_id`, `policy_hash` = model-dir digest, `config_json` = question schema version + temperature vector); lineage from the base checkpoint id. |
| Promotion gates (`training/evaluation.py:92`, held-out paired greedy runs) | Laya must beat `DiagonalLinUCB` and `DeterministicBaseline` on held-out seeds with zero safety violations — same rules as any policy. |
| Adaptive batch / workers / UPS gating (`run_adaptive_training_batch.py`, `training/scheduler.py`) | Inference co-location: Laya at 33 ms/question adds ~1–2 ms/decision to a 5 s polling loop — negligible. GPU use must respect `ResourcePhaseLock` (`training/scheduler.py:117`) so on-policy Laya updates don't collide with episode execution. |
| Autoresearch plumbing (`training/research/*`, `tools/run_autoresearch.py`) | Same allowlist-bound "propose only, code applies" pattern reused for any Laya-driven envelope changes; `llm_runtime_samples` table pattern reused for Laya inference accounting. |
| Observatory/nudge (`training/observer.py`, `save_guidance`) | Human guidance strings become candidate `rules` fields in Laya's state — same injection path, now consumed by the model instead of the hand policy. |

---

## 6. How existing logs become Laya training data

### 6.1 RL-side (primary, already perfect for this)

Source: `transitions.payload_json` in `data/training/experience.db` (~2,400 transitions) plus the big run DBs (`data/training-fresh-20260814-25x16-400x20-v2/experience.db` 41 MB, `data/training-linux-mining-staged-5-15-30-4000-20260823/experience.db` 58 MB / ~4,800 episodes, plus obstacle-staged, g21, overnight, archive dirs — ~300 MB total). Every row already contains everything a Laya case needs:

```
state   = scenario digest (family, objective, stage, budget, reward weights)
        + observation (rates, sustained ticks, resource remaining, drill audit)
        + candidate feature summary (per option)
questions = {
  "select_candidate": {type: choice, criteria: {<action_id>: "<feature summary>"}},
  "stage_gate":       {type: noul,  instructions: "Should we advance the stage now?"},
  "throughput_ok":    {type: noul,  instructions: "Is the build delivering target rate?"}
}
gold    = chosen_action_id (hard label)
        + reward-derived soft label (see below)
```

**Label strategies, in order of confidence:**
1. **Direct distillation** — gold = `chosen_action_id` where the episode ended `completed` (distills the current controller; ~4,800 completed staged episodes available).
2. **Hindsight preference** — for same-seed episodes run under different policies/generations (population + A/B infrastructure), pair episodes and use reward totals to produce pairwise preferences (loss as pairwise ranking or soft distribution).
3. **Failure-conditioned negatives** — episodes whose `result.failure_kind ∈ {strategy, timeout, power_unconnected}`: the chosen candidate's features at that decision become negative-biased soft labels (weight down by reward shortfall); `safety/identity/budget/fixture` failures are excluded entirely (never train on unsafe, matching `transition_can_update_policy`).
4. **Counterfactual feature scoring** — candidate features (predicted rate, cost, route excess) allow model-free pseudo-rewards for *unselected* candidates; combine with observed chosen reward to build soft targets over the full option set (bounded, conservative: unknown ≠ 0 semantics from `run_context.md`).

The `stage_action_trail` entries turn one long staged episode into 3+ sequential decision cases with pre/post observations — this is where "state changed after my decision" supervision comes from.

### 6.2 Deterministic-side logs (secondary, needs an extractor)

Sources under `~/.local/share/factorio-rl/deterministic/` and per-run dirs (paths per `docs/deterministic/run_context.md` and `docs/deterministic/checkpoint_fleet.md`):

| Log | Reusable for |
|---|---|
| `deterministic-events.jsonl` (`_RunLogger.emit`, `tools/run_log_format.py`, v2 schema `{seq, dt, type, message, signature, signature_occurrence}`) | Decision-point reconstruction: what state preceded each `type`, what decision followed, what outcome. |
| `deterministic-mission-state.json` (schema `deterministic_mission_state.schema.json`: `stage, current_target, status, blockers[], events[]`) | Persistent goal/task state block for Laya state digest. |
| `deterministic-blockers.jsonl` (typed `code/classification/state/target/message`) | `noul` targets for `failure_needs_recovery`, `state_healthy`; failure-kind taxonomy for multi-choice recovery questions. |
| `autonomous-priorities.json` (`priority_list.py:167`: `{item, target, rating, progress_percent, status, reason, retry_tick}`) | Gold labels for `next_priority` (distill `PriorityList.next`) and dynamic option generation. |
| `research-queue.json` + `research_status` reports + `rank_research_candidates` scores | `research_now` / tech-choice questions (allow-list bounded, exactly like the ranker). |
| Material reservation ledger (`schemas/material_reservation_ledger.schema.json`) | Supply-wait vs build-now labels (loans, reservations, stock vs allocated). |
| `last-10-runs.md` + `run-journal-notes.json` + `tools/run_context.build_context` (12k-char packet, `tools/run_context.py:97`) | Cross-run trend features (compressed) and the "same failure twice → stop" gate examples. |
| Campaign decision blocks (`tools/campaign_prompts.py` `SYNTHESIS`/`CAMPAIGN_DECISION`/`REVIEW_DECISION` outputs in `campaign_loop_log`) | **Expert distillation set**: every logged campaign decision has rationale + confidence + evidence — the richest natural-language supervision in the repo. |
| Helper agent case packets + review reports (`helper_agent/packet_builder.py:335`, `Helper_Agent/config/prompts/review_system_prompt.md`) | Post-hoc diagnosis labels: "what should have been done at pass N" judgments. |

**Important caveat (user-provided context):** the ~100s of deterministic runs are unclassified and nearly all ended in *some* problem; the system improved over time. Therefore:
- Order runs chronologically (manifests carry `started_at`/`repository_revision`) and **weight later runs higher** (the controller improved; early decisions are worse supervision).
- Use blocker classification (`bug` vs `intended_difficulty`) to split "controller was wrong" from "world was genuinely hard" labels.
- Use terminal state (`completed` vs `stuck` vs `error` from `mission_state.MISSION_STATUSES`) for hindsight relabeling of decisions preceding failure.
- Log provenance (manifest hash: `source_save_sha256`, `repository_revision`, `bootstrap_profile`) must travel with every training case — the repo's provenance discipline (`checkpoint_fleet.md` bundles) extends naturally to datasets.

### 6.3 New artifact: `laya_case` schema

Propose `schemas/laya_case.schema.json` (v1): `{case_id, source{kind: rl_transition|deterministic_run|campaign_decision, episode_id/run_id, provenance_hash}, state, questions, gold, weights, created_at, dataset_version}` — one file per case group, JSONL, hashed like every other evidence artifact. A converter module (e.g. `training/laya_dataset.py`) reads SQLite + deterministic logs → cases; unit-testable against real DB fixtures.

---

## 7. Dynamic candidate generation from existing tooling

Principle: **options are compiled, not enumerated**. Every question's option list is produced at decision time by the same code that produces today's decision inputs:

1. **RL side** — candidate catalogs already exist per family (`training/candidates/mining_delivery.py:409`); the candidate compiler emits `{action_id, plan_hash, features, plan}`; option text = short rendering of `action_id` + top features. As families grow (furnace → transport → power → connect → repair per the curriculum ladder), each new generator automatically becomes a new question type.
2. **Deterministic side** — new `DecisionContext` builders that wrap existing decision inputs:
   - `PriorityList` items → `next_priority` options (bounded to ≤8 by rating; overflow handled by coarse→fine: pick bucket `production/logistics/power/research/recovery`, then pick item within bucket).
   - Available next actions from `core/action_catalog.py` → `task_policy` options (build/repair/expand/wait/yield).
   - Surveyed sites (`choose_mining_origin_with_reserve` output) → `where` options.
   - Tier ladder options from `choose_belt_tier` / `_stage_inserter_type` / tier policy → `how_much`/`tier` options.
   - `research_options` filtered by `rank_research_candidates` eligibility → `research_target` options.
3. **Tool selection** (the user's "choose the tool it needs and how to use it"): maintain a **tool registry** (`orchestrator/game_bridge.py` wrappers + their Lua commands + schema + failure modes), and give each option label a tool reference. The per-task context pack (§9) provides "how to use" rules; the `choice` answer selects which tool(s) the deterministic layer should invoke, in what order. Laya never invokes tools itself.

Coarse→fine splitting is mandatory wherever the natural option count exceeds ~15 (Laya's own documented limit).

---

## 8. Laya input/output schema

**State (input, JSON, target ≤ ~900 tokens with max_len=1024 training):**

```json
{
  "run":     {"run_id", "episode_id", "tick", "elapsed_ticks", "bootstrap_profile"},
  "goal":    {"family", "objective", "target_rate_per_tick", "sustain_ticks", "stage_index", "stage_count", "acceptance": "<C-gate digest>"},
  "task":    {"current_task_id", "task_kind", "state", "age_ticks", "progress_percent", "retry_count", "last_failure_kind"},
  "state_digest": {
    "power":   {"generation_kw", "accumulator_mj", "unpowered_consumers", "poles"},
    "logistics":{"robots", "providers_available", "pending_ghosts", "ghost_blockages_top3"},
    "stock":   {"total", "accessible", "allocated", "top_shortages"},
    "production":{"rate_now", "rate_target", "sustained_ticks", "bottleneck_hint"},
    "resource":{"remaining", "patch_bounds_hint"}
  },
  "recent_history": ["<5-8 compressed event lines: decision → outcome>", "..."],
  "rules":    ["<3-6 hard rules + 2-3 task-specific soft rules from the context pack>"],
  "candidates": [{"id", "tool", "summary", "features_digest"}]
}
```

**Questions (asked in one forward pass):**

```json
{
  "state_healthy":   {"type": "noul"},
  "next_action":     {"type": "choice", "criteria": {"continue_current": "...", "wait": "...", "recover": "...", "expand": "...", "new_task": "..."}},
  "selected_option": {"type": "choice", "criteria": {"<dynamic candidate ids>": "<summaries>"}},
  "urgency":         {"type": "score", "criteria": ["idle", "soon", "blocking"]}
}
```

**Output (returned by `agent.predict`)**: per question — `{choice, probabilities{...}, confidence}` / `{score, distribution, confidence}` / `{noul}`. We consume: argmax choice, confidence (fallback gate), probabilities (soft ranking for tie-breaks and logging).

**Calibration:** refit temperature per (question type, option count) on our held-out cases after each fine-tune, store in the policy row's `config_json`. Confidence thresholds gate fallback to the deterministic baseline.

**Context budget discipline:** every field above is rendered from bounded, existing structures — `run_context.py`'s 12k packet is the *superset* we compress from, not the input itself. The compression rules (latest milestone, last-3 decisions, top-3 blockers, unknown stays unknown) mirror `docs/deterministic/run_context.md` so both human and model consumers share one renderer.

---

## 9. Run context, history, and goals representation + docs restructuring

The user requirement: *per-task context packs* — docs restructured so the decision layer gets task-specific context ("based on the task selected, the model is given additional context on how it should perform, then chooses the tool it needs and how to use it").

**Proposal: `docs/decision_context/` directory**, one pack per task family (mirroring the curriculum ladder):

```
docs/decision_context/
  README.md                    (router: pack selection rules)
  extraction.md                (mine siting, drill counts, expansion, retirement)
  refining.md                  (furnace lines, steel ladder, fuel rules)
  transport.md                 (belt vs logistic, tiers, bridges, fluid pipes)
  power.md                     (districts, solar/steam, coverage)
  logistics_mall.md            (mall cells, requesters, loans, reservations)
  research.md                  (allow-list, pack economics, scheduler contract)
  recovery.md                  (blocker taxonomy, repair-before-duplicate, fail-closed stops)
  expansion.md                 (new mine transaction, budget, corridor rules)
```

Each pack contains, in fixed order (so rendering is deterministic):
1. **Goal** — what this task is for (from `docs/deterministic/planning.md` invariants, distilled).
2. **Hard rules** — the validators/gates that will reject (fail-closed behavior made explicit to the model).
3. **Soft preferences** — cost/throughput/zoning priors (these become reward weights too).
4. **Tool inventory** — the RCON commands + bridge wrappers relevant to the task, each with: schema, authorization requirement, failure modes, when-not-to-use.
5. **Worked examples** — 2–3 compressed (state → decision → outcome) examples drawn from historical logs.

Renderer: `DecisionContext.pack(task_kind)` selects the pack, merges persistent goal state + live digest + pack rules into the Laya state JSON. This keeps Laya's 512–1024-token budget feasible: persistent goal/task state lives in `deterministic-mission-state.json` (compact), only the selected pack's rules enter the prompt, full history stays in the event log (recent_history is a renderer output, not a raw dump).

This restructuring also simplifies the existing doc tree: `docs/deterministic/planning.md`'s lifecycle invariant list becomes the source that packs distill from, with packs versioned so dataset provenance can point at the pack version used at decision time.

---

## 10. Where the Laya decision layer integrates

**Training side (first, lowest risk):**
- `training/policies.py`: new `LayaPolicy` (select/update/to_dict/from_dict). `select` = one forward pass over the rendered state + candidate question; `update` = RLCD online update or deferred to batch.
- `tools/run_training_batch.py` / `run_adaptive_training_batch.py`: unchanged loops; Laya enters via `policy.json`-style checkpoint (model dir + config hash) in the store's `policies` table; cohort learning, held-out paired evaluation, promotion all reused verbatim.
- Optional fast fallback chain: `LayaPolicy` (confidence ≥ gate) → `DiagonalLinUCB` → `DeterministicBaseline`.

**Deterministic side (shadow first):**
- New `orchestrator/decision_layer.py` + `tools/autonomous_run.py` integration behind a `--decision-shadow` flag: at each decision seam (pass survey, task policy, research, recovery), render the context, ask Laya, **record {context, laya_decision, deterministic_decision, agreement, confidence, outcome}** to a new JSONL (e.g. `deterministic-laya-decisions.jsonl`) — execution always follows the deterministic decision in shadow mode.
- Confidence-gated hybrid (Stage F below): Laya's choice is executed only when (a) it agrees with a hard-validator-checked allowlist, (b) confidence ≥ calibrated threshold, (c) the option maps to an existing deterministic action entry in `core/action_catalog.py`; otherwise deterministic decision, disagreement logged.
- Campaign/helper layer: Laya becomes the fast first-pass reviewer/decision model; the big LLM (GLM/Qwen sessions) handles only low-confidence or novel cases — mirroring Laya's own "confidence gating" pattern and cutting local-LLM latency in the 12-h campaign loop.

---

## 11. Additional logging/data to collect before training

1. **Decision-context snapshots at every deterministic decision seam** — today only outcomes and blockers are logged; we lack the *state at decision time*. Add a bounded decision record (digest ≤ ~2 KB: the §8 state JSON + chosen action + available options) emitted to `deterministic-laya-decisions.jsonl` in shadow mode. This is the single most valuable new dataset.
2. **Counterfactual option digests** — for each seam, log all *considered* options with their computed features (the deterministic code computes these anyway; currently discarded after argmax).
3. **Outcome linkage** — ensure every decision record carries the eventual outcome (task completed/failed + blocker code + tick delta), enabling hindsight labels without manual annotation.
4. **Campaign decision capture** — persist the structured `SYNTHESIS`/`CAMPAIGN_DECISION` blocks with the exact run context they were made against (currently they're in loop logs, not tied to machine-readable context).
5. **Normalizer for the ~100s of unclassified runs** — a one-time classifier pass: manifest chronology + revision + blocker codes → per-run quality weight; also cluster failure modes across runs (feeds `failure_kind` taxonomy expansion).
6. **RL-side enrichment** — extend `training/episode.py:_observation()` (currently ~7–14 floats) to include the power/logistics/stock digest fields that the deterministic side already computes, so Laya's state is consistent across both sides. Keep the transition schema additive (v1.3) per the pinned-contract restart rule.
7. **Timing/latency telemetry** — record Laya forward-pass latency in `llm_runtime_samples`-style rows to satisfy the "no concurrent GPU-heavy LLM + training without measurements" rule.

---

## 12. Staged path from current controller to Laya-driven controller

**Stage 0 — Instrument (no behavior change).**
Add decision-record logging (§11.1–11.3) in both the deterministic runner and the RL episode loop. Ship: raw data for everything downstream. Lifecycle: restart runners.

**Stage 1 — Offline dataset v1.**
`training/laya_dataset.py` converts existing `experience.db` transitions (~7,000 cases) + reconstructible deterministic decisions into `laya_case.schema.json` JSONL, chronologically weighted, provenance-hashed. Split train/held-out with the same stable hash discipline as `scenario_split`.

**Stage 2 — Distillation fine-tune + calibration.**
Adapt the notebook pipeline (2×T4 or single 8 GB GPU, fp16, gradient checkpointing; `max_len=1024`, `head_max_len=256`): train on Stage-1 gold labels (option-marker sequences built by `laya.common.build_sequence`), then fit temperatures on our held-out data. Success gate: accuracy comfortably above the deterministic-baseline majority-class rate on `next_priority`/`select_candidate`, calibrated ECE < 0.15 on `noul` questions.

**Stage 3 — Shadow mode.**
`--decision-shadow` in `autonomous_run.py` and a `LayaShadowPolicy` alongside the incumbent in training batches. Log agreement rates per question type. Gate: ≥85% agreement on health gates, ≥70% on prioritization; no safety-relevant disagreement. Lifecycle: restart runners; no mod changes.

**Stage 4 — On-policy RLCD in disposable episodes.**
`LayaPolicy` runs the mining/staged curriculum on-policy: choose candidates, observe episode reward, RLCD update (GRPO-style group baseline over a batch of same-seed episodes), reusing `ResourcePhaseLock` for GPU isolation. Gate: beats `DiagonalLinUCB` champion on held-out seeds under `promotion_decision` with zero safety violations.

**Stage 5 — Bounded live hybrid.**
Confidence-gated execution of Laya choices in deterministic runs for a *single* decision type first (task selection), scoped to a checkpoint-fleet lane (`docs/deterministic/checkpoint_fleet.md` predicates), with rollback = flip the gate off. Only after: matched-pair verify, `reliability_state.py` acceptance streaks, and the `docs/architecture.md` promotion ladder.

**Stage 6 — Generalize.**
Extend to more decision types (research, recovery, expansion ordering) and more scenario families; each new family's candidate generator + context pack = new question types without retraining the option space (request-time options), only periodic fine-tunes as the domain grows.

Anti-goals: Laya never touches execution, never emits coordinates/plans, never overrides hard validators, never becomes the sole controller without the promotion gates, and never replaces the reward pipeline. Per `docs/system_invariants.md`, a learned policy gains real-base authority only through explicit promotion with bounded scope, observable results, and rollback.

---

## 13. Open questions

1. Do we fine-tune from the English checkpoint at `max_len=1024` (notebook-proven) and give up the base 512 config entirely? (Recommended: yes.)
2. Should RLCD online updates happen inside `run_episode` (immediate) or batched per cohort (matches the 100-episode policy cohort gate)? Recommend batched first.
3. How much of `docs/deterministic/planning.md`'s lifecycle invariant list should be encoded as pack *hard rules* (rendered into state) vs. left as pure validators the model never sees? (Leaning: hard gates invisible to Laya — it can't violate them anyway; only soft preferences need stating.)
4. Multi-task generalization: one checkpoint per family or one shared checkpoint with question-type conditioning? (Start shared; split only if interference appears.)
5. Exactly how far back do the ~100s of deterministic runs go in terms of decision-reconstructibility — needs a one-time audit pass over the archive before committing to Stage-1 deterministic-side extraction.
