<!-- Path: docs/archive/rl/Laya_Session_Handoff_2026-09-25.md | Purpose: Complete product, architecture, implementation, evidence, training, and resume handoff for the Laya migration. -->

# Laya session handoff — 2026-09-25

## Read this first

The product goal is to replace the hand-authored deterministic decision cascade
with a fast local Laya director **after** Laya proves that it is better. The
presentable deterministic workflow, Operations Console, saves, controls, and
recovery path remain the usable incumbent and rollback until that proof exists.

The repository has a committed Laya foundation and an offline-tested disposable
primary runner. It does **not** yet have a trained full-workflow director,
connected-factory acceptance evidence, or real-base authority. Do not report
that Laya has replaced the deterministic workflow.

Canonical implementation commit:

~~~text
95d0387693d2fd55470467e1ce30d5515155c90b
feat(RL Laya): add independent director foundation
~~~

The branch is `codex/deterministic-checkpoint-fleet`. The worktree also contains
substantial unrelated and earlier uncommitted work. Preserve it.

## The user's goal

The user wants the best autonomous Factorio agent: one able to start from a base
save and decide how to progress across unfamiliar seeds, terrain, resources,
layouts, inventories, research states, bottlenecks, and failures.

The motivation is that the deterministic controller has accumulated thousands
of changes. Some assumptions conflict, some were created for much easier
bootstrap conditions, and each new failure tends to produce another branch.
That system is expensive to extend, hard to debug, and brittle when the seed or
base conditions change.

The intended replacement is:

- Laya chooses goals and priorities.
- Laya chooses whether to build, repair, reroute, expand, upgrade, research,
  wait, interrupt, retire, or recover.
- Laya chooses among current legal sites, scales, routes, tiers, and variants
  using live facts, documented guidelines, recent decisions, measured progress,
  and end goals.
- Existing deterministic work becomes a mechanics toolkit: observation,
  candidate enumeration, geometry realization, validation, exact execution,
  measurement, transaction identity, and rollback.
- RL is the disposable failure laboratory. It reproduces drift and failures,
  evaluates controlled alternatives, produces clean data, and feeds later
  Laya fine-tuning.
- Human guidance stays high level. The user can nudge direction without teaching
  every placement.
- Documentation and toolkit defects stay separate from policy mistakes.

The desired long-term loop is:

~~~text
observe world + goals + memory + relevant guidelines
  -> compile current legal options
  -> Laya chooses one bounded decision
  -> hard validation and exact tool execution
  -> measure progress, cost, throughput, safety, and failure
  -> update durable trajectory memory
  -> repeat

failure or objective drift
  -> immutable failure packet
  -> faithful disposable replay
  -> controlled alternatives
  -> root-cause classification
  -> clean policy data OR reviewed tool/docs proposal
  -> fine-tune and calibrate challenger
  -> held-out and incumbent comparison
  -> promote, reject, or roll back
~~~

## Coexistence contract

1. Keep the deterministic workflow operational and presentable until Laya
   outperforms it under matched conditions.
2. Laya uses a separate explicit disposable entry point. It does not redirect
   the deterministic runner or dashboard.
3. The Laya runner has no hidden deterministic or LinUCB strategy fallback.
4. Mechanics may reject illegal actions but may not strategically choose among
   remaining legal alternatives.
5. Comparisons pin seed, initial save/state, inventory, research, objective,
   material/time budgets, Factorio version, and mod set. An older easier start
   is not replacement evidence.
6. Shadow completion belongs to the executing baseline, not Laya.
7. Real Nauvis authority requires a separately promoted checkpoint, narrow
   action scope, observable results, and rollback.
8. The deterministic incumbent remains available as rollback after any future
   promotion.

The adopted plan is `docs/rl/Laya_Implementation_Plan.md`; the broader design is
`docs/rl/Laya_director_final_plan.md`.

## Authority split

Laya owns goal arbitration, operation selection, site/scale/route/tier/variant
selection, repair versus duplicate, upgrade versus expand, act versus wait,
recovery, interruption, and factory direction.

The toolkit owns current observations, recipe/prototype facts, enumeration of
legal alternatives, realization of the exact selected plan, isolation,
authorization, collision/material/fluid/power/ownership/budget validation,
execution, transaction receipts, re-observation, measurement, and cleanup.

Historical modules mix policy and mechanics. Reuse them per responsibility;
never import the old controller as Laya's hidden brain.

## Decision protocol

Laya is a typed decision model. It does not emit arbitrary console commands,
raw Lua, free-form coordinates, or an unbounded factory plan. The runner asks
one bounded question, executes one bounded transaction, observes again, and
asks the next question.

The implemented vocabulary includes:

~~~text
serve_goal
choose_operation
select_site
select_scale
select_route
select_research
select_upgrade
select_candidate
wait_or_act
recovery
~~~

The first primary runner requires `choose_operation`, `select_candidate`,
`wait_or_act`, and `recovery` in the checkpoint capability manifest. A
manifest is an evidence-backed assertion of trained scope. Never fabricate one
to bypass the gate. Untrained disposable data collection uses the separate
exploration gate (`--mode laya-exploration-disposable`, see
`docs/rl/training.md`), which asserts no capability and is never comparison or
promotion evidence.

## Context, memory, and documentation

The active profile uses a 1,024-token request with a 256-token decision head.
Full observations, plans, reports, histories, hashes, and provenance stay
outside the prompt in durable evidence.

`training/laya_sequence.py` defines `factorio-decision-v2`. Training and
inference share this builder. It:

- preserves objective, goals, acceptance gap, active transaction, failures,
  compact history, relevant guidelines, budgets, and decisive option features;
- factors shared features and uses compact columnar records;
- keeps plans, signatures, registry manifests, and full provenance external;
- rejects overflow rather than silently truncating evidence.

Old trained adapters using the truncating representation are rejected by
default. Legacy evaluation/shadow requires `--allow-legacy-sequence` and is
diagnostic only.

`docs/rl/laya_guidelines.json` is the reviewed guideline catalog.
`orchestrator/laya_knowledge.py` verifies each quotation against active source
documents and records their hashes. It excludes archived advice and does not
derive policy from historical code. Coach notes may add bounded soft goals but
cannot select an action or bypass validation.

## Implemented components

### Contracts and registry

- Versioned state, decision, case, registry, guideline, Coach, failure, and
  improvement-proposal schemas.
- Immutable tool definitions with explicit schemas, predicates, validators,
  authorization, reports, risk tier, and rollback metadata.
- Non-ranking operation compilation and validator pruning.
- A decision-authority inventory in
  `docs/rl/laya_decision_authority_inventory.md`.

### Independent disposable runner

- `training/laya_runner.py`: model-owned loop, durable journal, bounded
  decisions, no strategy fallback, failure packets, cleanup, restart protection.
- `training/laya_toolkit.py`: mining/furnace mechanics, current-option
  compilation, fresh pre-execution plan/scope/budget validation.
- `tools/run_laya_director.py`: explicit
  `--mode laya-primary-disposable`, configured worker/endpoint matching,
  checkpoint capability gate, and no production mode.
- Pending transactions are journaled before mutation. Ambiguous interrupted
  transactions cannot be automatically repeated.

Current scope is only disposable `mining_delivery`, `furnace_refining`,
bounded waiting, safe escalation, and one explicit retry path. It does not yet
operate a connected factory, research tree, chemicals, mall, power expansion,
logistics network, or real save.

### Data and training integrity

- Future terminal failures are removed from pre-action state.
- Failed actions are not positive labels without reviewer evidence or
  independently executed same-state counterfactual evidence.
- Unknown all-zero targets are excluded rather than made uniform preferences.
- Successful historical actions remain weak imitation evidence.
- Case, question, source-quality, and temporal weights affect the loss,
  including at micro-batch one.
- Dataset builds report emitted and excluded rows with reasons.

The cleaned v2 export contained:

~~~text
2,367 source transitions
2,111 emitted successful mining select_candidate cases
256 timeout cases excluded for unsupported failed-action labels

train        1,495
calibration    163
held_out       308
test           145
~~~

All 2,111 cases fit the v2 sequence. They are only two-option mining
`select_candidate` cases and cannot train a complete director.

### Evidence and failure learning

- Shadow artifacts are exclusive and cannot overwrite previous evidence.
- Decisions, full baseline transitions/scenarios, and summaries are linked but
  separate.
- Laya selection coverage is separate from baseline completion.
- Failure packets preserve objective, initial state, decisions, provenance,
  replay limits, and evidence.
- Improvement proposals and an append-only ledger exist.

Automatic replay orchestration, fidelity checks, controlled alternative
execution, cause attribution, and proposal acceptance are not yet connected
end to end.

### Comparison gate

`training/laya_comparison.py` and `tools/compare_laya_workflow.py` require a
frozen suite, all expected pairs including failures, matched conditions,
unique primary-execution artifacts, disposable identity, zero hard violations,
and per-case non-regression.

Passing yields only `candidate_for_review`; it cannot switch controllers or
grant production authority.

## Verification reached

Before the foundation commit:

- 188 focused Laya and related training tests passed in the working tree;
- the scoped commit passed checks in a separate repository copy;
- real-tokenizer checks covered all cleaned cases and the document-guided runner;
- Python compilation and diff checks passed;
- no primary live Factorio episode was run;
- no real save or deterministic workflow was changed.

Commit `95d0387` contains 59 files, 9,503 insertions, and 17 deletions. It was
not pushed in this session.

## Previous model and evidence

`data/laya-checkpoints/laya-flex-v1` was trained on 29,709 legacy mining
candidate cases.

~~~text
held-out accuracy  65.856%
test accuracy      61.290%
inference failures 0
~~~

An earlier overnight shadow had 280 episodes, 279 decisions, 224 selected
agreements, 55 escalations, one episode error, and zero failed baseline
transitions. The baseline executed; this was not autonomous Laya evidence.

A later ten-episode batch produced ten Laya context-budget escalations while the
baseline completed. That exposed the representation mismatch fixed by v2. The
old checkpoint remains diagnostic-only.

## Current training state — important

A v2 mining-selector run launched on 2026-09-22 as
`laya-director-v2-training.service` using BF16, micro-batch 2, accumulation 16,
checkpointing off, compile + FlexAttention, option width 2, four epochs, and
checkpoint interval 50.

The run is **not active and did not finish**. It produced no final adapter and no
evaluation reports. The stale `training-status.json` still says training; do
not use it as liveness evidence.

Last observed progress:

~~~text
epoch                 3 / 4
optimizer step        123 / 188
examples/second       2.255
peak VRAM             5.53 GB
mean loss             0.6466
estimated remaining   922 seconds at that moment
last update           2026-09-22 17:58 IST
~~~

Entropy collapsed to about `2.16e-6`; held-out calibration and accuracy must be
reviewed carefully before considering the result useful.

Durable snapshots exist at steps 50, 94, and 100. The latest complete resume
point is:

~~~text
data/laya-checkpoints/laya-director-v2-training/checkpoints/step-00000100
adapter SHA-256:
dadb0f137322c6383f5cb19f4675485282669f79d322dd885e87b0a99eda4d9c
~~~

Resuming loses uncheckpointed steps 101–123.

The cleaned dataset was in `/tmp/laya-audited-cases-v2.jsonl` and is now gone,
likely after a host restart. Rebuild it before resuming.

## Exact resume sequence

Rebuild and confirm 2,111 emitted / 256 excluded:

~~~bash
.venv/bin/python tools/build_laya_dataset.py \
  --database data/training/experience.db \
  --derive-seed-splits \
  --step-level \
  --output /tmp/laya-audited-cases-v2.jsonl
~~~

Run the same host GPU preflight:

~~~bash
LAYA_MICRO_BATCH=2 \
LAYA_GRADIENT_ACCUMULATION=16 \
LAYA_GRADIENT_CHECKPOINTING=0 \
LAYA_TORCH_COMPILE=1 \
LAYA_ATTN_IMPLEMENTATION=flex_attention \
LAYA_FIXED_OPTION_WIDTH=2 \
.venv/bin/python tools/laya_preflight.py
~~~

Resume with the persistent service pattern:

~~~bash
mkdir -p logs/laya-director-v2 data/laya-checkpoints

systemd-run --user \
  --unit=laya-director-v2-training.service \
  --collect \
  --working-directory="$PWD" \
  --property=Restart=no \
  --property=StandardOutput=append:"$PWD/logs/laya-director-v2/service.log" \
  --property=StandardError=append:"$PWD/logs/laya-director-v2/service.log" \
  --setenv=LAYA_CASES_PATH=/tmp/laya-audited-cases-v2.jsonl \
  --setenv=LAYA_MODEL_DIR="$PWD/data/laya-base" \
  --setenv=LAYA_CHECKPOINT_DIR="$PWD/data/laya-checkpoints/laya-director-v2-training" \
  --setenv=LAYA_LOG_DIR="$PWD/logs/laya-director-v2" \
  --setenv=LAYA_MICRO_BATCH=2 \
  --setenv=LAYA_GRADIENT_ACCUMULATION=16 \
  --setenv=LAYA_GRADIENT_CHECKPOINTING=0 \
  --setenv=LAYA_TORCH_COMPILE=1 \
  --setenv=LAYA_ATTN_IMPLEMENTATION=flex_attention \
  --setenv=LAYA_FIXED_OPTION_WIDTH=2 \
  --setenv=LAYA_CHECKPOINT_INTERVAL=50 \
  --setenv=LAYA_CHECKPOINT_KEEP=3 \
  --setenv=LAYA_RESUME_CHECKPOINT=latest \
  --setenv=LAYA_MAX_RETRIES=3 \
  --setenv=LAYA_RETRY_DELAY_SECONDS=5 \
  /usr/bin/bash "$PWD/scripts/run_laya_training_resumable.sh"
~~~

The launch scripts are uncommitted working-tree files. Preserve and review them
before using this command in a clean checkout.

Completion requires a root adapter/config, `evaluation-held-out.json`,
`evaluation-test.json`, v2 sequence metadata, zero inference failures, and a
truthful complete status. This remains a mining selector; do not grant it the
full-director capability manifest.

## How the system is supposed to run

### Offline preparation

1. Collect complete decision traces from deterministic behavior, Coach labels,
   primary Laya episodes, and controlled replays.
2. Classify by decision kind and failure cause.
3. Exclude unsafe, malformed, future-leaking, and unsupported labels.
4. Keep related replay variants in the same split.
5. Train on the shared v2 representation.
6. Calibrate on the calibration split.
7. Evaluate by decision family, confidence, latency, and failures.
8. Issue capabilities only for demonstrated decision families.

### Shadow

1. Start an explicit disposable worker.
2. Run Laya on current candidates while the incumbent executes.
3. Preserve probabilities, escalation, all options, baseline action, full
   transition, scenario, and outcome.
4. Attribute completion to the baseline.
5. Mine disagreement/failure cases for replay rather than blind imitation.

### Primary disposable execution

1. Invoke `tools/run_laya_director.py --mode laya-primary-disposable` with an
   exact configured worker, scenario, v2 checkpoint, and valid capabilities.
2. Provision one disposable episode.
3. Build bounded state from world, memory, failure, budget, and guidelines.
4. Compile legal options without ranking.
5. Ask Laya one typed question.
6. Journal the decision and transaction before mutation.
7. Re-observe and verify option identity, signature, plan hash, scope, area,
   entities, and budget.
8. Execute the exact plan, wait, or escalate.
9. Measure, link outcome, and repeat.
10. Recycle the disposable episode and report cleanup separately.

### Improvement cycle

This is the intended but incomplete automatic cycle:

1. enqueue failure packets;
2. reproduce exact initial state;
3. prove replay fidelity;
4. execute controlled alternatives;
5. classify policy/observation/tool/validator/docs/environment cause;
6. release labels only for measured policy mistakes;
7. route other defects to separate proposals/tests;
8. train a challenger;
9. run held-out non-regression and incumbent comparisons;
10. preserve parent checkpoint and rollback lineage.

Laya is a classifier, not a prose/code author. Documentation and toolkit changes
need a separate offline authoring and review process.

## What remains

Highest priority:

1. Resume and finish the v2 mining-selector run; audit its overconfidence and
   held-out/test results.
2. Collect real data for `choose_operation`, `wait_or_act`, and `recovery`,
   then goal/site/scale/route/research/upgrade.
3. Add furnace cases; the current cleaned dataset is mining only.
4. Train and calibrate a genuinely multi-decision checkpoint.
5. Issue a truthful capability manifest and run primary disposable episodes.

Mechanics still needed include power, logistics, construction coverage, belt and
pipe routing, production chains, repair, expansion, upgrades, retirement,
fluids/oil, research, persistent goals, and competing priorities. Each binding
must expose facts and alternatives without carrying hidden old strategy.

Evaluation must compare simple baselines, training LinUCB, and the actual
deterministic workflow as distinct comparators under identical starts. Connected
factory evidence is required before full-workflow parity can be claimed.

## Worktree and lifecycle warnings

- The repository is intentionally dirty. Do not reset it or sweep unrelated
  changes into a commit.
- Earlier Coach/Observer and training episode/schema work remains uncommitted.
- The committed foundation itself required no Factorio or deterministic restart.
- Resuming GPU training requires no Factorio lifecycle action.
- Shadow/primary disposable runs require the correct training worker.
- Lua changes require deployment and worker/GUI restart; no Lua deployment was
  part of the foundation commit.
- Nothing in this handoff authorizes real Nauvis mutation.

## Suggested first prompt for the next task

~~~text
Read docs/archive/rl/Laya_Session_Handoff_2026-09-25.md completely. Preserve the
deterministic workflow and every unrelated worktree change. Rebuild the cleaned
v2 dataset, verify 2,111 emitted and 256 excluded, then resume
laya-director-v2-training from the durable step-100 checkpoint. Own the terminal
and service lifecycle. When training and held-out/test evaluation finish, audit
calibration and overconfidence before granting any capability. Do not fabricate
a full-director manifest and do not touch real Nauvis.
~~~

