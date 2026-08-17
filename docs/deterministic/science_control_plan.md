<!-- Path: docs/deterministic/science_control_plan.md | Purpose: Stage Factorio 2.1 science telemetry and bounded research control for the deterministic runtime. -->

# Science telemetry and research control plan

## Decision

Factorio 2.1 should make the deterministic runtime better at *observing and
diagnosing* research.  The Python deterministic planner remains the authority
that selects a technology.  Lab circuit control is an optional, explicitly
authorized actuator to evaluate only after the read-only feedback loop is
proven on an isolated surface.

This plan deliberately separates the narrow science report from
`/snapshot`.  A full Nauvis snapshot can be large, its contract is strict, and
science observation has a different cadence and retention need.  A dedicated
report also lets a caller ask about one force and one surface without exporting
the rest of the base.

## Current baseline and 2.1 delta

Today, `factorio_mod/research.lua` exposes `/research_status` and
`/set_research`.  The latter clears the selected force's queue and queues an
explicit technology; `tools/autonomous_run.py research` prepares that
technology's science packs before calling it.  The status report contains
force-level research state and required science packs.  `chain_telemetry.py`
can aggregate a configured line's working and total labs, but it has no
per-lab status or input-inventory report.

Factorio 2.1 adds a lab control behavior that can read lab contents, research
cost, and technology level, and can set research from circuit conditions.  It
also supports selecting the red or green input/output network.  The runtime
API already permits direct entity status, inventory, and circuit-signal reads.
Direct reads are the default observation path; a wire network is an optional
in-game control mechanism, not the transport from Factorio to Python.

Primary references:

- [LuaLabControlBehavior](https://lua-api.factorio.com/latest/classes/LuaLabControlBehavior.html)
- [LuaEntity inventory, status, and signal access](https://lua-api.factorio.com/latest/classes/LuaEntity.html)
- [LuaForce research queue access](https://lua-api.factorio.com/latest/classes/LuaForce.html)

The difference from 2.0.77 is therefore not a replacement for force-level
research selection.  It is a new source of local lab state and a potential
bounded in-game actuator.  The decision, authorization, and audit trail stay
outside the circuit network.

## Completion tracker

Checked items have repository-level evidence only.  They do not indicate that
the deterministic mod is deployed or that Nauvis/player was observed.

### Phase 1 — completed in the repository

- [x] Strict `ScienceStatus` v1 schema, read-only Lua report command, bridge
  collection method, and report inspector.
- [x] Focused schema, bridge, inspector, and Lua-stub regression tests.
- [x] Lua syntax validation and focused Python test suite.
- [ ] Disposable Factorio 2.1.14 runtime validation.
- [ ] Explicitly authorized real-base read-only observation.

### Phase 2 — completed in the repository

- [x] Pure ScienceStatus diagnosis, replay fixtures, and finite candidate
  ranking.
- [x] Deterministic diagnosis precedence, finite allow-list enforcement, and
  report-identity audit tests.
- [ ] An authorized `research select` actuator; the existing explicit
  `research <technology>` actuator remains unchanged.

### Phase 3 — deferred experiment

- [ ] Disposable lab-circuit controller behavior matrix and authorization
  contract.

## Invariants and non-goals

- The report command is read-only: it must not create a force, change research,
  alter a lab control behavior, place wires, or mutate an inventory.
- Every request names an existing `surface` and `force`.  An unknown or
  mismatched target fails closed and writes an error report.
- Each report records the Factorio tick, surface, force, and schema version.
  Callers must not combine reports from different targets or silently reuse a
  stale report after an actuator runs.
- Real Nauvis/player mutation still needs explicit user authorization.  A
  disposable training result is not production authority.
- The report uses stable symbolic status names, never the numeric value of
  `defines.entity_status`.
- This plan does not introduce an autonomous all-technology search, dynamic
  research switching by every lab, or a hand-authored exception per science
  recipe.

## Contract: `ScienceStatus` v1

Create `schemas/science_status.schema.json` as a strict Draft 7 contract and
document it in `schemas/README.md`.  Use an independent `schema_version` so
the existing snapshot schema remains unchanged.

```json
{
  "schema_version": "1.0.0",
  "ok": true,
  "tick": 123456,
  "surface": "nauvis",
  "force": "player",
  "research": {
    "current": "automation",
    "progress": 0.42,
    "queue": ["logistics"],
    "current_science_packs": {"automation-science-pack": 1},
    "current_research_unit_count": 10
  },
  "labs": {
    "total": 4,
    "working": 3,
    "status_counts": {"working": 3, "no_power": 1},
    "input_inventory": {"automation-science-pack": 7},
    "entries": [
      {
        "unit_number": 41,
        "position": {"x": 10.5, "y": -2.5},
        "status": "working",
        "input_inventory": {"automation-science-pack": 3}
      }
    ]
  }
}
```

`current` may be `null`; `queue`, inventories, and entries must serialize as
arrays/objects with their documented empty form rather than Lua's ambiguous
empty-table representation.  `entries` are sorted by `unit_number` for
deterministic report content.  Item-count maps omit zero values; consumers
must compare their keys and values rather than JSON object key order.

The v1 report intentionally exposes raw observations only.  A derived
diagnosis is Python-owned, reproducible from the report, and must not be
serialized by Lua as hidden policy.

## Phase 1 — read-only science telemetry

### Deliverables

1. [x] Add `factorio_mod/science_telemetry.lua` and register it from
   `factorio_mod/control.lua`.
2. [x] Add `/science_status` with required JSON `{surface, force}` and write
   `factorio_mod/science_reports/science_status_<tick>.json`.
3. [x] For labs owned by that force on that surface, collect:
   - `unit_number`, position, symbolic entity status, and `lab_input`
     inventory;
   - total/working/status-count aggregates and aggregate science-pack
     inventory;
   - force research name, progress, queue, current research ingredients, and
     current research-unit count.
4. [x] Add `GameBridge.science_status(surface, force)` using the existing
   new-report collection behavior.  It must validate inputs before opening an
   RCON connection where practical.
5. [x] Add `schemas/science_status.schema.json`, a focused Python schema test,
   bridge command construction test, and Lua regression coverage for sorting,
   empty collections, invalid targets, and read-only behavior.
6. [x] Add a small `tools/science_status.py` inspector that validates one saved
   report and prints the report identity plus aggregate state.  It must not
   call an actuator.

### Acceptance evidence

- [x] Unit/schema tests prove strict contract validation and deterministic
  order.
- [ ] A disposable Factorio 2.1.14 fixture proves the command registers, reports
  a working lab and a deliberately starved or unpowered lab, and leaves force
  research, lab inventories, and control behavior unchanged.
- [ ] A real-base observation is optional and requires explicit authorization.  If
  authorized, it is read-only and reports the exact server, surface, force,
  mod revision, report path, and tick.

## Phase 2 — deterministic diagnosis and candidate ranking

Phase 2 consumes saved `ScienceStatus` reports; it does not add a new game
mutation.

Implementation status: the pure diagnosis and allow-list-bounded ranking
modules, replay fixtures, and focused tests are complete.  They are not wired
to a new actuator; a proposed selection remains advisory until a separately
authorized execution contract is implemented.

### Diagnosis

Implement a pure `orchestrator/science_diagnosis.py` function that returns a
versioned diagnosis from the report and a measured comparison window.  Initial
diagnoses are deliberately few:

| Preconditions | Diagnosis | Planner hand-off |
|---|---|---|
| No current research | `research_not_selected` | Request an authorized selection decision |
| Current research, zero required packs in labs, no working labs | `science_delivery_or_supply_missing` | Trace the required pack's supply → delivery → inserter chain |
| Packs present and any lab has `no_power` | `lab_power_limited` | Run the existing power diagnosis before adding labs |
| Packs present, labs working, progress does not advance over a tick window | `research_progress_stalled` | Re-read research state and check queue/control mismatch |
| Working labs are below an explicit target and no higher-priority fault exists | `lab_capacity_limited` | Propose a normal, validated capacity plan |
| Progress advances | `research_progressing` | Measure consumption rate and retain observation |

The comparison window is required before declaring a progress stall.  One
same-tick report is a state observation, not throughput evidence.

### Selection

Implement `orchestrator/research_scheduler.py` as a pure ranker.  It receives
an explicit, finite, goal-derived allow-list of candidate technologies plus
their force-level status and the latest diagnosis.  It returns a deterministic
decision containing the selected technology, score components, rejection
reasons, and input report identities.

Initially rank only technologies that are enabled, incomplete, and permitted
by the caller's goal.  Score benefits and cost as separate terms, for example
goal unlock value, prerequisite value, required science-pack totals, observed
supply feasibility, and opportunity cost.  Do not turn the technology tree
into an unbounded autonomous search or conceal a fixed priority list in the
ranker.

`tools/autonomous_run.py research <technology>` remains the sole real-base
actuator in this phase.  A future `research select` mode must require an
explicit execution authorization carrying the candidate allow-list and the
selected report identities.

### Acceptance evidence

- [x] Pure tests cover every diagnosis, report-tick ordering, and deterministic
  tie-breaking.
- [x] Replay fixtures cover a science shortage, a power fault, a stalled research
  window, and a progressing research window.
- [x] Candidate-ranking tests prove it cannot select disabled, completed,
  unobserved, or out-of-allow-list technologies.

## Phase 3 — disposable lab-circuit controller experiment

The Factorio 2.1 lab control behavior is valuable, but its condition
precedence and transition semantics must be measured rather than assumed.
This is an experiment, not a production feature commitment.

1. Create a disposable fixture with a dedicated force, two or more labs, and a
   controlled red/green network.
2. Verify the exact behavior of research conditions when the signal changes,
   disappears, conflicts, or competes with a force queue; verify behavior with
   multiple configured labs.
3. Record a concise compatibility matrix: Factorio build, signal input,
   configured behavior, selected technology, queue result, and observed tick.
4. Only if those results are deterministic and useful, define an explicit
   `research_control_authorization` contract.  It must identify one controller
   lab by `unit_number`, surface, force, expected position, desired condition,
   expiry tick, and rollback configuration.
5. Implement a mutation command only behind that authorization.  It must
   preserve the previous lab behavior, configure exactly one controller, write
   an audit report, and provide an equally authorized restore operation.

No Phase 3 command may infer a controller from proximity, configure all labs,
create an unowned wire network, or run on Nauvis/player without a separate
explicit request.

## Later 2.1 work, ordered by value

1. **Science-boundary fluid and heat telemetry.** Add small, explicitly owned
   sensor reports for relevant pipes, tanks, boilers, and heat exchangers.
   These report fluid identity, amount, and temperature where available, so a
   science shortage can be traced to a fluid/power fault without weakening the
   no-mixing invariant.
2. **Requester and buffer visibility.** Observe simultaneous request and
   contents to distinguish absent upstream production from a logistic delivery
   failure.  This should feed the same supply → delivery → inserter diagnosis.
3. **Mirrored layout actions.** Add a prototype-gated `mirrored` action field
   to shared placement contracts, then test collision, ports, and routing on
   disposable episodes before exposing it to the deterministic planner or RL.
4. **Validated direct upgrades.** Factorio 2.1's direct entity upgrade API may
   reduce executor steps, but only as an implementation detail of the existing
   upgrade authorization/rollback path.
5. **Space-platform controls.** Defer platform-specific API until the runtime
   has an explicit platform subsystem.  It must not blur the Nauvis,
   disposable-training, and platform runtime boundaries.

GUI, Factoriopedia, programmable-speaker, and benchmark-only changes are not
on the deterministic planning critical path.

## Rollout and lifecycle

| Slice | Repository changes | Required lifecycle action | Production authority |
|---|---|---|---|
| Phase 1 | Lua mod, schema, bridge, tests, inspector | Deploy deterministic mod; restart deterministic Factorio and Python runner | Read-only only |
| Phase 2 | Python diagnosis/ranker, fixtures, tests | Restart affected Python runner | No new game mutation |
| Phase 3 experiment | Training fixture/mod and tests | Deploy/restart only the isolated training worker | Disposable surface only |
| Phase 3 promotion | Authorization, Lua controller, rollback, evidence | Separate explicit approval, deployment, restart, and live observation | Bounded single-lab scope |

At every slice, record the highest achieved evidence level: unit test,
integration/replay, deployed runtime, or observed live result.  A Lua change
is not active until the intended deterministic server reloads the deployed mod.
