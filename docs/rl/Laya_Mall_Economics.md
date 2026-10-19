<!-- Path: docs/rl/Laya_Mall_Economics.md | Purpose: Define the read-only ops mall fact contract and its evidence limits. -->

# Mall economics observations

The ops runner records `observation_digest.mall_economics` and renders the same
facts in its bounded brief. This additive version-1 projection changes no menu
eligibility, policy features, weights, geometry, reservations or operation
selection. Existing `projects`, `loans`, bill and `cell_lent` consumers remain
compatible. It is observation infrastructure for later explicit stock-gate,
loan-settle and reclaim alternatives, not an implementation of those operations.

| Field | Meaning |
| --- | --- |
| `slots.capacity` | Current tooling's admission cap, not a count of built machines. |
| `slots.occupied` | Existing mall survey's committed live **or ghosted** recipe slots in the applicable bank. |
| `slots.free` | `max(0, capacity - occupied)` when both finite counts are known. This is admission headroom; it proves neither construction materials nor usable assemblers. |
| `slots.unlimited` | True for an observed unlimited cap; null for unknown admission evidence. An unlimited cap has null capacity and free counts. |
| `slots.scope` | Bootstrap bank or independent demand bank, following the tooling's existing transition. |
| `projects[owner]` | Active ledger project's target, state, required bill, recorded reservation and missing allocation. Fully allocated projects remain visible. |
| `stock.network` | Sum of the existing saved-world survey's provider/storage inventories across logistic networks. Requester/buffer contents, machine WIP and cargo are excluded by that survey. |
| `stock.reserved` | Active ledger reservations summed by item; project keys retain ownership separately. |
| `stock.unreserved` | Network count minus recorded reservations, floored at zero. This is a snapshot comparison, not claimant-specific allocatable stock or affordability. |
| `loans[]` | Lender recipe, target item, material-project owner key, requester position and side identify the borrowed cell and displaced recipe. |
| Loan target/progress | `target` is the requested network threshold; `production_target` includes spare output. `network_stock` and `remaining_to_target` compare the survey to the requested threshold. They do not attribute output to this cell or establish delivery. |
| Loan step/progress | Current/step recipe, step target, required crafts, measured completed crafts and machine status. Durable `completed_step_targets` are recorded prerequisite credits, never added to physical stock. |

A failed or missing component is null, independently of successful components.
Successful empty surveys are empty maps/lists. An absent network item is zero
only after the network item survey succeeds. Loan craft progress is unknown
when health telemetry is missing, recipe and baseline disagree, or the observed
counter predates the baseline. The health survey batches all active cells and
reuses its finished-craft encoding; the projection makes no new raw RCON command.
The cap is observed through existing admission predicates without the execution
helper's exception-to-default fallback, so telemetry failure cannot claim eight
available slots.

Ledger allocations are copied as recorded, without rebalance or persistence;
they may predate the network snapshot. No borrower eligibility count, displaced
production rate, requester input sufficiency, stock velocity, or target completion
claim is inferred. Network stock can change between observations and does not
prove that separated networks can serve the same transaction.

The brief retains the 8,000-character default and existing combined rule budget.
It shows slot headroom with a materials/borrowability qualifier, iron/copper
stock, loan target and craft progress, and reservations including fully allocated
owners. It trims history and economic entries with explicit omission/truncation
markers when necessary; the structured decision journal retains the full fact
projection. Character limits remain character limits, not token guarantees.

Recommended next operations should expose legal alternatives and quoted intent:
preview a stock gate with its measured threshold and affected producer; preview
settle/reclaim with cell identity, current bill, target progress, displaced output
and rollback postconditions. These facts alone do not authorize either operation.
Validation here uses survey/ledger fakes and the runner's recorded decision state;
there is no deployed or live-world evidence. Integration requires a newly started
runner using the integrated code; no mod redeploy or Factorio restart is needed
for this Python observation change.

## Decision facts (2026-10-19)

The runner adds `capacity` and `starters` to `mall_economics`, the brief's
CAPACITY, STARTERS and BLOCKED lines, and option features. No default rule
weighs these features; `docs/rl/laya_variants/ops-need-no-progress.json` is the
only rule that uses one, as an untested variant.

| Fact | Meaning |
| --- | --- |
| `capacity.cells/anchors/loaned/lendable` | Observed mall cells; `lendable` = non-anchor cells not already lent. An observation, not the tooling's borrowability decision. Null when the cell survey failed. |
| `capacity.covered_recipes` | Recipes of the observed cells. |
| `capacity.borrow_blocked` | When the latest produce/mall_slot attempt was `rationed_mall_borrow_deferred`: its item, active loans and the slot claim's shortfall. Otherwise null. |
| `starters[name]` | Present/healthy/producing/output and the first failing fact (`blocker`) from the latest acceptance sample, with its tick; may be up to ten decisions old. |
| `features.borrow_blocked`, `lendable_cells` | Produce and mall_slot options: the two capacity facts above. |
| `features.starter_blocker` | Plate options: the plate's starter blocker. |
| `features.no_progress`, `need_no_progress` | Number of latest attempts in a row, for this option or for any option serving its need, that ended blocked with the same outcome (nothing ok or built). Capped at 5. |
