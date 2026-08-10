<!-- Path: docs/architecture.md | Purpose: Define the RL-first architecture and the deterministic runtime boundary. -->

# Architecture

## Two cooperating systems

The repository deliberately keeps two runtimes:

1. **RL training system — primary focus.** It observes a disposable Factorio task, proposes structural and operational actions, receives measured outcomes, and improves policies across many attempts.
2. **Deterministic production runtime — reference and fallback.** It runs the current Nauvis factory using hand-written planners. Its mechanics are useful but incomplete; known problems in layout, transport, recovery, and scaling still require work.

The RL system may reuse stable mechanics such as schemas, recipe and prototype exports, legality checks, collision surveys, build-plan execution, and measurements. Reuse must sit behind explicit interfaces; the training runtime must not call the real-base orchestrator as a hidden policy.

## Shared boundary

```text
Factorio snapshot
      |
      v
normalized observation + live prototype facts
      |
      +----------------------+----------------------+
      |                                             |
      v                                             v
learned policy                             deterministic baseline
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

## Authority by artifact

- `AGENTS.md`: agent behavior and workflow.
- `docs/system_invariants.md`: isolation, safety, and evidence boundaries.
- `schemas/`: serialized contract shape and version.
- `docs/rl/` and `docs/deterministic/`: design intent for each system.
- Code and live evidence: what currently happens.

The latest explicit user direction controls product intent. When it changes an older design, update the relevant documentation, schema, test, and implementation together instead of treating stale prose as immutable.

## Promotion path

A learned policy progresses through offline validation, disposable live episodes, held-out evaluation, comparison with the deterministic baseline, and an explicitly authorized production trial. Every promoted version keeps its evidence, parent checkpoint, configuration, and rollback target.
