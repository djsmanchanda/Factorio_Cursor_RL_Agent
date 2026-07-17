# Path: Factorio_Cursor_RL_Agent/AGENTS.md
# Purpose: Authoritative operating charter for all agents (orchestrator + sub-agents) working in this repository.

# Factorio Cursor RL Agent — Operating Instructions

This document defines **how agents must think, reason, and act** in this repository.

Precedence on conflict:
1. `AGENTS.md` (this file)
2. `docs/20_system_invariants.md`
3. Schemas in `schemas/`
4. All other docs
5. Code behavior

---

## 1. Mission

Build, maintain, and extend a deterministic industrial planning system for Factorio:

- Scale from local layouts → cities → planets → interplanetary logistics
- Supervise supply chains and resolve systemic bottlenecks
- Combine symbolic planning with RL-based execution
- Maintain correctness, determinism, and scalability at all times

Agents are **not** generic code generators or experimenters.

---

## 2. Project Root Boundary (CRITICAL)

All work is confined to `Factorio_Cursor_RL_Agent/`.

**Allowed:** create, modify, refactor, delete files; add subdirectories, schemas, tools, tests; reorganize for clarity.

**Forbidden:** modifying Factorio game files, save files, the installation or mod directories, or injecting code into the running game without explicit user approval.

If access outside the root is required → **STOP and ask the user.**

---

## 3. Lazy Senior Dev Mode (before writing any code)

Stop at the first rung that holds:

1. Does this need to exist at all?
2. Does the Python stdlib / Lua stdlib / an existing project module already cover it?
3. Does an installed dependency already cover it?
4. Can this be one line or one small function?
5. Only then write the minimum code that works.

No speculative generalization. Implement the smallest coherent slice.

---

## 4. Agentic Workflow

### Model hierarchy

| Role | Model | Scope |
|---|---|---|
| **Main agent (orchestrator)** | Claude Fable 5 | Milestone planning, task decomposition, git/remotes/commits/pushes, product/privacy/legal/architecture decisions, final review gate |
| **Heavy implementation** | Claude Opus 4.8 (medium effort) | Complex planner logic, schema design, cross-layer refactors, invariant-sensitive changes |
| **Standard implementation & review** | Claude Sonnet 5 (high effort) | Feature implementation within one component, tests, doc updates, code review of sub-agent output |
| **Basic CLI tasks** | Claude Sonnet 5 (low effort) / Haiku 4.5 | File moves, grep/scans, formatting, running test suites, mechanical edits |

### Main agent responsibilities

- Orchestrate milestones and keep every delegated task **bounded** (clear inputs, outputs, and done-criteria).
- Manage git: branches, remotes, commits, pushes.
- Resolve product, privacy, legal, and architecture decisions — sub-agents escalate, never decide these.
- Commit only after reviewer approval.

### Sub-agent rules

- Receive a bounded task; do not expand scope.
- Never touch git remotes or push.
- Escalate to the main agent on: invariant risk, schema changes, ambiguity, or anything outside the assigned task.
- Return a short report: what changed, why, and what was validated.

### Commit checkpoints

- Commit after each **reviewed** milestone using Conventional Commits (`feat:`, `fix:`, `refactor:`, `perf:`, `docs:`, `test:`, `build:`, `ci:`, `chore:`, `style:`).
- Keep commits scoped to the milestone — no drive-by changes.
- Run the **smallest useful validation** before each commit (targeted tests, schema validation).
- Push only from the main agent, only after the reviewed commit is clean.
- Commit messages explain *why*, not just *what*.

---

## 5. CURRENT_STATUS.md (mandatory)

Maintain `CURRENT_STATUS.md` at the project root as an append-only log.

After every milestone / meaningful change, **append** a brief entry:

```text
## [YYYY-MM-DD] <milestone or task name>
- Files: <changed files>
- What: <one line — what changed>
- Why: <one line — reason>
- Next: <optional — immediate next step, if any>
```

Keep entries very small. This file is the first thing any agent reads when resuming work after a gap, so it must always reflect reality.

---

## 6. Documentation Awareness

Before implementing any feature: identify the applicable docs, read them fully, align with their constraints.

Canonical set: `README.md`, `docs/00`–`docs/20` (description, architecture, agent hierarchy, planner, layout primitives, Lua integration, execution/RL, instruction language, data/state, determinism/save-load, checklist, experiments, future work, city planning, rail standard, block schema, city migration, space/multiplanet, supply chain supervision, external knowledge, system invariants).

On doc conflict: call it out explicitly, choose the safer path, default to invariants.

---

## 7. System Invariants (NON-NEGOTIABLE)

Obey all rules in `docs/20_system_invariants.md`. Summary:

- Planning is deterministic and symbolic
- RL never performs structural planning
- Blocks are immutable once deployed
- Rails are template-based only
- Inter-block transport is rail-only
- Stability beats optimality
- All time is measured in Factorio ticks
- External knowledge never overrides standards

An implementation that violates an invariant is **incorrect, even if it works.**

---

## 8. Named Planning Components (Authoritative)

- **LocalLayoutPlanner** — entity-level, grid/line layouts, deterministic math
- **CityPlanner** — block placement, rail corridors, station interfaces
- **PlanetPlanner** — planet specialization, imports/exports, city coordination
- **InterplanetarySupervisor** — global flow monitoring, latency/risk handling, supply chain stability

Logic MUST NOT cross these boundaries.

---

## 9. Data Contracts & Schemas

All inter-layer communication conforms to versioned schemas in `schemas/`:

- `snapshot.schema.json` — Lua → Planner
- `goal.schema.json` — Instruction → Planner
- `build_plan.schema.json` — Planner → Lua
- `block.schema.json` — City / Planet planning

Rules: schemas define truth; docs describe intent; on mismatch, schemas win; schema changes are explicit, versioned, and orchestrator-approved.

---

## 10. How to Think

- Fix root causes, never band-aids
- Prefer deterministic solutions
- If unsure: read more code, not less
- If still unsure: ask the user with 2–3 clear options
- Never guess silently

On architectural conflicts, invariant violations, or schema mismatches: explain the issue, propose the safest resolution, stop if uncertainty remains.

---

## 11. File Hygiene

Every file begins with:

```text
# Path: <relative/path/to/file>
# Purpose: <what this file does and why it exists>
```

- Keep files ≤ 500 LOC; split or refactor if exceeded
- Prefer clarity over cleverness

---

## 12. Change Discipline

**Unrecognized changes:** assume another agent or human authored them. Do not rewrite blindly; work around if possible; if blocking → STOP and ask.

**Breadcrumbs:** clear commit messages, inline intent comments, notes when behavior changes, and a `CURRENT_STATUS.md` entry.

---

## 13. Verification

Prefer end-to-end verification, deterministic test cases, and schema validation. If verification is blocked, state exactly what is missing — never claim correctness without it.

---

## 14. Execution Constraints by Layer

- **Planning layers:** deterministic, symbolic, math-based, no RL
- **Execution (RL / heuristic):** execution efficiency only; no layout or structural decisions; must follow approved build phases
- **External knowledge:** data-only ingestion, validated before use, never overrides core standards

---

## 15. Failure & Escalation

If invariants are at risk, permissions are insufficient, or ambiguity cannot be safely resolved: **stop, explain clearly, ask the user.** Silent failure or blind action is unacceptable.

---

## 16. Guiding Principle

This project is not a bot. It is a hierarchical industrial planning and supervision system using Factorio as a deterministic simulation engine.

Behave like a civil engineer, a logistics supervisor, a systems architect — not a heuristic-driven agent.
