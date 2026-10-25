<!-- Path: docs/rl/Laya_Stock_Gate_Control.md | Purpose: Define explicit standing-cell gate raises and their evidence boundaries. -->

# Standing stock-gate raises

`raise_stock_gate` exposes a configuration-only alternative on the deterministic
ops menu. The sampler, exploration floor, rule catalog and weights remain the
same. Pause, reclaim, retargeting and construction are outside this slice.

The read-only `mall_economics.cells` component records assembler tier, position,
unit number, surface, force, recipe, simple logistic gate, circuit-control flag,
requester identity/groups, cell side and reservation owners. Failed observations
are null. Eligibility requires a declared paired-mall cell, full footprint
coverage by its `paired_mall_<recipe>` placement reservation, no foreign overlap,
a matching standing requester group, no loan and no anchor or circuit control.
Reservation receipts now record `configuration_only`: pure settings updates
retain collision evidence but neither grant nor transfer placement ownership.
Legacy configuration receipts are recognized only for known operations whose
entire touched footprint belongs to this cell or its owner's recorded placement
coverage, including same-owner batches; explicit placement claims and foreign
overlap remain blocking. Loan/restoration history therefore preserves
the original placement claim. Unknown, ungated, compound or quality-specific conditions do
not authorize a change. Quad and undeclared topologies are not supported here.

An explicitly active material-ledger project with an unallocated bill supplies
the served owner. Held construction bills remain active under the ledger's
existing `_active` predicate. The target sums active bills for the item, using
the ledger's absolute stock-target convention. The network survey must be below
the target and the observed gate must be lower. This is a gate change, not a
material allocation or delivered-output promise. Aggregate provider/storage
stock does not prove separated networks can serve the same transaction.

The preview records one affected cell, current/target gate, served owner and
bill snapshot, scope, zero construction bill and possible future input draw.
After acknowledged submission the existing per-item, per-run gate floor rises,
including when readback is unavailable. Numeric and omitted-target refreshes,
and loan restoration, preserve that floor. An omitted target clears a gate only
when no positive floor exists. The floor is not an exclusive material reserve.
The action writes no requester setting, material project, reservation or recipe;
ordinary post-operation upkeep is skipped for this operation.

Execution recomputes eligibility and compares the selected cell and bill preview.
Changes return `stock_gate_snapshot_changed`; observation failures return
`stock_gate_observation_unknown`, with no submission. It uses the existing gate
planner, removes the recipe setter, validates the configure-only plan, and uses
shared build authorization and GameBridge submission. It avoids construction
clutter clearing, pole relocation, retries and reservation writes.

`stock_gate_verified` requires acknowledged submission and readback of the same
cell with the selected gate. `stock_gate_unverified` records `submitted=true`,
`verified=false`, `ok=false`. Submission exceptions record
`stock_gate_submission_failed` and `submission_attempted=true`; they do not
prove nothing was applied. Structured readback/error evidence survives in
`decision-states.jsonl` under `outcome.configuration_receipt`. Verified gate
configuration does not establish delivered belts or sustained throughput.

## Executor capability and concurrency

Offering and executing the action requires `guarded_configuration: true` from
the read-only versioned capability probe. Old, absent or malformed responses
cannot authorize it. See the shared
[configuration precondition protocol](../deterministic/configuration_preconditions.md).
The action carries expected unit number, recipe, prior gate, circuit-control
flag and requester identity/groups. Lua checks every guard before any plan
action, then again immediately before settings are written. Stale entity facts
reject the update. This does not lock Python's material ledger across processes;
changes after execution remain possible, so readback is separately required.

Evidence is offline stateful fake and mocked Lua command testing. No live RCON,
server, runner, save, installed-mod or campaign action was performed. Activation
requires deliberate executor-mod deployment and affected Factorio runtime reload,
then a fresh runner using matching Python/schema source. Until the deployed
executor reports support, the new operation is unavailable.
