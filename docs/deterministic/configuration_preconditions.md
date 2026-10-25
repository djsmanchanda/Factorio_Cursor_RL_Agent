<!-- Path: docs/deterministic/configuration_preconditions.md | Purpose: Define the compare-before-write executor protocol for owned stock gates. -->

# Owned configuration preconditions, protocol v1

Before offering or submitting a guarded operation, call
`orchestrator.configuration_preconditions.supports_configuration_preconditions(client)`.
It invokes exactly `/layout_executor_capabilities`, a read-only command returning
`{"configuration_preconditions":1}` through `rcon.print`. Require the result to be
`True`. Explicit other nonnegative integer versions return `False`; missing,
malformed, ambiguous or failed responses return `None`. An old executor's unknown
command response never grants support. This handshake is mandatory because old
executors can ignore unknown action properties.

Only `configure_entity` accepts the optional `configuration_preconditions` field:

```json
{
  "unit_number": 10,
  "recipe": "pipe",
  "logistic_condition": {"signal": "pipe", "comparator": "<", "constant": 20},
  "circuit_enabled": false,
  "requester": {
    "entity": "requester-chest",
    "position": {"x": 4.5, "y": 1.5},
    "unit_number": 11,
    "groups": ["mall:pipe"]
  }
}
```

All shown fields are required. Unit numbers are positive integers; gate constants
are integers. Requester groups are the exact sorted list of nonempty named
logistic-section groups, retaining duplicates; unnamed sections do not contribute.
The requester must match the plan's force and surface at the exact position.
The machine must match the action's entity, exact position and force, expected
unit and recipe, have logistics enabled, match the simple item-versus-constant
gate (no second signal, normal quality), and match `circuit_enabled` exactly.
Unknown properties in the precondition shape and read failures reject the guard.

The executor checks all guards before executing any actions, then repeats each
check synchronously immediately before configuring its target. Guard checks never
create control behaviors or logistic sections. A stale preflight rejects the
whole submission with report `error="configuration_precondition_failed"` and a
`placement_failures[].reason` beginning `configuration_precondition_failed:`.
The immediate check uses the same typed reason. Its suffix identifies the mismatch
or `read_failed`. A rejected action cannot change recipes or other settings or
create entities. Guarded actions contribute no bounds to incidental pole-wire
recomputation. Unrelated unguarded actions retain their existing behavior; this
protocol is a configuration compare-before-write guard, not general plan rollback.

For stock-gate updates, omit the action's `recipe`: the expected recipe belongs
in the guard and need not be reapplied. Legacy actions without a guard retain
the existing configuration semantics.

Repository tests exercise the Lua command with mocked Factorio objects. Activation
requires a separately authorized mod deployment and Factorio reload; repository
validation alone does not establish deployed support.
