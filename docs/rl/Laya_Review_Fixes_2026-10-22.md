<!-- Path: docs/rl/Laya_Review_Fixes_2026-10-22.md | Purpose: Record the verified local review fixes, overlapping owner changes and activation boundary. -->
# Review fixes prepared on Starship

2026-10-22 20:43 IST. Local branch: `work/starship/laya-review-fixes-20261022`.

This integration combines runtime `e37464d`, scheduler harness `96ace8f`, current
owner records `855e1b8`, and the exact started-foundation patch/test from
`b372672`. The latter is already being tested in C19; it is preserved rather
than independently redesigned. C17's completed result and no-adoption decision
are included. C18/C19 and the owner's 55-minute loop are not modified.

Changes prepared locally:

- Stock/production/consumption observations identify a stocked plastic plateau
  separately from measured flow; C5 and both C6 predicates keep their definitions.
- Acceptance journals now persist exact normalized foundation machine counts,
  health, producing/output values, readiness and observation tick. Missing
  observations remain explicit; physical furnace presence alone is not readiness.
- Active final-recipe loans retain their cell when one craft is physically
  supplied, when owned requester/assembler/inserter WIP supplies it, or while
  the machine works. A whole-batch shortage alone no longer forces restoration.
  This is an offline experimental candidate for the rare C17 churn cases,
  not an adopted fix or a modification to either C18/C19 arm.
- Scheduler soft temperature and low-UPS signals require persistence and settle
  after derating. Hard temperature/memory/liveness limits remain immediate.
  Existing configured thresholds and runtime thread defaults are unchanged.
- Batch reports preserve planned statuses, complete-pair counts, fresh-final
  observation denominators, versioned Laya C6 separately from legacy C6, and
  typed faults separately from caught exceptions. Arm order rotates by seed.
- The orchestrator owns the explicit selected-operation session and pure
  observations. Training imports its public facade; the import firewall passes
  without an exception or disabled test. Inherited wrapper decisions remain debt.
- Outcome export preserves logged actions, actual outcomes, provenance and
  save/seed-isolated evaluation splits. It does not invent alternate-action
  labels or launch training. See Laya_Ops_Outcome_Data.md for the contract and
  the remaining compatible-policy/value-model work.
- Mock campaign tests bind milestone writes to a temporary repository; 25
  historical fake status lines are preserved in the status archive. Superseded
  per-cohort scaler code/tests are archived rather than discarded.

Verification on the final integrated code: **383 focused tests passed**;
required fast gate **1238 passed, 3 skipped, 3056 deselected** (including the
previously failing import-firewall test). Scope: unit/mocked integration and
controlled observation replay. No live throughput or policy-improvement claim.
Logs on Starship: `/tmp/laya-review-final-focused-20261022.log` and
`/tmp/laya-review-final-fast-20261022.log`.

Handler action: inspect this branch and the existing experimental factors,
then queue fresh frozen comparisons behind C18/C19 with separate factors for
loan, scheduler and future policy changes. The next port base recorded by the
owner is 37700; recheck allocations before launching. Scheduler activation
requires synchronizing its script and restarting only its service at a recorded
boundary. Python operation/reporting changes require fresh runners. These
changes require no Lua mod deployment or Factorio engine restart.

No code was pushed or copied back to Miniship, no active frozen copy/save was
edited, and no runtime or schedule lifecycle action was taken. The user's
existing dirty checkout is preserved. The owner thread is to receive a brief
summary only; its 55-minute schedule must remain unchanged.
