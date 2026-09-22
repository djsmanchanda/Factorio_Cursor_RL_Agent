<!-- Path: docs/rl/Laya_Implementation_Plan.md | Purpose: Preserve the usable deterministic workflow while implementing and evaluating an independent Laya director. -->

# Laya implementation and coexistence plan

Adopted 2026-09-22. This supplements `Laya_director_final_plan.md` and supersedes
its earlier language suggesting that the current deterministic workflow should
be retired before Laya outperforms it.

## Product contract

- Keep the presentable deterministic runner, Operations Console, source save,
  controls, and recovery workflow usable. Do not redirect their entry points.
- Develop Laya behind a separate, explicitly selected disposable runner. A
  comparison run uses a frozen snapshot of the maintained incumbent; freezing
  an evaluation snapshot does not mean abandoning the working product.
- Replace the incumbent only after matched-condition held-out results establish
  better outcomes, no safety violations, non-regression, and a separate
  authorized production promotion. Retain the incumbent as rollback.
- Git history is evidence, not policy. Reuse a mechanic only after documenting
  its current input/output contract, assumptions, validators and observable
  result. Historical easy-start inventory or research is never an implicit
  prerequisite or a substitute for the current reduced-start contract.

## Ownership

Laya chooses priorities, operations, variants, wait/act and recovery. The toolkit
observes facts, enumerates legal alternatives, realizes the selected plan,
enforces hard rules, executes that exact plan and measures the result. No
strategy fallback calls the deterministic cascade. Unsupported operations are
explicit capability gaps. Repair tools must not quietly rebuild or duplicate.

Authoritative documents supply scoped, source-verified guidelines. External
memory retains full observations, goals, transactions, attempts and outcomes.
Only a compact decision projection enters the model; provenance and full
evidence remain in the journal. The protocol describes transaction boundaries,
not a prescribed recipe sequence.

## Ordered implementation

1. **Trustworthy decision and learning boundaries.** Use the same non-truncating
   token sequence in training and inference; version the representation and
   reject incompatible checkpoints. Remove future-outcome state leakage and
   fabricated positive labels for failures. Apply data-quality weights. Keep
   failures as evidence until measured alternatives or explicit review justify
   a preference.
2. **Independent executable director.** Add a separate disposable CLI and
   observe/choose/validate/execute/measure loop with durable decision memory,
   explicit model capabilities, scoped toolkit bindings and no baseline
   fallback. Begin with the existing mining and furnace mechanics. These are
   curriculum families, not proof of a complete connected factory.
3. **Evidence and failure collection.** Preserve unique run artifacts, full
   transitions, pre/post observations and automatic failure packets. Persist
   pending transactions before execution, refuse ambiguous restart replay,
   and make interruption/cleanup status visible.
4. **Matched evaluation.** Compare independent baseline/challenger executions
   under identical seed, initial state, starting inventory/research, objective,
   material/time budgets and game/mod versions. Distinguish the training
   LinUCB comparator from the actual deterministic factory workflow. Baseline
   completion during shadow is never credited as Laya execution success.
5. **Broader learned behavior.** Extract validated power, logistics, transport,
   repair, expansion, assembly, fluids and research mechanics. Add competing
   goals, dynamic demand and constrained starts. Replace narrow candidate
   catalogs with composable parameters and edits, preserving exploration.
6. **Closed improvement cycle.** Reproduce captured failures in disposable
   worlds, verify replay fidelity, run controlled alternatives, attribute cause,
   release clean labels, train/calibrate a challenger, evaluate held-out families,
   and record promotion or rejection. Observation/tool/doc defects create
   separate reviewed proposals. A classifier requires an explicit offline
   authoring process for new prose/code; it cannot silently author either.

Steps 1–4 are the first implementation tranche. Steps 5–6 require additional
mechanics, trained checkpoints and observed live acceptance; schema or mocked
test completion does not count as those milestones being achieved.

## Acceptance and comparison

- Run focused offline integration with deliberately disagreeing model choices,
  rejected/unknown options, uncertain decisions, changed demand, repeated waits,
  failures and interrupted execution. Verify no baseline action executes.
- Verify real tokenizer behavior without loading a GPU model, including overflow
  and retained objective/failure/rule/option context.
- Train a new representation-compatible checkpoint only from reviewed datasets.
  Existing `laya-flex-v1` is mining-choice-only and remains preserved; old
  checkpoint accuracy is not a general-director promotion gate.
- Evaluate several independent held-out seeds per family. Require reproducible
  sustained completion, zero hard violations, and improvements in completion,
  time or lifecycle cost without regressing prior families. Record escalation
  separately. Keep all replay relatives in one dataset split.
- A full-factory promotion needs matched evidence against the actual incumbent
  from the same starting-save contract, including bot-funded construction and
  research progression. Disposable item-source fixtures cannot establish parity
  with a scarce-inventory production save.

## Integration and lifecycle

Sol owns the common sequence boundary and independent director runner. Luna
owns causal training data and durable shadow evidence. The main agent owns
source-backed guidelines, matched evaluation, documentation, integration and
the scoped commit. No agent changes running services, installed mods, real
saves or GPU jobs as part of this implementation.

Python changes take effect in newly started Laya processes. The deterministic
runner and Factorio need no restart for this tranche. Any later Lua or
production deployment receives its own explicit lifecycle and acceptance.
