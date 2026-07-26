---
name: factorio-mod-troubleshooting
description: Diagnose this repository's Factorio mod command registration, deployment/restart drift, script-output pairing, report schemas, live-state invariants, and autonomous-run failures. Use when RCON connects but commands, reports, or workflows misbehave. Do NOT use to redeploy mods, restart/quit/save/reset a server, or mutate a live base without explicit user authorization.
---
# Path: .agents/skills/factorio-mod-troubleshooting/SKILL.md
# Purpose: Provide a fail-closed troubleshooting sequence for the repository's Factorio mod.

# Factorio Mod Troubleshooting

Classify the failure before changing anything.

## When to Activate

- Diagnose a missing or unknown registered command
- Reconcile source code with the deployed mod revision
- Trace a missing or stale `script-output` report
- Validate a mod-generated JSON artifact against its schema
- Investigate a GameBridge wait timeout
- Measure live factory invariants without mutation
- Separate autonomous-run code failures from live-state failures
- Determine whether a restart boundary explains drift

## Failure Routing

| First failing layer | Evidence | Next read-only action |
| --- | --- | --- |
| Transport | Auth error/refused connection | Verify endpoint and launch settings |
| Registration | Tick works; `/help` fails | Compare loaded mod and source revisions |
| Output | Command runs; no new JSON | Verify same-server `script-output` and subdir |
| Contract | New JSON fails validation | Compare tick, schema, mod revision |
| Live state | Valid report says `ok: false` | Inspect reported surface/force/invariant |
| Orchestrator | Inputs valid; Python traceback | Capture revision, traceback, process provenance |

Use [the authoritative runbook](../../../docs/31_factorio_mod_interaction_and_troubleshooting.md)
for command inventory, output directories, recipes, and the full failure matrix.

## Fail-closed Sequence

1. Record endpoint, server data directory, repo revision, and deployed-mod revision.
2. Run one tick probe and `/help <command>`.
3. Inspect the expected output directory without deleting artifacts.
4. Validate the newest report's tick and JSON/schema.
5. Use `python -m tools.verify_factory_invariants`; the direct form is broken.
6. Compare the failure to current source before blaming live state.
7. Ask before redeploy, save/reset, restart, quit, or autonomous retry.

BAD:

```text
No report appeared, so redeploy the mod, reset the surface, and rerun.
```

GOOD:

```text
RCON tick works; /help snapshot works; no new snapshot appeared in the supplied
script-output tree. Verify that tree belongs to this server before any retry.
```

A stale autonomous log may reference code that differs from the current worktree.
Record revision/import provenance; do not treat a fresh rerun as harmless.

## Before You Ship

- [ ] Failure is assigned to transport, registration, output, contract, live state, or orchestrator
- [ ] Source and deployed revisions are distinguished
- [ ] Report path and tick are captured
- [ ] Schema validation uses the matching repository schema
- [ ] Real-base checks use `nauvis` and `player`
- [ ] Repeated mutating retries were avoided
- [ ] Deployment/lifecycle/mutation next steps request explicit authorization
- [ ] Findings separate verified evidence from inference
