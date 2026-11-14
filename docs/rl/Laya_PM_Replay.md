<!-- Path: docs/rl/Laya_PM_Replay.md | Purpose: Define provenance and PM-specific evidence gates for the single-world replay. -->

# C4-to-PM diagnostic replay

Use the c6-v3 candidate branch containing the feed-contract repair. Recovery
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
code/mods. The known local diagnostic candidate is generation
`laya-f33dd24b9c04408797c719d31213b70a` in the mixed-checkpoint-after-c46-20261028
bank: C4 at tick 514525, one validated receipt, acceptance_eligible false.

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

The mechanism gate requires a PM placement receipt and subsequent positive
`machine_consumption["productivity-module"]` under a passing c6-v3 sample.
`verify` exits nonzero for generic C6 (including bulk-inserter), c6-v2 counters,
zero PM crafts, missing evidence or a world without explicit disposable
authority. Its success verifies PM operation only; the placement receipt and
fresh reload remain separate requirements. No cohort, adoption or production
authority follows automatically. Do not assume every C4-reaching C0 run
captured a reusable saved checkpoint.
