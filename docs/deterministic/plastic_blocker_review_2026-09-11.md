<!-- Path: docs/deterministic/plastic_blocker_review_2026-09-11.md | Purpose: Explain the repeated pre-plastic dependency stall and its acceptance test. -->

# Plastic progression: completed loans stranded upstream capacity

Latest evidence: episode-20260911T141325Z-9142, started 19:43:29 IST,
ended +2007s with electric-furnace 88% and a deferred plastic prerequisite.
The terminal item was not the failing producer.

At +607s, splitter borrowed the circuit assembler at (53.5,32.5) for three
splitters. By the terminal snapshot it held 3/3 with spare ceiling 3, and its
requester held the only 20 circuits. AM2's AM1 prerequisite had no circuits,
while telemetry reported the circuit producer stopped and borrowed by splitter.
Relevant raw log references at review time:
`autonomous-run.log:3719`, `:4199`, `:4207`, `:4233`, `:4304`.
These line numbers belong to the multi-run log named in the supplied packet;
use the episode identity if the live log has since rotated.

The survey retired fulfilled splitter demand. Normal task dispatch therefore
stopped visiting its loan. The global completion sweep explicitly skipped loans
whose spare ceiling equaled the required target, leaving the completed loan in
place indefinitely. It also did nothing without other construction pressure.

The fix services completed finite loans even after demand retirement, using
the existing completion/restore path. This restores the original circuit recipe,
clears the temporary gate, and restores the requester group. Completed optional
ceilings also release; unfinished optional work retains its prior handoff rules.
The guard remains unchanged. No requester inventory is teleported.

The previously uncommitted surplus-transfer patch is preserved at
`/tmp/pending-loan-surplus-before-lifecycle-fix.patch` and removed from the active
implementation. It treated the retained stock symptom without releasing the
producer, and removed source items without handling partial destination insertion.

## Performance assessment

Foundation swaps improved across the three retained runs: iron/copper were
814/999s, then 755/984s, then 662/875s. Latest steel arrived at 1892s versus
1394s in the older retained run, and the latest run submitted no oil packet.
This is a downstream dependency regression, not evidence of universally slower
execution. Plate/cable stockpiles cannot replace working circuit production.

A separate historical frontier risk remains: opening oil planning resites after
partial packet submission and a coverage wait. Prior evidence shows a shifted
chemical origin. That requires its own persisted-plan reproduction; it did not
cause this latest pre-oil stall and is not claimed fixed here.

## Verification and next-run acceptance

Local behavioral regressions first failed on the stranded finite loan, then
passed through the real completion/restore-plan path. They cover stock and craft
completion, absence/presence of other blockers, unfinished loans, and completed
spare ceilings. Existing spare-preemption, bootstrap, controller and chemical
coverage remains green.

The next authorized run should show the completed splitter loan restored before
it can strand circuit production, followed by AM2 batch completion, oil-machine
submission, and actual plastic craft/provider output. Only the first lifecycle
change is established by local tests; end-to-end plastic production is unverified.
Python only: restart runner to activate, with no Lua deployment or Factorio restart
required by this change. No live actions were performed during this review.
