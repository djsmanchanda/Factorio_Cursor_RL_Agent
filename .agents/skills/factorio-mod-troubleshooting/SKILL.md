---
name: factorio-mod-troubleshooting
description: Diagnose Factorio campaign stalls, unexpected placements, stale reports, command failures, and deployment drift using bounded run context and targeted evidence. Does not authorize live mutation or lifecycle actions.
---
# Path: .agents/skills/factorio-mod-troubleshooting/SKILL.md
# Purpose: Diagnose the first failing decision with compact, run-scoped evidence.

# Factorio troubleshooting

Read [operations](../../../docs/factorio_operations.md) for runtime paths and
[context workflow](../../../docs/deterministic/run_context.md) for packet creation.
Respect authorization already given in the session; this skill grants none.

## Start with the relevant evidence

For a campaign failure, generate/read `logs/latest-context.md` before reading
whole logs. The packet is a bounded index, not a complete diagnosis. Check the
run header, completion state, terminal evidence, milestone references and
inventory sample ticks. Missing or truncated fields remain unknown. Helper
findings cover only their observed interval; a timeout does not describe the
rest of the run.

For command/report failures, first identify the failing layer:

| Layer | Targeted check |
| --- | --- |
| Transport | Confirm endpoint/process; one tick probe |
| Registration | `/help <command>` and loaded/source mod versions |
| Output | Same-server script-output path, filename/request ID and freshness |
| Contract | Report tick, scope, schema and matching version |
| Planning/execution | Exact decision, submitted plan, executor report, observed entity |
| Production | Blocked dependency, stock ownership, input/output flow and status |

Do not probe every layer when a planner reproduction already identifies the
failure. Do not reset a preserved episode merely to get more observations.

## Investigate one causal chain

- Trace observation → decision → submitted plan → execution → outcome. A
  terminal task name is a symptom until tied to its blocked dependency.
- For an unexpected entity, search the exact coordinate in submitted plans and
  execution reports. Establish entity versus ghost, direction, owning project,
  placement time and tier-selection inputs before editing geometry.
- Keep requester/buffer WIP, transferable stock and logistic network stock
  distinct. Stock in another cell does not feed this cell. Net inventory change
  combines production, consumption, transfers and construction; it is not a
  production rate. Use measured craft/output counters for throughput claims.
- Read the cited raw lines and the smallest relevant report window. Expand
  evidence when a packet omits a field needed to distinguish hypotheses.
- Compare against the previous run, best verified milestone and same-mechanism
  runs. Longer survival and more stock are not acceptance criteria.

## Fix and verify

State the mechanism, competing explanation, smallest reusable change and
predicted observable result. Reproduce locally before a full rerun when
possible. Preserve validators and existing infrastructure; never repair a live
base to conceal a planner bug. A telemetry-only rerun needs a specific missing
measurement that cannot be obtained from the preserved evidence.

Report test evidence separately from deployed and live evidence. Python needs
the affected runner/controller restarted; Lua needs deliberate deployment and
runtime reload. Perform live actions only within explicit session authority.
Do not request authorization again when it already covers the exact action.
