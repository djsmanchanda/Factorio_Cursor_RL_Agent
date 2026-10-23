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

At **04:01 IST**, the user explicitly requested re-enabling checks in this
thread. Both existing completion-specific timers are now enabled: the
**55-minute monitor next runs at 04:56:53 IST**, and terminal accounting runs
at **13:23 IST** on October 24. This supersedes the prior disabled/no-next-wake
state. No new task IDs or threads were created. A bounded earlier trace review
identified T3 UI `scheduledTasks.setEnabled` calls behind the earlier disabling;
respect any future UI disable rather than recreating tasks to override it.
Exact task IDs/readbacks and the user-authorized reactivation are recorded in
`state.schedules`. Both completion tasks are bound to thread
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
instruction to restore target22 when capital finishes; that restoration is now
complete and timer prompts have been refreshed. The13:21:41 hard end and no-Opus-notification
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
At that observation the T3 timers were off; systemd independently enforced the
cutoff. The user's later reactivation below restores scheduled accounting.

## Checks re-enabled in this thread, 2026-10-24 04:02 IST

On the user's explicit answer, re-enabled the same completion-specific monitor
and terminal tasks, both bound to this thread. The next monitor is 04:56:53 IST;
the 13:23 IST terminal check remains a fallback. Refreshed prompts describe the
current parallel workflow, already-restored host target22, completed capital,
frozen inputs, original deadline, no extra trials/adoption and no Opus messages.
They stop the owned idle host and disable both completion timers once all mapped
jobs are terminal and cleanup is verified. The shared Opus observer is unchanged.

The immediate check at04:01:14 IST found58/72 new jobs completed and14 running,
none queued:capital12 complete, bottleneck46 complete/14 running. Source/freeze
checks passed; capital unit inactive, host and bottleneck units active. This is
an interim status, not a final bottleneck result. No runner, world, manifest,
budget or systemd deadline was changed while restoring the timers.

## Stay awake until done, then suspend (2026-10-24 04:22 IST)

The user first authorized keeping Starship awake until the remaining jobs
finish, then explicitly requested automatic sleep once done. At04:15 IST the
window had64/72 new jobs complete and8 running, with no freeze errors.

A temporary user service, `laya-frontier-completion-awake-20261024.service`,
holds a verified `sleep:handle-lid-switch` block inhibitor. Its `sleep_guard.py`
checks job states and owned world units, runs a final `checkin.py --cleanup`
once jobs are terminal and world units inactive, saves
`sleep-guard-final-checkin.log`, and exits to release the blocker. The existing
campaign hard end bounds the guard. No desktop configuration changed.

The SSH session's `CanSuspend` returned `challenge`, so automatic suspend needed
one YubiKey-approved root command. The resulting one-shot SYSTEM service
`laya-frontier-autosleep-20261024.service` is armed. Its completion wait,
`auto_sleep_wait.py`, runs as `djsm`, not root. Only a successful wait triggers
the privileged `ExecStartPost=/usr/bin/systemctl suspend`, once. No password,
SSH/polkit authorization change, permanent power policy or recurring suspend
timer was introduced.

The wait requires all120 joined records terminal, no reservations, verified
cleanup, no live owned world processes or freeze errors, inactive host/capital/
bottleneck units, and the temporary guard released/inactive. It cancels without
sleep if those conditions are not met by the campaign hard end plus90seconds.
Setup/verification are in state.sleep_guard/state.automatic_sleep; live receipts
are `sleep-guard.json` and `auto-sleep.json`. Unit readback confirms the normal
user wait is active and the privileged suspend action is registered. Actual
suspend has not yet occurred and is intentionally deferred until completion.

Both existing timer prompts record the user's intent. After automatic suspend,
SSH unavailability is expected and does not establish failed jobs or authorize
a restart. Final reports remain on Starship; retrieve them when it is reachable.
No Opus notification was sent.

## Completion and log transfer (2026-10-24 04:54 IST)

All72 new jobs completed; the joined capital60/bottleneck60 accounting and all219
unique Starship results are now local. The final selected logs/evidence copy
(5,246 files/3,354,952,365 bytes) was hash-verified and acknowledged at04:41:36
IST. Local completion receipts show cleanup complete, no owned world processes
and inactive host/capital/bottleneck units. The additional copy hold released
for the approved automatic suspend; actual kernel suspend remains unobserved.

[The transfer and C28 decision](Laya_Starship_Transfer_Decision_2026-10-24.md)
records complete own-control outcomes, intervention exposure and the calibrated
Miniship follow-up. It supersedes earlier pending-results/timer instructions:
the same two owned schedules now monitor C28 locally,55-minute checks plus
October27 18:07 IST terminal accounting. Do not SSH/wake Starship or disable
these schedules merely because Starship finished. No Opus notification or
shared-observer change.
