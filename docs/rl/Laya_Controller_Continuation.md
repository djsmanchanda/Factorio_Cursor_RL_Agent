<!-- Path: docs/rl/Laya_Controller_Continuation.md | Purpose: Keep controller implementation work moving and separate liveness, progress and acceptance evidence. -->

# Controller continuation and reporting

Controllers have no hourly or other periodic schedules (user instruction,
2026-11-14). The supervisor monitors cohorts on its existing schedule and wakes
the owner after terminal accounting and physical cleanup. A completion also
wakes any waiting controller whose explicitly queued fixture or replay becomes
eligible: A's PM replay must receive that wake when B's cohort releases the
host. A missing controller timer is expected, not a follow-through gap.

The supervisor deduplicates completion/dependency notifications and retains
material-failure alerts. Controllers do concrete review, implementation,
verification and successor queueing when woken; they do not create timers to
poll or continue independently. A plan/status-only turn does not complete an
implementation item. Keep reports in the owning controller thread; never send
routine reports to the user's review thread.

For each pending item, retain its next action, evidence path, last substantive
progress time and blocker in the controller's local handoff. If it cannot run
because a campaign is active, work offline in an isolated checkout and queue
the lifecycle action behind terminal accounting and physical cleanup. Never
patch or restart an active frozen arm.

## Cohort evidence

Use `tools/trackb_pair_report.py` for strict paired reports. A pair is eligible
only when both jobs completed and both predicates are known. A treatment-only
success is a win; missing pairs and unknown values remain explicit. Ever-reached
comes from the accumulated strict latch, even when the retained observation
tail no longer contains the pass. Held-at-end preserves null as unknown. The
CLI reports all planned statuses and completed-subset denominators; an interim
paired result does not authorize adoption.

`tools/laya_checkpoint_regression.py report` also retains early strict latches,
reports C7-C9 and resolves research targets per arm. A generic C6 pass does not
identify which recipe operated. PM diagnostics require c6-v3 evidence naming
positive `machine_consumption["productivity-module"]`, plus a placement receipt
and verified C4 replay provenance before a powered cohort is eligible.

Geometry outcomes such as planned/construction_pending have no `ok` field.
They are not tool failures. Explicit `ok: false` receipts retain their typed
code; geometry `status: failed` receipts retain their runner error type. An
explicitly empty summary fault map remains authoritative. Raw bug outcomes, runner errors and infrastructure failures must be reported separately.

## Admission and health

Validate the full planned UDP game and TCP RCON ranges before freezing, and
again before running a coordinator. Every range must fit in 1..65535. Numeric
overlap between UDP and TCP is legal. Same-protocol reuse requires an explicit
predecessor gate or released old worlds; check actual listeners before launch.

New coordinators publish a bounded `coordinator-health.json` independently of
`status.lock`, beginning after coordinator lock ownership and before recovery.
Its generation, boot, process creation and source component hashes and wall/monotonic clocks are
checked before presenting health. A fresh pulse shows only that the coordinator
health thread is alive; it does not prove dispatch progress, admission, engine
health or mission success. Missing or mismatched identity, clock regression and
legacy roots remain unknown. Terminal states describe coordinator exit, subject
to the same identity checks. Job progress and host scheduler verification are
separate. The dashboard reports unavailable aggregate status and its cause with
unknown counts while retaining a known planned denominator. Oversized files are
rejected before reading their contents; unchanged aggregate data is cached by
file identity. Unknown status does not display as zero running servers. Waiting with ongoing
progress is not a stall. Existing cohorts keep their loaded coordinator until
their cleanup.

## Adaptive concurrency (2026-11-13)

The user requested restoration of the host's measured stable-point search.
Use `tools/laya_checkpoint_regression.py run --adaptive-workers` for new and
safely resumed coordinators. This explicitly overrides only a frozen dispatch
pool cap and records `dispatch-control.json`; source, mods, seeds, speed and
acceptance predicates retain their frozen identities. Report the operational
boundary when comparing runs. Never force a fixed population of 16.

Admissions now happen once in the coordinator, before submitting execution to
its pool. Contended host admission returns immediately, so idle worker threads
cannot form a queue on the shared lock. The host gate still checks every
reservation and owns temperature, memory, UPS and aggregate-throughput limits.

`tools/laya_dispatch_handoff.py` can pause new admissions and replace a
coordinator only after every started job has finished and cleaned up, with no
remaining listeners. It preserves the active cooling request and restores the
prior automatic search target. A later manual ceiling supersedes the handoff;
the drain expires after two hours even if replacement cannot complete.
