<!-- Path: docs/rl/Laya_PM_Replay.md | Purpose: Define provenance and PM-specific evidence gates for the single-world replay. -->

# C4-to-PM diagnostic replay

Use a reviewed integration tip with current-version restart state. Recovery
reconstructs both positions and chest prototypes from the normal line
constructor. Belt-feed steel chests are valid when that constructor specifies
them; a different endpoint is still rejected before transport work.

`tools/laya_pm_replay.py` performs read-only preflight and evidence checks:

```bash
.venv/bin/python tools/laya_pm_replay.py preflight --bank BANK --generation GENERATION
.venv/bin/python tools/laya_pm_replay.py verify --world DISPOSABLE_WORLD
```

Preflight checks bundle hashes, the C4 origin predicate and bound disposable
reload receipts. Existing regression-only sources remain regression-only even
when these checks pass. It does not establish a fresh reload on the candidate
code/mods. Legacy checkpoint bundles may carry unknown dispatch/retry provenance;
never clear their fences to enable a replay. The fresh diagnostic bundle
`laya-8ff36fe1e751460d9bd931413a2e69cb` in
`data/laya-orchestration/c4-fresh-c987-20261116/checkpoint-bank` has restart
version 4, C4 at tick 530756 and a hash-bound native reload receipt. It remains
regression-only evidence.

The host resident cohorts are not a blocker by themselves: a replay boots when
its own game/rcon ports are free (distinct from every cohort base) and at least
`--min-available-gb` (default 8 GiB) of memory is available. `--require-idle-host`
restores the older whole-host gate for strict runs. This exists because the
cohort queue keeps a unit resident almost continuously, so an idle-only gate
would rarely open. Prepare one disposable copy with the existing
saved-world/restart interface and an independent deadline. Verify the target mod/engine identity and C4 reload.
Exercise the existing PM service operation, recording its exact selected
operation, outcomes, bills and changed stock. Fix the first demonstrated hard
failure; do not add a priority rule from an unfunded-bill label alone. The C95b
reference s84000 trace demonstrates AM1 batch -> LH prerequisite advance -> six
LH delivered, while its PM attempts return chemical_capability_wait. That
trace does not justify an LH-priority fix.

The live replay reads network stock from its owned world adapter through the
shared economics observation projection. Raw session economics has no stock
field. Missing, failed or malformed surveys leave stock unknown and do not
authorize funding; a valid empty network survey establishes zero stock. Before
each service or funding operation, the replay reads the survey tick, scope,
source and network counts and records them with the returned outcome. An
interrupted operation may therefore lack that journal observation. These counts differ from the bill's transferable
`available` items and requester WIP. Funding keeps each failed bill's absolute
target fixed at pre-operation network stock plus its shortfall; later reads
cannot raise that target. Nested bills receive their own pre-operation survey.
Unknown before/after stock produces an unknown delta. The budget is rechecked
after surveys so an expensive observation cannot authorize a late dispatch.

The mechanism gate requires a PM placement receipt and subsequent positive
`machine_consumption["productivity-module"]` under a passing c6-v3 sample.
`verify` exits nonzero for generic C6 (including bulk-inserter), c6-v2 counters,
zero PM crafts, missing evidence or a world without explicit disposable
authority. Its success verifies PM operation only; the placement receipt and
fresh reload remain separate requirements. No cohort, adoption or production
authority follows automatically. Do not assume every C4-reaching C0 run
captured a reusable saved checkpoint.

## Clock lifecycle (15 November 2026)

Reload validation observes the captured tick while paused. Only after that check
does the replay enter live mode through the existing adapter. Each production
attempt requires a strictly newer observed tick. A stalled or regressing clock
returns `clock_not_advancing`; advancing supply waits retain their operation
cause. The journal records attempt ticks, clock limits and the terminal outcome.

The driver checks its wall deadline and 108,000-tick limit between synchronous
operations and while waiting for advancement. It cannot preempt an operation
already executing. Its newly prepared disposable server is stopped on normal
exit, failed reload or replay error; existing world roots are refused. No retained
paused replay establishes AC crafting, PM placement or PM consumption. Those
require a separately authorized fresh disposable replay using the corrected code.
