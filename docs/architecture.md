<!-- Path: docs/architecture.md | Purpose: Define the RL-first architecture and the deterministic runtime boundary. -->

# Architecture

## Adopted direction

The target controller is the fine-tuned Laya autonomous director defined in
`docs/rl/Laya_director_final_plan.md`.  Laya owns strategic policy: goal order,
operation choice, site/scale alternatives, waiting, interruption, and recovery.
The existing deterministic cascade is a frozen migration baseline, not the
architecture to extend with new decision branches.

During migration, this document distinguishes the **current runtime** from the
**target authority split**.  Existing behavior remains active until its
replacement contract is implemented and verified; adopting the direction does
not silently grant a model live-base authority.

## Two cooperating systems

The repository deliberately keeps two runtimes:

1. **Laya learning and decision system — primary focus.** It observes disposable Factorio tasks, chooses among dynamically compiled operations, receives measured outcomes, and improves the director policy across many attempts.
2. **Factorio mechanics substrate — current deterministic runtime plus shared tooling.** It currently runs Nauvis through a hand-written cascade. Its target role is observation, feasible-operation compilation, geometry realization, validation, execution, and measurement. The cascade remains only as a frozen evaluation baseline during migration.

The learning system may reuse stable mechanics such as schemas, recipe and
prototype exports, legality checks, collision surveys, build-plan execution,
and measurements. Reuse must sit behind explicit interfaces; neither training
nor the director may call the real-base cascade as a hidden policy.

## Shared boundary

```text
Factorio snapshot
      |
      v
normalized observation + live prototype facts + goal/failure memory
      |
      +----------------------+----------------------+
      |                                             |
      v                                             v
Laya director                            frozen deterministic baseline
      |                                             |
      +---------------- candidate plan/action ------+
                            |
                            v
                 shared legality/safety validators
                            |
                            v
                  isolated execution + measurement
```

Shared contracts describe facts and actions. They must not force the learned policy to reproduce a deterministic layout recipe.

The operation compiler may remove options that are unavailable, illegal, or
outside authorization.  It may not rank the remaining options or silently
select a strategy.  A model or serving failure becomes a safe
observe/wait/escalate outcome after the director is primary; it does not revive
the old cascade as an undeclared policy fallback.

## Authority by artifact

- `AGENTS.md`: agent behavior and workflow.
- `docs/system_invariants.md`: isolation, safety, and evidence boundaries.
- `schemas/`: serialized contract shape and version.
- `docs/rl/` and `docs/deterministic/`: design intent for each system.
- Code and live evidence: what currently happens.

The latest explicit user direction controls product intent. When it changes an older design, update the relevant documentation, schema, test, and implementation together instead of treating stale prose as immutable.

## Promotion path

A learned policy progresses through offline validation, disposable live episodes, held-out evaluation, comparison with the deterministic baseline, and an explicitly authorized production trial. Every promoted version keeps its evidence, parent checkpoint, configuration, and rollback target.
