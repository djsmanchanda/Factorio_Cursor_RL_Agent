<!-- Path: AGENTS.md | Purpose: Concise operating charter for repository agents. -->

# Factorio Cursor RL Agent

## Project direction

This repository contains two isolated but interoperable systems:

- **RL training is the primary development focus.** It learns factory decisions through disposable Factorio episodes, measured outcomes, population selection, and bounded autoresearch. It may learn structural planning, including zoning, layouts, routing, and phased expansion.
- **The deterministic runtime is a working reference, not a finished system.** Its semi-stable planners, schemas, validators, and execution primitives may be reused by RL, while its known production and layout problems continue to be fixed.

## Core principle: Learn, don't accumulate exceptions

When a failure repeats, first ask whether it should become a better observation, action, reward signal, curriculum step, validator, or learned policy decision—not another hand-authored rule.

## Authority by domain

There is no single precedence list for unlike artifacts:

- The latest explicit user instruction defines product intent. If it changes documented intent, update the affected documentation and contracts with the implementation.
- `AGENTS.md` defines how agents work.
- `docs/system_invariants.md` defines isolation and safety boundaries.
- `schemas/` define serialized data shapes and versions.
- Area documentation defines current design intent.
- Code and live evidence describe current behavior; neither silently overrides intended behavior.

Ask only when intent is materially ambiguous, unsafe, or requires authority beyond the repository.

## Three boundaries

Read [system invariants](docs/system_invariants.md). The boundaries most often violated are:

1. **Keep runtimes isolated.** Real Nauvis, disposable RL training, and `experimental/legacy_autonomy` are distinct. Never silently substitute one for another.
2. **Learn structure in training; protect production.** RL may create zoning, layouts, and routes inside disposable training surfaces. A learned policy gains real-base authority only through explicit promotion, validation, and rollback controls.
3. **Evidence has levels.** Unit test -> integration or replay -> deployed runtime -> observed live result. Report the highest level actually reached.

## Learning principles

- Separate hard safety/legality validators from soft design preferences.
- Hard validators cover Factorio legality, collisions, resource budgets, fluid purity, and runtime isolation.
- Soft priors and rewards should encourage zoning, direct continuous transport, short routes, low entity/time/resource cost, sufficient throughput, expansion space, and repair-before-duplicate behavior.
- Preserve exploration with seeded randomization and diverse populations. Do not collapse training into a catalog of hand-authored layouts.
- Promote policies only against held-out scenarios and the deterministic baseline; preserve checkpoints and rollback.

## Documentation router

Before implementing: identify the subsystem, read its listed documents, and ignore unrelated documents and the archive.

| Work | Read first |
|---|---|
| Architecture, shared contracts, boundaries | `docs/architecture.md`, `docs/system_invariants.md` |
| RL, curriculum, rewards, autoresearch | `docs/rl/README.md`, `docs/rl/training.md` |
| Deterministic runtime and planners | `docs/deterministic/README.md`, `docs/deterministic/planning.md` |
| Live Factorio, RCON, deploy/restart | `docs/factorio_operations.md` |
| Prior designs and handoffs | `docs/archive/` only when history is needed |

## Working loop

1. **Understand:** read the latest relevant log, identify the subsystem, and isolate the first hard failure. Trace observation -> decision -> plan -> execution -> outcome.
2. **Plan:** state the smallest expected reusable fix. For RL failures, first examine observation coverage, action expressiveness, reward credit, curriculum, and evaluation.
3. **Implement:** fix the cause without manually repairing a live base or adding a one-off coordinate/recipe workaround.
4. **Verify:** run the narrowest useful tests, then the relevant episode or live workflow when requested and authorized.
5. **Report:** give the important evidence and the exact lifecycle action still required.

If progress is still being made, do not declare the system stuck merely because a timer expired. Diagnose whether it needs more time, supply, throughput, an alternate action, or a retry.

## Coding preferences

- Keep things simple. Channel YAGNI energy unless told otherwise.
- Propose bold ideas when they can materially improve the work.
- Be careful with destructive actions the user did not explicitly request.
- Prefer focused tests that protect behavior. Avoid repetitive test slop and broad suites when a narrow test proves the change.
- Keep test output token-efficient: use quiet/count output and short tracebacks by default, run focused tests before broad suites, and report only the summary plus actionable failure excerpts. Preserve enough diagnostics to debug a failure; redirect unusually verbose tool output to a temporary artifact instead of streaming it into the conversation.
- Use concise comments to explain intent or non-obvious use, not every line. Keep comments current.
- Add a brief path/purpose comment at the start of human-authored files when the format supports comments.
- Keep responses compact: outcome, evidence, required next action.
- Preserve unrecognized worktree changes; assume they belong to the user or another agent.

## Factorio operations

- Follow `docs/factorio_operations.md` for current runtime paths, ports, deployment, and restart procedures.
- Existing real infrastructure is authoritative: route around it or fail safely. Do not hide planner bugs with manual in-game fixes.
- After every change, classify and report the required lifecycle action: restart a runner, redeploy a mod, restart Factorio, or do nothing.
- Work outside this repository, including saves, installed mods, and servers, requires explicit user authorization.

## Changes, status, and git

- Make the smallest coherent change and avoid unrelated refactors.
- Append one short entry to `CURRENT_STATUS.md` after a durable milestone; do not log transient attempts.
- After a user-requested fix or feature is implemented and verified, create one scoped commit unless the user says not to commit. Do not commit plans, diagnosis-only work, or unverified changes.
- RL commit subjects use `feat(RL ...): ...` or `fix(RL ...): ...`.
- Only the main agent manages commits, remotes, or pushes. Delegated tasks stay bounded and return changed files plus validation.
- If verification is blocked, state the blocker precisely and do not claim completion.
