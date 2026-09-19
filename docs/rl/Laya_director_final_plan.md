<!-- Path: docs/rl/Laya_director_final_plan.md | Purpose: Canonical plan to replace the deterministic policy cascade with a fine-tuned Laya autonomous director. -->

# Laya autonomous director — final implementation plan

**Status:** adopted product direction; M0 contracts and the fail-closed registry
seam are implemented.  This document supersedes the three exploratory Laya
classifier plans for future design work.  Existing runtime contracts remain in
force until each is explicitly migrated and verified.

## 1. Mission

Build a fast local Factorio agent that learns to run a factory across changing
seeds, terrain, resources, layouts, budgets, and failure histories.  It must
choose what to do, when to do it, how much to do, and where to do it from live
observations and versioned guidelines.  It improves through measured,
reproducible disposable episodes rather than accumulating a new hand-written
branch after each real-base failure.

Laya is the policy brain.  It is not a replacement for the learning system:
the training environment, reward store, held-out evaluation, policy lineage,
and promotion process remain the way the director learns and earns authority.

## 2. Authority split

The current deterministic cascade is being retired as a decision maker.  No
new product behavior should be added as a priority rule, special-case recovery
branch, fixed layout preference, or one-off seed workaround.

The retained deterministic subsystem is a world interface and safety shield:

- observe Factorio facts and preserve a durable mission/failure history;
- enumerate seed-specific feasible operations and their parameter choices;
- synthesize geometry for a selected operation and validate it;
- enforce surface/force isolation, authorization, actual-item funding,
  blueprint-ghost construction, collision legality, fluid purity, budgets,
  ownership, and rollback scope;
- execute a validated transaction, collect its reports, and measure outcome.

It does **not** rank goals, select a strategy, decide to wait versus expand,
choose repair versus duplication, choose an action site, or silently substitute
one strategy for another.  Validators may reject an operation or require a
safe stop; they may not make a policy choice.

The old cascade stays only as a frozen comparison baseline during evaluation.
Once the director becomes primary, a sidecar/model failure produces a safe
observe/wait/escalate result, not a hidden return to the old cascade.

## 3. Director decision protocol

Laya is a non-autoregressive classifier, so it never emits raw text commands,
coordinates, recipes, or arbitrary arguments.  The system uses short,
hierarchical, dynamically compiled choice questions at every decision boundary.
Each answer is logged with its probability distribution and calibration data.

```text
live observation + goal stack + recent outcome trail + selected guideline pack
                                  |
                                  v
                legal operation compiler / tool registry
                                  |
                                  v
  Laya: goal -> operation family -> concrete transaction -> site/size variant
                                  |
                                  v
      deterministic validator -> bot-built execution -> measured outcome
                                  |
                                  v
       trajectory store -> reward / failure attribution -> policy training
```

The director normally selects one bounded, reversible transaction and then
re-observes.  This receding-horizon control avoids pretending that a classifier
can safely invent an unbounded command bundle, while still letting it create
novel multi-step strategies through successive choices.

Each decision pack includes at least:

- `serve_goal`: choose a goal or recovery obligation from the persisted stack;
- `next_operation`: choose an eligible operation family;
- `variant`: choose a compiled plan/site/scale/route/tier alternative;
- `continue_or_wait` and `interrupt`: calibrated boolean choices;
- `recovery`: choose among repair, reroute, resize, retire, defer, or escalate
  when the measured result is unhealthy.

Choices are restricted to a small option set (normally at most 12; never more
than 20).  Large spaces are split coarse-to-fine without using a hand-written
priority score.  `score` questions are avoided unless a held-out study proves
they add value; Laya's `choice` and boolean primitives are the default.

## 4. Tool registry and operation compiler

Create one registry for every capability the director may request.  An entry
contains:

```text
tool_id, operation schema, read dependencies, eligibility predicates,
parameter generators, expected-effect measurements, cost estimate, risk tier,
required validators, execution authorization, report readers, rollback method
```

The registry is not an action catalog ranked by deterministic code.  It is a
declarative inventory.  Parameter generators survey the present world and
produce alternatives such as legal mining sites, expansion sizes, transport
routes, repair targets, research candidates, or validated build-plan variants.
The director makes the choice among them.

Every generated operation carries a stable signature, a plan/configuration
hash, precise scope, and expected measurements.  It is revalidated immediately
before execution because the live world can change between selection and use.

The initial operation families are: observe/diagnose, mine, smelt, assemble,
route, power, logistics, research, repair, upgrade, retire, reserve-space, and
wait/escalate.  Each begins with the capabilities that are actually executable;
a catalog-known but unexecutable operation is observation-only, never a choice.

## 5. Guideline packs and recursive improvement

Guidelines are executable context, not brittle controller code.  Replace
scattered prose with compact, task-scoped packs containing stable IDs:

```text
rule_id, scope, kind(hard|soft), statement, rationale, test assertion,
provenance, version, promotion evidence
```

Hard rules compile to the safety shield and are never negotiable.  Soft rules
are injected into the relevant Laya state and translated into reward features:
for example direct transport, repair before duplicate capacity, expansion
space, short routes, throughput, material efficiency, and recoverability.

The agent may propose a guideline or curriculum change when failure attribution
shows an uncovered recurring cause.  It cannot edit authoritative docs directly.
The proposal lifecycle is:

```text
attributed failure -> proposed rule/scenario -> disposable reproduction
-> paired held-out evaluation -> versioned promotion or rejection
```

Promotion requires a reduction in the targeted failure class with no safety or
held-out regression.  The resulting record identifies the model, dataset,
scenario seeds, guideline version, and parent policy so an incorrect change can
be rolled back.

## 6. State, evidence, and dataset contracts

Define versioned `laya_state`, `laya_case`, `laya_decision`, `tool_registry`,
and `guideline_pack` schemas before model work.  The compact state renderer has
a fixed ordering and a trained limit of 1,024 tokens.  It includes only:

- objective, goal stack, acceptance gap, and current transaction;
- normalized production, power, logistics, stock, resource, space, and budget
  facts, preserving unknown as unknown;
- the last few decision -> outcome records and active typed failures;
- the task-scoped guideline rules and generated option summaries;
- schema/model/guideline/tool-registry provenance.

Record at every decision point: complete eligible and validator-pruned options,
their features and hashes, Laya logits/probabilities/temperature/margin/entropy,
the selected and executed action, reports, next state, reward components, and
the eventually attributed outcome.  Deterministic-run logs are contrastive and
chronologically weighted evidence; new structured telemetry and disposable
episodes are the primary dataset.

Train/held-out splits are by scenario family and seed, never random rows from
the same trajectory.  Contract, identity, fixture, execution, and safety
failures remain excluded from policy credit; they instead improve the shield,
operation compiler, or environment.

## 7. Learning system

Use the Laya English checkpoint and specialize it for this domain, targeting
`max_len=1024` and `head_max_len=256` when the measured memory envelope permits,
with post-training temperature fitting per question type and option-count
bucket.  Zero-shot behavior is not acceptable.

### Local RTX 4060 training profile

The upstream `laya_finetune_typed_decisions_2xT4_kaggle.ipynb` is the
reference implementation to adapt, not run unmodified.  It explicitly requires
two GPUs, uses DDP across two 16 GB T4s, fully trains the 421M-parameter model,
uses fp16 plus gradient checkpointing, and has a per-GPU micro-batch of 8.
DDP replicates the complete model and optimizer on each GPU; it does not make
the full training footprint fit in an 8 GB RTX 4060 Laptop GPU.

The default local path is therefore **single-GPU LoRA/RLCD**, with the Laya
decision head fully trainable and adapters on the ModernBERT encoder.  It keeps
the notebook's typed sequence format, logit-noise group reward, proper-scoring
rule objective, soft cross-entropy guidance, and post-training calibration,
while training only the adapter and head weights.  It is the practical first
fine-tune on a 4060; a full-model run is an optional later experiment, not an
assumption.

**Confirmed laptop envelope (2026-09-19):** RTX 4060 Laptop GPU with 8,188 MiB
VRAM.  The supplied `nvidia-smi` snapshot showed 1,163 MiB in use, including
about 956 MiB by Factorio, leaving roughly 7,025 MiB before framework overhead.
Training therefore requires a scheduled, GPU-exclusive window: stop Factorio
and other GPU applications first, verify free memory again, and restart the
game only after the training process has released the device.  This is an
operational prerequisite, not a reason to weaken the policy or safety design.

The implementation must provide a reproducible `laya-4060-lora` profile:

- one CUDA process; no DDP, and no concurrent Factorio GPU workload or other
  model job while the training phase lock is held;
- bf16 when the installed CUDA/PyTorch stack supports it, otherwise fp16 with
  gradient scaling; gradient checkpointing is required;
- start with micro-batch 1, gradient accumulation 32, RLCD group size 4,
  four epochs, and a 512-token memory smoke test; then probe 768 and 1,024
  tokens at micro-batch 1 before selecting the production context limit;
- train the decision head and LoRA adapters with separate learning rates;
  choose target modules only after inspecting the installed Laya/ModernBERT
  module names, and record adapter rank, alpha, dropout, target modules, base
  model hash, package versions, CUDA version, and peak allocated memory;
- persist checkpoints locally with the dataset, schema, guideline-pack, and
  registry versions.  Publishing to a model hub is opt-in and never part of a
  training run by default.

Before a full run, execute a local preflight that verifies the GPU/driver,
CUDA, bf16 support, model load, a forward/backward/optimizer step, and peak
memory at each context length.  Reject a profile that has less than 10% free
VRAM after a representative step.  The reported driver is NVIDIA 610.57.04
with CUDA UMD 13.3, but PyTorch/Laya compatibility and actual training headroom
still require this preflight at implementation time.

The Kaggle notebook's stated four-to-five-hour 2xT4 runtime is not a local
runtime estimate.  The 4060 profile records examples/sec, optimizer steps/sec,
and peak memory during a 100-step pilot, then computes a run estimate before
the full fine-tune.  If 1,024 tokens cannot fit, the compact renderer must be
made smaller through measured feature ablation; it must not silently omit
objective, safety, option, or outcome fields.

Calibration and evaluation are stricter than the reference notebook: reserve
separate seed-family splits for training, calibration, and final evaluation.
Do not calibrate on a subsample of the training items.  Record exact-choice
accuracy, advantage/regret against alternatives, Brier/NLL, ECE by question
type and option-count bucket, latency, correct-escalation rate, and zero
safety-violation rate.  Promotion is based on Factorio held-out outcomes, not
the generic typed-decisions benchmark score.

Learning proceeds in three complementary modes:

1. **Distillation:** successful and contrastive historical trajectories teach
   the initial decision vocabulary, without treating old policy actions as
   ground truth.
2. **Counterfactual preference:** same-seed alternatives and decomposed rewards
   create conservative option preferences; unknown options are never labelled
   failures merely because they were unselected.
3. **On-policy RLCD:** populations of Laya checkpoints choose actions in
   disposable Factorio episodes, learn from measured rewards and attributed
   failures, and compete against frozen baselines on held-out seeds.

The existing transition store, reward decomposition, lineage, cohort handling,
and promotion mechanism are reused after their contracts are extended.  A
`LayaPolicy` is a policy family, not an external advisory service.

## 8. Curriculum and success definition

The curriculum must train macro decisions as well as geometry micro-skills.
Every family randomizes seed, terrain, resource quality, clutter, fixtures,
demands, budgets, prior damage, available tiers, and research state.  It grows:

1. extraction, delivery, power, and simple production;
2. multi-stage solid and fluid production, site/route/scale choices;
3. damaged-factory diagnosis, repair, rerouting, retirement, and recovery;
4. competing goals, resource scarcity, research, upgrades, and expansion;
5. full science progression, modules/beacons, and rocket-scale factory goals.

Advancement requires sustained objective completion, zero safety violations,
non-regression on earlier families, reproducible held-out evidence, and a
measurable advantage over the frozen deterministic baseline.  "Beat Factorio"
means reliably reaching these escalating objectives across unseen randomized
worlds with better time, throughput, resilience, and lifecycle cost—not merely
repeating one successful base layout.

## 9. Delivery milestones

### M0 — contracts and direction lock

Add the five schemas, document the authority split, identify every existing
policy branch to retire, and add tests proving the registry can only expose
authorized executable operations.  Freeze new deterministic decision logic.

Implemented: versioned state/case/decision/registry/guideline schemas, strict
contract fixtures, the decision-authority inventory, and an immutable registry
definition seam that exposes only executable operations carrying explicit
authorization metadata.  This is a definition boundary only; runtime option
compilation and decision telemetry begin in M1.

### M1 — registry, renderer, and telemetry

Implement the declarative registry, seed-specific operation compilers, compact
state renderer, guideline-pack compiler, and append-only decision evidence in
both runtimes.  Run without changing action selection.  Acceptance: each
decision has reproducible context/options/outcome linkage and no hidden
priority selection in the registry.

### M2 — data and offline director

Build ETL, dataset-quality reporting, seed-family held-out splits, and the
local Laya serving/training path.  Implement the `laya-4060-lora` preflight and
100-step performance pilot before fine-tuning director v0 and calibrating it.
Acceptance: an evidence-backed local memory/runtime profile, materially above
majority and frozen-baseline imitation metrics, calibrated held-out confidence,
stable latency, and no illegal tool proposals.

### M3 — shadow and disposable co-director

Run Laya on every decision in shadow, then let it execute only in disposable
episodes.  Mine disagreement cases, improve candidate expressiveness and
observations, and add scenario reproductions for attributed failure families.
Acceptance: Laya beats the baseline on frozen seeds with zero safety violations
and reproducible lineage.

### M4 — director-primary disposable factory

Remove the deterministic cascade from the disposable controller path.  Laya
owns the full goal/action/site/scale/recovery loop; unavailable or uncertain
model states safely escalate rather than call the cascade.  Acceptance: three
independent fresh successes at each curriculum rung and no regression in prior
rungs.

### M5 — bounded real-base authority

Only after explicit authorization, promote one director checkpoint to a
narrow, observable, rollback-capable real-base scope.  Expand scope by
evidence, not confidence alone.  The deterministic baseline remains available
for comparison, not as a hidden policy fallback.

## 10. Implementation rules

- Fix deterministic code only to expose reliable observations, compile/validate
  operations, preserve hard safety, or repair a training/runtime blocker.
- Do not manually repair a real factory to create positive evidence.
- A model proposal never bypasses the production promotion path.
- No documentation or model checkpoint is authoritative without its versioned
  evidence and rollback parent.
- Report the highest evidence level reached: unit, disposable integration,
  held-out evaluation, deployed runtime, or observed live result.
