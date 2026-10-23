<!-- Path: docs/rl/Laya_Frontier_Report_Freshness_2026-10-23.md | Purpose: Record stale-observation diagnosis and preregister a separate bounded repair comparison. -->

# Frontier report freshness, October 23, 2026

The Starship continuation recovered both requested threads. The policy helper
had finished and its source was integrated in `5fa9a89`. The original supervision
turns subsequently failed; their schedules are superseded by this continuation.
Operational ownership, schedule IDs and deadlines are in the main Starship
checkout's `data/laya-orchestration/frontier-starship-20261023/state.json`.

At 2026-10-23 20:21:47 IST, sampling had completed 60/60 and research 12/12;
capital had five completed, 18 preparing/running and 37 queued, with all 60
bottleneck jobs queued. Frozen source, batch/variant/mod and source-save checks
passed. Sampling C6-v2 successes were control 7/12, epsilon .45 and temperature
3 each 4/12, family exploration and novelty each 5/12. These exploratory
contrasts do not justify adoption. Family exploration's final C5 lead is a
different endpoint and does not override the preregistered C6 selection rule.

All twelve research pilots had unavailable research acceptance and zero
measured demand rungs. In control seed 80900, the last decision was tick 609919
and its requested research report tick 609911, but its only surviving science
report was tick 238636. The bridge compared reports without their tick, deleted
fresh identical-state science reports and returned the old sample. The research
freshness validator correctly rejected it. Ramp-arm C6 differences cannot show
an effect of demand growth when the modified rung was never reached.

An independent pass over the first 500 decision records per research job,
excluding the first 300 game ticks, found **0/3,026** fresh known research
observations. It matches the operational accounting totals: control 0/717,
growth x1.25 0/1,144, growth x4 0/1,165. This bounds the observation failure;
it does not classify unknown earned research as measured failure.

The repair includes the observation tick in report identity. Exact same-tick
duplicates can still share a file; later observations retain their real tick,
bounded by the existing 200-report retention window. No freshness, funding,
legality, recovery or earned-research predicate changes. A bridge-to-research
regression fails before the repair and passes afterward. Seventy focused checks
pass; the required fast gate has 1,354 passes and one skip. No new live repair
result is established by those tests. Active frozen cohorts remain unchanged.

## Supplemental repair comparison

Before any admission, freeze the repair commit and a separate immutable batch
with these parameters. This is a diagnostic repair comparison, separate from
the original screens and their optional confirmation cohort.

- Twelve planned jobs: `control` versus `fresh-ticks`, six matched policy seeds
  81100–81105, alternating arm order. Control remains `5fa9a89`; the repair arm
  differs only in report identity and its regression/documentation changes.
- Same source save, engine 2.1.23, private mod copies, default guided policy,
  mining-productivity-4 objective and .25/doubling curriculum in both arms.
- 540,000 game ticks and 6,000 wall seconds per trial; at most six concurrent
  worlds, ports 39500–39505 / 31500–31505, using the existing shared registry.
- Wait for the original bottleneck cohort before admitting or reserving worlds.
  The cohort must stop admission when a full wall allowance no longer fits
  before 2026-10-24 03:46:47 IST; the hard end stays 03:51:47 IST. Unstarted
  jobs remain in the twelve-job denominator. No window extension or retries.
- Primary diagnostic endpoint: fresh research-observation availability after
  the initial 300 game ticks, measured from recorded decision/status evidence.
  Record available/known observations, eligible decisions and planned jobs.
  Also report offered/selected research operations, demand-rung exposure,
  earned research, C6-v2, strict ever/final and typed faults separately.
- Count every status and attempt. Availability alone is not research success;
  a research operation may change telemetry, so an unchanged-state failure is
  established by the bounded source regression rather than assumed for every
  control trajectory. No automatic adoption or policy training.

Record batch/variant/source/mod hashes, code revisions, exact owned unit and
systemd deadline in a supplemental freeze and operational state. Do not edit
the original window's frozen batches, source copies or variants. Future checks
must include this supplemental cohort without merging its denominator or
endpoint with the original research-growth pilots.

## Queue and supervision verified (2026-10-23 20:40 IST)

Repair source `3cf5f37` is frozen separately. Unit
`laya-frontier-freshness-r2-20261023.service` is active with all twelve jobs
queued, zero reserved slots and dependency `bottleneck`. Its systemd limit is
25,842 seconds from 20:40:34 IST with 30-second cgroup cleanup. The first
supervisor invocation exited before any world attempt because its Python
module path was missing; that launch and journal remain recorded separately.

The old Starship monitor/terminal schedules were backed up and disabled.
Replacement schedules are enabled in Miniship thread
`94dcfaf2-9531-4234-bde5-b6ae616de9b1`: next monitor October 23 21:02:30 IST,
then every thirty minutes; terminal accounting October 24 03:53 IST.
Hard testing cutoff remains 03:51:47 IST. Exact IDs and launch attempts are in
remote operational state. No running arm was restarted or patched.
