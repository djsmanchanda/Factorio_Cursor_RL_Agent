<!-- Path: docs/rl/Laya_Frontier_Completion_2026-10-24.md | Purpose: Record the authorized completion of the 72 previously unstarted Starship frontier jobs. -->

# Starship frontier completion, October 24, 2026

The user authorized a new twelve-hour Starship window for the 72 omitted jobs
and explicitly requested **no notification to the Opus controller**. This
supersedes the earlier closed-state lifecycle instructions for this follow-up;
the [original terminal result](Laya_Frontier_Result_2026-10-24.md) remains an
unchanged account of the first window.

The completion window starts **2026-10-24 01:21:41 IST**, ends at
**13:21:41 IST**, and reserves its final five minutes for cleanup. Cohort end
is **13:16:41 IST**. Only the originally cancelled, never-started IDs may be
admitted. No completed trial, research pilot, smoke or freshness trial is rerun.
No confirmation, A/A, adoption or extra experiment is authorized by this plan.

Capital retains its original 6,000-second allowance for the final 12 jobs,
preserving its comparison with the 48 already completed. The omitted IDs are
foundation8 and batch-free at seed 80709, and all five arms at seeds 80710 and
80711. Bottleneck runs all 60 original jobs, seeds 80800–80811 across its five
arms, with the user-selected 7,200-second allowance applied before any admission.
Both cohorts retain 540,000 ticks and their original arm rotation and predicates.
The last full-budget admissions are 11:36:41 IST for capital and 11:16:41 IST
for bottleneck; no window is shortened to admit another job.

## Frozen identity and accounting

Operational root on Starship:
`/home/djsm/Github_Desktop/Factorio_Cursor_RL_Agent/data/laya-orchestration/frontier-completion-starship-20261024`.
Read its `state.json` first. Source remains the original frozen commit
`5fa9a8938935b8df0bddb7de71c89ab9cd3ba36a`; the source-save SHA-256 remains
`347eba65065014f82261330d84d5ca2fa3e3427cad3ab18dcfdf496dd9c442b0`.
Factorio remains 2.1.23, build 87750. Source, project mods, variants, seeds,
geometry, funding, ownership, tick windows and acceptance predicates are unchanged.

The original terminal state, final accounting, cohort plans, cuts, statuses and
events are preserved at the original paths, with hashes and archived copies in
the new window's `original-evidence/`. New manifests, freeze records and ledgers
live under `cohorts/{capital,bottleneck}/`. The capital status carries all 48
completed records field for field; only its 12 omitted IDs become queued in
this separate ledger. Bottleneck has 60 queued IDs and no carried completions.
Thus the joined ledgers have 120 rows, comprising **72 new admissions and 48
carried completions**. They are not 120 new trials. Original cancellation events
remain evidence; they are linked rather than replayed as new cancellation orders.

Capital launched first. At 01:35 IST, the user's request to start six more jobs
authorized bottleneck to overlap capital. Their cohort concurrency remains 18.
The shared host target is temporarily 18, admitting six bottleneck worlds beside
the 12 capital worlds; bottleneck may fill freed slots as capital finishes.
Restore the prior target 22 once capital is terminal with zero reservations.
Maximum 24, thermal settings 200/210, and the original memory and liveness
controls are preserved. Private
world namespaces end in `-completion-20261024`; capital ports start at
39500/31500 and bottleneck at 39600/31600. Existing world evidence and installed
mods are untouched. No real-base mutation or installed-mod deployment occurs.

All new trials use the original source, including its known stale-research-report
limitation. This window completes the original C6/strict-checkpoint comparisons;
it does not test the freshness repair or establish factory-earned research.
Report the capital completion epoch separately from its earlier jobs, and
compare every candidate to its own cohort control. Do not pool the 7,200-second
bottleneck comparison with the earlier 6,000-second cohorts. Timing is also
subject to the changed host-load epoch.

## Launch evidence and supervision

Preflight instantiated the frozen runner and exercised its resume path: exactly
72 jobs were queued, all 48 carried records were unchanged, and source/save/mod/
variant/plan checks passed. At **01:24:34 IST**, all three new services were
active: eight capital jobs running, four capital queued, all 60 bottleneck jobs
waiting for capital, zero freeze errors. A new private manifest has the canonical
source hash, and its runner log records progressing funded ghost construction.
This is observed startup/progress, not an experiment result. Original source
tests are reused; no runtime implementation changed in this follow-up.
At **01:27:17 IST**, all 12 omitted capital jobs were running and all 60
bottleneck jobs remained queued; carried records and freeze checks still passed.

Owned user units are `laya-frontier-completion-host-20261024.service`,
`laya-frontier-completion-capital-20261024.service`, and
`laya-frontier-completion-bottleneck-20261024.service`. Their recorded
`RuntimeMaxSec`, 30-second `TimeoutStopSec` and `KillMode=control-group` bound
the window independently of model authentication. Unit expiry is set before
the deadline with termination margin.

Completion-specific timers were initially created at a 55-minute cadence and
13:23 IST for terminal accounting. **Both are currently disabled, with no next
wake**, superseding the earlier 02:23:07/13:23 schedule notes. A bounded trace
review at 02:49 IST found T3 UI WebSocket calls to `scheduledTasks.setEnabled`:
the old-window monitor was disabled around 01:25 IST and the completion-specific
monitor around 01:36 IST; both corresponding terminal timers are also disabled.
Do not recreate timers to override that UI state. The user has been offered an
optional choice to re-enable checks here; leave them off unless requested.
Exact task IDs/readbacks are in `state.schedules`. Both completion tasks are bound to thread
`94dcfaf2-9531-4234-bde5-b6ae616de9b1`; no new thread or subagent was created.
The shared Opus/Miniship observer was left unchanged and
no controller notification was sent. The parent remains on managed Personal_2;
its token-refresh risk is not repaired by timers or service runtime limits.

On a user continuation or an explicitly re-enabled wake, run this window's
`checkin.py --cleanup` using the repository
venv via `ssh starship`. It checks original evidence hashes, completion freezes,
all mapped statuses and the carried records, and preserves a timestamped
observation. When all new jobs are terminal and cohort units are inactive, it
stops only the owned idle scheduler. At the hard end, verify exact owned cgroups
and world processes are empty, retain every failed/unstarted/interrupted or
unknown outcome, publish final own-control accounting, and disable both owned
schedules. Leave other-owner schedules and campaigns untouched. Do not cancel
queued jobs to reprioritize another diagnostic or to select an interim winner.

## Six additional concurrent jobs, 2026-10-24 01:35 IST

The user requested six more starts if the existing work was healthy. At
01:32:59 IST all 12 capital heartbeats were fresh, no process failure was
observed, and 10.9 GiB was available. The host reported about 100 mean UPS;
the manual host target remained authoritative. This is startup/liveness
evidence, not evidence that the experimental policies are successful.

Before any bottleneck admission, only its waiting supervisor was stopped.
Original manifest/freeze/status/plan/events and host configuration were archived
under `amendments/parallel-six-20261024-v1/`. The sole batch change removes
`depends_on: [capital]`; concurrency18, all60 IDs/order,7200-second allowance,
source/variants, tick cap and original deadline remain unchanged. The stopped
controller had no admitted world or attempt; its shutdown receipt is retained.
No capital world was stopped or restarted. Amended hashes and an append-only
preadmission receipt are in the completion ledger/state.

The supported scheduler CLI set target18 and published a fresh18-slot gate
before the bottleneck supervisor resumed with its remaining original systemd
allowance. This bounds the initial activity to12 capital plus6 bottleneck worlds
without introducing a second set of trials. Existing memory/liveness admission
checks still apply. Both owned timer prompts carry this amendment and the
instruction to restore target22 when capital finishes, but the timers are now
disabled as described above. The13:21:41 hard end and no-Opus-notification
requirement remain. Preserve the differing load epoch when reporting timing.

## Capital complete; bottleneck continues, 2026-10-24 02:51 IST

All 12 omitted capital jobs completed, giving 60/60 original capital jobs and
12 known endpoints per arm. C6-v2 successes are control 6, mall-flat 6,
foundation8 6, batch-free 7 and steel30 8. Own-control discordant pairs are
2:2, 3:3, 3:2 and 4:2 respectively; steel30's paired difference is +16.7 percentage
points. Strict C6 ever/final is 2/1 for both steel30 and control. Strict C5-final
is 2/12 versus 0/12, a point observation rather than sustained service.
All 60 capital jobs have zero caught exceptions; typed bug events are separate:
control 76, mall-flat 18, foundation8 16, batch-free 4, steel30 9. These are
repeated events, not affected-job counts. There is no adoption or confirmation.
The new 12-job epoch overlaps bottleneck; retain this load/timing caveat.

The archived capital result is
`observations/capital-complete-20261024T0250IST.json` (observation 02:51 IST),
linked with its hash from `state.capital_complete`. At 02:49 IST the new 72-job
accounting was 26 completed/18 running/28 queued: capital 12 complete and
bottleneck 14 complete/18 running/28 queued, with zero freeze errors.

At 02:49:50 IST, after verifying all 60 capital records terminal and no capital
reservations, restored the user-selected host target 22 through the supported
CLI. Bottleneck retains its 18-world cohort limit; no world was restarted or
budget changed. The command/readback receipt is
`amendments/parallel-six-20261024-v1/target22-restore.json`. The capital unit is
inactive; host and bottleneck units continue under the existing bounded window.
With T3 timers off, systemd still enforces the cutoff; final accounting and
earlier idle-host cleanup need a user continuation or re-enabled monitoring.
