<!-- Path: docs/deterministic/checkpoint_evidence_contract.md | Purpose: Define sampled strict checkpoint evidence and restart provenance. -->

# Checkpoint evidence and sampling

`checkpoint-sampling-v2-adjacent-exact-dedup` identifies future rows: exact adjacent equal snapshots collapse to their first object, including equal newly allocated objects; changed same-tick samples remain. Current historical predicate versions are unchanged. Current status, first observed earned transition, inherited baseline and optional explicit proxy phase are distinct. A failed poll makes current status unavailable and establishes a gap; it cannot create an earned transition or consumer delta across that gap. Inherited status is never an earned event. Version 1 restart bundles preserve latches but have unknown origin and gap history; no earned event is inferred from them.

The accumulator retains the first failed-to-passed observed event per checkpoint, first sampled positive consumer delta per unit and observed recipe generation, inherited baseline predicates, last successful status/tick, active consumer identities and counters, generation numbers, failed-poll count, and the latest 100 generation/gap boundaries (with a dropped count). Active identities, first consumer events, and generation identities each have a 256-unit bound; generation identities are evicted in insertion order after that bound, so identity after eviction is unknown. This is not complete episode history. Retarget, disappearance, replacement, counter reset, a regressing or missing tick, absent consumer telemetry and failed polls establish boundaries. A same-unit same-recipe sampled increase does not prove there was no unseen retarget; it is sampled evidence only. Logistics chests and invalid counters are excluded. Generic consumers, exact productivity-module recipe consumption, and research are separate facts.

The current bounded 20-sample projection retains adjacent positive-tick plastic counter changes, including flat intervals and recovery. There is no specified maximum gap or required rate, so continuity and coverage remain unknown even across 120 game seconds. No new C5 predicate is claimed. Historical c5-v1 remains sample sensitive. Historical c6-v3 remains available independently: its retained-history calculation can bridge a failed poll because failure markers are not snapshots; its result is not corrected evidence of uninterrupted consumer observation. The accumulator's consumer delta never bridges a recorded failure or missing telemetry. Neither establishes unseen continuity. Proxy phase is null unless explicitly supplied by the caller and is never inferred from strict reached flags.

Restart version 2 carries the accumulator and preflights it before installing sidecars. Future frozen runners must use this source and explicitly pass proxy phase when available. These are source contracts, not live or cohort results.

## Version 3 invocation transfer

Restart bundles now include the exact episode-owned operation invocation directory.
New journals record the episode explicitly; older local journals retain their
directory scope. Import validates names, scope, payload shape and digests before
installing any sidecars, then flushes files and their directory entries. Unknown
or interrupted dispatch remains unknown and fences subsequent session operations,
constructor maintenance and direct invocation submissions. Returned submissions
and empty pre-dispatch records do not fence. Failed writes roll back installed
files; symlinks cannot change journal ownership.

Version 1 and 2 imports install an unknown-provenance marker. Re-exporting that
state preserves the marker; it does not prove the absence of an earlier dispatch.
Correlated readback is required before configuration uncertainty can be cleared.
The existing 256-file and 16 MiB transfer bounds still apply; overflow refuses
export/import rather than omitting journals. This is a bounded uncertainty
fence, not full controller or loan restart equivalence. Background submissions outside the selected invocation journal remain separate
acceptance work. Runtime manifests retain their own immutable startup checks.

## Version 4 loan gate retry transfer

Version 4 adds the bounded episode-owned loan gate retry file (256 records,
256 KiB). Charges are flushed to disk and their directory entries before
configuration dispatch. A charge remains spent if dispatch or receipt observation
fails; invocation uncertainty independently fences further work. Reopening the
actual session constructor does not reset the budget. The key includes the
cell, lender, borrower, loan demand and step demand, while changes to a
configuration group's encoded craft baseline do not create a new budget.

The three-refresh limit and fed-cell/mismatched-readback requirements are
unchanged. Same or regressing ticks and missing or regressing craft counts
cannot charge or replenish it. Observed craft advancement above the durable
high-water mark resets the attempt count while retaining the last charged tick.
A missing historical budget starts unknown; it waits for observed advancement.
A newly proposed loan can initialize a known budget before initial configuration.
Unscoped callers cannot issue gate refreshes. This preserves safe refusal when
no episode-owned durable state can be established.

Version 1–3 imports create an unknown retry-provenance file; version 1–2
imports also retain the existing invocation-uncertainty marker. Re-export does
not upgrade either provenance claim. Payload/scope/type/digest validation and
all import preflight complete before installing any sidecar. File and directory
flush failures stop dispatch. The single-runner ownership rule still applies;
this file is not a multi-writer coordinator. The separate ops-generation
checkpoint path also captures this exact episode-owned file instead of the
retired Python attempt dictionary; its existing source-hash checks still refuse
old incompatible generations.

This contract covers bounded retry conservation in offline fixtures. It does
not prove full loan/controller restart equivalence, machine identity continuity,
correlated configuration readback, delivery, or live efficacy. A reviewed new
frozen runner is required for future use; no runtime action was performed.
