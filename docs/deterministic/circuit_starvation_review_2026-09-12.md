<!-- Path: docs/deterministic/circuit_starvation_review_2026-09-12.md | Purpose: Preserve the causal evidence and acceptance criteria for the circuit-loan starvation fix. -->

# Circuit-loan scheduling starvation

Episode `episode-20260911T181531Z-30357`, revision `be7c7be`, used
`reduced-v1` and the original `347eba…` source. It ran **research**, not the
supervised workflow's `produce plastic-bar` acceptance target. It ended at
+3277s without science completion. No live mutation was used for this diagnosis.

## Evidence and mechanism

The run block is lines 3148–4481 of the isolated runtime's
`logs/autonomous-run.log`. At +1481s the splitter loan queues missing circuits.
The circuit loan borrows a cable cell and begins by producing cable. From
+1504s, optional AM2 promotion repeatedly returns a temporary-batch deferral.
The main loop treats the core-preparation return as a consumed pass and skips
its ordinary queued-work scheduler. The compaction summary records 248 core
promotion repetitions and 246 temporary-batch waits.

The terminal loan tuple is **target=electronic-circuit, recipe=copper-cable**.
Its `disabled_by_control_behavior` status belongs to the cable prerequisite,
not a circuit recipe that failed to enable. Cable stock is 2571 at the terminal
inventory sample; the loan needs another scheduling turn to reconcile its
completed prerequisite and select circuits. AM2 and splitter both lack circuits.
Increasing a timeout or removing the stock cap cannot repair this scheduling
path. The factory was accumulating other materials while the relevant loan was
not being serviced.

## Fix boundary and prediction

An unchanged deferred core-mall promotion must yield to queued production work.
Real promotion or a loan state transition must still consume its pass so the
next action observes the new state. Coverage-wait polling must keep its existing
cadence. Share that decision across core-mall admission paths; avoid a special
case for circuits, a coordinate, or this particular stock count.

The offline regression should reproduce a capped cable prerequisite, a blocked
AM2 promotion, and a queued circuit loan, then demonstrate that the loan reaches
its circuit step. The next fresh run should show circuit output, AM2 and splitter
batch completion, and movement toward oil/plastic. Longer survival alone is not
acceptance. If the loan gets serviced but still cannot produce circuits, inspect
its aligned requester/machine inventories, reservations and gate before changing
anything else.

## Next campaign

After loading the Python fixes, use **Start new 12h loop** in the Operations
Console. That workflow requires three fresh runs with 120 game seconds of
sustained plastic production before research. **Start fresh campaign** launches
the ordinary research target and does not establish that acceptance certificate.
The source save and preserved failed world were not reset during this repair.
