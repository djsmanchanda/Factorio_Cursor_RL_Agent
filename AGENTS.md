<!-- Path: AGENTS.md | Purpose: Concise operating charter for repository agents. -->

# Factorio Cursor RL Agent

## Project direction

This repository contains three isolated but interoperable systems:

- **Deterministic runtime: current main showcase.** Its planners, validators and execution tooling (starters, mine/furnace lines, extensions, mall cells, belts, poles, pipelines) are Laya's action library; keep fixing and improving them.
- **Laya over deterministic tooling: main focus.** A doc-guided decision layer chooses which deterministic operation to run and when. It replaces the deterministic decision layer (gates, defers, rationing, fixed stage order), not its geometry. Target: C0–C6 in `orchestrator/checkpoint_milestones.py`, then mining-productivity-4.
- **RL: test bed** for experiments, training and A/B testing on disposable worlds.

## Core principle: Learn, don't accumulate exceptions

When a failure repeats, prefer a better fact, operation, reward, curriculum step, validator or learned decision over another hand-authored rule. For Laya: add missing option facts first, then missing operations, then a quoted rule or weight tested as a variant. Do not add code branches or rebuild deterministic geometry in Laya-specific modules.

## Authority by domain

- The latest explicit user instruction defines product intent; update affected docs and contracts with it.
- `AGENTS.md` defines how agents work; `docs/system_invariants.md` defines isolation and safety; `schemas/` define serialized shapes.
- Area documentation defines design intent. Code and live evidence describe current behavior; neither silently overrides intent.
- Dated handoffs and reports are evidence, not current instructions.

Ask only when intent is materially ambiguous, unsafe, or requires authority beyond the repository.

## Three boundaries

Read [system invariants](docs/system_invariants.md). The boundaries most often violated are:

1. **Keep runtimes isolated.** Real Nauvis, disposable training and `experimental/legacy_autonomy` are distinct. Never silently substitute one for another.
2. **Protect production.** Laya and RL act only on disposable surfaces. Real-base authority requires explicit promotion, validation and rollback; no record so far grants it.
3. **Evidence has levels.** Unit test -> integration or replay -> matched experiment cohort (n/N, window, frozen commit) -> deployed runtime -> observed live result. Report the highest level reached. Proxy milestones are not deterministic milestones; report both.

## Experiments and adoption

- Campaigns run from a frozen code copy under a user systemd unit with a deadline. Commit before freezing, or record the uncommitted diff in `freeze.json`. Never edit, redeploy or restart under an active campaign; queue behind it.
- Change one factor per arm (preferences multiply). Count every planned job; omitted crashes bias results.
- Adopt only by the rule in `docs/rl/laya_variants/README.md`. Smoke runs check wiring; training means decision volume at real choice points.
- Separate hard legality validators from soft preferences. Preserve seeded exploration; do not collapse into hand-authored layouts.

## Documentation router

Identify the subsystem, read its documents, and ignore unrelated documents and the archive.

| Work | Read first |
|---|---|
| Architecture, boundaries | `docs/architecture.md`, `docs/system_invariants.md` |
| Laya runner, strategy, resumption | `docs/rl/Laya_Current_Handoff.md`, `docs/rl/Laya_Clean_Runner.md`, `docs/rl/Laya_Strategy_Guidelines.md` |
| Laya experiments and adoption | `docs/rl/laya_variants/README.md` |
| RL training, rewards | `docs/rl/README.md`, `docs/rl/training.md` |
| Deterministic runtime, checkpoint fleet | `docs/deterministic/README.md`, `docs/deterministic/planning.md`, `docs/deterministic/checkpoint_fleet.md` |
| Tests | `docs/testing.md` (`.venv/bin/python -m pytest -m fast` per change) |
| Live Factorio, RCON, deploy/restart | `docs/factorio_operations.md` |

## Working loop

1. **Understand:** read the latest relevant log; isolate the first hard failure along observation -> decision -> plan -> execution -> outcome.
2. **Plan:** state the smallest reusable fix.
3. **Implement:** fix the cause, not a live base, coordinate or recipe.
4. **Verify:** run the narrowest useful tests, then the episode or live workflow when requested and authorized.
5. **Report:** outcome, evidence, and the exact lifecycle action still required.

Do not declare a run stuck merely because a timer expired while progress continues; diagnose time, supply, throughput, alternate action or retry.

Delegate reading test data, experiment reports and logs through the active environment's supported subagent harness. Subagents return a compact summary citing paths, counts and run IDs. Read small files directly.

In T3 Code, launch delegated work only with the `t3-code` MCP tool `delegate_task` and monitor it with `task_status` (full tool names may have an MCP prefix). Discover both tools before delegating. If either is unavailable, read the evidence directly and report the fallback. Never substitute native `collaboration.*` tools, including `collaboration.spawn_agent`, or launch a native subagent to test availability: on ChatGPT-sharing connections, native agent messages can fail both child and parent before a fallback can run. See [T3 Code issue #17679](https://github.com/pingdotgg/t3code/issues/17679).

Inherit T3's model/provider defaults; omit explicit model and reasoning-effort overrides. Do not force GPT-6-Luna or switch providers to satisfy delegation. If T3-owned delegation returns an unsupported-feature error, read the evidence directly instead of retrying. Outside T3 Code, choose a subagent model only when the active harness and provider support it.

## Coding preferences

- Keep things simple; YAGNI unless told otherwise. Propose bold ideas when they materially help.
- Prefer focused tests that protect behavior; avoid test slop. Use quiet output and short tracebacks; redirect verbose output to a temporary file.
- Run the narrow regression while iterating, then the fast gate once per verified change; retain the test exit status when showing a log tail. Reuse results for unchanged code/environment. For long T3 supervision, use scheduled wakes and bounded status reads; for background commands, use their returned session ID or completion notification instead of shell sleep loops or self-matching `pgrep -f`.
- Comment intent, not every line. Start human-authored files with a `Path: … | Purpose: …` comment.
- Archive, don't delete: this repository is open source. Move superseded code to an old-workflow folder with a README.
- Preserve unrecognized worktree changes; assume they belong to the user or another agent.

## Factorio operations

- Follow `docs/factorio_operations.md` for paths, ports, deployment and restarts. Side-by-side campaigns need separate port bases.
- Before launching, restarting or deploying, check `systemctl --user list-units 'laya-*'` and the active campaign's deadline.
- Existing real infrastructure is authoritative: route around it or fail safely. Do not hide planner bugs with manual in-game fixes.
- After every change, report the lifecycle action: restart runners using it, redeploy a mod, restart Factorio, queue behind a campaign, or nothing.
- Work outside this repository (saves, installed mods, servers) requires explicit user authorization.

## Changes, status, and git

- Make the smallest coherent change; avoid unrelated refactors.
- Append one short `CURRENT_STATUS.md` entry (what, evidence, lifecycle) after a durable milestone. Older entries live in `docs/archive/status/`.
- Write one dated handoff per session in `docs/handoffs/` (gitignored, local only); record durable facts in tracked docs. Update `Laya_Current_Handoff.md` when a campaign starts, ends or is adopted. Use absolute dates with IST times. Helper and observer model output goes in `*_helper_runs/`; it is evidence, not authority.
- Commit each verified user-requested fix or feature once, unless told not to. Do not commit plans, diagnosis-only work or unverified changes.
- RL and Laya subjects use `feat(RL ...): ...` or `fix(RL ...): ...`.
- Only the main agent manages commits, remotes or pushes. Delegated tasks return changed files plus validation.
- If verification is blocked, state the blocker precisely and do not claim completion.
