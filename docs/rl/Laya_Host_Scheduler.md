<!-- Path: docs/rl/Laya_Host_Scheduler.md | Purpose: Operate the shared slot budget and migrate future cohorts without overstating live evidence. -->

# Host slot scheduler

`tools/laya_host_scheduler.py` owns the host ceiling; the fixed pool in
`tools/run_laya_batch.py` admits worlds against its fresh gate. Performance starts
at 24 and permits at most 30 worlds; balanced permits 26 and cool permits 20.
`powerprofilesctl get` selects the mode automatically. Miniship's configured limits
(`data/laya-host/config.json`, 2026-10-22) are 95 C (97 C hard) for
performance/balanced, 85 C for cool, and UPS floors of 100 (p95) / 90 (p98). A trial
keeps its extra world whenever aggregate UPS does not fall (`min_total_ups_gain` 0,
set 2026-10-22 so the count grows while there is headroom). These
are configured ceilings, not measured sustainable capacities.

The scheduler reads decision-log UPS, temperature, memory and runner liveness.
Growth compares aggregate UPS after the increased population has a complete
measurement window. Incomplete trials do not establish a sweet spot. A safety
cap applies even while the population or its measurements are incomplete.
Different factory phases still confound aggregate UPS: validate campaign jobs
completed per wall-hour in matched windows before claiming a throughput gain.

## Future cohorts

Freeze the committed scheduler-aware batch harness and matching runner code.
Use a cohort root directly under the shared registry and distinct port bases.
For Miniship the registry is
`/home/miniship/Github_Desktop/Factorio_Cursor_RL_Agent/data/laya-experiments`.
Configure the fixed pool with:

```json
{
  "concurrency": 30,
  "adaptive": false,
  "host_budget": {
    "registry": "/home/miniship/Github_Desktop/Factorio_Cursor_RL_Agent/data/laya-experiments",
    "stale_seconds": 180,
    "admit_interval_seconds": 10,
    "admit_min_available_gb": 4.5
  }
}
```

The repository runner has no per-cohort scaler. It reserves each admission under
a shared Linux file lock and publishes `preparing` before releasing that lock.
It counts all cohorts' preparing/running worlds, including legacy cohorts and
stale occupancy records. It ignores old per-cohort ceilings such as C14b's 14;
their real worlds still consume slots. A missing, invalid or stale gate blocks
new admissions. Failed cleanup retains its reservation until the owner checks
the world and reconciles the status. The published `allocations` field is
diagnostic; it does not enforce fairness between cohorts.

All participating new supervisors must share the registry and this protocol.
Legacy supervisors do not take the admission lock and discard stale gates after
900 seconds, so mixed operation lacks the new protocol's host-wide guarantee.
Keep legacy constraints explicit while they drain.

**Do not migrate by setting `adaptive: false` on the old Miniship harness.** Its
admission check depends on the legacy scaler's decision and stays at `start`
when that scaler is disabled. Use the new harness for fresh cohorts. It refuses
to overwrite an existing `job-status.json`. `--resume` retains terminal outcomes
and records unfinished attempts before rerunning them in fresh worlds; it does
not reattach running worlds. Use it only after confirming the old supervisor
and its worlds are stopped. Let active frozen cohorts finish. Replacing an idle
cohort requires stopping its old supervisor, retaining its freeze/status and
all planned-job accounting, then launching a new recorded cohort. Do not patch
active frozen code or silently discard previous attempts.

Runtime controls are optional and recorded in each world's manifest:
`runtime.update_threads`, `runtime.cpu_affinity` (a taskset list or `slot`),
`runtime.nice`, and `runtime.python_nice`. Python niceness applies only to the
decision runner; preparation leaves the engine's inherited priority alone.
Keep runtime settings identical across matched arms unless that setting is the
declared experimental factor.

## Controls

Run commands from the repository root with the same explicit state paths as the
service. For the currently installed Miniship copy:

```bash
H=/home/miniship/.local/share/laya-host/laya_host_scheduler.py
.venv/bin/python "$H" --root data/laya-host --registry data/laya-experiments status
.venv/bin/python "$H" --root data/laya-host --registry data/laya-experiments target 12 --for 2h
.venv/bin/python "$H" --root data/laya-host --registry data/laya-experiments pause --for 1h
.venv/bin/python "$H" --root data/laya-host --registry data/laya-experiments resume
.venv/bin/python "$H" --root data/laya-host --registry data/laya-experiments bump +2
.venv/bin/python "$H" --root data/laya-host --registry data/laya-experiments mode auto
```

`--until "2026-10-21T22:00:00+05:30"` is an alternative to `--for`.
Commands take effect on the next service cycle (up to 60 seconds); expiry also
uses that cycle. Pause means target zero: running worlds finish, and new ones
wait. Expiry/resume restores the saved target and automatic search. Changing
`configure --start` alone does not reset an existing search; `reset` does.

Two ways to change the slot count:

- **`target N` pins a ceiling.** The count stays at N (lower only under a safety
  cap) and automatic growth stops until `resume`/`clear-target` or expiry, which
  restore the previous target.
- **`bump N` or `bump +N` nudges the search.** It clears any manual target and
  restarts the automatic search from the new count. The scheduler then keeps it
  while healthy, tries higher when aggregate UPS improves, and steps down on the
  UPS floors, temperature, memory or an unstable runner. Use it to say "try more"
  without taking control away from the scheduler.

## Installation and lifecycle

`systemd/laya-host-scheduler.service` is a user-unit template for the conventional
checkout path. Installation copies the verified script to
`~/.local/share/laya-host/laya_host_scheduler.py` and the unit to
`~/.config/systemd/user/`, followed by daemon reload and a scheduler restart.
Inspect active `laya-*` units and deadlines first. Deployment is a separate,
explicitly authorized lifecycle action; a repository commit does not update the
installed copy. Preserve `data/laya-host/config.json`; read `status` afterward
to verify mode, ceiling, gate freshness and actual worlds. No Factorio/mod restart
is required for scheduler-only changes. Cohort code requires a new freeze.

## Audit evidence, 2026-10-21

Thread `d3a4dea1-e1c3-46b4-bca5-3e3f7c3bc4ad` deployed the earlier scheduler.
A read-only check at 16:35 IST confirmed start 24 / maxima 30,26,20,
target 24, actual 12 worlds, 244 queued, and both legacy supervisors active.
The source and installed scheduler hashes matched at audit start. The admission
integration and trial fixes in this document have offline regression evidence;
they have not been deployed or exercised in a live campaign.

The one-update-thread result is a short offline benchmark, not a live throughput
gain. Thread ordinal 3, activity 265–279: copied C14b `steelbill-s79404` took
30.733/32.814 ms for 600 updates with one thread versus 217.215 ms with 16,
checksum 2399675684; affinity varied and samples were single runs. Another save
gave 49.550/54.489 versus 480.379/495.873 ms. The thread's combined range mixed
those conditions. Test `update_threads=1` cohort-wide in a recorded comparison
before adopting it as a performance default.

## Review fixes prepared on Starship (2026-10-22)

Soft thermal derating now requires three consecutive samples (`thermal_samples`),
while a low UPS tail requires two (`unhealthy_samples`). A soft reduction holds
for `settle_windows * window_seconds` before another soft drain. A transient hot
sample inhibits growth without lowering admission. Hard temperature, low-memory
and unstable-runner caps still apply immediately, including during settling.
Existing configured temperature/UPS thresholds are unchanged. The knobs are
exposed as `configure --thermal-samples N --unhealthy-samples N` (1–10).
These are tested control semantics, not demonstrated live throughput gains.

Test update-thread count in a separate frozen arm with `runtime.update_threads`;
do not combine it with the controller change when estimating either effect.
Activation requires the handler to synchronize the verified script and restart
only the scheduler at a recorded campaign boundary. Active frozen arms are not
changed by this repository work.

## Cohort admission and durable attempts (2026-10-23)

New frozen `run_laya_batch` supervisors support `--admission drain|run|cancel_queued
--request-id ID --reason TEXT` (optional repeated `--job-id ID` for selective
cuts). Requests are idempotent; reusing an ID with different content fails.
Drain stops new admissions while reaping, heartbeats, reports and cleanup
continue. It does not signal running children. Cancel affects only queued jobs.
The supervisor remains alive until work is resumed, cancelled or deadline-cut.
Never SIGSTOP a supervisor to edit its queue.

`planned-jobs.json` preserves the original denominator; `batch-events.jsonl`
retains requests, cuts and complete attempt receipts. Interrupted queue retries
remain fresh-world attempts with full prior evidence. `--resume` refuses a live
owned runner/world and a second supervisor is excluded by a lifetime lock.
Cleanup errors keep their world reservation and slot; other completions still
reap. Exit/collection and cleanup timestamps are separate. A game-window stop,
wall cap, cohort deadline and operator stop have distinct causes.

A manifest's `depends_on` lists predecessor cohort roots. Missing, unknown or
unreconciled occupancy blocks admission without reserving slots. Terminal
predecessors unlock only execution, never candidate adoption. Freeze the chosen
baseline and contrast explicitly. Live/final v3 reports retain all planned jobs
and unknown-aware final, choice/output and research pair counts. Older frozen
supervisors do not gain these controls; do not modify active freezes.
