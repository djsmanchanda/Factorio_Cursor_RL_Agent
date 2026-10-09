<!-- Path: docs/archive/rl/Laya_classifier_plan_Muse.md | Purpose: Finished design for an autonomous guideline-grounded Laya director layer over the deterministic Factorio tooling. Design-only; not yet authoritative. -->

# Laya_classifier_plan_Muse — Autonomous Guideline-Grounded Laya Director

**Label:** `Laya_classifier_plan_Muse`
**Target model:** [convaiinnovations/laya](https://huggingface.co/convaiinnovations/laya) (ModernBERT-large, 421M, non-autoregressive)
**Status:** design proposal. `docs/rl/README.md`, `docs/rl/training.md`, and `docs/deterministic/*` remain authoritative until this design is adopted.
**Date grounded:** 2026-09-19. All file cites below were verified against the live tree on that date.

## 0. Relation to the sibling plans

Two earlier explorations exist: `docs/archive/rl/Laya_classifier_plan_antigravity.md` and `docs/archive/rl/Laya_classifier_plan_GLM.md`.
Both frame Laya as a **ranker over deterministically generated candidates**, with one hierarchical question per existing
controller seam (`next_priority` ← `PriorityList.next`, `research_next` ← `rank_research_candidates`, …).
This plan deliberately takes the other fork: Laya becomes an **autonomous director** that owns what, which, when, where,
and how, selecting operation bundles from a unified tool registry grounded in versioned guideline packs.
Deterministic code shrinks to geometric realization plus legality shielding.
The survey sections (§2–§3) are re-grounded from direct reads (including a correction: the controller lives in
`orchestrator/`, not `tools/`, contrary to the sibling plans' cites); the architecture from §4 onward is an independent design.

## 1. Laya model study (model card fetched 2026-09-19)

- **Backbone:** ModernBERT-large (395M, bidirectional, fully fine-tuned) + decision head (2 transformer layers,
  option-marker scorer, act/escalate head); 421M total. Apache 2.0.
- **Non-autoregressive:** one forward pass answers **all questions** in the call (~33 ms single, ~7 ms/question batched
  on T4). Nothing is generated, so nothing needs parsing and nothing can hallucinate text.
- **Primitives:** `choice` (selected option + per-option probabilities; answer space defined **at request time** via
  per-option `[MASK]` scoring, so dynamic candidate lists need no retraining), `score` (ordinal rubric; weakest —
  SST-5 0.372), `noul` (calibrated P(true); strongest — 0.857 on typed-decisions).
- **Checkpoints:** `laya` English (512-token context), `laya-multilingual` (1024, weaker English),
  `laya-typed-decisions` (1024, fine-tuned on its own benchmark). We fine-tune our own from the English checkpoint.
- **Training (RLCD):** policy reports a distribution; exploration adds zero-mean Gaussian noise to logits; reward is a
  strictly proper scoring rule (log + spherical + RPS for ordinal); REINFORCE with GRPO-style group-mean baseline plus
  full-weight soft cross-entropy guidance. Notebook rows are `{id, workflow, state, questions, gold}` JSON;
  trains at `max_len=1024`, `head_max_len=256`; per-(question-type, option-count) temperature refit via LBFGS on held-out data.
- **Hard limits that shape this design:** near-chance zero-shot (0.362 vs 0.461 majority baseline) → fine-tuning is
  mandatory; `choice` ≤ ~20 options (fixed `head_max_len` budget) → coarse-to-fine decomposition, never one giant flat
  decision; ships overconfident (ECE 0.466 → 0.081 after refit) → calibrate on our data; English-only (fine — our
  rendering is deterministic English); 8 GB VRAM repo constraint fits 421M fp16 with gradient checkpointing.

## 2. How the current system actually works

### 2.1 Deterministic runtime (real base, Nauvis/player)

Entry: `tools/autonomous_run.py` → `run()` in `orchestrator/autonomous_builder.py:12894` (13,212-line controller).
Each pass: `_survey_pass` (`autonomous_builder.py:12209`) picks a task via `PriorityList.next`
(`orchestrator/priority_list.py:105`); `_serve_ready_pass` (`:12779`) serves up to `_MAX_MALL_TASKS_PER_PASS` ready
tasks, else a bounded `PRIORITY WAIT` (`:12836`), else `_advance_the_goal`. Pre-pass prep cascades run first
(`_prep_plate_extraction` :10471, `_prep_plate_foundation` :10768, `_prep_the_belt_cell` :10961, `_prep_intermediate`
:11450, `_prep_core_mall` :11558, `_prep_steel_district` :4503, `_top_up_solar_generation` :3635). Progress accounting
(ghost counts, relevant-stock growth, loan/coverage revisions → `budget.credit_progress_pass()`) is inline in `run()`
(`:12949–13013`); livelock guards are `_livelock_step` (`:12415`) and `_refuse_to_spin` (`:12445`).
Repair-vs-serve branching lives in `_serve_healthy_line` / `_repair_stalled_line` (`:5125` / `:5167`); the
build-or-fetch cascade is `ensure_produced` (`:8019`); mall borrowing in `_borrow_free_mall_cell` (`:6581`) and
`_start_bootstrap_loan` (`:7030`).

Geometry comes from `planners/` + `orchestrator/stage_*`. Every mutation funnels through `GameBridge`
(`orchestrator/game_bridge.py:44`: `command()` + ~20 typed wrappers) → RCON (`tools/rcon_client.py`) →
`factorio_mod/*.lua` (22 console commands: ghost/construction/layout/upgrade/deconstruction/world-seeding/research/telemetry)
→ JSON reports under `script-output/factorio_mod/*_reports/`, collected by `_run_and_collect` (`game_bridge.py:207`)
with retention/dedupe. Observation is `orchestrator/live_base.py` narrow Lua surveys plus report-file readers
(`request_snapshot`, `science_status`, `logistic_inventory`, `research_status`, `export_recipe_catalog`).

### 2.2 RL training (disposable surfaces, loopback only)

`tools/run_training_batch.py` / `run_adaptive_training_batch.py` / `run_ab_training_batch.py` →
`run_episode()` in `training/episode.py:120`: provision → observe → `policy.select(candidates, obs, seed)` (`:144`) →
`bridge.execute()` (chunked `/training_upload` + `/training_execute`, `training/factorio_bridge.py:201`) → 5 s poll loop
with in-place `_stage_candidates` re-decisions (`:176–210`) → `reward_components()` (`training/rewards.py:96`,
9 components + total) → validated transition (`schemas/training_transition.schema.json` v1.2.0) → conditional
`policy.update` gated by `transition_can_update_policy` (`training/policies.py:32`: only completed / strategy-failure /
timeout outcomes train; contract, fixture, capability, execution, identity, budget, power, and safety failures never do).
Candidates: `training/candidates/mining_delivery.py:409` (+ `furnace_refining.py`); policies: `DeterministicBaseline`
(`:57`) and `DiagonalLinUCB` (`:82`); features versioned V1–V3 (`training/features.py:116`, active
`mining-efficiency-v2`, 26 names); evidence in `TrainingStore` SQLite (`training/store.py:78`:
scenarios / policies / episodes / transitions / evaluations / champions / proposals tables); promotion via
`promotion_decision` / `FitnessVector.rank_key` (`training/evaluation.py:92`).

### 2.3 Verified data volume

`data/training/experience.db`: 2,367 transitions (2,111 completed / 256 timed_out / 122 aborted);
`data/training-fresh-20260814-25x16-400x20-v2`: 7,924; `data/training-overnight-20260814-5-10-30`: 3,873;
`data/training-policy23-20260813-050253`: 7,280; plus ~30 smaller DBs → **≈23k RL transitions total**.
Deterministic evidence lives outside the repo at `~/.local/share/factorio-rl/{deterministic,training,…}/`:
`logs/deterministic-mission-state.json`, `logs/deterministic-blockers.jsonl`, `logs/deterministic-events.jsonl`,
`logs/campaign-loop/` (paths from `tools/autonomous_run.py:768`, `tools/campaign_loop_log.py:19`,
`tools/milestone_checkpoint.py:74`).

## 3. Full inventory: observe, control, decide, persist

### 3.1 Everything the agent can observe

- **Live world (Lua surveys, `orchestrator/live_base.py`):** lines (`find_line` :64), idle rows (:137), resources
  (`nearest_resource` :175), buildable ground (`area_clear` :426, `find_clear_area` :461, `find_clear_areas` :510),
  entities at tiles (:612–:660), belt directions (:690), chest stocks (:707), entity signatures (:736), logistic demand
  (:777), safe-to-clear + removal (:795–:810), ghost removal results (:57), power networks (`pole_network_id` :891,
  other-network poles :908, powered poles :941), occupancy (`occupied_tiles` :1000), water (`water_tiles` :1096),
  ghost blockages (`ghost_blockages` :1118), area records (:1229), deconstruction tiles (:1270), transport occupancy
  snapshots (:1296–:1323), stock (`available_items` :1485, `transferable_items` :1507), roboports (:1553–:1592).
- **Report files (via `GameBridge`):** full snapshots, ghost observations, execution/construction/layout/live-execution
  reports, research status/options, science telemetry, logistic inventories, topology reports, recipe catalogs.
- **Run-internal state:** mission ledger (goal, stage, target, attempt, blockers — `orchestrator/mission_state.py:42`),
  priority ready/deferred sets, loan/retirement/material ledgers, research queue, `ControlBudget` counters
  (`orchestrator/controller_budget.py:94`, `max_waits = 4 × passes`), checkpoint predicates
  (`orchestrator/checkpoint_milestones.py`), production acceptance (`tools/production_acceptance.py`).
- **Training-side:** episode reports (`factorio_training_lab/episode_measurement.lua`, 600-tick delivery window,
  per-drill capacity audit, power/footprint/budget facts) → `_observation` (`training/episode.py:28`).

### 3.2 Everything it can control (the tool surface)

All 22 `factorio_mod` RCON commands (ghost/construction/layout/upgrade/deconstruction/world-seed/research/verify),
all 8 `factorio_training_lab` commands (provision/upload/execute/observe/recycle/cleanup/focus/view), plus the
controller-side planners/stages that parameterize them (siting, belt bridging, smelter/mall/refinery/power layouts,
transport-mode and belt-tier choice, loan/borrow/retire operations, research queueing). §6 turns this surface into a
formal registry.

### 3.3 Current decision points (what the cascade decides today)

Task selection (`PriorityList.next`); serve-vs-wait (`_serve_ready_pass`, `ControlBudget`); build-vs-repair-vs-retire
(`ensure_produced`, `_serve_healthy_line` vs `_repair_stalled_line`, `diagnose_replenishment`, `mine_retirement.py`);
mine siting/expansion (`choose_mining_origin[_with_reserve]`, `build_mining_stage(expand=…)`); transport mode/tier
(`_transport_mode`, `choose_belt_tier`); power/roboport extension (`_top_up_solar_generation`, `extend_power`,
`extend_roboport_coverage`); research (`rank_research_candidates`, `orchestrator/research_scheduler.py:161`, pure
allow-list ranker firing only on `research_not_selected`); loans/borrowing/preempt/yield; interrupt/livelock
(`_livelock_step`, `_refuse_to_spin`); failure recovery (`WorkStateSignal` states, mall-demand filing,
`MissionStateLedger.record_blocker` :170); checkpoint/acceptance gates.

### 3.4 Goals, tasks, and what persists across a run

Durable mission ledger (`MissionStateLedger`, v1 payload: mission/episode IDs, bootstrap profile, command, target,
surface, force, revision, save provenance, status, stage, current target, attempt, controllers, blockers, events) plus
an append-only blocker JSONL stream. Typed lifecycle signal `WorkStateSignal`
(`orchestrator/work_state.py:16`: 9 states × {bug, intended_difficulty}). Research intent persists via the durable
research queue; training evidence persists in `experience.db` with identity-hash binding on every transition
(`training/store.py:181`).

## 4. The director: one recurring decision instead of a cascade

The cascade in §2.1/§3.3 is the thing being replaced, so this plan does not anchor Laya's decisions to its seams.
Instead, a fine-tuned Laya director makes **one recurring decision** at every pass boundary of `run()`
(`orchestrator/autonomous_builder.py:12957`), every stage transition, and every `WorkStateSignal`:

> Given the guideline pack, the tool registry, structured state, the goal stack, failure memory, and budget —
> emit the next authorized operation bundle: `[{tool, args, where, how_much, why_rule}]`, or `wait`, or `escalate`.

The former `_prep_*` / `_serve_*` / `_repair_*` steps become stateless tools the director invokes, ignores, or
sequences freely. The director can interleave power + mall + scouting in one pass or deliberately defer the "deepest
missing stage" — behaviors the hardcoded order cannot express, and the class of behaviors whose absence produces the
documented livelocks. Laya's primitives map onto the decision as follows: `choice` over currently eligible tools
(§6) and over goals (§7); `noul` for serve-vs-wait, interrupt-vs-continue, healthy-vs-recover; `score` avoided
(weakest primitive — levels become small `choice` questions). All questions in a pack are answered in one forward pass.

```
guideline pack (compiled, versioned) + tool registry (schemas + preconditions)
        + structured state + goal stack + failure memory + budget
                            ↓
              fine-tuned Laya director (choice + noul, one pass)
                            ↓
   operation bundle: [{tool, args, where, how_much, why_rule}] | wait | escalate
                            ↓
   deterministic shield → geometric realizers → GameBridge → Factorio → outcome
                            ↓
        outcome + reward → trajectory store → failure attribution
                            ↓
   guideline compiler (doc patches) + curriculum + Laya retrain ← recursive loop
```

## 5. Guidelines as executable context

Your intent is currently scattered across `AGENTS.md`, `docs/deterministic/planning.md` lifecycle invariants,
`docs/reference/factorio_mechanics.md`, and campaign prompts (`tools/campaign_prompts.py`). The director needs them as
a **compiled, versioned, retrievable artifact**:

- **Guideline compiler** (new, deterministic): parses task-indexed markdown packs into
  `{rule_id, statement, scope_tasks, hard_or_soft, test_assertion}`. Hard rules (blueprint-only, no manual base
  repair, fluid purity, isolation per `docs/system_invariants.md`) compile to shield assertions; soft rules
  (direct-belt-over-chest, repair-before-duplicate, power-first, short-routes, expansion-space) compile to prompt
  lines plus reward-shaping terms.
- **Per-decision injection:** the director's `state` carries `guideline_pack_id + version` and the top-K rules by
  task scope — an oil decision gets fluid + refinery-siting rules, not mall-loan rules. Stable rule IDs double as
  training features (`why_rule` in every emitted bundle).
- **Docs restructure required:** split `docs/deterministic/planning.md` invariants into task-indexed rule packs with
  stable IDs plus a `rules_table.json`; update `docs/rl/training.md` (Laya as a policy family) and
  `docs/architecture.md` (director seam) only when this design is adopted.

## 6. Tool registry as the action space (dynamic candidates, unified what/how/where)

Laya never invents actions and never emits raw coordinates. Candidates emerge from **one registry** built from §3.2:

- **Sources (all already real):** `GameBridge` methods (`orchestrator/game_bridge.py:75`) for RCON/file-report ops;
  `live_base.py` surveys (§3.1) as read tools; planners/stages as where/how realizers (belt bridging, smelter blocks,
  mall layouts, refinery blueprints, power districts); `rank_research_candidates`
  (`orchestrator/research_scheduler.py:161`) as a research-opinion tool, not the decider;
  `core/action_catalog.py` expansion options demoted from "what could be done" to one advisory input among many.
- **Registry entry:** `{tool_id, args_schema, preconditions (state predicates), cost_estimate, effects, idempotent,
  risk_tier}`. Risk tiers gate autonomy: read-only and ghost-submit tools are free; deconstruction / upgrade /
  research-queue tools need `P(act)` above threshold; save/surface-mutating tools stay human-authorized.
- **Decision flow:** preconditions are evaluated deterministically → typically 5–15 eligible tools → pre-validator
  prune (authorization, scope, budget, collision, fluid, surface checks; log K and pruned count) → cap K≤20 with
  coarse-to-fine split when more (zone/family first, then within-winner) → Laya `choice` over short
  `tool_id + signature + key numerics` labels (fits the `head_max_len` budget). `where`/`how_much` come from the
  tool's own siting/sizing helpers (`find_clear_areas` :510, `choose_mining_origin`, `expansion_target()`), with Laya
  picking among returned sites.
- **Prioritization as learned utility:** the goal stack (ordered goals with measured gaps, persisted in the mission
  ledger) replaces `PriorityList` + `ControlBudget` control flow. Laya chooses which goal to serve each pass; budget
  counters (waits/passes/remediations left) are numeric state, so patience-vs-urgency is learned per situation instead
  of hardcoded as "goal first, then background" (`_serve_ready_pass`, `autonomous_builder.py:12779`).

## 7. What the director takes over vs what stays deterministic

**Moves to learned (deliberately beyond ranker plans):** prep ordering, serve-vs-wait timing, goal ordering, interrupt
judgment, site choice among surveyed options, sizing choices, research pick among the allow-list, recovery-strategy
choice, expansion-vs-upgrade-vs-module tradeoffs. Geometry generation stays deterministic; *which* geometry to
request, *where*, and *when* becomes Laya's.

**Stays deterministic (safety shield, runs after every Laya output):** legality/collision/budget/fluid-purity/
surface-force/fixture validators; authorization envelopes and Lua `scope_limits{max_count, block_filter}`;
blueprint-ghost-only execution with bot construction; `GameBridge._run_and_collect` collection semantics;
runtime isolation (Nauvis / disposable training / retired legacy); `transition_can_update_policy` /
`transition_can_train_policy` gates (`training/policies.py:21,32`); held-out + baseline promotion
(`training/evaluation.py:92`); checkpoints and rollback. Laya proposes, never executes — the same pattern as the
quarantined `rl_action_proposal.schema.json` (`requires_authorization: const true`). Low `P(act)`, high entropy, or
margin below threshold → deterministic fallback (`DeterministicBaseline`, `PriorityList`,
`rank_research_candidates`) executes instead. Fail-closed on sidecar error/timeout.

## 8. Reusing RL infrastructure (micro-skills + macropolicy split)

- **Training episodes become micro-skill trainers:** placement speed, belt routing, power layout — RL fine-tunes tool
  *parameters* via the existing loop (`run_episode` → `reward_components` → `TrainingStore` → `promotion_decision`).
  A `LayaPolicy` implementing the `policy.select(candidates, observation, seed)` interface
  (`training/episode.py:144`, with `select`/`update`/`to_dict`/`from_dict` per `training/policies.py`) lets Laya
  compete as one policy arm against `DeterministicBaseline`/`DiagonalLinUCB` on identical seeds.
- **The director trains on the macro game:** run-scope rewards (milestone completion + time-to-milestone + resource
  efficiency + failure-class penalties) following the decomposed philosophy of `training/rewards.py`; V3 feature
  vocabulary (`training/features.py:116`) renders candidate numerics; observation dict
  (`training/episode.py:28`) is the numeric core of the Laya `state` JSON.
- **Lineage/promotion unchanged:** Laya checkpoints stored as `policies` rows (algorithm `laya-decision`, config =
  model hash + state-schema version + temperatures + question-pack + guideline-pack versions, parent linkage);
  evaluated by `aggregate_fitness`/`promotion_decision` on frozen held-out seeds; champions table unchanged.
  421M fp16 fits the 8 GB VRAM constraint with gradient checkpointing, scheduled via `ResourcePhaseLock`
  (`training/scheduler.py`).

## 9. Logs become tool-use trajectories (not candidate rows)

The 100s of unclassified failed runs are the seed asset, mined as a **command-level MDP**:

1. **Parse** each run into `(state, tool_call, args, observation_delta, outcome)` sequences from runner logs
   (`RUN START`, `PRIORITY WAIT`, loan/retire lines) + `deterministic-events.jsonl` +
   `deterministic-blockers.jsonl` + `deterministic-mission-state.json`.
2. **Attribute** every terminal/stall into a failure taxonomy — starved-vs-capacity confusion, power brownout,
   anchor-loan deadlock, unroutable corridor, fluid-mix attempt, premature tier request, livelock spin
   (`_livelock_step` :12415), wrong-goal priority — using blocker codes (`mission_state.py:170`), `WorkStateSignal`
   states, and reward-component signatures. Successful segments (followed within N passes by
   `credit_progress_pass` progress) are positive examples; attributed-failure segments are contrastive.
3. **Feed three consumers per failure:** (a) director fine-tune rows — the causal decision gets a negative example
   with the attributed cause in state; (b) guideline patch proposal when no rule covers the cause (§12);
   (c) curriculum entry — spawn disposable scenarios reproducing the failure mode so the next iteration must survive it.
4. **Format:** notebook-compatible `{id, workflow, state, questions, gold}` rows via `laya.common.build_sequence`;
   RL transitions (≈23k, §2.3) additionally yield `(context, K options, hindsight-optimal choice, proper-reward)`
   rows after `transition_can_update_policy` filtering, with `stage_action_trail` pre/post pairs multiplying rows.

## 10. Laya input/output schema (v1)

**Input `state`** (≤1024 tokens trained; truncation priority: objective > goal-gap > stress deltas > trail > rules):

```json
{
  "schema": "laya-factorio-state/1",
  "guideline_pack": {"id": "deterministic-v3", "rules": ["power-first", "repair-before-duplicate"]},
  "run": {"goal": "plastic-bar", "tick": 123456, "surface": "nauvis", "force": "player"},
  "goal_stack": [{"goal": "plastic-bar@0.5/s", "gap": 0.5}, {"goal": "power_sat>=0.9", "gap": 0.28}],
  "numeric_obs": {"delivered_rate_per_tick": 0.0, "resource_remaining": 42000000.0,
                  "capacity_audit_available": 1.0, "productive_ratio": 0.0},
  "stress": {"power_sat": 0.62, "pending_ghosts": 47, "failed_placements": 2, "waits_used": "3/80"},
  "failure_memory": [{"class": "anchor-loan-deadlock", "ticks_ago": 5400}],
  "trail": [{"tool": "build_mining_stage", "where": "site-B", "outcome": "progress"}],
  "eligible_tools": {"k": 9, "pruned_by_validator": 3}
}
```

**`questions`:** one pack per decision point — `serve_goal` (choice over goal stack), `next_tool` (choice over
eligible registry tools), `serve_or_wait` (noul), `interrupt` (noul), `recover` (noul); `choice` criteria are short
`tool_id + signature + key numerics` strings. **Output logged per decision:** `{qtype, candidates[K], probs[K],
top1, margin, entropy, K/255, P_act/P_escalate, temp, policy_id, guideline_pack, verdict, tool_args, why_rule,
report_ids, ctx_hash, next_ctx_hash}`.

## 11. Integration points

1. **Registry + compiler first (deterministic, no Laya):** wrap §3.2 tools with schemas/preconditions/tiers; compile
   §5 rule packs; log every cascade decision as `(state, tool, args, outcome)` — that log is the dataset.
2. **Training seam:** `LayaPolicy` in `training/policies.py`, wired into `run_episode` (`episode.py:144`) as one arm;
   shadow-scored vs baseline/LinUCB on identical seeds.
3. **Deterministic seam:** director sidecar (`laya.load`, preloaded at runner start — cold build costs seconds, then
   ~33 ms/forward) called at the pass boundary in `run()`; one forward pass answers the whole pack.
   Shadow mode first (log Laya bundle vs cascade action + confidence), then co-director (cascade executes on
   agreement or high `P(act)`; disagreements are the highest-value training data), then director-primary with
   deterministic fallback on escalate.
4. **Serving:** loopback sidecar; fail-closed on any error/timeout; per-(question-type, K) temperature from refit.

## 12. Additional logging to collect before training

Per decision (both controllers): `ctx_hash`, eligible + pruned-by-validator tools with features, Laya probs + logits
+ temp + `P_act`, margin/entropy, Laya bundle vs baseline vs executed action, verdict + reason, `why_rule`,
report/execution IDs, outcome + decomposed reward + `next_ctx_hash`. Plus: ECE/margin/escalation-rate dashboards;
remedy/livelock invocation counts; per-(type, K) latency. RL rows extend the transition payload compatibly in
`experience.db`; deterministic rows go to JSONL beside `deterministic-events.jsonl`. Run 1–2 weeks of shadow logging
before the first fine-tune so the dataset contains (context, candidates, both policies' answers, outcome).

## 13. Self-improving docs (concrete loop)

Each guideline carries `rule_id`, version, scope, assertion, and provenance (motivating failures). The precedent
exists: `build_research_packet` / `validate_proposal` / `apply_proposal`
(`training/research/controller.py:52`, `training/research/proposals.py:48`) with allowlisted numeric envelopes first
and code-edit envelopes only in isolated worktrees with paired eval. Guideline proposals follow the same lifecycle —
propose → bounded disposable trial → paired eval → adopt/rollback — recorded in
`TrainingStore.research_proposals` (`training/store.py:217`) for lineage. A patch ships only if runs with it show
fewer attributed failures of its class on held-out seeds with no regression elsewhere: docs earn authority by
measurement, per `docs/system_invariants.md` evidence levels. Model drafts and trials packs autonomously in disposable
runs; promotion to the shipped pack keeps the human-owned evidence bar.

## 14. Staged path: cascade → director → beating Factorio

1. **Log:** registry + guideline compiler + per-decision logger; shadow Laya sidecar (base checkpoint, inference
   only) to validate rendering/latency/K≤20.
2. **Dataset:** ETL (transitions → rows; deterministic evidence → tool-use trajectories + attributions) with
   train/held-out seed splits and a quality report (class balance, exclusion counts).
3. **Distill (director v0):** behavioral-clone fine-tune on trajectories (notebook recipe,
   `max_len=1024`/`head_max_len=256`); temperature refit per (type, K); offline gates — next-tool accuracy,
   ECE < 0.15 (target 0.08), zero post-shield illegal calls.
4. **Co-director:** agreement-gated execution; disagreement mining loop.
5. **Director-primary in disposable scenarios + fresh-run milestones** (generalize the plastic-reliability pattern —
   3 distinct fresh successes — per rung): burner → red/green → oil/plastic/sulfur → blue → modules/beacons →
   yellow/purple → rocket. RLCD on-policy with run-scope rewards; 100-terminal-episode advance gate; paired greedy
   held-out eval.
6. **Guideline self-improvement live** (§13); **population** of director checkpoints competing on held-out seeds with
   elitist promotion; deterministic cascade retained forever as the baseline every challenger must beat.
7. **Real-base authority** only through explicit bounded trials with rollback plans per `docs/system_invariants.md`.

## 15. Open questions and unresolved items

- Full `apply_remedy` enumeration and the `run()` tail past ~line 13013 need a final read.
- Campaign-supervisor (`tools/campaign_supervisor.py`, `tools/opencode_campaign_orchestrator.py`) internals and
  helper-agent packet details (`helper_agent/`, `Helper_Agent/`) were skimmed, not fully inventoried.
- Per-K temperature formula needs the `laya` package source (PyPI fetch blocked in sandbox).
- Guideline-pack versioning schema and `rules_table.json` location are proposed, not yet ratified.
- Rocket-ladder scenario families beyond mining/furnace do not exist yet; each rung needs a scenario generator +
  sustain gate before director training can climb it.

